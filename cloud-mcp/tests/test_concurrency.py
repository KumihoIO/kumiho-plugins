"""Two tenants through ``/mcp`` at the same time must not see each other.

This is the property the whole per-request design exists to guarantee. The stub
tool reads the ambient state from inside a worker thread (like every real
Kumiho tool does via ``asyncio.to_thread``), so a leak anywhere in the chain —
contextvar, client pool, Redis token override — shows up as a mismatched
tenant in the response.
"""

from __future__ import annotations

import json

import anyio
import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc, service_claims
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(settings, fake_clients):
    # A slow tool guarantees the two requests overlap rather than serialise.
    return create_app(settings, server_factory=lambda: build_stub_server(delay=0.05))


async def _whoami(http, token: str) -> dict:
    response = await http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "whoami", "arguments": {}}),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload.get("isError") is not True, payload
    return json.loads(payload["content"][0]["text"])


async def test_two_tenants_never_see_each_others_client(app, control_plane, keypair, fake_clients):
    token_a = keypair.sign(base_claims(tenant_id="tenant-aaaa", tenant_slug="acme", sub="user-a"))
    token_b = keypair.sign(base_claims(tenant_id="tenant-bbbb", tenant_slug="globex", sub="user-b"))

    results: dict = {}

    async with client_for(app, control_plane) as http:

        async def call(label: str, token: str) -> None:
            results[label] = await _whoami(http, token)

        async with anyio.create_task_group() as tg:
            for _ in range(4):
                tg.start_soon(call, "a", token_a)
                tg.start_soon(call, "b", token_b)

    assert results["a"]["ctx_tenant"] == "tenant-aaaa"
    assert results["a"]["ctx_user"] == "user-a"
    assert results["a"]["client_tenant"] == "tenant-aaaa"
    assert results["a"]["client_token"] == token_a

    assert results["b"]["ctx_tenant"] == "tenant-bbbb"
    assert results["b"]["ctx_user"] == "user-b"
    assert results["b"]["client_tenant"] == "tenant-bbbb"
    assert results["b"]["client_token"] == token_b

    tenants = {client.tenant_id for client in fake_clients}
    assert tenants == {"tenant-aaaa", "tenant-bbbb"}


async def test_interleaved_requests_report_their_own_tenant(app, control_plane, keypair):
    """Run many overlapping calls and assert every single answer is self-consistent."""
    tokens = {
        f"tenant-{i}": keypair.sign(
            base_claims(tenant_id=f"tenant-{i}", tenant_slug=f"slug-{i}", sub=f"user-{i}")
        )
        for i in range(6)
    }
    seen: list = []

    async with client_for(app, control_plane) as http:

        async def call(tenant: str, token: str) -> None:
            seen.append((tenant, await _whoami(http, token)))

        async with anyio.create_task_group() as tg:
            for tenant, token in tokens.items():
                tg.start_soon(call, tenant, token)
                tg.start_soon(call, tenant, token)

    assert len(seen) == 12
    for tenant, snapshot in seen:
        assert snapshot["ctx_tenant"] == tenant
        assert snapshot["client_tenant"] == tenant
        assert snapshot["client_token"] == tokens[tenant]


async def test_redis_token_override_follows_the_caller(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        snapshot = await _whoami(http, token)
    assert snapshot["redis_token_set"] is True
    assert snapshot["redis_token_matches_ctx"] is True


async def test_service_token_identity_is_namespaced(app, control_plane, keypair):
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        response = await http.post(
            "/mcp",
            json=rpc("tools/call", {"name": "whoami", "arguments": {}}),
            headers={**MCP_HEADERS, "x-api-key": token},
        )
    snapshot = json.loads(response.json()["result"]["content"][0]["text"])
    assert snapshot["ctx_user"] == "service:svc-token-1"
    assert snapshot["ctx_tenant"] == "tenant-bbbb"
    assert snapshot["ctx_token_id"] == "svc-token-1"


async def test_client_pool_reuses_per_tenant_and_token(app, control_plane, keypair, fake_clients):
    token_a = keypair.sign(base_claims(tenant_id="tenant-aaaa", jti="jti-1"))
    token_b = keypair.sign(base_claims(tenant_id="tenant-aaaa", jti="jti-2"))
    async with client_for(app, control_plane) as http:
        await _whoami(http, token_a)
        await _whoami(http, token_a)
        assert len(fake_clients) == 1  # same tenant, same jti -> pooled
        await _whoami(http, token_b)
        assert len(fake_clients) == 2  # rotated credential -> fresh channel


async def test_nothing_leaks_into_the_ambient_client(app, control_plane, keypair):
    """Outside a request there must be no configured default client."""
    import kumiho

    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        await _whoami(http, token)
    assert kumiho._client_context_var.get() is None
    assert kumiho._default_client is None
