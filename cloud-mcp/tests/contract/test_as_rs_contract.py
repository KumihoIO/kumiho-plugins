"""AS <-> RS cross-contract verification (WP-E2, part 2).

Everything here runs against artefacts the *authorization server itself*
produced (``as_fixture.json``, written by kumiho-control's
``src/lib/oauth/contract.test.ts``) and against a real HTTP control plane on
127.0.0.1. Nothing at the seam is stubbed at the Python object level, so a
change to the AS's claim set, JWKS, introspection or discovery contract fails
here.

Run:  python -m pytest tests/contract -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contract_support import (  # noqa: E402
    DISCOVERY_PATH,
    INTROSPECT_PATH,
    FixtureSigner,
    StubControlPlane,
    authenticator_for,
    headers,
    load_fixture,
    settings_for,
)

from kumiho_cloud_mcp.auth import AuthError, challenge_header  # noqa: E402
from kumiho_cloud_mcp.clients import DiscoveryRouter, RoutingError  # noqa: E402
from kumiho_cloud_mcp.settings import REQUIRED_SCOPE  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """Self-contained: this package must not depend on a sibling conftest."""
    return "asyncio"


@pytest.fixture(scope="module")
def fixture():
    return load_fixture()


@pytest.fixture(scope="module")
def signer(fixture):
    return FixtureSigner(fixture)


@pytest.fixture
def stub(fixture):
    server = StubControlPlane(fixture).start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def settings(stub, fixture):
    return settings_for(stub, fixture)


@pytest.fixture
def auth(settings):
    return authenticator_for(settings)


# ---------------------------------------------------------------------------
# 2.1 / 2.2 — the AS's own token, verified by the RS's own verifier
# ---------------------------------------------------------------------------


async def test_the_as_minted_token_verifies_against_the_served_jwks(auth, fixture, stub):
    """The verbatim artefact of `mintAccessToken`, checked cryptographically.

    Its `exp` is frozen at fixture-generation time, so the signature and every
    non-temporal claim are verified here and the live-token path is covered by
    the next test.
    """
    token = fixture["tokens"]["mcp_access"]
    key = await auth.jwks.get_key(fixture["kid"])

    claims = jwt.decode(
        token,
        key.key,
        algorithms=["ES256"],
        audience=fixture["audience"],
        issuer=fixture["issuer"],
        options={"verify_exp": False, "require": ["exp", "iss", "aud"]},
    )

    assert claims["token_use"] == "mcp_access"
    assert claims["aud"] == "kumiho-server"
    assert claims["resource"] == fixture["resource"]
    assert claims["exp"] - claims["iat"] == 3600
    assert len(stub.jwks_requests) == 1


async def test_a_live_access_token_is_accepted(auth, signer, fixture):
    token = signer.sign(signer.access_claims())
    principal = await auth.authenticate(headers(authorization=f"Bearer {token}"))

    assert principal.kind == "oauth"
    assert principal.tenant_id == fixture["tokens"]["mcp_access_claims"]["tenant_id"]
    assert principal.user_id == fixture["tokens"]["mcp_access_claims"]["sub"]
    assert principal.client_id == fixture["tokens"]["mcp_access_claims"]["client_id"]
    assert principal.tenant_slug == "acme"
    assert principal.region_code == "us-east-1"
    assert REQUIRED_SCOPE in principal.scopes
    assert principal.token_id == principal.claims["jti"]
    # The raw token is never rendered, only fingerprinted.
    assert principal.token_fp and token not in principal.token_fp


async def test_a_token_bound_to_another_resource_is_refused(auth, signer):
    token = signer.sign(signer.access_claims(resource="https://mcp.evil.example/mcp"))
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))
    assert excinfo.value.status == 401
    assert "another resource" in excinfo.value.description


async def test_an_expired_token_is_refused(auth, signer):
    now = int(time.time())
    token = signer.sign(signer.access_claims(iat=now - 7200, nbf=now - 7200, exp=now - 3600))
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))
    assert excinfo.value.status == 401
    assert excinfo.value.description == "token expired"


async def test_wrong_issuer_and_wrong_audience_are_refused(auth, signer):
    for overrides in ({"iss": "https://control.evil.example"}, {"aud": "some-other-api"}):
        token = signer.sign(signer.access_claims(**overrides))
        with pytest.raises(AuthError):
            await auth.authenticate(headers(authorization=f"Bearer {token}"))


async def test_alg_none_is_refused(auth, signer, stub):
    token = signer.sign_none(signer.access_claims())
    before = len(stub.jwks_requests)
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))
    assert "unsupported signing algorithm" in excinfo.value.description
    # Refused on the header alone: no key lookup, no outbound request.
    assert len(stub.jwks_requests) == before


async def test_hs256_key_confusion_is_refused(auth, signer, fixture, stub):
    """Signing with the published public key as an HMAC secret must not verify."""
    token = signer.sign_hs256(signer.access_claims(), signer.public_key_bytes())

    before = len(stub.jwks_requests)
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))
    assert "unsupported signing algorithm" in excinfo.value.description
    assert len(stub.jwks_requests) == before

    # Belt and braces: even past the allowlist, PyJWT refuses it for an EC key.
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(token, signer.public_key, algorithms=["ES256"], options={"verify_aud": False})


async def test_an_unknown_kid_refreshes_the_jwks_once_then_rejects(auth, signer, stub):
    # Warm the cache with a good token first.
    await auth.authenticate(headers(authorization=f"Bearer {signer.sign(signer.access_claims())}"))
    warmed = len(stub.jwks_requests)
    assert warmed == 1

    token = signer.sign(signer.access_claims(), kid="kumiho-cp-key-99")
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))

    assert "signing key not found" in excinfo.value.description
    # Exactly one extra fetch: an unknown kid may be a rotation, but a stream of
    # bogus tokens must not become a stream of outbound requests.
    assert len(stub.jwks_requests) == warmed + 1


async def test_a_token_signed_by_an_unpublished_key_is_refused(auth, signer, stub, fixture):
    stub.publish_only()  # the control plane publishes nothing
    token = signer.sign(signer.access_claims())
    with pytest.raises(AuthError):
        await auth.authenticate(headers(authorization=f"Bearer {token}"))


async def test_the_401_challenge_matches_what_claude_parses(settings):
    assert challenge_header(settings) == (
        f'Bearer resource_metadata="{settings.prm_url}", scope="memory"'
    )
    assert challenge_header(settings, error="invalid_token") == (
        f'Bearer resource_metadata="{settings.prm_url}", scope="memory", error="invalid_token"'
    )


# ---------------------------------------------------------------------------
# 2.2 — service token: introspection request/response shape
# ---------------------------------------------------------------------------


async def test_service_token_introspection_matches_the_as_route(auth, signer, stub, fixture):
    token = signer.sign(signer.service_claims())
    principal = await auth.authenticate(headers(x_api_key=token))

    assert principal.kind == "service"
    assert principal.token_id == fixture["service_token_id"]
    # Plan §1.10: the memory identity for an API key.
    assert principal.user_id == f"service:{fixture['service_token_id']}"
    # The tenant comes from the introspection answer, not from the JWT.
    assert principal.tenant_id == fixture["introspection_response"]["tenant_id"]

    assert len(stub.introspect_requests) == 1
    request = stub.introspect_requests[0]
    assert request.method == "POST"
    assert request.path == INTROSPECT_PATH
    assert request.headers["x-control-plane-key"] == fixture["test_internal_key"]
    assert request.body == {"token_id": fixture["service_token_id"]}
    assert "authorization" not in request.headers


async def test_service_token_introspection_is_cached(auth, signer, stub):
    token = signer.sign(signer.service_claims())
    await auth.authenticate(headers(x_api_key=token))
    await auth.authenticate(headers(x_api_key=token))
    assert len(stub.introspect_requests) == 1


async def test_a_revoked_api_key_is_refused(auth, signer, stub, fixture):
    stub.introspection[fixture["service_token_id"]] = {
        "active": False,
        "tenant_id": None,
        "expires_at": None,
    }
    token = signer.sign(signer.service_claims())
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(x_api_key=token))
    assert "revoked" in excinfo.value.description


async def test_introspection_failure_fails_closed(auth, signer, stub):
    stub.introspection_status = 500
    token = signer.sign(signer.service_claims())
    with pytest.raises(AuthError):
        await auth.authenticate(headers(x_api_key=token))


async def test_a_service_token_bound_to_another_resource_is_refused(auth, signer, stub):
    """`resource` is checked before the token_use/type split, for both kinds."""
    token = signer.sign(signer.service_claims(resource="https://mcp.evil.example/mcp"))
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(x_api_key=token))
    assert "another resource" in excinfo.value.description
    assert stub.introspect_requests == []


async def test_a_token_that_is_neither_kind_is_refused(auth, signer):
    claims = signer.access_claims()
    claims.pop("token_use")
    token = signer.sign(claims)
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(headers(authorization=f"Bearer {token}"))
    assert "neither an MCP access token nor a service token" in excinfo.value.description


# ---------------------------------------------------------------------------
# 2.3 — discovery: request shape out, DiscoveryRecord shape in
# ---------------------------------------------------------------------------


async def test_discovery_request_shape_matches_the_control_plane_route(auth, signer, stub, settings):
    token = signer.sign(signer.access_claims())
    principal = await auth.authenticate(headers(authorization=f"Bearer {token}"))

    router = DiscoveryRouter(settings)
    target = await router.resolve(principal)

    assert target == stub.discovery_payload["region"]["grpc_authority"]
    assert len(stub.discovery_requests) == 1
    request = stub.discovery_requests[0]
    assert request.method == "POST"
    assert request.path == DISCOVERY_PATH
    assert request.headers["authorization"] == f"Bearer {token}"
    # The route's zod schema is `.strict()`: any extra key is a 400.
    assert set(request.body) == {"tenant_hint"}
    assert request.body["tenant_hint"] == principal.tenant_slug
    # …and `tenantHintMatchesRecord` compares the hint against the record, so
    # the slug in the token has to be the slug the directory returns.
    assert request.body["tenant_hint"] == "acme"


async def test_discovery_response_satisfies_the_sdk_discovery_record(stub):
    """The AS's payload must deserialize into the SDK's DiscoveryRecord."""
    discovery = pytest.importorskip("kumiho.discovery")
    record = discovery.DiscoveryRecord.from_dict(stub.discovery_payload)

    assert record.tenant_id == stub.discovery_payload["tenant_id"]
    assert record.region.grpc_authority == "us-east-1.kumiho.cloud:443"
    assert record.region.server_url == "https://us-east-1.kumiho.cloud"
    assert record.region.region_code == "us-east-1"
    assert list(record.roles) == ["owner"]


async def test_discovery_falls_back_to_server_url_when_there_is_no_grpc_authority(
    auth, signer, stub, settings
):
    stub.discovery_payload["region"].pop("grpc_authority")
    token = signer.sign(signer.access_claims())
    principal = await auth.authenticate(headers(authorization=f"Bearer {token}"))

    target = await DiscoveryRouter(settings).resolve(principal)
    assert target == "https://us-east-1.kumiho.cloud"


async def test_discovery_failure_is_a_routing_error_not_a_silent_default(
    auth, signer, stub, settings
):
    stub.discovery_status = 502
    token = signer.sign(signer.access_claims())
    principal = await auth.authenticate(headers(authorization=f"Bearer {token}"))

    with pytest.raises(RoutingError):
        await DiscoveryRouter(settings).resolve(principal)


# ---------------------------------------------------------------------------
# 2.4 — the documents Claude reads, checked against plan §0
# ---------------------------------------------------------------------------


def test_protected_resource_metadata_matches_rfc_9728_and_the_plan(settings, fixture):
    prm = settings.protected_resource_metadata()

    # "PRM `resource` must equal the MCP URL exactly as entered."
    assert prm["resource"] == fixture["resource"] == "https://mcp.kumiho.cloud/mcp"
    # "`authorization_servers[0]` is used (no fallback)."
    assert prm["authorization_servers"][0] == fixture["issuer"]
    assert prm["scopes_supported"] == ["memory", "offline_access"]
    assert prm["bearer_methods_supported"] == ["header"]
    assert prm["resource_documentation"]


def test_the_resource_the_as_stamps_is_the_url_the_rs_publishes(settings, fixture):
    """The single most breakable coupling in the whole flow."""
    assert settings.public_url == fixture["resource"]
    assert fixture["tokens"]["mcp_access_claims"]["resource"] == settings.public_url


def test_the_as_and_rs_agree_on_issuer_audience_and_algorithm(settings, fixture):
    assert settings.issuer == fixture["issuer"] == "https://control.kumiho.cloud"
    assert settings.audience == fixture["audience"] == "kumiho-server"
    assert fixture["alg"] == "ES256"
    assert fixture["kid"] == "kumiho-cp-key-1"


def test_as_metadata_meets_every_bullet_claude_requires(fixture, settings):
    metadata = fixture["authorization_server_metadata"]

    # PKCE S256 on every request.
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    # CIMD is only chosen when both of these hold.
    assert metadata["client_id_metadata_document_supported"] is True
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    # Only advertise auth methods that are implemented.
    assert "client_secret_post" not in metadata["token_endpoint_auth_methods_supported"]
    # Claude appends offline_access when it is listed, and rotates refresh tokens.
    assert metadata["scopes_supported"] == ["memory", "offline_access"]
    assert set(metadata["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert metadata["response_types_supported"] == ["code"]
    # DCR endpoint present for the oauth_dcr path.
    assert metadata["registration_endpoint"].endswith("/api/oauth/register")
    assert metadata["token_endpoint"].endswith("/api/oauth/token")
    assert metadata["revocation_endpoint"].endswith("/api/oauth/revoke")
    assert metadata["jwks_uri"].endswith("/.well-known/kumiho-jwks.json")
    # The AS in the PRM must be the issuer in the metadata.
    assert metadata["issuer"] == settings.protected_resource_metadata()["authorization_servers"][0]
    # Everything is https.
    for key in (
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
        "revocation_endpoint",
        "jwks_uri",
    ):
        assert metadata[key].startswith("https://"), key
