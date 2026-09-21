"""Content-free request timing, scoped across ASGI and SDK worker threads."""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from uuid import uuid4

import grpc

_current = ContextVar("mcp_request_timing", default=None)
logger = logging.getLogger("kumiho.cloud_mcp.timing")


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
    timings["until_result"] = round((perf_counter() - state["start"]) * 1000, 3)

    def enrich(payload):
        if not isinstance(payload, dict):
            return payload
        payload = dict(payload)
        payload["request_id"] = state["request_id"]
        payload["mcp_timing_ms"] = timings
        payload["mcp_rpc_counts"] = rpc_counts
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
            with state["lock"]:
                counts = state.setdefault("rpc_counts", {})
                counts[name[4:]] = counts.get(name[4:], 0) + 1
                state["stages"][name] = (
                    state["stages"].get(name, 0.0) + (perf_counter() - started) * 1000
                )

        try:
            response = continuation(client_call_details, request)
        except BaseException:
            complete()
            raise
        # Capture request state explicitly: completion may occur on a gRPC thread.
        # Do not block future() callers or change exception delivery semantics.
        response.add_done_callback(complete)
        return response
