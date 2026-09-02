"""Shared fixtures.

Everything here is hermetic: an ES256 key pair is generated per session, the
control plane (JWKS, introspection, discovery) is an ``httpx.MockTransport``,
and the Kumiho gRPC client is a stub. No network, no local credentials.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kumiho_cloud_mcp.settings import Settings, load_settings  # noqa: E402

ISSUER = "https://control.test"
AUDIENCE = "kumiho-server"
PUBLIC_URL = "https://mcp.test/mcp"
KID = "kumiho-cp-key-1"
OTHER_KID = "kumiho-cp-key-2"
INTERNAL_KEY = "internal-test-key"


# ---------------------------------------------------------------------------
# keys / tokens
# ---------------------------------------------------------------------------


class KeyPair:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.jwk = json.loads(
            jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key())  # type: ignore[arg-type]
        )
        self.jwk.update({"kid": kid, "use": "sig", "alg": "ES256"})

    def sign(self, claims: Dict[str, Any], *, kid: Optional[str] = None) -> str:
        return jwt.encode(
            claims, self.private, algorithm="ES256", headers={"kid": kid or self.kid}
        )


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """One backend: the service runs under uvicorn/asyncio in production."""
    return "asyncio"


@pytest.fixture(scope="session")
def keypair() -> KeyPair:
    return KeyPair(KID)


@pytest.fixture(scope="session")
def rogue_keypair() -> KeyPair:
    """A key the control plane never published — signatures must not verify."""
    return KeyPair(OTHER_KID)


def base_claims(**overrides: Any) -> Dict[str, Any]:
    now = int(time.time())
    claims: Dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "firebase-uid-1",
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
        "token_use": "mcp_access",
        "client_id": "dcr_test",
        "scope": "memory offline_access",
        "resource": PUBLIC_URL,
        "tenant_id": "tenant-aaaa",
        "tenant_slug": "acme",
        "tenant_tier": "pro",
        "region_code": "us-east-1",
    }
    claims.update(overrides)
    return claims


def service_claims(**overrides: Any) -> Dict[str, Any]:
    now = int(time.time())
    token_id = overrides.pop("token_id", "svc-token-1")
    claims: Dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "tenant-bbbb",
        "iat": now,
        "exp": now + 365 * 24 * 3600,
        "jti": token_id,
        "token_id": token_id,
        "type": "service_token",
        "tenant_id": "tenant-bbbb",
        "tenant_slug": "globex",
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# fake control plane
# ---------------------------------------------------------------------------


class FakeControlPlane:
    """MockTransport backend for JWKS, introspection and discovery."""

    def __init__(self, keypair: KeyPair) -> None:
        self.keypair = keypair
        self.jwks_calls = 0
        self.introspect_calls = 0
        self.discovery_calls = 0
        self.introspection: Dict[str, Dict[str, Any]] = {
            "svc-token-1": {"active": True, "tenant_id": "tenant-bbbb", "expires_at": None}
        }
        self.published_keys: List[KeyPair] = [keypair]
        self.introspection_status = 200

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("kumiho-jwks.json"):
            self.jwks_calls += 1
            return httpx.Response(200, json={"keys": [k.jwk for k in self.published_keys]})
        if path.endswith("/service-token/introspect"):
            self.introspect_calls += 1
            if request.headers.get("x-control-plane-key") != INTERNAL_KEY:
                return httpx.Response(403, json={"error": "forbidden"})
            if self.introspection_status != 200:
                return httpx.Response(self.introspection_status, json={"error": "nope"})
            body = json.loads(request.content or b"{}")
            record = self.introspection.get(
                body.get("token_id"), {"active": False, "tenant_id": None, "expires_at": None}
            )
            return httpx.Response(200, json=record)
        if path.endswith("/api/discovery/tenant"):
            self.discovery_calls += 1
            return httpx.Response(
                200,
                json={
                    "region": {
                        "server_url": "https://us-east-1.kumiho.cloud",
                        "grpc_authority": "us-east-1.kumiho.cloud:443",
                    }
                },
            )
        return httpx.Response(404, json={"error": "unmapped", "path": path})


@pytest.fixture
def control_plane(keypair: KeyPair) -> FakeControlPlane:
    return FakeControlPlane(keypair)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return load_settings(
        {
            "KUMIHO_MCP_PUBLIC_URL": PUBLIC_URL,
            "KUMIHO_AS_ISSUER": ISSUER,
            "KUMIHO_JWKS_URL": f"{ISSUER}/.well-known/kumiho-jwks.json",
            "KUMIHO_CONTROL_PLANE_URL": ISSUER,
            "KUMIHO_CONTROL_PLANE_INTERNAL_KEY": INTERNAL_KEY,
            "KUMIHO_MCP_HOSTED": "1",
            "KUMIHO_MCP_JSON_RESPONSE": "1",
            "KUMIHO_MCP_ENABLE_SSE": "0",
            "KUMIHO_MCP_LOG_LEVEL": "WARNING",
            # The production cooldown throttles JWKS refreshes; tests that care
            # about it set it explicitly (see test_jwks_refresh_cooldown).
            "KUMIHO_MCP_JWKS_COOLDOWN_SECONDS": "0",
        }
    )


# ---------------------------------------------------------------------------
# fake kumiho client
# ---------------------------------------------------------------------------


class FakeKumihoClient:
    """Stands in for a gRPC channel; records what it was built with."""

    def __init__(self, *, target: str, token: Optional[str], metadata) -> None:
        self.target = target
        self.token = token
        self.metadata = list(metadata)
        self.closed = False

    @property
    def tenant_id(self) -> Optional[str]:
        for key, value in self.metadata:
            if key == "x-tenant-id":
                return value
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_clients(monkeypatch) -> List[FakeKumihoClient]:
    """Replace real client construction; return the list of built clients."""
    import kumiho_cloud_mcp.clients as clients_module

    built: List[FakeKumihoClient] = []

    def _fake(*, target: str, token: Optional[str], metadata) -> FakeKumihoClient:
        client = FakeKumihoClient(target=target, token=token, metadata=metadata)
        built.append(client)
        return client

    monkeypatch.setattr(clients_module, "_construct_client", _fake)
    return built


# ---------------------------------------------------------------------------
# ASGI driving
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def run_lifespan(app):
    """Drive the ASGI lifespan protocol (httpx.ASGITransport does not)."""
    from anyio import create_memory_object_stream

    send_from_app, recv_from_app = create_memory_object_stream(16)
    send_to_app, recv_to_app = create_memory_object_stream(16)

    async with anyio.create_task_group() as tg:

        async def runner() -> None:
            await app(
                {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}},
                recv_to_app.receive,
                send_from_app.send,
            )

        tg.start_soon(runner)
        await send_to_app.send({"type": "lifespan.startup"})
        message = await recv_from_app.receive()
        assert message["type"] == "lifespan.startup.complete", message
        try:
            yield
        finally:
            await send_to_app.send({"type": "lifespan.shutdown"})
            await recv_from_app.receive()


@contextlib.asynccontextmanager
async def client_for(app, control_plane: FakeControlPlane):
    """Start the app, swap in the fake control plane, hand back an HTTP client."""
    async with run_lifespan(app):
        stub = httpx.AsyncClient(transport=control_plane.transport())
        app.state.authenticator.attach(stub)
        app.state.discovery_router.attach(stub)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://mcp.test"
            ) as http:
                yield http
        finally:
            await stub.aclose()


MCP_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def rpc(method: str, params: Optional[dict] = None, *, id_: int = 1) -> dict:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        payload["params"] = params
    return payload
