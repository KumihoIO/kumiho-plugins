"""Harness for the AS <-> RS cross-contract suite (WP-E2, part 2).

The point of this package is that nothing is mocked at the seam under test.
``as_fixture.json`` is written by the *authorization server's own* minting code
(``packages/origin/src/lib/oauth/contract.test.ts`` in kumiho-control), and the
control plane here is a real HTTP server on 127.0.0.1 that the resource server
reaches with real ``httpx`` requests. So a drift in the token claim set, the
JWKS shape, the introspection contract or the discovery contract fails here
rather than in production.

Regenerating the fixture after an AS change::

    cd <kumiho-control>/packages/origin
    KUMIHO_CONTRACT_FIXTURE_OUT=<this dir>/as_fixture.json \\
        npx jest src/lib/oauth/contract.test.ts

The private key in the fixture is a throwaway P-256 key generated for that test
run. It signs nothing outside this harness.

Deliberately *not* named ``conftest.py``: ``tests/`` already has one that sibling
suites import by bare module name, and a second top-level ``conftest`` module
shadows it.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jwt
from jwt.algorithms import ECAlgorithm

FIXTURE_PATH = Path(__file__).with_name("as_fixture.json")

JWKS_PATH = "/.well-known/kumiho-jwks.json"
INTROSPECT_PATH = "/api/control-plane/service-token/introspect"
DISCOVERY_PATH = "/api/discovery/tenant"


def load_fixture() -> Dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------


class FixtureSigner:
    """Re-signs the AS's own claim sets with the AS's own key.

    The verbatim tokens in the fixture carry a fixed ``exp``, so anything
    time-sensitive (a live token, an expired one) is re-signed here. The claims
    always start from what the AS actually produced, so the contract under test
    is still the AS's.
    """

    def __init__(self, fixture: Dict[str, Any]) -> None:
        self.fixture = fixture
        self.kid = fixture["kid"]
        self._private = ECAlgorithm.from_jwk(json.dumps(fixture["private_jwk"]))
        self._public = ECAlgorithm.from_jwk(json.dumps(fixture["jwks"]["keys"][0]))

    @property
    def public_key(self) -> Any:
        return self._public

    def access_claims(self, **overrides: Any) -> Dict[str, Any]:
        claims = deepcopy(self.fixture["tokens"]["mcp_access_claims"])
        now = int(time.time())
        claims.update({"iat": now, "nbf": now, "exp": now + 3600})
        claims.update(overrides)
        return claims

    def service_claims(self, **overrides: Any) -> Dict[str, Any]:
        claims = deepcopy(self.fixture["tokens"]["service_token_claims"])
        now = int(time.time())
        claims.update({"iat": now, "nbf": now, "exp": now + 365 * 24 * 3600})
        claims.update(overrides)
        return claims

    def sign(self, claims: Dict[str, Any], *, kid: Optional[str] = None) -> str:
        return jwt.encode(
            claims, self._private, algorithm="ES256", headers={"kid": kid or self.kid}
        )

    def public_key_bytes(self) -> bytes:
        """The raw P-256 point, i.e. what an attacker reads from the JWKS.

        This is the HMAC secret an algorithm-confusion attempt would use: PyJWT
        refuses a PEM or a JWK document as a secret outright, so the raw
        coordinate bytes are the only shape the attack can actually take.
        """
        jwk = self.fixture["jwks"]["keys"][0]

        def decode(value: str) -> bytes:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        return decode(jwk["x"]) + decode(jwk["y"])

    def sign_hs256(self, claims: Dict[str, Any], secret: bytes) -> str:
        """An HMAC token that claims the control plane's kid (alg confusion)."""
        return jwt.encode(claims, secret, algorithm="HS256", headers={"kid": self.kid})

    def sign_none(self, claims: Dict[str, Any]) -> str:
        """An unsigned `alg: none` token, hand-assembled (PyJWT will not make one)."""

        def segment(payload: Dict[str, Any]) -> str:
            raw = json.dumps(payload, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        return f'{segment({"alg": "none", "typ": "JWT", "kid": self.kid})}.{segment(claims)}.'


# ---------------------------------------------------------------------------
# a real control plane on 127.0.0.1
# ---------------------------------------------------------------------------


class RecordedRequest:
    __slots__ = ("method", "path", "headers", "body")

    def __init__(self, method: str, path: str, headers: Dict[str, str], body: Any) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.method} {self.path} body={self.body!r}>"


class StubControlPlane:
    """Serves the three control-plane endpoints the resource server calls.

    A genuine socket server rather than an ``httpx.MockTransport``: the point of
    this suite is to exercise the RS's own HTTP client, JWKS cache and
    introspection plumbing end to end.
    """

    def __init__(self, fixture: Dict[str, Any]) -> None:
        self.fixture = fixture
        self.jwks_requests: List[RecordedRequest] = []
        self.introspect_requests: List[RecordedRequest] = []
        self.discovery_requests: List[RecordedRequest] = []

        self.internal_key: str = fixture["test_internal_key"]
        self.jwks: Dict[str, Any] = deepcopy(fixture["jwks"])
        self.introspection: Dict[str, Dict[str, Any]] = {
            fixture["service_token_id"]: deepcopy(fixture["introspection_response"])
        }
        self.introspection_status = 200
        self.discovery_payload: Dict[str, Any] = deepcopy(fixture["discovery_response"])
        self.discovery_status = 200

        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "StubControlPlane":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # silence the default stderr log
                return

            def _read_json(self) -> Any:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return {"_raw": raw.decode("utf-8", "replace")}

            def _respond(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                headers = {k.lower(): v for k, v in self.headers.items()}
                if self.path == JWKS_PATH:
                    outer.jwks_requests.append(RecordedRequest("GET", self.path, headers, None))
                    self._respond(200, outer.jwks)
                    return
                self._respond(404, {"error": "unmapped", "path": self.path})

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                headers = {k.lower(): v for k, v in self.headers.items()}
                body = self._read_json()

                if self.path == INTROSPECT_PATH:
                    outer.introspect_requests.append(
                        RecordedRequest("POST", self.path, headers, body)
                    )
                    if headers.get("x-control-plane-key") != outer.internal_key:
                        self._respond(401, {"error": "unauthorized"})
                        return
                    if outer.introspection_status != 200:
                        self._respond(outer.introspection_status, {"error": "unavailable"})
                        return
                    record = outer.introspection.get(
                        body.get("token_id"),
                        {"active": False, "tenant_id": None, "expires_at": None},
                    )
                    self._respond(200, record)
                    return

                if self.path == DISCOVERY_PATH:
                    outer.discovery_requests.append(
                        RecordedRequest("POST", self.path, headers, body)
                    )
                    if outer.discovery_status != 200:
                        self._respond(outer.discovery_status, {"error": "unavailable"})
                        return
                    self._respond(200, outer.discovery_payload)
                    return

                self._respond(404, {"error": "unmapped", "path": self.path})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- helpers ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        assert self._server is not None, "start() first"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def jwks_url(self) -> str:
        return f"{self.base_url}{JWKS_PATH}"

    def publish_only(self, *kids: str) -> None:
        """Restrict the served JWKS to these kids (key rotation / unknown kid)."""
        self.jwks = {"keys": [k for k in self.fixture["jwks"]["keys"] if k["kid"] in kids]}


def settings_for(stub: StubControlPlane, fixture: Dict[str, Any], **overrides: Any):
    """RS settings wired at the stub, otherwise exactly the plan §2.4 values."""
    from kumiho_cloud_mcp.settings import load_settings

    environ = {
        "KUMIHO_MCP_PUBLIC_URL": fixture["resource"],
        "KUMIHO_AS_ISSUER": fixture["issuer"],
        "KUMIHO_JWKS_URL": stub.jwks_url,
        "KUMIHO_CONTROL_PLANE_URL": stub.base_url,
        "KUMIHO_CONTROL_PLANE_INTERNAL_KEY": stub.internal_key,
        "KUMIHO_MCP_HOSTED": "1",
        "KUMIHO_MCP_LOG_LEVEL": "WARNING",
        # The production cooldown would suppress the unknown-kid refresh the
        # contract asserts; the cooldown itself is covered by tests/test_auth.py.
        "KUMIHO_MCP_JWKS_COOLDOWN_SECONDS": "0",
    }
    environ.update({k: str(v) for k, v in overrides.items()})
    return load_settings(environ)


def authenticator_for(settings) -> Any:
    from kumiho_cloud_mcp.auth import Authenticator

    return Authenticator(settings)


def headers(**values: str) -> Dict[str, str]:
    """Minimal case-insensitive header mapping, the shape `extract_token` wants."""
    return {k.replace("_", "-").lower(): v for k, v in values.items()}


AuthErrorFactory = Callable[..., Exception]
