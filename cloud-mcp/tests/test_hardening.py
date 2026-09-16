"""Production hardening: the startup contract, the dev tenant header, /healthz.

Three things this covers that nothing else does:

* the service must **refuse to boot** in production on a dependency set too old
  to enforce the connector profile (the ``_compat`` shim degrades so gracefully
  that a mispinned deploy would otherwise come up healthy and serve the wrong
  tool list);
* ``x-kumiho-dev-tenant`` must move the tenant in dev mode and be inert
  everywhere else;
* ``/healthz`` must report the per-tenant memory-manager count, which is the
  only externally visible proof that hosted mode is not a singleton.
"""

from __future__ import annotations

import json

import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import (
    MIN_KUMIHO_MEMORY_VERSION,
    MIN_KUMIHO_VERSION,
    StartupContractError,
    _dependency_problems,
    _enforce_dependency_contract,
    _version_tuple,
    create_app,
)
from kumiho_cloud_mcp.settings import DEV_TENANT_HEADER, DEV_TENANT_ID, dev_identity, load_settings

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# startup contract
# ---------------------------------------------------------------------------


def test_version_tuple_parses_release_and_prerelease():
    assert _version_tuple("0.13.0") == (0, 13, 0)
    assert _version_tuple("1.4.0rc1") == (1, 4, 0)
    assert _version_tuple("1.4") == (1, 4)
    assert _version_tuple("1.4.0.post1") == (1, 4, 0)
    assert _version_tuple(None) is None
    assert _version_tuple("dev") is None


def test_installed_dependencies_satisfy_the_contract():
    # The venv this suite runs in is the one the service ships against. If this
    # ever fails, the pin in pyproject.toml and the floor here disagree.
    assert _dependency_problems() == []


def _settings(**over):
    base = {
        "KUMIHO_MCP_LOG_LEVEL": "CRITICAL",
        "KUMIHO_MCP_ENABLE_SSE": "0",
        "KUMIHO_MCP_JSON_RESPONSE": "1",
    }
    base.update(over)
    return load_settings(base)


def test_production_refuses_to_start_on_an_old_sdk(monkeypatch):
    import kumiho_cloud_mcp.app as app_module

    monkeypatch.setattr(
        app_module, "_dependency_problems", lambda: ["kumiho 0.12.0 is older than 0.13.0"]
    )
    with pytest.raises(StartupContractError) as excinfo:
        _enforce_dependency_contract(_settings())
    message = str(excinfo.value)
    assert "0.12.0" in message
    assert "KUMIHO_MCP_ALLOW_SHIM=1" in message


def test_allow_shim_is_the_documented_override(monkeypatch):
    import kumiho_cloud_mcp.app as app_module

    monkeypatch.setattr(app_module, "_dependency_problems", lambda: ["no profile= parameter"])
    # Dev mode and the explicit override both continue; nothing else does.
    _enforce_dependency_contract(_settings(KUMIHO_MCP_ALLOW_SHIM="1"))
    _enforce_dependency_contract(_settings(KUMIHO_MCP_DEV_MODE="ce"))


def test_create_app_raises_before_building_a_server(monkeypatch):
    import kumiho_cloud_mcp.app as app_module

    monkeypatch.setattr(app_module, "_dependency_problems", lambda: ["kumiho-memory 1.3.0"])
    built = []
    with pytest.raises(StartupContractError):
        create_app(_settings(), server_factory=lambda: built.append(1))
    # The guard runs first: a refused start must not have constructed anything.
    assert built == []


def test_minimum_versions_match_the_pins():
    assert MIN_KUMIHO_VERSION == (0, 13, 0)
    assert MIN_KUMIHO_MEMORY_VERSION == (1, 4, 0)


# ---------------------------------------------------------------------------
# x-kumiho-dev-tenant
# ---------------------------------------------------------------------------


def test_dev_identity_is_deterministic_and_uuid_shaped():
    a1 = dev_identity("alpha")
    a2 = dev_identity("ALPHA")
    b = dev_identity("beta")

    assert a1 == a2, "the label is normalised, so the same tenant comes back"
    assert a1 != b
    tenant_id = a1[0]
    assert len(tenant_id) == 36 and tenant_id.count("-") == 4
    assert dev_identity(None)[0] == DEV_TENANT_ID
    assert dev_identity("   ")[0] == DEV_TENANT_ID


async def _whoami(http, headers) -> dict:
    response = await http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "whoami", "arguments": {}}),
        headers={**MCP_HEADERS, **headers},
    )
    assert response.status_code == 200, response.text
    return json.loads(response.json()["result"]["content"][0]["text"])


async def test_dev_tenant_header_selects_a_tenant(control_plane, fake_clients):
    settings = _settings(
        KUMIHO_MCP_DEV_MODE="ce",
        KUMIHO_LOCAL_SERVER_ENDPOINT="127.0.0.1:9190",
    )
    app = create_app(settings, server_factory=build_stub_server)

    async with client_for(app, control_plane) as http:
        default = await _whoami(http, {})
        alpha = await _whoami(http, {DEV_TENANT_HEADER: "alpha"})
        beta = await _whoami(http, {DEV_TENANT_HEADER: "beta"})

    assert default["ctx_tenant"] == DEV_TENANT_ID
    assert alpha["ctx_tenant"] == dev_identity("alpha")[0]
    assert beta["ctx_tenant"] == dev_identity("beta")[0]
    assert len({default["ctx_tenant"], alpha["ctx_tenant"], beta["ctx_tenant"]}) == 3
    # The user moves with the tenant: the active-session pointer is keyed by
    # (context, user), so two dev tenants sharing a user id would share a
    # session and the isolation test would prove nothing.
    assert len({default["ctx_user"], alpha["ctx_user"], beta["ctx_user"]}) == 3


async def test_dev_tenant_header_is_ignored_outside_dev_mode(
    settings, control_plane, keypair, fake_clients
):
    """The header must not be a tenant-switching primitive in production."""
    app = create_app(settings, server_factory=build_stub_server)
    token = keypair.sign(base_claims(tenant_id="tenant-real", sub="user-real"))

    async with client_for(app, control_plane) as http:
        seen = await _whoami(
            http,
            {"authorization": f"Bearer {token}", DEV_TENANT_HEADER: "alpha"},
        )

    assert seen["ctx_tenant"] == "tenant-real"
    assert seen["ctx_tenant"] != dev_identity("alpha")[0]


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


async def test_healthz_reports_tenant_managers(settings, control_plane, fake_clients):
    app = create_app(settings, server_factory=build_stub_server)
    async with client_for(app, control_plane) as http:
        payload = (await http.get("/healthz")).json()

    assert payload["status"] == "ok"
    managers = payload["tenant_managers"]
    assert set(managers) >= {"loaded", "count", "process_singleton"}
    assert isinstance(managers["count"], int)
    # Hosted mode must never build the process-wide singleton.
    assert managers["process_singleton"] is False
    assert payload["sdk"]["kumiho"] is not None
