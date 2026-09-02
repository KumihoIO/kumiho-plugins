"""Token verification for the resource server (plan §2.4).

Two credentials are accepted and they are *the same JWT format*, minted by the
control plane with the same ES256 key:

``Authorization: Bearer <jwt>`` where ``token_use == "mcp_access"``
    An OAuth 2.1 access token issued by ``control.kumiho.cloud`` for this
    resource. Signature + ``iss`` + ``aud`` + ``exp`` are enough; the token is
    short-lived (1 h) so there is nothing to revoke.

``x-api-key: <jwt>`` (or ``Authorization: Bearer``) where ``type == "service_token"``
    A dashboard API key. These live for a year and *deleting one in the
    dashboard does not invalidate the JWT*, so every request additionally
    introspects ``token_id`` against the control plane. That call fails closed:
    if we cannot confirm the key is still active, the request is rejected.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import anyio
import httpx
import jwt
from jwt import PyJWK

from .logging_setup import token_fingerprint
from .settings import (
    DEV_TENANT_ID,
    DEV_TENANT_SLUG,
    DEV_TOKEN_ID,
    DEV_USER_ID,
    REQUIRED_SCOPE,
    Settings,
)

logger = logging.getLogger("kumiho.cloud_mcp.auth")

_ALLOWED_ALGS = ("ES256",)


class AuthError(Exception):
    """A request that must be answered with 401 (or 403 for scope)."""

    def __init__(
        self,
        code: str,
        description: str,
        *,
        token_present: bool,
        status: int = 401,
    ) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.token_present = token_present
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Everything downstream needs about the caller."""

    tenant_id: str
    user_id: str
    token: str
    kind: str  # "oauth" | "service" | "dev"
    token_id: Optional[str] = None
    client_id: Optional[str] = None
    tenant_slug: Optional[str] = None
    region_code: Optional[str] = None
    expires_at: Optional[float] = None
    scopes: List[str] = field(default_factory=list)
    claims: Dict[str, Any] = field(default_factory=dict)

    @property
    def token_fp(self) -> Optional[str]:
        return token_fingerprint(self.token)


def extract_token(headers) -> Tuple[Optional[str], str]:
    """Return ``(token, source)`` from ``Authorization`` or ``x-api-key``.

    ``source`` is ``"bearer"``, ``"x-api-key"`` or ``"none"``. A malformed
    ``Authorization`` header (not ``Bearer <t>``) counts as *presented but bad*
    so the challenge carries ``error="invalid_token"``.
    """
    authorization = headers.get("authorization")
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip(), "bearer"
        return "", "bearer"  # present but unusable
    api_key = headers.get("x-api-key")
    if api_key and api_key.strip():
        return api_key.strip(), "x-api-key"
    return None, "none"


class JwksCache:
    """ES256 verification keys from the control plane, cached for an hour.

    An unknown ``kid`` forces one refresh (rate-limited by a cooldown so a
    stream of bogus tokens cannot turn into a stream of outbound requests).
    """

    def __init__(self, url: str, *, ttl: float, cooldown: float, timeout: float) -> None:
        self.url = url
        self.ttl = ttl
        self.cooldown = cooldown
        self.timeout = timeout
        self._keys: Dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._last_attempt: float = 0.0
        self._lock = anyio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def attach(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_key(self, kid: Optional[str]) -> PyJWK:
        now = time.monotonic()
        fresh = (now - self._fetched_at) < self.ttl
        if fresh and kid and kid in self._keys:
            return self._keys[kid]
        if fresh and not kid and len(self._keys) == 1:
            return next(iter(self._keys.values()))

        async with self._lock:
            # Another task may have refreshed while we waited.
            now = time.monotonic()
            fresh = (now - self._fetched_at) < self.ttl
            if fresh and kid and kid in self._keys:
                return self._keys[kid]
            if not fresh or (kid and kid not in self._keys):
                if (now - self._last_attempt) >= self.cooldown or not self._keys:
                    await self._refresh()

        if kid and kid in self._keys:
            return self._keys[kid]
        if not kid and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise AuthError(
            "invalid_token",
            "signing key not found in the control-plane JWKS",
            token_present=True,
        )

    async def _refresh(self) -> None:
        self._last_attempt = time.monotonic()
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owns = self._client is None
        try:
            response = await client.get(self.url, timeout=self.timeout)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:  # noqa: BLE001 - network / parse errors alike
            logger.warning("jwks refresh failed", extra={"jwks_url": self.url, "error": str(exc)[:200]})
            return
        finally:
            if owns:
                await client.aclose()

        keys: Dict[str, PyJWK] = {}
        for entry in document.get("keys", []) or []:
            try:
                key = PyJWK.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - skip keys we cannot use
                logger.warning("skipping unusable jwks entry", extra={"error": str(exc)[:200]})
                continue
            kid = entry.get("kid") or getattr(key, "key_id", None)
            if kid:
                keys[kid] = key
        if keys:
            self._keys = keys
            self._fetched_at = time.monotonic()
            logger.info("jwks refreshed", extra={"kids": sorted(keys), "jwks_url": self.url})


class ServiceTokenIntrospector:
    """Revocation check for dashboard API keys, cached 60 s, fail-closed."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._lock = anyio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def attach(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def introspect(self, token_id: str) -> Dict[str, Any]:
        now = time.monotonic()
        cached = self._cache.get(token_id)
        if cached and cached[0] > now:
            return cached[1]

        key = self.settings.control_plane_internal_key
        if not key:
            # No introspection key configured => we cannot prove the key is
            # live. Refuse rather than trust a year-long credential.
            logger.error("KUMIHO_CONTROL_PLANE_INTERNAL_KEY is unset; rejecting api-key auth")
            raise AuthError(
                "invalid_token",
                "service token introspection is not configured on this server",
                token_present=True,
            )

        client = self._client or httpx.AsyncClient(timeout=self.settings.http_timeout_seconds)
        owns = self._client is None
        try:
            response = await client.post(
                self.settings.introspection_url,
                json={"token_id": token_id},
                headers={"x-control-plane-key": key},
                timeout=self.settings.http_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "service token introspection failed",
                extra={"token_id": token_id, "error": str(exc)[:200]},
            )
            raise AuthError(
                "invalid_token",
                "could not verify the API key with the control plane",
                token_present=True,
            ) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code >= 400:
            logger.warning(
                "service token introspection rejected",
                extra={"token_id": token_id, "status": response.status_code},
            )
            raise AuthError("invalid_token", "API key is not valid", token_present=True)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError(
                "invalid_token", "malformed introspection response", token_present=True
            ) from exc

        if not isinstance(payload, dict):
            raise AuthError("invalid_token", "malformed introspection response", token_present=True)

        async with self._lock:
            self._cache[token_id] = (
                time.monotonic() + self.settings.introspection_cache_seconds,
                payload,
            )
            if len(self._cache) > 4096:
                cutoff = time.monotonic()
                self._cache = {k: v for k, v in self._cache.items() if v[0] > cutoff}
        return payload


class Authenticator:
    """Turns an inbound request's headers into a :class:`Principal`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jwks = JwksCache(
            settings.jwks_url,
            ttl=settings.jwks_cache_seconds,
            cooldown=settings.jwks_refresh_cooldown_seconds,
            timeout=settings.http_timeout_seconds,
        )
        self.introspector = ServiceTokenIntrospector(settings)

    def attach(self, client: httpx.AsyncClient) -> None:
        self.jwks.attach(client)
        self.introspector.attach(client)

    # -- public ----------------------------------------------------------
    async def authenticate(self, headers) -> Principal:
        if self.settings.dev:
            return self._dev_principal(headers)

        token, source = extract_token(headers)
        if token is None:
            raise AuthError("missing_token", "no credentials presented", token_present=False)
        if not token:
            raise AuthError(
                "invalid_token", "malformed Authorization header", token_present=True
            )

        claims = await self._verify_jwt(token)
        return await self._principal_from_claims(token, claims, source)

    # -- internals -------------------------------------------------------
    def _dev_principal(self, headers) -> Principal:
        token, _ = extract_token(headers)
        return Principal(
            tenant_id=DEV_TENANT_ID,
            user_id=DEV_USER_ID,
            token=token or "",
            kind="dev",
            token_id=DEV_TOKEN_ID,
            tenant_slug=DEV_TENANT_SLUG,
            scopes=["memory"],
            claims={"dev_mode": "ce"},
        )

    async def _verify_jwt(self, token: str) -> Dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", "not a JWT", token_present=True) from exc

        alg = header.get("alg")
        if alg not in _ALLOWED_ALGS:
            raise AuthError(
                "invalid_token", f"unsupported signing algorithm {alg!r}", token_present=True
            )

        key = await self.jwks.get_key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(_ALLOWED_ALGS),
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("invalid_token", "token expired", token_present=True) from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("invalid_token", "wrong audience", token_present=True) from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("invalid_token", "wrong issuer", token_present=True) from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", "signature verification failed", token_present=True) from exc

        resource = claims.get("resource")
        if resource and resource != self.settings.public_url:
            # RFC 8707: a token bound to some other resource must not be
            # accepted here, even if it verifies.
            raise AuthError("invalid_token", "token is bound to another resource", token_present=True)
        return claims

    async def _principal_from_claims(
        self, token: str, claims: Dict[str, Any], source: str
    ) -> Principal:
        token_use = claims.get("token_use")
        token_type = claims.get("type")
        scopes = _split_scope(claims.get("scope"))
        exp = claims.get("exp")
        expires_at = float(exp) if isinstance(exp, (int, float)) else None

        if token_use == "mcp_access":
            tenant_id = _require(claims, "tenant_id")
            user_id = claims.get("sub") or claims.get("user_id")
            if not user_id:
                raise AuthError("invalid_token", "token has no subject", token_present=True)
            if scopes and REQUIRED_SCOPE not in scopes:
                raise AuthError(
                    "insufficient_scope",
                    f"the {REQUIRED_SCOPE!r} scope is required",
                    token_present=True,
                    status=403,
                )
            return Principal(
                tenant_id=tenant_id,
                user_id=str(user_id),
                token=token,
                kind="oauth",
                token_id=claims.get("jti"),
                client_id=claims.get("client_id"),
                tenant_slug=claims.get("tenant_slug"),
                region_code=claims.get("region_code"),
                expires_at=expires_at,
                scopes=scopes or [REQUIRED_SCOPE],
                claims=claims,
            )

        if token_type == "service_token":
            token_id = claims.get("token_id") or claims.get("jti")
            if not token_id:
                raise AuthError("invalid_token", "service token has no token_id", token_present=True)
            result = await self.introspector.introspect(str(token_id))
            if not result.get("active"):
                raise AuthError("invalid_token", "API key has been revoked", token_present=True)
            tenant_id = (
                result.get("tenant_id") or claims.get("tenant_id") or claims.get("sub")
            )
            if not tenant_id:
                raise AuthError("invalid_token", "service token has no tenant", token_present=True)
            return Principal(
                tenant_id=str(tenant_id),
                user_id=f"service:{token_id}",
                token=token,
                kind="service",
                token_id=str(token_id),
                tenant_slug=claims.get("tenant_slug"),
                region_code=claims.get("region_code"),
                expires_at=expires_at,
                scopes=[REQUIRED_SCOPE],
                claims=claims,
            )

        raise AuthError(
            "invalid_token",
            "token is neither an MCP access token nor a service token",
            token_present=True,
        )


def _require(claims: Dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not value:
        raise AuthError("invalid_token", f"token is missing {name}", token_present=True)
    return str(value)


def _split_scope(raw: Any) -> List[str]:
    if isinstance(raw, str):
        return [s for s in raw.replace(",", " ").split() if s]
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw if s]
    return []


def challenge_header(settings: Settings, *, error: Optional[str] = None) -> str:
    """The exact ``WWW-Authenticate`` value Claude looks for (plan §2.4)."""
    parts = [f'Bearer resource_metadata="{settings.prm_url}"', f'scope="{REQUIRED_SCOPE}"']
    if error:
        parts.append(f'error="{error}"')
    return ", ".join(parts)
