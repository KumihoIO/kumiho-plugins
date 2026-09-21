"""Concurrency and timing contracts; no production credentials or data."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import perf_counter

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

    def echo(request, context):
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
            grpc.insecure_channel(f"127.0.0.1:{port}"), RpcTimingInterceptor()
        ) as channel:
            call = channel.unary_unary("/test/Echo")
            assert call(b"sync") == b"sync"
            assert call.future(b"future").result() == b"future"
            with pytest.raises(grpc.RpcError) as exc:
                call(b"fail")
            assert exc.value.code() == grpc.StatusCode.NOT_FOUND
            assert state["stages"]["rpc_Echo"] > 0
    finally:
        _current.reset(token)
        server.stop(0).wait()
