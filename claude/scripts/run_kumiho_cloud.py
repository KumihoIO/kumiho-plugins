#!/usr/bin/env python3
"""Start Kumiho MCP with a Codex-isolated official Cloud client.

This adapter is selected only by the shared launcher when the host is Codex.
Claude keeps its existing Cloud bootstrap, including support for an explicitly
configured control plane.  Codex, however, must never inherit that host-local
routing or its discovery cache merely because it was started from a Claude
shell.
"""

from __future__ import annotations

import base64
import json
import os
import re
import runpy
import sys
import time
from pathlib import Path


OFFICIAL_CONTROL_PLANE_URL = "https://control.kumiho.cloud"
UNAUTHENTICATED_ENDPOINT = "needs-auth.kumiho.invalid:443"
CODEX_AUTH_DIRNAME = "codex-cloud"


def _config_root() -> Path:
    """Return the shared Kumiho root without reusing another host's auth file."""
    override = (
        os.getenv("KUMIHO_CODEX_CONFIG_ROOT", "")
        or os.getenv("KUMIHO_CONFIG_DIR", "")
        or ""
    ).strip()
    return Path(override).expanduser() if override else Path.home() / ".kumiho"


def _auth_config_dir() -> Path:
    """Codex-only credentials live below the shared ``~/.kumiho`` root."""
    return _config_root() / CODEX_AUTH_DIRNAME


def _prepare_environment() -> Path:
    """Pin discovery inputs before importing the SDK.

    ``kumiho`` can auto-configure at import time when KUMIHO_AUTO_CONFIGURE is
    inherited.  Clear that switch first, then set the only control plane and
    cache this adapter is allowed to use.
    """
    config_root = _config_root()
    auth_dir = config_root / CODEX_AUTH_DIRNAME
    cache_path = auth_dir / "discovery-cache.json"
    auth_dir.mkdir(parents=True, exist_ok=True)
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
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
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    ):
        os.environ.pop(key, None)
    # The package runtime remains shared at ``<root>/venv``. Only credentials
    # and the route cache are host-isolated, preventing a Claude custom-control-
    # plane bearer from ever being offered to the official Codex endpoint.
    os.environ["KUMIHO_CODEX_CONFIG_ROOT"] = str(config_root)
    os.environ["KUMIHO_CONFIG_DIR"] = str(auth_dir)
    os.environ["KUMIHO_CONTROL_PLANE_URL"] = OFFICIAL_CONTROL_PLANE_URL
    os.environ["KUMIHO_DISCOVERY_CACHE_FILE"] = str(cache_path)
    os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    return cache_path


def _codex_user_agent() -> str:
    value = (
        os.getenv("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", "") or ""
    ).strip()
    if re.fullmatch(r"kumiho-codex/[0-9A-Za-z.+-]{1,80}", value):
        return value
    root = Path(__file__).resolve().parent.parent
    for directory in (".codex-plugin", ".claude-plugin"):
        try:
            version = json.loads(
                (root / directory / "plugin.json").read_text(encoding="utf-8")
            ).get("version")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        candidate = (
            f"kumiho-codex/{version.strip()}"
            if isinstance(version, str)
            else ""
        )
        if re.fullmatch(r"kumiho-codex/[0-9A-Za-z.+-]{1,80}", candidate):
            return candidate
    return "kumiho-codex/unknown"


def _clean_token_candidate(value: object) -> str:
    token = value.strip() if isinstance(value, str) else ""
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if any(char in token for char in "\r\n\0"):
        return ""
    return token


def _add_unique_token(target: list[str], value: object) -> None:
    token = _clean_token_candidate(value)
    if token and token not in target:
        target.append(token)


def _cached_token_candidates() -> list[str]:
    """Load every unexpired Codex-only token, including legacy api_token."""
    candidates: list[str] = []
    try:
        body = json.loads(
            (_auth_config_dir() / "kumiho_authentication.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return candidates
    if not isinstance(body, dict):
        return candidates
    now = int(time.time())
    for token_key, expiry_key in (
        ("control_plane_token", "cp_expires_at"),
        ("id_token", "expires_at"),
        ("api_token", "api_token_expires_at"),
    ):
        expiry = body.get(expiry_key)
        if isinstance(expiry, (int, float)) and int(expiry) <= now + 30:
            continue
        _add_unique_token(candidates, body.get(token_key))
    return candidates


def _refreshable_cache_candidates() -> list[str]:
    """Ask the public auth helper to refresh, then include legacy cache forms."""
    candidates: list[str] = []
    auth_path = _auth_config_dir() / "kumiho_authentication.json"
    if auth_path.is_file():
        try:
            from kumiho.auth_cli import ensure_token

            result = ensure_token(interactive=False)
            token = result[0] if isinstance(result, tuple) else result
            _add_unique_token(candidates, token)
        except Exception:
            pass
    for token in _cached_token_candidates():
        _add_unique_token(candidates, token)
    return candidates


def _token_is_locally_current(token: str) -> bool:
    """Reject malformed/expired JWTs before using an offline route cache."""
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        return False
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(claims, dict):
        return False
    expiry = claims.get("exp")
    return not isinstance(expiry, (int, float)) or int(expiry) > int(time.time()) + 30


def _client_from_official_discovery(
    kumiho,
    token: str,
    cache_path: Path,
    *,
    force_refresh: bool = True,
):
    """Use SDK cache/record parsing while overriding its generic HTTP UA."""
    def resolve():
        return kumiho.client_from_discovery(
            id_token=token,
            control_plane_url=OFFICIAL_CONTROL_PLANE_URL,
            cache_path=str(cache_path),
            force_refresh=force_refresh,
        )

    try:
        from kumiho import discovery as discovery_module
        original_post = discovery_module.requests.post
    except Exception:
        # The UA hook is best-effort across future SDK transports. Routing
        # safety does not depend on it: the public call still pins both the
        # official control plane and Codex-only cache.
        return resolve()

    def post_with_codex_identity(url, *args, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers["User-Agent"] = _codex_user_agent()
        kwargs["headers"] = headers
        return original_post(url, *args, **kwargs)

    try:
        discovery_module.requests.post = post_with_codex_identity
    except Exception:
        # Telemetry must never make an otherwise-compatible public SDK fail.
        return resolve()
    try:
        return resolve()
    finally:
        try:
            discovery_module.requests.post = original_post
        except Exception:
            # The discovery result is already valid; a private telemetry hook
            # restoration failure must not turn it into an auth failure.
            pass


def _configure_client(cache_path: Path, *, allow_cached_route: bool = True):
    import kumiho

    authenticated = True
    try:
        cache_candidates = _refreshable_cache_candidates()
        client = None
        token = ""
        last_error: Exception | None = None

        # Codex accepts only its own credential directory. The shared Desktop
        # runtime is still reused, but Claude/custom-control-plane credentials
        # and ambient shell tokens never cross this official-service boundary.
        for candidate in cache_candidates:
            try:
                client = _client_from_official_discovery(
                    kumiho, candidate, cache_path, force_refresh=True
                )
                token = candidate
                break
            except Exception as exc:
                last_error = exc

        # Runtime startup may use an unexpired Codex-only route cache while
        # offline. Onboarding passes allow_cached_route=False so it never calls
        # an unverified token "authenticated".
        if client is None and allow_cached_route and cache_path.is_file():
            for candidate in cache_candidates:
                if not _token_is_locally_current(candidate):
                    continue
                try:
                    client = _client_from_official_discovery(
                        kumiho, candidate, cache_path, force_refresh=False
                    )
                    token = candidate
                    break
                except Exception as exc:
                    last_error = exc

        if client is None or not token:
            if last_error is not None:
                raise last_error
            raise RuntimeError("no Cloud credential is available")
        os.environ["KUMIHO_AUTH_TOKEN"] = token
    except Exception as exc:
        # Keep the MCP surface available so Codex can still expose onboarding
        # and give a useful auth error.  The reserved .invalid endpoint can
        # never resolve and the empty token cannot fall back to Cloud caches.
        authenticated = False
        os.environ["KUMIHO_AUTH_TOKEN"] = ""
        client = kumiho.connect(
            endpoint=UNAUTHENTICATED_ENDPOINT,
            token="",
            enable_auto_login=False,
            use_discovery=False,
        )
        print(
            "[kumiho-codex] Official Cloud discovery is not ready "
            f"({type(exc).__name__}); run $kumiho-onboard.",
            file=sys.stderr,
        )

    kumiho.configure_default_client(client)

    def keep_explicit_cloud_client(*_args, **_kwargs):
        kumiho.configure_default_client(client)
        return client

    # kumiho.mcp_server checks configuration for each tool call.  Preserve the
    # check while preventing it from consulting Claude's/default cache later.
    kumiho.auto_configure_from_discovery = keep_explicit_cloud_client
    return client, authenticated


def _run_target() -> None:
    if os.getenv("KUMIHO_CLAUDE_HOST") == "codex":
        from codex_thread_context import install_codex_thread_context

        install_codex_thread_context()
    args = sys.argv[1:]
    if args[:1] == ["--module"] and len(args) >= 2:
        module = args[1]
        sys.argv = [module, *args[2:]]
        runpy.run_module(module, run_name="__main__")
        return
    if args[:1] == ["--script"] and len(args) >= 2:
        script = args[1]
        sys.argv = [script, *args[2:]]
        runpy.run_path(script, run_name="__main__")
        return
    if args[:1] == ["--code"] and len(args) == 2:
        sys.argv = ["-c"]
        exec(
            compile(args[1], "<kumiho-cloud-code>", "exec"),
            {"__name__": "__main__"},
        )
        return

    from kumiho.mcp_server import main as run_server

    run_server()


def main() -> None:
    cache_path = _prepare_environment()
    auth_check = sys.argv[1:] == ["--auth-check"]
    _client, authenticated = _configure_client(
        cache_path,
        allow_cached_route=not auth_check,
    )
    if auth_check:
        if authenticated:
            print("[kumiho-codex] Cached Cloud authentication verified.")
            return
        raise SystemExit(1)
    _run_target()


if __name__ == "__main__":
    main()
