"""Runtime settings, read once from the environment.

Everything the service needs is an env var so the same image can run under App
Runner, under ``uvicorn`` on a laptop, and inside pytest. Nothing is read from
``~/.kumiho`` — hosted mode is explicitly filesystem-free (plan §2.1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PUBLIC_URL = "https://mcp.kumiho.cloud/mcp"
DEFAULT_ISSUER = "https://control.kumiho.cloud"
DEFAULT_CONTROL_PLANE_URL = "https://control.kumiho.cloud"
DEFAULT_AUDIENCE = "kumiho-server"
DEFAULT_LOCAL_CE_ENDPOINT = "127.0.0.1:9190"
DEFAULT_LOCAL_REDIS_URL = "redis://127.0.0.1:6379"
DEFAULT_RESOURCE_DOCUMENTATION = "https://kumiho.io/docs/connect/claude"

# Fixed identity used by ``KUMIHO_MCP_DEV_MODE=ce``. Deliberately obvious.
DEV_TENANT_ID = "00000000-0000-4000-8000-00000000dev0"
DEV_TENANT_SLUG = "dev-local"
DEV_USER_ID = "dev-local-user"
DEV_TOKEN_ID = "dev-local-token"

SCOPES_SUPPORTED: Tuple[str, ...] = ("memory", "offline_access")
REQUIRED_SCOPE = "memory"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the process configuration."""

    public_url: str
    issuer: str
    jwks_url: str
    control_plane_url: str
    control_plane_internal_key: Optional[str]
    audience: str
    hosted: bool
    dev_mode: Optional[str]
    local_server_endpoint: str
    local_redis_url: str
    port: int
    max_body_bytes: int
    request_timeout_seconds: float
    jwks_cache_seconds: float
    jwks_refresh_cooldown_seconds: float
    introspection_cache_seconds: float
    discovery_cache_seconds: float
    client_cache_max: int
    http_timeout_seconds: float
    resource_documentation: str
    log_level: str
    enable_sse: bool
    json_response: bool
    scopes_supported: Tuple[str, ...] = field(default=SCOPES_SUPPORTED)

    # ---- derived -------------------------------------------------------
    @property
    def dev(self) -> bool:
        """True when auth is disabled and a fixed fake tenant is used."""
        return self.dev_mode == "ce"

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    @property
    def mcp_path(self) -> str:
        path = urlsplit(self.public_url).path or "/mcp"
        return "/" + path.strip("/")

    @property
    def prm_url(self) -> str:
        """RFC 9728 metadata URL advertised in the 401 challenge."""
        return f"{self.public_origin}/.well-known/oauth-protected-resource"

    @property
    def introspection_url(self) -> str:
        return f"{self.control_plane_url.rstrip('/')}/api/control-plane/service-token/introspect"

    @property
    def discovery_url(self) -> str:
        return f"{self.control_plane_url.rstrip('/')}/api/discovery/tenant"

    def protected_resource_metadata(self) -> dict:
        """RFC 9728 document. ``resource`` must equal the MCP URL exactly."""
        return {
            "resource": self.public_url,
            "authorization_servers": [self.issuer],
            "scopes_supported": list(self.scopes_supported),
            "bearer_methods_supported": ["header"],
            "resource_documentation": self.resource_documentation,
        }


def load_settings(environ: Optional[dict] = None) -> Settings:
    """Build :class:`Settings` from ``os.environ`` (or an override mapping)."""

    if environ is not None:
        # Tests hand us a dict; splice it in for the duration of the call.
        previous = dict(os.environ)
        os.environ.clear()
        os.environ.update({k: str(v) for k, v in environ.items()})
        try:
            return load_settings(None)
        finally:
            os.environ.clear()
            os.environ.update(previous)

    dev_mode = _env("KUMIHO_MCP_DEV_MODE")
    dev_mode = dev_mode.lower() if dev_mode else None
    port = _env_int("PORT", 8080)

    default_public = f"http://127.0.0.1:{port}/mcp" if dev_mode == "ce" else DEFAULT_PUBLIC_URL
    public_url = _env("KUMIHO_MCP_PUBLIC_URL", default_public) or default_public
    issuer = (_env("KUMIHO_AS_ISSUER", DEFAULT_ISSUER) or DEFAULT_ISSUER).rstrip("/")
    jwks_url = _env("KUMIHO_JWKS_URL") or f"{issuer}/.well-known/kumiho-jwks.json"
    control_plane_url = (
        _env("KUMIHO_CONTROL_PLANE_URL", DEFAULT_CONTROL_PLANE_URL) or DEFAULT_CONTROL_PLANE_URL
    ).rstrip("/")

    return Settings(
        public_url=public_url,
        issuer=issuer,
        jwks_url=jwks_url,
        control_plane_url=control_plane_url,
        control_plane_internal_key=_env("KUMIHO_CONTROL_PLANE_INTERNAL_KEY"),
        audience=_env("KUMIHO_MCP_AUDIENCE", DEFAULT_AUDIENCE) or DEFAULT_AUDIENCE,
        # Always on, dev mode included: several SDK guards key off the *process*
        # flag between requests, not only off an active request context, so a
        # dev run that left it unset would exercise a different code path than
        # production.
        hosted=_env_bool("KUMIHO_MCP_HOSTED", default=True),
        dev_mode=dev_mode,
        local_server_endpoint=_env("KUMIHO_LOCAL_SERVER_ENDPOINT", DEFAULT_LOCAL_CE_ENDPOINT)
        or DEFAULT_LOCAL_CE_ENDPOINT,
        local_redis_url=_env("KUMIHO_MCP_DEV_REDIS_URL", DEFAULT_LOCAL_REDIS_URL)
        or DEFAULT_LOCAL_REDIS_URL,
        port=port,
        max_body_bytes=_env_int("KUMIHO_MCP_MAX_BODY_BYTES", 2 * 1024 * 1024),
        request_timeout_seconds=_env_float("KUMIHO_MCP_REQUEST_TIMEOUT_SECONDS", 60.0),
        jwks_cache_seconds=_env_float("KUMIHO_MCP_JWKS_CACHE_SECONDS", 3600.0),
        jwks_refresh_cooldown_seconds=_env_float("KUMIHO_MCP_JWKS_COOLDOWN_SECONDS", 30.0),
        introspection_cache_seconds=_env_float("KUMIHO_MCP_INTROSPECTION_CACHE_SECONDS", 60.0),
        discovery_cache_seconds=_env_float("KUMIHO_MCP_DISCOVERY_CACHE_SECONDS", 600.0),
        client_cache_max=_env_int("KUMIHO_MCP_CLIENT_CACHE_MAX", 1024),
        http_timeout_seconds=_env_float("KUMIHO_MCP_HTTP_TIMEOUT_SECONDS", 10.0),
        resource_documentation=_env("KUMIHO_MCP_DOCS_URL", DEFAULT_RESOURCE_DOCUMENTATION)
        or DEFAULT_RESOURCE_DOCUMENTATION,
        log_level=(_env("KUMIHO_MCP_LOG_LEVEL", "INFO") or "INFO").upper(),
        enable_sse=_env_bool("KUMIHO_MCP_ENABLE_SSE", default=True),
        json_response=_env_bool("KUMIHO_MCP_JSON_RESPONSE", default=False),
    )
