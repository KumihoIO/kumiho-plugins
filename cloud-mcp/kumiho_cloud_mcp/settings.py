"""Runtime settings, read once from the environment.

Everything the service needs is an env var so the same image can run under App
Runner, under ``uvicorn`` on a laptop, and inside pytest. Nothing is read from
``~/.kumiho`` — hosted mode is explicitly filesystem-free (plan §2.1).
"""

from __future__ import annotations

import hashlib
import os
import re
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

#: Dev-only header that picks a *different* fake tenant for one request.
#:
#: Honoured **only** when ``KUMIHO_MCP_DEV_MODE=ce``, i.e. only in the mode
#: that has already disabled authentication entirely; outside dev mode the
#: header is not read at all and a caller's tenant comes from their verified
#: token and nowhere else. It exists so that the isolation tests can drive two
#: tenants through one process over the real transport, which a single fixed
#: fake identity cannot do. Document it as dev-only in README.md.
DEV_TENANT_HEADER = "x-kumiho-dev-tenant"

SCOPES_SUPPORTED: Tuple[str, ...] = ("memory", "offline_access")
REQUIRED_SCOPE = "memory"

#: The SDK's revision-stacking gate switch (kumiho-SDKs #168), read by
#: ``kumiho.mcp_server._middle_band_enabled`` out of ``os.environ`` on every
#: store — not by this package.
STACK_MIDDLE_BAND_ENV = "KUMIHO_STACK_MIDDLE_BAND"

#: Hosted deployments run **strong-only**: a capture stacks onto an existing
#: item only at score >= 0.75 *and* above the lexical-overlap floor; the 0.55
#: type-match band is withheld.
#:
#: The SDK's own default is the two-band gate, which is right for a single
#: operator who can watch their own corpus. It is not right here. The bands
#: were calibrated on one corpus, and the contested 0.55-0.75 middle is where
#: an unrelated same-type neighbour in a topically homogeneous space scores —
#: a false stack there moves the ``published`` tag onto somebody else's
#: memory, i.e. it *hides* a memory rather than merely duplicating one. A
#: shared server has no way to watch per-tenant score distributions yet, so it
#: takes the conservative half until the ``stack_*`` telemetry on every store
#: result says otherwise. ``stack_mode`` on that result names which gate fired.
HOSTED_STACK_MIDDLE_BAND_DEFAULT = False


def middle_band_enabled(default: bool = HOSTED_STACK_MIDDLE_BAND_DEFAULT) -> bool:
    """Whether the SDK's middle stacking band is on, per the live environment.

    Deliberately the SDK's own predicate ("anything but ``0`` is on"), not
    :func:`_env_bool`: an operator who wrote ``KUMIHO_STACK_MIDDLE_BAND=true``
    must get the same answer here as the SDK gives itself, or ``/healthz``
    would report a mode the store path is not in. Only the *unset* case
    differs, and that difference is the whole point — unset means strong-only
    for a hosted process, two-band for a laptop.
    """
    raw = os.environ.get(STACK_MIDDLE_BAND_ENV)
    if raw is None:
        return default
    return raw.strip() != "0"


def dev_identity(label: Optional[str]) -> Tuple[str, str, str, str]:
    """``(tenant_id, tenant_slug, user_id, token_id)`` for a dev-mode label.

    ``None`` or an empty label gives the fixed default identity, so the
    ordinary dev run is unchanged. Any other label is hashed into a
    UUID-shaped tenant id — the graph backend and the Redis key namespace both
    expect a UUID — deterministically, so the same label names the same tenant
    across restarts and across the two client sessions of an isolation test.
    """
    if not label or not label.strip():
        return DEV_TENANT_ID, DEV_TENANT_SLUG, DEV_USER_ID, DEV_TOKEN_ID
    slug = re.sub(r"[^a-z0-9-]+", "-", label.strip().lower()).strip("-")[:40] or "dev"
    digest = hashlib.sha256(f"kumiho-dev-tenant:{slug}".encode()).hexdigest()
    tenant_id = f"{digest[:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
    return tenant_id, f"dev-{slug}", f"dev-{slug}-user", f"dev-{slug}-token"


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
    allow_shim: bool
    stack_middle_band: bool
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
        # 120 s, not the 60 s of plan §2.4. `kumiho_memory_consolidate` is one
        # tool call that fans out into a whole session's worth of work —
        # summarize the buffer, write the memories, discover edges — and each
        # of those is a round trip to the graph backend. Measured against CE on
        # a laptop a consolidate of a short session already spends tens of
        # seconds; a real one on a cold region would trip a 60 s cap and the
        # client would see a 504 *after* the writes had partly landed, which is
        # the worst of both outcomes. The cap exists to stop a hung upstream
        # from pinning a worker, and 120 s still does that.
        request_timeout_seconds=_env_float("KUMIHO_MCP_REQUEST_TIMEOUT_SECONDS", 120.0),
        jwks_cache_seconds=_env_float("KUMIHO_MCP_JWKS_CACHE_SECONDS", 3600.0),
        jwks_refresh_cooldown_seconds=_env_float("KUMIHO_MCP_JWKS_COOLDOWN_SECONDS", 30.0),
        introspection_cache_seconds=_env_float("KUMIHO_MCP_INTROSPECTION_CACHE_SECONDS", 60.0),
        discovery_cache_seconds=_env_float("KUMIHO_MCP_DISCOVERY_CACHE_SECONDS", 600.0),
        client_cache_max=_env_int("KUMIHO_MCP_CLIENT_CACHE_MAX", 1024),
        http_timeout_seconds=_env_float("KUMIHO_MCP_HTTP_TIMEOUT_SECONDS", 10.0),
        resource_documentation=_env("KUMIHO_MCP_DOCS_URL", DEFAULT_RESOURCE_DOCUMENTATION)
        or DEFAULT_RESOURCE_DOCUMENTATION,
        log_level=(_env("KUMIHO_MCP_LOG_LEVEL", "INFO") or "INFO").upper(),
        # OFF by default. The deprecated HTTP+SSE transport doubles the
        # authenticated surface (`/sse` plus an unauthenticated-by-design
        # `/messages/` POST whose only tenant binding is an in-memory session
        # map) for clients Claude no longer uses. Turn it on per deployment,
        # knowingly, if some legacy client ever needs it.
        enable_sse=_env_bool("KUMIHO_MCP_ENABLE_SSE", default=False),
        json_response=_env_bool("KUMIHO_MCP_JSON_RESPONSE", default=False),
        # Dev-only. Lets the service start on a `kumiho`/`kumiho-memory` older
        # than the connector contract, falling back to _compat's local tool
        # filtering. Never set in a deployment: the shim path cannot enforce
        # the profile the directory listing was reviewed against.
        allow_shim=_env_bool("KUMIHO_MCP_ALLOW_SHIM", default=False),
        # Strong-only unless the operator says otherwise. Read here so the
        # decision is part of the settings snapshot, and published back into
        # os.environ by ``create_app`` — the SDK reads the variable, not us.
        stack_middle_band=middle_band_enabled(),
    )
