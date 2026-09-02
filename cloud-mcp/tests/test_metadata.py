"""Protected-resource metadata, the unauthenticated surface, and headers."""

from __future__ import annotations

import pytest
from conftest import ISSUER, PUBLIC_URL, client_for
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.settings import load_settings

pytestmark = pytest.mark.anyio

PRM_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
]


@pytest.fixture
def app(settings, fake_clients):
    return create_app(settings, server_factory=build_stub_server)


@pytest.mark.parametrize("path", PRM_PATHS)
async def test_prm_document(app, control_plane, path):
    async with client_for(app, control_plane) as http:
        response = await http.get(path)
    assert response.status_code == 200
    assert response.json() == {
        # RFC 9728: ``resource`` must equal the MCP URL exactly as entered, and
        # Claude reads authorization_servers[0] with no fallback.
        "resource": PUBLIC_URL,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["memory", "offline_access"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://kumiho.io/docs/connect/claude",
    }


@pytest.mark.parametrize("path", PRM_PATHS)
async def test_prm_is_publicly_readable_and_uncached(app, control_plane, path):
    async with client_for(app, control_plane) as http:
        response = await http.get(path)
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex"


async def test_prm_url_matches_the_401_challenge(app, control_plane):
    """Whatever the challenge points at must actually serve the document."""
    async with client_for(app, control_plane) as http:
        challenge = (await http.post("/mcp", json={})).headers["www-authenticate"]
        url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
        response = await http.get(url)
    assert url == "https://mcp.test/.well-known/oauth-protected-resource"
    assert response.status_code == 200


async def test_healthz(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "kumiho-cloud-mcp"
    assert body["mcp_endpoint"] == PUBLIC_URL


async def test_root_points_at_the_endpoint(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await http.get("/")
    assert response.status_code == 200
    assert "claude mcp add --transport http kumiho-memory" in response.text
    assert PUBLIC_URL in response.text


async def test_every_response_is_no_store_and_noindex(app, control_plane):
    async with client_for(app, control_plane) as http:
        for path in ["/", "/healthz", *PRM_PATHS]:
            response = await http.get(path)
            assert response.headers["cache-control"] == "no-store", path
            assert response.headers["x-robots-tag"] == "noindex", path


async def test_oversized_body_is_rejected_before_auth(app, control_plane):
    """413 must not require credentials — the cap exists to protect the process."""
    async with client_for(app, control_plane) as http:
        response = await http.post(
            "/mcp",
            content=b"x" * (app.state.settings.max_body_bytes + 1),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


async def test_body_under_the_cap_reaches_auth(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await http.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","x":"' + b"y" * 4096 + b'"}',
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 401


def test_public_url_derivations():
    settings = load_settings(
        {"KUMIHO_MCP_PUBLIC_URL": "https://mcp.kumiho.cloud/mcp", "KUMIHO_MCP_LOG_LEVEL": "CRITICAL"}
    )
    assert settings.public_origin == "https://mcp.kumiho.cloud"
    assert settings.mcp_path == "/mcp"
    assert settings.prm_url == "https://mcp.kumiho.cloud/.well-known/oauth-protected-resource"
    assert (
        settings.introspection_url
        == "https://control.kumiho.cloud/api/control-plane/service-token/introspect"
    )
    assert settings.discovery_url == "https://control.kumiho.cloud/api/discovery/tenant"


def test_defaults_match_the_plan():
    settings = load_settings({"KUMIHO_MCP_LOG_LEVEL": "CRITICAL"})
    assert settings.public_url == "https://mcp.kumiho.cloud/mcp"
    assert settings.issuer == "https://control.kumiho.cloud"
    assert settings.jwks_url == "https://control.kumiho.cloud/.well-known/kumiho-jwks.json"
    assert settings.audience == "kumiho-server"
    assert settings.max_body_bytes == 2 * 1024 * 1024
    # 120 s rather than the plan's 60 s: one `kumiho_memory_consolidate` call
    # is a whole session's worth of graph writes (see settings.py).
    assert settings.request_timeout_seconds == 120.0
    assert settings.client_cache_max == 1024
    assert settings.port == 8080
    # Both default OFF: SSE is the deprecated transport and doubles the
    # authenticated surface; the shim cannot enforce the reviewed profile.
    assert settings.enable_sse is False
    assert settings.allow_shim is False


def test_dev_mode_defaults():
    settings = load_settings({"KUMIHO_MCP_DEV_MODE": "ce", "KUMIHO_MCP_LOG_LEVEL": "CRITICAL"})
    assert settings.dev is True
    assert settings.public_url == "http://127.0.0.1:8080/mcp"
    assert settings.local_server_endpoint == "127.0.0.1:9190"
    assert settings.local_redis_url == "redis://127.0.0.1:6379"
