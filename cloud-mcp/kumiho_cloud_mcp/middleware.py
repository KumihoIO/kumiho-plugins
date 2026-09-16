"""ASGI middleware: body cap, request timeout, and no-store/noindex headers."""

from __future__ import annotations

import json
import logging
from typing import Iterable, Optional

import anyio

logger = logging.getLogger("kumiho.cloud_mcp.http")

_BODYLESS_METHODS = frozenset({"GET", "HEAD", "DELETE", "OPTIONS"})


async def _send_json(send, status: int, payload: dict, extra_headers: Iterable = ()) -> None:
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """Reject request bodies over ``max_bytes`` with 413.

    The body is buffered and replayed so the limit is enforced even when the
    client lies about ``Content-Length``. Requests that cannot carry a body are
    passed straight through — buffering a ``GET`` would swallow the
    ``http.disconnect`` that keeps an SSE stream alive.
    """

    def __init__(self, app, max_bytes: int = 2 * 1024 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method", "").upper() in _BODYLESS_METHODS:
            return await self.app(scope, receive, send)

        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await self._reject(send)
                except ValueError:
                    pass
                break

        body = bytearray()
        more = True
        while more:
            message = await receive()
            kind = message["type"]
            if kind == "http.request":
                body += message.get("body", b"")
                if len(body) > self.max_bytes:
                    return await self._reject(send)
                more = message.get("more_body", False)
            elif kind == "http.disconnect":
                return
            else:  # pragma: no cover - defensive
                more = False

        buffered = bytes(body)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            # Delegate afterwards so disconnects still reach the app.
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send) -> None:
        await _send_json(
            send,
            413,
            {
                "error": "payload_too_large",
                "error_description": f"request body exceeds {self.max_bytes} bytes",
            },
        )


class TimeoutMiddleware:
    """Cap how long a single request may run.

    Applied only to methods that carry a JSON-RPC payload. A streamable-HTTP
    ``GET`` is a long-lived event stream by design and is exempt. If the app
    has already started a response when the deadline hits we let the
    cancellation tear the connection down rather than emitting a second
    response head.
    """

    def __init__(self, app, seconds: float = 60.0) -> None:
        self.app = app
        self.seconds = seconds

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or self.seconds <= 0
            or scope.get("method", "").upper() in _BODYLESS_METHODS
        ):
            return await self.app(scope, receive, send)

        started = False

        async def tracking_send(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with anyio.move_on_after(self.seconds) as cancel_scope:
            await self.app(scope, receive, tracking_send)
        if cancel_scope.cancelled_caught and not started:
            logger.warning(
                "request timed out", extra={"path": scope.get("path"), "timeout": self.seconds}
            )
            await _send_json(
                send,
                504,
                {"error": "gateway_timeout", "error_description": "request exceeded the time limit"},
            )


class SecurityHeadersMiddleware:
    """``Cache-Control: no-store`` + ``X-Robots-Tag: noindex`` on every response.

    Everything this service serves is either tenant data or discovery metadata
    that must be re-read; nothing here is safe for a shared cache, and none of
    it should be indexed.
    """

    def __init__(self, app, *, extra: Optional[dict] = None) -> None:
        self.app = app
        self.extra = extra or {}

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def wrapped(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                if b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                if b"x-robots-tag" not in present:
                    headers.append((b"x-robots-tag", b"noindex"))
                for name, value in self.extra.items():
                    key = name.lower().encode()
                    if key not in present:
                        headers.append((key, value.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped)
