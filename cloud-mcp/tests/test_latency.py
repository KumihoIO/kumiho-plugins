"""Concurrency and timing contracts; no production credentials or data."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import perf_counter
from types import SimpleNamespace

import grpc
import httpx
import mcp.types as types
import pytest
from test_pooling import build_pool, pool_settings, principal_for

from kumiho_cloud_mcp.auth import ServiceTokenIntrospector
from kumiho_cloud_mcp.singleflight import KeyedGate
from kumiho_cloud_mcp.timing import (
    RequestTimingMiddleware,
    RpcTimingInterceptor,
    _current,
    _server_timings,
    annotate_result,
    stage,
)

pytestmark = pytest.mark.anyio


async def test_same_credential_builds_once_but_other_tenant_does_not_wait(monkeypatch):
    pool, _ = build_pool(monkeypatch)
    original = pool._build
    entered, finish = asyncio.Event(), asyncio.Event()
    builds = []

    async def build(principal):
        builds.append(principal.tenant_id)
        if principal.tenant_id == "slow":
            entered.set()
            await finish.wait()
        return await original(principal)

    monkeypatch.setattr(pool, "_build", build)
    first = asyncio.create_task(pool.acquire(principal_for("slow", "j")))
    await entered.wait()
    rest = [asyncio.create_task(pool.acquire(principal_for("slow", "j"))) for _ in range(8)]
    other = await asyncio.wait_for(pool.acquire(principal_for("other", "j")), 2)
    finish.set()
    leases = await asyncio.gather(first, *rest)
    assert builds.count("slow") == 1
    assert len({id(lease.client) for lease in leases}) == 1
    for lease in [other, *leases]:
        await lease.release()
    assert not pool._build_gate._entries
    await pool.aclose()


async def test_cancelled_waiter_and_failed_owner_release_gate():
    gate = KeyedGate()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def owner():
        async with gate.hold("key"):
            entered.set()
            await finish.wait()
            raise ValueError("retryable")

    async def waiter():
        async with gate.hold("key"):
            return True

    first = asyncio.create_task(owner())
    await entered.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    finish.set()
    with pytest.raises(ValueError):
        await first
    assert await waiter()
    assert not gate._entries


async def test_introspection_shares_fill_and_does_not_extend_ttl():
    introspector = ServiceTokenIntrospector(pool_settings(KUMIHO_CONTROL_PLANE_INTERNAL_KEY="test"))
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"active": True, "tenant_id": "tenant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        introspector.attach(client)
        results = await asyncio.gather(*(introspector.introspect("key") for _ in range(10)))
        assert len(calls) == 1 and all(row["active"] for row in results)
        deadline = introspector._cache["key"][0]
        await introspector.introspect("key")
        assert introspector._cache["key"][0] == deadline
        introspector._cache["key"] = (0, {})
        await introspector.introspect("key")
        assert len(calls) == 2
    assert not introspector._fill_gate._entries


async def test_request_timings_are_isolated_and_preserve_content():
    results, headers = [], []

    async def app(scope, receive, send):
        with stage("authentication"):
            await asyncio.sleep(0)
        with stage("tool_handler"):
            await asyncio.sleep(0)
        payload = {
            "count": 1,
            "context": "unchanged",
            "timing_ms": {"total": 1},
            "approx_payload_tokens": 1,
        }
        result = annotate_result(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(payload))],
                structured_content=payload,
            )
        )
        parsed = json.loads(result.content[0].text)
        assert parsed == result.structured_content
        assert parsed["context"] == "unchanged" and parsed["timing_ms"] == {"total": 1}
        assert parsed["approx_payload_tokens"] > 1
        results.append(parsed)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            headers.append(dict(message["headers"])[b"x-kumiho-request-id"].decode())

    middleware = RequestTimingMiddleware(app)
    await asyncio.gather(
        *(middleware({"type": "http", "method": "POST"}, receive, send) for _ in range(2))
    )
    assert len(set(headers)) == 2
    assert {r["request_id"] for r in results} == set(headers)
    assert _current.get() is None


async def test_rpc_timing_preserves_blocking_future_and_errors():
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    completed = Event()

    class CompletionInterceptor(RpcTimingInterceptor):
        def intercept_unary_unary(self, continuation, client_call_details, request):
            response = super().intercept_unary_unary(continuation, client_call_details, request)
            # This callback is registered after the timing callback on the same
            # response. Future.result() can return before callbacks finish.
            response.add_done_callback(lambda _: completed.set())
            return response

    def echo(request, context):
        context.send_initial_metadata((("x-kumiho-timing-handler-ms", "1.25"),))
        if request == b"fail":
            context.abort(grpc.StatusCode.NOT_FOUND, "missing")
        return request

    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "test",
                {
                    "Echo": grpc.unary_unary_rpc_method_handler(echo),
                },
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    state = {"start": perf_counter(), "request_id": "test", "stages": {}, "lock": Lock()}
    token = _current.set(state)
    try:
        with grpc.intercept_channel(
            grpc.insecure_channel(f"127.0.0.1:{port}"), CompletionInterceptor()
        ) as channel:
            call = channel.unary_unary("/test/Echo")
            assert call(b"sync") == b"sync"
            assert completed.wait(2)
            completed.clear()
            assert call.future(b"future").result() == b"future"
            assert completed.wait(2)
            completed.clear()
            with pytest.raises(grpc.RpcError) as exc:
                call(b"fail")
            assert completed.wait(2)
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
            assert state["stages"]["rpc_Echo"] > 0
            # Only successful sync/future responses contribute server durations.
            assert state["server_timings"] == {"Echo": {"handler": 2.5}}
    finally:
        _current.reset(token)
        server.stop(0).wait()


class _TimingResponse:
    def __init__(self, metadata=(), code=grpc.StatusCode.OK):
        self.metadata = metadata
        self.status = code
        self.callbacks = []

    def code(self):
        return self.status

    def initial_metadata(self):
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def finish(self):
        for callback in self.callbacks:
            callback(self)


@pytest.mark.parametrize("value", ["-1", "NaN", "inf", "-inf", "1e999", "", "bad", "1" * 33, b"12"])
def test_server_timings_reject_invalid_values(value):
    assert _server_timings(_TimingResponse([
        ("x-kumiho-timing-query-ms", value),
        ("authorization", "private"),
        ("x-kumiho-timing-unknown-ms", "100"),
    ])) == {}


@pytest.mark.parametrize("metadata", [
    [("x-kumiho-timing-query-ms", "1"), ("ignored", "x" * 8192)],
    [("x-kumiho-timing-query-ms", "1"), *[("ignored", "x")] * 64],
    [("x-kumiho-timing-query-ms", "1"), ("malformed",)],
    [("x-kumiho-timing-query-ms", "1"), ("malformed", object())],
    ValueError("metadata unavailable"),
])
def test_server_timings_fail_closed_for_unbounded_or_malformed_metadata(metadata):
    assert _server_timings(_TimingResponse(metadata)) == {}


def test_server_timings_drop_duplicates_errors_and_unknown_headers():
    metadata = [
        ("x-kumiho-timing-query-ms", "1"),
        ("x-kumiho-timing-query-ms", "2"),
        ("x-kumiho-timing-query-ms", "3"),
        ("x-kumiho-timing-total-ms", "4.25"),
        ("x-kumiho-timing-request-gate-ms", "0"),
        ("x-kumiho-timing-budget-read-ms", "0.125"),
        ("x-kumiho-timing-private-query-ms", "4"),
    ]
    assert _server_timings(_TimingResponse(metadata)) == {
        "total": 4.25, "request_gate": 0.0, "budget_read": 0.125,
    }
    assert _server_timings(_TimingResponse(metadata, grpc.StatusCode.NOT_FOUND)) == {}


def test_server_timings_capture_request_state_and_accumulate_without_altering_rpc():
    states = [
        {"start": perf_counter(), "request_id": str(i), "stages": {}, "lock": Lock()}
        for i in range(2)
    ]
    responses = []
    interceptor = RpcTimingInterceptor()
    for state, method, duration in [(states[0], "Search", "1.25"), (states[0], "Search", "2.5"),
                                    (states[1], "Evaluate", "7")]:
        response = _TimingResponse([("x-kumiho-timing-handler-ms", duration)])
        token = _current.set(state)
        try:
            result = interceptor.intercept_unary_unary(
                lambda *_, response=response: response, SimpleNamespace(method=f"/kumiho.KumihoService/{method}"), b"x",
            )
            assert result is response
        finally:
            _current.reset(token)
        responses.append(response)
    # gRPC invokes callbacks on completion threads, outside the original context.
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda response: response.finish(), responses))
    assert states[0]["server_timings"] == {"Search": {"handler": 3.75}}
    assert states[1]["server_timings"] == {"Evaluate": {"handler": 7.0}}
    assert states[0]["rpc_counts"] == {"Search": 2}
    assert states[1]["rpc_counts"] == {"Evaluate": 1}
    payload = {"count": 1, "context": "unchanged", "approx_payload_tokens": 1}
    token = _current.set(states[0])
    try:
        annotated = annotate_result(types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
        ))
    finally:
        _current.reset(token)
    assert annotated.structured_content == json.loads(annotated.content[0].text)
    assert annotated.structured_content["server_timing_ms"] == {"Search": {"handler": 3.75}}
    assert annotated.structured_content["context"] == "unchanged"


def test_server_timing_callback_failure_cannot_change_rpc_result():
    state = {"start": perf_counter(), "request_id": "failure", "stages": {}, "lock": Lock()}
    response = _TimingResponse(ValueError("metadata unavailable"))
    token = _current.set(state)
    try:
        result = RpcTimingInterceptor().intercept_unary_unary(
            lambda *_: response, SimpleNamespace(method="/kumiho.KumihoService/Search"), b"x",
        )
        response.finish()
        assert result is response
        assert state["rpc_counts"] == {"Search": 1}
        assert "server_timings" not in state
    finally:
        _current.reset(token)


@pytest.mark.parametrize("stage_name", [
    "auth", "prewarm", "request-gate", "handler", "request-gate-finish", "total",
    "project-validation", "embedding", "query", "hydration", "config", "cache-read",
    "budget-reserve", "provider", "budget-settle", "cache-write", "budget-read", "plan",
    "db-resolve", "db-begin", "db-lock", "db-execute", "db-consume", "db-commit",
    "provider-permit", "provider-rate", "provider-http", "provider-backoff",
])
def test_server_timing_contract_accepts_each_published_stage(stage_name):
    assert _server_timings(_TimingResponse([
        (f"x-kumiho-timing-{stage_name}-ms", "12.345"),
    ])) == {stage_name.replace("-", "_"): 12.345}


@pytest.mark.parametrize(
    "text_payload,structured_payload,should_annotate",
    [
        ({"error": "failed"}, {"ok": True}, False),
        ({"ok": True}, {"error": "failed"}, False),
        ({"success": False, "detail": "failed"}, {"ok": True}, False),
        ({"nested": {"error": "user content"}}, {"ok": True}, True),
    ],
)
def test_storage_timing_preserves_top_level_failures_and_nested_content(
    text_payload, structured_payload, should_annotate
):
    state = {
        "start": perf_counter(),
        "request_id": "test-request-id",
        "stages": {"tool_handler": 1.0},
        "rpc_counts": {},
        "lock": Lock(),
    }
    token = _current.set(state)
    try:
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(text_payload))],
            structured_content=structured_payload,
        )
        annotated = annotate_result(result, preserve_error_payload=True)
    finally:
        _current.reset(token)

    if should_annotate:
        text_result = json.loads(annotated.content[0].text)
        assert text_result["nested"] == {"error": "user content"}
        assert text_result["request_id"] == "test-request-id"
        assert annotated.structured_content["request_id"] == "test-request-id"
    else:
        assert annotated is result
        assert json.loads(annotated.content[0].text) == text_payload
        assert annotated.structured_content == structured_payload
