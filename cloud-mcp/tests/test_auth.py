"""Every authentication branch, through the real ASGI stack."""

from __future__ import annotations

import time

import pytest
from conftest import (
    AUDIENCE,
    INTERNAL_KEY,
    ISSUER,
    MCP_HEADERS,
    PUBLIC_URL,
    FakeControlPlane,
    KeyPair,
    base_claims,
    client_for,
    rpc,
    service_claims,
)
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.settings import load_settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(settings, fake_clients):
    return create_app(settings, server_factory=build_stub_server)


async def _post(http, headers=None):
    return await http.post(
        "/mcp", json=rpc("tools/list"), headers={**MCP_HEADERS, **(headers or {})}
    )


def _challenge(response) -> str:
    return response.headers.get("www-authenticate", "")


# ---------------------------------------------------------------------------
# missing / malformed credentials
# ---------------------------------------------------------------------------


async def test_no_credentials_gets_challenge_without_error(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await _post(http)
    assert response.status_code == 401
    assert _challenge(response) == (
        'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource", '
        'scope="memory"'
    )
    assert "error=" not in _challenge(response)
    assert response.headers["cache-control"] == "no-store"


async def test_malformed_authorization_header_reports_invalid_token(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": "Basic abc123"})
    assert response.status_code == 401
    assert _challenge(response) == (
        'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource", '
        'scope="memory", error="invalid_token"'
    )


async def test_garbage_bearer_token_is_rejected(app, control_plane):
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert 'error="invalid_token"' in _challenge(response)


# ---------------------------------------------------------------------------
# OAuth access tokens
# ---------------------------------------------------------------------------


async def test_valid_access_token_reaches_the_mcp_server(app, control_plane, keypair, fake_clients):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "whoami"
    assert fake_clients[0].tenant_id == "tenant-aaaa"
    assert fake_clients[0].token == token
    assert fake_clients[0].target == "us-east-1.kumiho.cloud:443"


async def test_api_key_header_is_accepted_for_access_tokens_too(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"x-api-key": token})
    assert response.status_code == 200


async def test_expired_token(app, control_plane, keypair):
    token = keypair.sign(base_claims(exp=int(time.time()) - 10))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "token expired"


async def test_wrong_audience(app, control_plane, keypair):
    token = keypair.sign(base_claims(aud="someone-else"))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "wrong audience"


async def test_audience_may_be_a_list_containing_the_expected_value(app, control_plane, keypair):
    token = keypair.sign(base_claims(aud=["other", AUDIENCE]))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 200


async def test_wrong_issuer(app, control_plane, keypair):
    token = keypair.sign(base_claims(iss="https://evil.example"))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "wrong issuer"


async def test_unknown_kid_triggers_one_refresh_then_fails(app, control_plane, keypair):
    token = keypair.sign(base_claims(), kid="kumiho-cp-key-999")
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "signing key not found" in response.json()["error_description"]
    assert control_plane.jwks_calls == 1


async def test_rotated_kid_is_picked_up_on_refresh(app, control_plane, keypair, rogue_keypair):
    """A key published *after* the cache warmed must still verify."""
    warm = keypair.sign(base_claims())
    fresh = rogue_keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        assert (await _post(http, {"authorization": f"Bearer {warm}"})).status_code == 200
        control_plane.published_keys.append(rogue_keypair)
        response = await _post(http, {"authorization": f"Bearer {fresh}"})
    assert response.status_code == 200
    assert control_plane.jwks_calls == 2


async def test_jwks_refresh_cooldown_throttles_bogus_kids(control_plane, keypair, fake_clients):
    """A flood of unknown kids must not become a flood of JWKS fetches."""
    settings = load_settings(
        {
            "KUMIHO_MCP_PUBLIC_URL": PUBLIC_URL,
            "KUMIHO_AS_ISSUER": ISSUER,
            "KUMIHO_JWKS_URL": f"{ISSUER}/.well-known/kumiho-jwks.json",
            "KUMIHO_CONTROL_PLANE_URL": ISSUER,
            "KUMIHO_MCP_JSON_RESPONSE": "1",
            "KUMIHO_MCP_ENABLE_SSE": "0",
            "KUMIHO_MCP_LOG_LEVEL": "CRITICAL",
            "KUMIHO_MCP_JWKS_COOLDOWN_SECONDS": "30",
        }
    )
    app = create_app(settings, server_factory=build_stub_server)
    async with client_for(app, control_plane) as http:
        for index in range(5):
            token = keypair.sign(base_claims(), kid=f"bogus-{index}")
            assert (await _post(http, {"authorization": f"Bearer {token}"})).status_code == 401
    assert control_plane.jwks_calls == 1


async def test_signature_from_an_unpublished_key_is_rejected(app, control_plane, rogue_keypair):
    """Same kid as a published key, different private key: must not verify."""
    from conftest import KID

    token = rogue_keypair.sign(base_claims(), kid=KID)
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "signature verification failed"


async def test_non_es256_algorithm_is_refused(app, control_plane):
    import jwt as pyjwt

    token = pyjwt.encode(base_claims(), "shared-secret", algorithm="HS256")
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "unsupported signing algorithm" in response.json()["error_description"]


async def test_token_bound_to_another_resource(app, control_plane, keypair):
    token = keypair.sign(base_claims(resource="https://mcp.other.example/mcp"))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "token is bound to another resource"


async def test_missing_resource_claim_is_allowed(app, control_plane, keypair):
    claims = base_claims()
    claims.pop("resource")
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {keypair.sign(claims)}"})
    assert response.status_code == 200


async def test_scope_without_memory_is_403(app, control_plane, keypair):
    token = keypair.sign(base_claims(scope="offline_access"))
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in _challenge(response)


async def test_unknown_token_kind_is_rejected(app, control_plane, keypair):
    claims = base_claims()
    claims.pop("token_use")
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"authorization": f"Bearer {keypair.sign(claims)}"})
    assert response.status_code == 401
    assert "neither an MCP access token nor a service token" in response.json()["error_description"]


# ---------------------------------------------------------------------------
# service tokens (dashboard API keys)
# ---------------------------------------------------------------------------


async def test_active_service_token_via_x_api_key(app, control_plane, keypair, fake_clients):
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"x-api-key": token})
    assert response.status_code == 200
    assert control_plane.introspect_calls == 1
    assert fake_clients[0].tenant_id == "tenant-bbbb"


async def test_revoked_service_token_is_rejected(app, control_plane, keypair):
    control_plane.introspection["svc-token-1"] = {"active": False, "tenant_id": None}
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"x-api-key": token})
    assert response.status_code == 401
    assert response.json()["error_description"] == "API key has been revoked"


async def test_introspection_result_is_cached(app, control_plane, keypair):
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        for _ in range(3):
            assert (await _post(http, {"x-api-key": token})).status_code == 200
    assert control_plane.introspect_calls == 1


async def test_introspection_failure_fails_closed(app, control_plane, keypair):
    control_plane.introspection_status = 500
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"x-api-key": token})
    assert response.status_code == 401
    assert response.json()["error_description"] == "API key is not valid"


async def test_missing_internal_key_fails_closed(control_plane, keypair, fake_clients):
    settings = load_settings(
        {
            "KUMIHO_MCP_PUBLIC_URL": PUBLIC_URL,
            "KUMIHO_AS_ISSUER": ISSUER,
            "KUMIHO_JWKS_URL": f"{ISSUER}/.well-known/kumiho-jwks.json",
            "KUMIHO_CONTROL_PLANE_URL": ISSUER,
            "KUMIHO_MCP_JSON_RESPONSE": "1",
            "KUMIHO_MCP_ENABLE_SSE": "0",
            "KUMIHO_MCP_LOG_LEVEL": "CRITICAL",
        }
    )
    assert settings.control_plane_internal_key is None
    app = create_app(settings, server_factory=build_stub_server)
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        response = await _post(http, {"x-api-key": token})
    assert response.status_code == 401
    assert "introspection is not configured" in response.json()["error_description"]
    assert control_plane.introspect_calls == 0


async def test_internal_key_is_sent_on_introspection(app, control_plane, keypair):
    """A wrong key must not be silently accepted (fake CP 403s on mismatch)."""
    assert app.state.settings.control_plane_internal_key == INTERNAL_KEY
    token = keypair.sign(service_claims())
    async with client_for(app, control_plane) as http:
        assert (await _post(http, {"x-api-key": token})).status_code == 200


# ---------------------------------------------------------------------------
# dev mode
# ---------------------------------------------------------------------------


async def test_dev_mode_skips_auth_and_pins_a_fake_tenant(control_plane, fake_clients):
    settings = load_settings(
        {
            "KUMIHO_MCP_DEV_MODE": "ce",
            "KUMIHO_MCP_JSON_RESPONSE": "1",
            "KUMIHO_MCP_ENABLE_SSE": "0",
            "KUMIHO_MCP_LOG_LEVEL": "CRITICAL",
            "KUMIHO_LOCAL_SERVER_ENDPOINT": "127.0.0.1:9190",
        }
    )
    app = create_app(settings, server_factory=build_stub_server)
    async with client_for(app, control_plane) as http:
        response = await _post(http)
    assert response.status_code == 200
    assert fake_clients[0].target == "127.0.0.1:9190"
    assert fake_clients[0].token is None
    assert control_plane.jwks_calls == 0


def test_challenge_header_shape(settings):
    from kumiho_cloud_mcp.auth import challenge_header

    assert challenge_header(settings) == (
        'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource", '
        'scope="memory"'
    )
    assert challenge_header(settings, error="invalid_token").endswith('error="invalid_token"')


def test_extract_token_sources():
    from kumiho_cloud_mcp.auth import extract_token

    class H(dict):
        def get(self, key, default=None):  # headers are case-insensitive in Starlette
            return dict.get(self, key, default)

    assert extract_token(H()) == (None, "none")
    assert extract_token(H({"authorization": "Bearer abc"})) == ("abc", "bearer")
    assert extract_token(H({"authorization": "bearer abc"})) == ("abc", "bearer")
    assert extract_token(H({"authorization": "Bearer"})) == ("", "bearer")
    assert extract_token(H({"x-api-key": "  key  "})) == ("key", "x-api-key")


def test_unused_fixture_shim(keypair: KeyPair, control_plane: FakeControlPlane):
    """Keeps the imported type names honest for readers of this file."""
    assert keypair.jwk["kid"] == control_plane.published_keys[0].jwk["kid"]
