#!/usr/bin/env python3
"""Kumiho memory MCP launcher for OpenAI Codex CLI.

Thin shim over the Claude launcher: the environment pipeline (venv
provisioning, dotenv/cached-auth hydration, control-plane discovery, CE
mode, LLM fallback) is identical for every MCP host — only the discovery
user-agent differs.  The shim keeps one source of truth in
``claude/scripts/run_kumiho_mcp.py``; this repo ships as a monorepo, so
the relative path is stable.
"""

import json
import ipaddress
import os
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit

CODEX_USER_AGENT_PRODUCT = "kumiho-codex"
CODEX_CONFIG_SCHEMA = 1
CODEX_BACKEND_ENV = "KUMIHO_CODEX_BACKEND"
CODEX_ENDPOINT_ENV = "KUMIHO_CODEX_CE_ENDPOINT"
CODEX_REDIS_ENV = "KUMIHO_CODEX_CE_REDIS_URL"
CODEX_LLM_ENV = "KUMIHO_CODEX_CE_LLM_BASE_URL"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"
CE_ENDPOINT_SCHEMES = {"grpc", "grpcs", "http", "https"}
CODEX_DEDICATED_ENV = (
    CODEX_BACKEND_ENV,
    CODEX_ENDPOINT_ENV,
    CODEX_REDIS_ENV,
    CODEX_LLM_ENV,
)
CE_ROUTING_ENV = (
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "UPSTASH_REDIS_URL",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "KUMIHO_MCP_HOSTED",
    "KUMIHO_HOSTED_LOCAL_REDIS",
    "KUMIHO_LOCAL_REDIS_URL",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_AUTO_CONFIGURE",
    "KUMIHO_DISCOVERY_CACHE_FILE",
    "KUMIHO_SERVER_USE_TLS",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_REQUIRE_TLS",
)
CLOUD_ROUTING_ENV = (
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_FIREBASE_API_KEY",
    "KUMIHO_FIREBASE_ID_TOKEN",
    "KUMIHO_FIREBASE_PROJECT_ID",
    "KUMIHO_USE_CONTROL_PLANE_TOKEN",
    "KUMIHO_WORKSPACE_ROOT",
    "KUMIHO_ENV_FILE",
)

#: Resolution order: the monorepo-relative claude launcher (dev checkouts —
#: always the freshest), then the vendored copy shipped inside this plugin
#: (marketplace snapshots copy only the plugin directory, so ../claude does
#: not exist there). test_launcher_parity.py guards the vendored copy
#: against drifting from the canonical claude/scripts version.
_LAUNCHER_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent
    / "claude" / "scripts" / "run_kumiho_mcp.py",
    Path(__file__).resolve().parent / "_vendored_launcher.py",
)


def _codex_config_path() -> Path:
    """Return the host-specific, secret-free backend configuration path."""
    root = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    config_dir = Path(root).expanduser() if root else Path.home() / ".kumiho"
    return config_dir / "codex.json"


def _codex_user_agent() -> str:
    manifest = Path(__file__).resolve().parent.parent / ".codex-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        version = None
    clean = version.strip() if isinstance(version, str) and version.strip() else "unknown"
    return f"{CODEX_USER_AGENT_PRODUCT}/{clean}"


def _reset_to_safe_cloud() -> None:
    """Fail closed to official Cloud routing and Codex-owned auth only."""
    for key in (*CODEX_DEDICATED_ENV, *CE_ROUTING_ENV, *CLOUD_ROUTING_ENV):
        os.environ.pop(key, None)
    # The Cloud adapter reads only ``~/.kumiho/codex-cloud``. Never let an
    # ambient or Claude custom-control-plane bearer cross into this process.
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    os.environ[CODEX_BACKEND_ENV] = "cloud"


def _fail_closed_backend(message: str) -> None:
    """Stop before either backend can inherit credentials or routing.

    A missing config is the one case that retains the historical Cloud
    default. Once the user has explicitly selected CE, however, malformed or
    unreadable state must never turn that data-boundary choice into Cloud.
    """
    for key in (*CODEX_DEDICATED_ENV, *CE_ROUTING_ENV, *CLOUD_ROUTING_ENV):
        os.environ.pop(key, None)
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    print(f"[kumiho-codex] {message}; refusing to start.", file=sys.stderr)
    raise SystemExit(2)


def _normalize_endpoint(raw: object) -> str:
    if not isinstance(raw, str):
        raise ValueError("CE endpoint must be a string")
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("CE endpoint must be a non-empty host:port")
    has_scheme = "://" in value
    parsed = urlsplit(value if has_scheme else f"//{value}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme and scheme not in CE_ENDPOINT_SCHEMES:
        raise ValueError("CE endpoint has an unsupported scheme")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CE endpoint contains unsupported URL components")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CE endpoint has an invalid port") from exc
    if not parsed.hostname:
        raise ValueError("CE endpoint must include a host")
    if port is None:
        if not scheme:
            raise ValueError("CE endpoint must include a port")
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    plaintext = scheme in {"", "http", "grpc"}
    loopback_host = host.rstrip(".").lower()
    loopback = loopback_host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(loopback_host).is_loopback
        except ValueError:
            loopback = False
    if plaintext and not loopback:
        raise ValueError("remote CE endpoints must use https:// or grpcs://")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    return f"{scheme}://{authority}" if scheme else authority


def _validate_url(
    raw: object,
    *,
    schemes: set[str],
    label: str,
    require_tls_for_remote: bool = False,
) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a string")
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError(f"{label} is empty or invalid")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in schemes or not parsed.netloc:
        raise ValueError(f"{label} has an unsupported URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain credentials, query, or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port") from exc
    insecure_scheme = (
        parsed.scheme.lower()
        if require_tls_for_remote and parsed.scheme.lower() in {"http", "redis"}
        else ""
    )
    if insecure_scheme:
        host = (parsed.hostname or "").rstrip(".").lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            secure = "HTTPS" if insecure_scheme == "http" else "rediss://"
            raise ValueError(f"{label} must use {secure} outside loopback")
    return value


def _apply_ce_settings(endpoint: object, redis_url: object, llm_base_url: object) -> None:
    normalized_endpoint = _normalize_endpoint(endpoint)
    normalized_redis = _validate_url(
        redis_url or DEFAULT_CE_REDIS_URL,
        schemes={"redis", "rediss"},
        label="CE Redis URL",
        require_tls_for_remote=True,
    )
    normalized_llm = None
    if llm_base_url:
        normalized_llm = _validate_url(
            llm_base_url,
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        )

    for key in (*CODEX_DEDICATED_ENV, *CE_ROUTING_ENV, *CLOUD_ROUTING_ENV):
        os.environ.pop(key, None)
    os.environ[CODEX_BACKEND_ENV] = "ce"
    os.environ[CODEX_ENDPOINT_ENV] = normalized_endpoint
    os.environ[CODEX_REDIS_ENV] = normalized_redis
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = normalized_endpoint
    os.environ["UPSTASH_REDIS_URL"] = normalized_redis
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    if normalized_llm:
        os.environ[CODEX_LLM_ENV] = normalized_llm
        os.environ["KUMIHO_LLM_BASE_URL"] = normalized_llm


def _apply_codex_config(path: Path | None = None) -> None:
    """Apply Codex-only backend settings before the shared launcher starts.

    Claude and Codex intentionally share the package runtime, but neither the
    credential cache nor backend selection. In particular, choosing CE for Codex must not rewrite Claude
    Desktop settings, and a Claude CE environment must not force Codex away
    from a Cloud backend selected by its own onboarding wizard.
    """
    config_path = path or _codex_config_path()
    try:
        body = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Legacy/manual registration may intentionally provide dedicated
        # Codex variables. Otherwise default safely to the official Cloud
        # path rather than inheriting Claude's CE selection.
        inherited = (os.getenv(CODEX_BACKEND_ENV, "") or "").strip().lower()
        endpoint = (os.getenv(CODEX_ENDPOINT_ENV, "") or "").strip()
        if inherited == "cloud":
            _reset_to_safe_cloud()
        elif inherited == "ce":
            try:
                _apply_ce_settings(
                    endpoint,
                    os.getenv(CODEX_REDIS_ENV, ""),
                    os.getenv(CODEX_LLM_ENV, ""),
                )
            except ValueError:
                _fail_closed_backend("Invalid explicit Codex CE configuration")
        elif inherited or endpoint or any(
            (os.getenv(key, "") or "").strip()
            for key in (CODEX_REDIS_ENV, CODEX_LLM_ENV)
        ):
            _fail_closed_backend(
                "Incomplete or unknown explicit Codex backend configuration"
            )
        else:
            _reset_to_safe_cloud()
        return
    except (OSError, json.JSONDecodeError):
        _fail_closed_backend("Codex backend configuration is unreadable")

    for key in (*CODEX_DEDICATED_ENV, *CE_ROUTING_ENV, *CLOUD_ROUTING_ENV):
        os.environ.pop(key, None)

    if not isinstance(body, dict) or body.get("schema_version") != CODEX_CONFIG_SCHEMA:
        _fail_closed_backend("Codex backend configuration has an unsupported schema")

    backend = body.get("backend")
    if backend == "cloud":
        _reset_to_safe_cloud()
        return

    if backend == "ce":
        try:
            _apply_ce_settings(
                body.get("endpoint"),
                body.get("redis_url") or DEFAULT_CE_REDIS_URL,
                body.get("llm_base_url"),
            )
        except ValueError:
            _fail_closed_backend("Codex CE backend configuration is invalid")
        return

    _fail_closed_backend("Codex backend configuration names an unknown backend")


def main() -> None:
    launcher = next((p for p in _LAUNCHER_CANDIDATES if p.exists()), None)
    if launcher is None:
        print(
            "[kumiho-codex] No launcher found (looked for the monorepo "
            f"claude launcher and the vendored copy): {_LAUNCHER_CANDIDATES}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    # This variable belongs to the Claude host and may be inherited when
    # Codex is started from a Claude-managed shell.  Installed Codex plugins
    # derive their own stable data directory from the cache path.
    for key in (
        "CLAUDE_PLUGIN_DATA",
        *CLOUD_ROUTING_ENV,
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_DISCOVERY_CACHE_FILE",
    ):
        os.environ.pop(key, None)
    # Provisioning and import self-tests are backend-independent maintenance
    # operations. Skipping config parsing here is what lets an explicit
    # onboarding run repair a malformed/obsolete codex.json instead of dying
    # before the wizard reaches its repair branch. Normal MCP startup remains
    # fail-closed.
    maintenance_only = any(
        arg in {"--provision", "--self-test"} for arg in sys.argv[1:]
    )
    if not maintenance_only:
        _apply_codex_config()
    os.environ["KUMIHO_CLAUDE_DISCOVERY_USER_AGENT"] = _codex_user_agent()
    # Identify the host: the shared launcher gates Claude-Desktop config
    # writes (bootstrap + token sync) on this, so a codex-spawned run can
    # never create or rewrite another host's config files.
    os.environ["KUMIHO_CLAUDE_HOST"] = "codex"
    sys.argv[0] = str(launcher)
    runpy.run_path(str(launcher), run_name="__main__")


if __name__ == "__main__":
    main()
