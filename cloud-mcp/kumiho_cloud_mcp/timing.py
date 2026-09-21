"""Content-free request timing, scoped across ASGI and SDK worker threads."""

import json
import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from uuid import uuid4

import grpc

_current = ContextVar("mcp_request_timing", default=None)
logger = logging.getLogger("kumiho.cloud_mcp.timing")

_SERVER_TIMING_STAGES = frozenset({
    "auth", "prewarm", "request-gate", "handler", "request-gate-finish", "total",
    "project-validation", "embedding", "query", "hydration", "config", "cache-read",
    "budget-reserve", "provider", "budget-settle", "cache-write", "budget-read", "plan",
    "db-resolve", "db-begin", "db-lock", "db-execute", "db-consume", "db-commit",
    "provider-permit", "provider-rate", "provider-http", "provider-backoff",
})
_SERVER_TIMING_HEADERS = {
    f"x-kumiho-timing-{name}-ms": name.replace("-", "_")
    for name in _SERVER_TIMING_STAGES
}


def _server_timings(response):
    """Read bounded, numeric diagnostics from the final successful RPC attempt."""
    try:
        if response is None or response.code() != grpc.StatusCode.OK:
            return {}
        metadata = response.initial_metadata() or ()
        timings, seen = {}, set()
        size = 0
        for index, (key, value) in enumerate(metadata):
            if index >= 64 or not isinstance(key, str) or not isinstance(value, (str, bytes)):
                return {}
            size += len(key.encode("utf-8")) + (len(value.encode("utf-8")) if isinstance(value, str) else len(value))
            if size > 8192:
                return {}
            stage_name = _SERVER_TIMING_HEADERS.get(key)
            if stage_name is None:
                continue
            # Duplicate values are ambiguous; omit that stage entirely.
            if stage_name in seen:
                timings.pop(stage_name, None)
                continue
            seen.add(stage_name)
            if not isinstance(value, str) or not value or len(value) > 32:
                continue
            try:
                duration = float(value)
            except (ValueError, OverflowError):
                continue
            if math.isfinite(duration) and duration >= 0:
                timings[stage_name] = duration
        return timings
    except Exception:  # Diagnostics must never change RPC delivery or errors.
        return {}


@contextmanager
def stage(name):
    started = perf_counter()
    try:
        yield
    finally:
        state = _current.get()
        if state is not None:
            with state["lock"]:
                state["stages"][name] = (
                    state["stages"].get(name, 0.0) + (perf_counter() - started) * 1000
                )


def annotate_result(result):
    state = _current.get()
    if state is None or result.is_error:
        return result
    with state["lock"]:
        timings = {k: round(v, 3) for k, v in state["stages"].items()}
        rpc_counts = dict(state.get("rpc_counts", {}))
        server_timings = {
            method: {key: round(value, 3) for key, value in values.items()}
            for method, values in state.get("server_timings", {}).items()
        }
    timings["until_result"] = round((perf_counter() - state["start"]) * 1000, 3)

    def enrich(payload):
        if not isinstance(payload, dict):
            return payload
        payload = dict(payload)
        payload["request_id"] = state["request_id"]
        payload["mcp_timing_ms"] = timings
        payload["mcp_rpc_counts"] = rpc_counts
        if server_timings:
            payload["server_timing_ms"] = server_timings
        if "approx_payload_tokens" in payload:
            from kumiho_memory.context_compose import approx_tokens

            payload.pop("approx_payload_tokens")
            payload["approx_payload_tokens"] = approx_tokens(
                json.dumps(payload, ensure_ascii=False, default=str)
            )
        return payload

    content = []
    for block in result.content:
        if block.type == "text":
            try:
                payload = json.loads(block.text)
            except (ValueError, TypeError):
                content.append(block)
                continue
            if isinstance(payload, dict):
                block = block.model_copy(
                    update={"text": json.dumps(enrich(payload), ensure_ascii=False, default=str)}
                )
        content.append(block)
    updates = {"content": content}
    if isinstance(result.structured_content, dict):
        updates["structured_content"] = enrich(result.structured_content)
    return result.model_copy(update=updates)


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        state = {"start": perf_counter(), "request_id": uuid4().hex, "stages": {}, "lock": Lock()}
        token = _current.set(state)
        status = None

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-kumiho-request-id", state["request_id"].encode()),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        finally:
            # A timed-out worker can finish later; snapshot under the same lock
            # used by callbacks, and always release the request context first.
            with state["lock"]:
                timings = {k: round(v, 3) for k, v in state["stages"].items()}
                counts = dict(state.get("rpc_counts", {}))
                server_timings = {
                    method: {key: round(value, 3) for key, value in values.items()}
                    for method, values in state.get("server_timings", {}).items()
                }
            _current.reset(token)
            timings["http_total"] = round((perf_counter() - state["start"]) * 1000, 3)
            logger.info(
                "mcp request timing",
                extra={
                    "request_id": state["request_id"],
                    "http_status": status,
                    "method": scope.get("method"),
                    "mcp_timing_ms": timings,
                    "mcp_rpc_counts": counts,
                    "server_timing_ms": server_timings,
                },
            )


class RpcTimingInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Measure full logical RPC including inner SDK retries, never request data."""

    def intercept_unary_unary(self, continuation, client_call_details, request):
        state = _current.get()
        if state is None:
            return continuation(client_call_details, request)
        name = "rpc_" + client_call_details.method.rsplit("/", 1)[-1]
        started = perf_counter()

        def complete(_response=None):
            try:
                server_timings = _server_timings(_response)
                with state["lock"]:
                    counts = state.setdefault("rpc_counts", {})
                    counts[name[4:]] = counts.get(name[4:], 0) + 1
                    state["stages"][name] = (
                        state["stages"].get(name, 0.0) + (perf_counter() - started) * 1000
                    )
                    if server_timings:
                        totals = state.setdefault("server_timings", {}).setdefault(name[4:], {})
                        for key, duration in server_timings.items():
                            total = totals.get(key, 0.0) + duration
                            if math.isfinite(total):
                                totals[key] = total
            except Exception:  # Timing callbacks must not affect the original RPC.
                return

        try:
            response = continuation(client_call_details, request)
        except BaseException:
            complete()
            raise
        # Capture request state explicitly: completion may occur on a gRPC thread.
        # Do not block future() callers or change exception delivery semantics.
        response.add_done_callback(complete)
        return response
