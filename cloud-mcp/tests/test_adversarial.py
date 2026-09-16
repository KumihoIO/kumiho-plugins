"""Regressions for the pre-merge adversarial review of PR #101."""
from dataclasses import replace

import anyio
import httpx
import pytest
from stub_server import build_stub_server
from test_pooling import build_pool, principal_for

from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.auth import AuthError, JwksCache

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json={"keys": []})])
async def test_expired_jwks_never_uses_stale_keys(keypair, response):
    replies = [httpx.Response(200, json={"keys": [keypair.jwk]}), response]
    cache = JwksCache("https://control.test/jwks", ttl=60, cooldown=0, timeout=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: replies.pop(0))) as http:
        cache.attach(http)
        await cache.get_key(keypair.kid)
        cache._fetched_at -= 61
        with pytest.raises(AuthError):
            await cache.get_key(keypair.kid)


async def test_cold_jwks_failure_is_rate_limited():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503)
    cache = JwksCache("https://control.test/jwks", ttl=60, cooldown=60, timeout=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        cache.attach(http)
        async def rejected():
            with pytest.raises(AuthError):
                await cache.get_key("bogus")
        async with anyio.create_task_group() as group:
            for _ in range(12):
                group.start_soon(rejected)
    assert len(calls) == 1


@pytest.mark.parametrize("document", [[], {"keys": 42}, {"keys": [{"kid": []}]}])
async def test_malformed_jwks_is_an_auth_failure(document):
    cache = JwksCache("https://control.test/jwks", ttl=60, cooldown=60, timeout=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document))) as http:
        cache.attach(http)
        with pytest.raises(AuthError):
            await cache.get_key("bogus")


@pytest.mark.parametrize("token_id", ["same-jti", ""])
async def test_different_bearers_do_not_reuse_credentials(monkeypatch, token_id):
    pool, built = build_pool(monkeypatch)
    principal = principal_for("tenant", token_id)
    first = await pool.acquire(replace(principal, token="credential-a"))
    second = await pool.acquire(replace(principal, token="credential-b"))
    try:
        assert first.client is not second.client
        assert len(built) == 2
    finally:
        await first.release()
        await second.release()
        await pool.aclose()


async def test_slow_body_is_covered_by_request_deadline(settings):
    app = create_app(replace(settings, request_timeout_seconds=0.02), server_factory=build_stub_server)
    messages = []
    async def receive():
        await anyio.sleep_forever()
    async def send(message):
        messages.append(message)
    with anyio.fail_after(0.5):
        await app({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send)
    assert messages[0]["status"] == 504


def test_legacy_sse_cannot_be_enabled(settings):
    with pytest.raises(ValueError, match="Streamable HTTP"):
        create_app(replace(settings, enable_sse=True), server_factory=build_stub_server)


async def test_cancelled_request_releases_retired_channel(monkeypatch):
    pool, built = build_pool(monkeypatch)
    first = await pool.acquire(principal_for("tenant", "first"))
    first._entry.expires_at = 0
    second = await pool.acquire(principal_for("tenant", "second"))
    with anyio.CancelScope() as cancellation:
        cancellation.cancel()
        await first.release()
    try:
        assert first._entry.leases == 0
        assert built[0].closed
    finally:
        await second.release()
        await pool.aclose()


@pytest.mark.parametrize("kid", [[], {}, 1, ""])
async def test_malformed_signing_key_id_is_an_auth_failure(kid):
    cache = JwksCache("https://control.test/jwks", ttl=60, cooldown=60, timeout=1)
    with pytest.raises(AuthError):
        await cache.get_key(kid)
