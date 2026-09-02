"""Client pooling, routing-cache bounds and SDK-construction safety.

Four defects E1 found in clients.py, each pinned here:

1. a client built outside the pool was never closed and never evicted;
2. `_construct_client` dropped `skip_auth_token_load` with a warning when the
   installed SDK did not accept it, so a hosted request could run on the
   operator's ~/.kumiho credentials;
3. `DiscoveryRouter._cache` was an unbounded dict, keyed by a value that comes
   out of a token;
4. (auth.py) an absent `scope` claim was read as `memory`.
"""

from __future__ import annotations

import time

import httpx
import pytest
from conftest import ISSUER, base_claims

import kumiho_cloud_mcp.clients as clients_module
from kumiho_cloud_mcp.auth import AuthError
from kumiho_cloud_mcp.clients import (
    DISCOVERY_CACHE_MAX,
    MIN_CLIENT_TTL_SECONDS,
    REQUIRED_CLIENT_KWARGS,
    ClientContractError,
    ClientPool,
    DiscoveryRouter,
    client_construction_problems,
)
from kumiho_cloud_mcp.settings import load_settings

pytestmark = pytest.mark.anyio


class FakeClient:
    def __init__(self, name: str = "c") -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


def principal_for(tenant: str, token_id: str, *, expires_in: float = 3600.0):
    from kumiho_cloud_mcp.auth import Principal

    return Principal(
        tenant_id=tenant,
        user_id="u",
        token="tok",
        kind="oauth",
        token_id=token_id,
        tenant_slug=tenant,
        expires_at=time.time() + expires_in,
        scopes=["memory"],
    )


def pool_settings(**overrides):
    environ = {
        "KUMIHO_MCP_PUBLIC_URL": "https://mcp.test/mcp",
        "KUMIHO_AS_ISSUER": ISSUER,
        "KUMIHO_CONTROL_PLANE_URL": ISSUER,
        "KUMIHO_MCP_LOG_LEVEL": "WARNING",
    }
    environ.update({k: str(v) for k, v in overrides.items()})
    return load_settings(environ)


class StaticRouter(DiscoveryRouter):
    async def resolve(self, principal):  # type: ignore[override]
        return "region.test:443"


def build_pool(monkeypatch, settings=None):
    """A pool whose clients are FakeClients, so closing is observable."""
    settings = settings or pool_settings()
    built: list = []

    def _fake(*, target: str, token, metadata) -> FakeClient:
        client = FakeClient(f"client-{len(built)}")
        built.append(client)
        return client

    monkeypatch.setattr(clients_module, "_construct_client", _fake)
    return ClientPool(settings, StaticRouter(settings)), built


# ---------------------------------------------------------------------------
# 1. no client escapes the pool unclosed
# ---------------------------------------------------------------------------


async def test_a_near_expiry_token_still_gets_a_pooled_client(monkeypatch):
    """The old code returned an *unpooled* client when the TTL rounded to zero.

    Nothing held a reference to it afterwards, so its channel was never closed:
    one leaked gRPC connection per request made in a token's last seconds.
    """
    pool, built = build_pool(monkeypatch)
    # A token that lapsed between verification and the pool: `ttl <= 0`, which
    # is exactly the branch that used to `return client` without pooling it.
    principal = principal_for("t1", "jti-1", expires_in=-1.0)

    lease = await pool.acquire(principal)
    assert len(built) == 1
    # It is in the pool, so something will close it.
    assert pool._entries, "client was handed out without being pooled"
    await lease.release()

    await pool.aclose()
    assert built[0].closed is True


async def test_expiry_eviction_closes_the_channel(monkeypatch):
    pool, built = build_pool(monkeypatch)
    principal = principal_for("t1", "jti-1")

    lease = await pool.acquire(principal)
    await lease.release()
    assert built[0].closed is False

    # Force expiry, then touch the pool.
    for entry in pool._entries.values():
        entry.expires_at = time.monotonic() - 1

    lease = await pool.acquire(principal_for("t2", "jti-2"))
    await lease.release()

    assert built[0].closed is True, "an expired entry was dropped without closing"
    await pool.aclose()


async def test_lru_overflow_closes_the_evicted_channel(monkeypatch):
    pool, built = build_pool(monkeypatch, pool_settings(KUMIHO_MCP_CLIENT_CACHE_MAX=2))

    for index in range(3):
        lease = await pool.acquire(principal_for(f"t{index}", f"jti-{index}"))
        await lease.release()

    assert len(pool._entries) == 2
    assert built[0].closed is True, "the LRU victim was dropped without closing"
    assert built[1].closed is False
    await pool.aclose()


async def test_eviction_never_closes_a_channel_still_in_use(monkeypatch):
    """Refcounting is the point: a lease outlives its pool entry."""
    pool, built = build_pool(monkeypatch, pool_settings(KUMIHO_MCP_CLIENT_CACHE_MAX=1))

    held = await pool.acquire(principal_for("t0", "jti-0"))

    # Evict it while it is still leased.
    evictor = await pool.acquire(principal_for("t1", "jti-1"))
    await evictor.release()

    assert built[0].closed is False, "closed a channel a live request was using"
    # …and it closes the moment the borrower lets go.
    await held.release()
    assert built[0].closed is True

    await pool.aclose()


async def test_the_lease_context_manager_always_releases(monkeypatch):
    pool, built = build_pool(monkeypatch, pool_settings(KUMIHO_MCP_CLIENT_CACHE_MAX=1))
    principal = principal_for("t0", "jti-0")

    with pytest.raises(RuntimeError, match="boom"):
        async with pool.lease(principal):
            raise RuntimeError("boom")

    # The lease was returned, so eviction is free to close it.
    lease = await pool.acquire(principal_for("t1", "jti-1"))
    await lease.release()
    assert built[0].closed is True
    await pool.aclose()


async def test_a_reused_entry_is_the_same_channel(monkeypatch):
    pool, built = build_pool(monkeypatch)
    principal = principal_for("t0", "jti-0")

    first = await pool.acquire(principal)
    second = await pool.acquire(principal)
    assert first.client is second.client
    assert len(built) == 1

    await first.release()
    await second.release()
    assert built[0].closed is False  # still pooled

    await pool.aclose()
    assert built[0].closed is True


async def test_aclose_closes_everything_including_leased(monkeypatch):
    pool, built = build_pool(monkeypatch)
    held = await pool.acquire(principal_for("t0", "jti-0"))
    await pool.aclose()
    assert built[0].closed is True
    # Releasing afterwards must not explode.
    await held.release()


async def test_ttl_is_floored_and_capped_by_the_token(monkeypatch):
    pool, _ = build_pool(monkeypatch)
    assert pool._ttl_for(principal_for("t", "j", expires_in=0.0)) == MIN_CLIENT_TTL_SECONDS
    assert pool._ttl_for(principal_for("t", "j", expires_in=-100.0)) == MIN_CLIENT_TTL_SECONDS
    # A 60 s token gets ~60 s, not the 15 min default.
    assert 50 <= pool._ttl_for(principal_for("t", "j", expires_in=60.0)) <= 61
    assert pool._ttl_for(principal_for("t", "j", expires_in=99999.0)) == 15 * 60.0


# ---------------------------------------------------------------------------
# 2. construction is fail-closed, not fail-open
# ---------------------------------------------------------------------------


def test_the_installed_sdk_satisfies_the_construction_contract():
    assert client_construction_problems(refresh=True) == []


def test_an_sdk_without_the_hosting_switches_is_a_hard_error(monkeypatch):
    """The whole point: a missing switch must never be a warning.

    Without `skip_auth_token_load` the SDK may load ~/.kumiho, so a request that
    presented no usable credential would run as the operator.
    """

    class OldClient:
        def __init__(self, target=None, auth_token=None, default_metadata=None):
            self.target = target

    module = type("FakeKumihoClientModule", (), {"_Client": OldClient})
    monkeypatch.setitem(__import__("sys").modules, "kumiho.client", module)

    problems = client_construction_problems(refresh=True)
    assert problems, "an SDK missing the hosting switches was reported as fine"
    for name in REQUIRED_CLIENT_KWARGS:
        assert name in problems[0]

    with pytest.raises(ClientContractError) as excinfo:
        clients_module._construct_client(target="x:1", token=None, metadata=[])
    assert "skip_auth_token_load" in str(excinfo.value)

    client_construction_problems(refresh=True)  # restore the cache for later tests


async def test_the_pool_refuses_to_build_on_an_unsafe_sdk(monkeypatch):
    settings = pool_settings()
    pool = ClientPool(settings, StaticRouter(settings))
    monkeypatch.setattr(
        clients_module,
        "client_construction_problems",
        lambda **_: ["the installed kumiho client does not accept skip_auth_token_load"],
    )
    with pytest.raises(ClientContractError):
        await pool.acquire(principal_for("t", "j"))


async def test_dev_mode_is_exempt(monkeypatch):
    """Dev mode is how the shim gets exercised; it has no real credentials."""
    settings = pool_settings(KUMIHO_MCP_DEV_MODE="ce")
    pool, built = build_pool(monkeypatch, settings)
    monkeypatch.setattr(
        clients_module,
        "client_construction_problems",
        lambda **_: ["would be fatal in production"],
    )
    lease = await pool.acquire(principal_for("t", "j"))
    assert lease.client is built[0]
    await lease.release()
    await pool.aclose()


def test_the_startup_contract_reports_construction_problems(monkeypatch):
    """`app._dependency_problems` is what turns this into a refusal to boot."""
    import kumiho_cloud_mcp.app as app_module

    monkeypatch.setattr(
        app_module,
        "client_construction_problems",
        lambda **_: ["the installed kumiho client does not accept skip_auth_token_load"],
    )
    problems = app_module._dependency_problems()
    assert any("skip_auth_token_load" in problem for problem in problems)

    with pytest.raises(app_module.StartupContractError):
        app_module._enforce_dependency_contract(pool_settings())

    # …and dev mode downgrades it to a warning, as it does for the other checks.
    app_module._enforce_dependency_contract(pool_settings(KUMIHO_MCP_DEV_MODE="ce"))


# ---------------------------------------------------------------------------
# 3. the routing cache is bounded
# ---------------------------------------------------------------------------


async def test_the_discovery_cache_is_bounded_and_lru(monkeypatch):
    settings = pool_settings(KUMIHO_MCP_DISCOVERY_CACHE_SECONDS=600)
    router = DiscoveryRouter(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"region": {"grpc_authority": "region.test:443"}}
        )

    router.attach(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    # tenant_id comes out of a token, so this is an attacker-shaped workload.
    for index in range(DISCOVERY_CACHE_MAX + 50):
        await router.resolve(principal_for(f"tenant-{index}", "j"))

    assert len(router._cache) <= DISCOVERY_CACHE_MAX
    # The oldest were the ones dropped.
    assert "tenant-0" not in router._cache
    assert f"tenant-{DISCOVERY_CACHE_MAX + 49}" in router._cache


async def test_the_discovery_cache_sweeps_expired_entries(monkeypatch):
    settings = pool_settings(KUMIHO_MCP_DISCOVERY_CACHE_SECONDS=600)
    router = DiscoveryRouter(settings)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"region": {"grpc_authority": "region.test:443"}})

    router.attach(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    await router.resolve(principal_for("t0", "j"))
    await router.resolve(principal_for("t0", "j"))
    assert calls["n"] == 1  # served from cache

    router._cache["t0"] = (time.monotonic() - 1, "stale.test:443")
    # Any later resolve sweeps it, and t0 itself re-resolves rather than
    # returning the stale answer.
    await router.resolve(principal_for("t1", "j"))
    assert "t0" not in router._cache
    assert await router.resolve(principal_for("t0", "j")) == "region.test:443"


# ---------------------------------------------------------------------------
# 4. a token with no scope is not a token with every scope
# ---------------------------------------------------------------------------


async def test_an_access_token_without_a_scope_claim_is_refused(settings, control_plane, keypair):
    from kumiho_cloud_mcp.auth import Authenticator

    auth = Authenticator(settings)
    auth.attach(httpx.AsyncClient(transport=control_plane.transport()))

    claims = base_claims()
    claims.pop("scope")
    token = keypair.sign(claims)

    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate({"authorization": f"Bearer {token}"})

    assert excinfo.value.code == "insufficient_scope"
    assert excinfo.value.status == 403
    assert "no scope" in excinfo.value.description


async def test_an_empty_scope_claim_is_refused(settings, control_plane, keypair):
    from kumiho_cloud_mcp.auth import Authenticator

    auth = Authenticator(settings)
    auth.attach(httpx.AsyncClient(transport=control_plane.transport()))

    token = keypair.sign(base_claims(scope="   "))
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate({"authorization": f"Bearer {token}"})
    assert excinfo.value.code == "insufficient_scope"


async def test_a_service_token_keeps_its_own_path(settings, control_plane, keypair):
    """Dashboard API keys carry no `scope` claim and must still work."""
    from conftest import service_claims

    from kumiho_cloud_mcp.auth import Authenticator

    auth = Authenticator(settings)
    auth.attach(httpx.AsyncClient(transport=control_plane.transport()))

    claims = service_claims()
    assert "scope" not in claims
    principal = await auth.authenticate({"x-api-key": keypair.sign(claims)})

    assert principal.kind == "service"
    assert principal.scopes == ["memory"]


async def test_a_scoped_access_token_reports_exactly_its_scopes(settings, control_plane, keypair):
    from kumiho_cloud_mcp.auth import Authenticator

    auth = Authenticator(settings)
    auth.attach(httpx.AsyncClient(transport=control_plane.transport()))

    token = keypair.sign(base_claims(scope="memory offline_access"))
    principal = await auth.authenticate({"authorization": f"Bearer {token}"})
    assert principal.scopes == ["memory", "offline_access"]
