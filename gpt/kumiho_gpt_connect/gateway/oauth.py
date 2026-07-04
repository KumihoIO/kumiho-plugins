"""A minimal OAuth 2.1 authorization server for the ChatGPT MCP connector.

Scope is deliberately small: exactly what ChatGPT's custom-connector client
needs — authorization code + PKCE (S256), Dynamic Client Registration (RFC
7591), RFC 8414 AS metadata, and RFC 9728 protected-resource metadata. There
is no password database: the single local user proves control by entering the
one-time PIN the installer printed. Tokens are short-lived RS256 JWTs signed by
a locally generated key; the gateway verifies them in-process.

This is not a general-purpose IdP. It authenticates *one* local operator
exposing *their own* CE memory to *their own* ChatGPT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .. import config as cfgmod

ACCESS_TTL = 3600            # 1 hour
CODE_TTL = 300               # 5 minutes
_SCOPE = "kumiho.memory"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class OAuthServer:
    def __init__(self, cfg: cfgmod.Config) -> None:
        self.cfg = cfg
        self._key = self._load_or_create_key()
        self._public_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._kid = cfg.instance_id
        self._clients: Dict[str, dict] = self._load_clients()
        self._codes: Dict[str, dict] = {}       # code -> record (in-memory, short-lived)
        self._refresh: Dict[str, dict] = {}      # refresh_token -> {client_id}

    # ----- key + client persistence ---------------------------------------

    def _load_or_create_key(self) -> rsa.RSAPrivateKey:
        path = cfgmod.signing_key_path()
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cfgmod._harden(path)
        return key

    def _load_clients(self) -> Dict[str, dict]:
        path = cfgmod.clients_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_clients(self) -> None:
        cfgmod.clients_path().write_text(
            json.dumps(self._clients, indent=2) + "\n", encoding="utf-8"
        )
        cfgmod._harden(cfgmod.clients_path())

    # ----- metadata --------------------------------------------------------

    @property
    def issuer(self) -> str:
        return self.cfg.issuer

    def _jwk(self) -> dict:
        pub = self._key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self._kid,
            "n": _b64url(pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")),
            "e": _b64url(pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")),
        }

    async def protected_resource_metadata(self, request: Request) -> JSONResponse:
        # RFC 9728 — tells ChatGPT which AS guards this MCP resource.
        return JSONResponse(
            {
                "resource": self.cfg.connector_url or self.issuer,
                "authorization_servers": [self.issuer],
                "bearer_methods_supported": ["header"],
                "scopes_supported": [_SCOPE],
            }
        )

    async def authorization_server_metadata(self, request: Request) -> JSONResponse:
        # RFC 8414
        iss = self.issuer
        return JSONResponse(
            {
                "issuer": iss,
                "authorization_endpoint": f"{iss}/authorize",
                "token_endpoint": f"{iss}/token",
                "registration_endpoint": f"{iss}/register",
                "jwks_uri": f"{iss}/jwks",
                "scopes_supported": [_SCOPE],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    async def jwks(self, request: Request) -> JSONResponse:
        return JSONResponse({"keys": [self._jwk()]})

    # ----- dynamic client registration (RFC 7591) --------------------------

    async def register(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        redirect_uris = body.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return JSONResponse(
                {"error": "invalid_client_metadata", "error_description": "redirect_uris required"},
                status_code=400,
            )
        client_id = "kgc-" + secrets.token_urlsafe(16)
        record = {
            "client_id": client_id,
            "redirect_uris": [str(u) for u in redirect_uris],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": str(body.get("client_name", "ChatGPT")),
        }
        self._clients[client_id] = record
        self._save_clients()
        return JSONResponse(record, status_code=201)

    # ----- authorize (PIN consent) -----------------------------------------

    def _client_for(self, client_id: str, redirect_uri: str) -> Optional[dict]:
        client = self._clients.get(client_id)
        if not client or redirect_uri not in client.get("redirect_uris", []):
            return None
        return client

    async def authorize(self, request: Request):
        if request.method == "GET":
            q = request.query_params
            missing = [p for p in ("client_id", "redirect_uri", "code_challenge") if not q.get(p)]
            if missing:
                return HTMLResponse(f"<h1>Invalid request</h1><p>Missing: {', '.join(missing)}</p>", status_code=400)
            if q.get("code_challenge_method", "S256") != "S256":
                return HTMLResponse("<h1>Invalid request</h1><p>Only PKCE S256 is supported.</p>", status_code=400)
            if not self._client_for(q["client_id"], q["redirect_uri"]):
                return HTMLResponse("<h1>Unknown client</h1>", status_code=400)
            return HTMLResponse(_consent_page(dict(q)))

        # POST — the user submitted the consent form with the PIN.
        form = await request.form()
        client_id = str(form.get("client_id", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        if not self._client_for(client_id, redirect_uri):
            return HTMLResponse("<h1>Unknown client</h1>", status_code=400)
        if not secrets.compare_digest(str(form.get("pin", "")).strip().upper(), self.cfg.pin.upper()):
            return HTMLResponse(_consent_page(dict(form), error="Incorrect PIN — try again."), status_code=401)

        code = secrets.token_urlsafe(24)
        self._codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": str(form.get("code_challenge", "")),
            "scope": str(form.get("scope", _SCOPE)),
            "exp": time.time() + CODE_TTL,
        }
        sep = "&" if "?" in redirect_uri else "?"
        target = f"{redirect_uri}{sep}code={code}"
        if form.get("state"):
            target += f"&state={form['state']}"
        return RedirectResponse(target, status_code=302)

    # ----- token ------------------------------------------------------------

    async def token(self, request: Request) -> JSONResponse:
        form = await request.form()
        grant = str(form.get("grant_type", ""))
        if grant == "authorization_code":
            return self._token_from_code(form)
        if grant == "refresh_token":
            return self._token_from_refresh(form)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    def _token_from_code(self, form) -> JSONResponse:
        code = str(form.get("code", ""))
        rec = self._codes.pop(code, None)
        if not rec or rec["exp"] < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if str(form.get("redirect_uri", "")) != rec["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)
        # PKCE S256 verification.
        verifier = str(form.get("code_verifier", ""))
        expected = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        if not verifier or not secrets.compare_digest(expected, rec["code_challenge"]):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE failed"}, status_code=400)
        return self._issue(rec["client_id"], rec["scope"])

    def _token_from_refresh(self, form) -> JSONResponse:
        token = str(form.get("refresh_token", ""))
        rec = self._refresh.get(token)
        if not rec:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return self._issue(rec["client_id"], rec.get("scope", _SCOPE))

    def _issue(self, client_id: str, scope: str) -> JSONResponse:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": "kumiho-ce-operator",
            "aud": self.cfg.connector_url or self.issuer,
            "iat": now,
            "exp": now + ACCESS_TTL,
            "scope": scope,
            "client_id": client_id,
        }
        access = jwt.encode(claims, self._pem(), algorithm="RS256", headers={"kid": self._kid})
        refresh = secrets.token_urlsafe(32)
        self._refresh[refresh] = {"client_id": client_id, "scope": scope}
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": ACCESS_TTL,
                "refresh_token": refresh,
                "scope": scope,
            }
        )

    def _pem(self) -> bytes:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    # ----- resource-side verification --------------------------------------

    def verify_bearer(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            return jwt.decode(
                token,
                self._public_pem,
                algorithms=["RS256"],
                audience=self.cfg.connector_url or self.issuer,
                issuer=self.issuer,
            )
        except jwt.PyJWTError:
            return None

    # ----- routes -----------------------------------------------------------

    def routes(self) -> list[Route]:
        return [
            Route("/.well-known/oauth-authorization-server", self.authorization_server_metadata),
            Route("/.well-known/oauth-protected-resource", self.protected_resource_metadata),
            Route("/jwks", self.jwks),
            Route("/register", self.register, methods=["POST"]),
            Route("/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
        ]


def _consent_page(params: dict, error: str = "") -> str:
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{_esc(params.get(k, ""))}">'
        for k in ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope")
    )
    err = f'<p style="color:#c0392b">{_esc(error)}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Connect Kumiho Memory to ChatGPT</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:26rem;margin:4rem auto;padding:0 1rem}}
input[type=text]{{font-size:1.4rem;letter-spacing:.2em;text-align:center;width:100%;padding:.6rem;margin:.5rem 0}}
button{{width:100%;padding:.7rem;font-size:1rem;background:#111;color:#fff;border:0;border-radius:.4rem}}</style>
</head><body>
<h1>Connect Kumiho Memory</h1>
<p>ChatGPT wants to access your local Kumiho memory. Enter the PIN shown by the installer to approve.</p>
{err}
<form method="post" action="/authorize">
{hidden}
<input type="text" name="pin" autocomplete="one-time-code" autofocus placeholder="PIN" maxlength="16">
<button type="submit">Approve</button>
</form>
<p style="color:#888;font-size:.8rem">If you did not start this, close this page — no access is granted without the PIN.</p>
</body></html>"""


def _esc(v: Any) -> str:
    return (
        str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
