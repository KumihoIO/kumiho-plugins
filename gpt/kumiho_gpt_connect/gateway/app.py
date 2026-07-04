"""Starlette app: OAuth AS + Bearer-checked streaming reverse proxy to the
inner MCP server.

Only the MCP paths are guarded. Everything under the OAuth well-knowns,
``/register``, ``/authorize``, ``/token`` and ``/jwks`` is public by design —
that is how ChatGPT bootstraps the flow. An unauthenticated MCP request gets a
401 whose ``WWW-Authenticate`` header points at the protected-resource
metadata, which is exactly how an MCP client discovers where to authenticate.
"""

from __future__ import annotations

from typing import Iterable

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from .. import config as cfgmod
from .oauth import OAuthServer

# Headers we must not blindly copy across the proxy hop.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "content-encoding", "host",
}

# The public, unauthenticated OAuth endpoints only ever receive tiny bodies.
# Cap them so an anonymous caller can't OOM the process with a giant POST
# before any validation runs. The proxy path is exempt (it streams).
_OAUTH_BODY_PATHS = frozenset({"/register", "/authorize", "/token"})
_MAX_OAUTH_BODY = 64 * 1024


class BodyLimitMiddleware:
    """ASGI middleware: reject over-sized bodies on the OAuth endpoints (413)."""

    def __init__(self, app, max_bytes: int = _MAX_OAUTH_BODY, paths=_OAUTH_BODY_PATHS) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self.paths:
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        return await self._reject(send)
                except ValueError:
                    pass
                break
        body = bytearray()
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if len(body) > self.max_bytes:
                    return await self._reject(send)
                more = msg.get("more_body", False)
            elif msg["type"] == "http.disconnect":
                return
            else:
                more = False
        buffered = bytes(body)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    async def _reject(self, send) -> None:
        payload = b'{"error":"payload_too_large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode())]})
        await send({"type": "http.response.body", "body": payload})


def build_app(cfg: cfgmod.Config, inner_base_url: str) -> Starlette:
    oauth = OAuthServer(cfg)
    # read=None keeps long-lived SSE streams open, but connect/write/pool are
    # bounded so a slow client/inner can't tie up connections indefinitely.
    client = httpx.AsyncClient(
        base_url=inner_base_url,
        timeout=httpx.Timeout(None, connect=10.0, write=30.0, pool=10.0),
    )

    def _challenge() -> dict:
        rm = f"{cfg.issuer}/.well-known/oauth-protected-resource"
        return {"WWW-Authenticate": f'Bearer resource_metadata="{rm}"'}

    async def health(request: Request) -> PlainTextResponse:
        return PlainTextResponse("kumiho-gpt-connect: ok\n")

    async def proxy(request: Request) -> StreamingResponse | JSONResponse:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or oauth.verify_bearer(token) is None:
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers=_challenge(),
            )

        # Forward the request verbatim (same path/query) to the inner server.
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        query = request.url.query
        url = request.url.path + (f"?{query}" if query else "")

        upstream_req = client.build_request(
            request.method,
            url,
            headers=fwd_headers,
            content=request.stream(),
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "bad_gateway", "detail": str(exc)[:200]}, status_code=502
            )
        resp_headers = _clean_headers(upstream.headers.items())
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=BackgroundTask(upstream.aclose),
        )

    mcp = cfg.mcp_path.rstrip("/") or "/mcp"
    routes = [
        Route("/", health, methods=["GET"]),
        *oauth.routes(),
        # Streamable-HTTP MCP endpoint (primary).
        Route(mcp, proxy, methods=["GET", "POST", "DELETE"]),
        Route(mcp + "/{rest:path}", proxy, methods=["GET", "POST", "DELETE"]),
        # SSE transport fallback (older MCP clients / mcp-proxy SSE mode).
        Route("/sse", proxy, methods=["GET"]),
        Route("/messages", proxy, methods=["POST"]),
        Route("/messages/{rest:path}", proxy, methods=["POST"]),
    ]

    async def _shutdown() -> None:
        await client.aclose()

    return Starlette(routes=routes, middleware=[Middleware(BodyLimitMiddleware)], on_shutdown=[_shutdown])


def _clean_headers(items: Iterable) -> dict:
    return {k: v for k, v in items if k.lower() not in _HOP_BY_HOP}
