#!/usr/bin/env python3
"""Run Kumiho MCP in Cloud mode through the Python SDK.

The plugin owns only the host boundary: Cloud always uses Kumiho's official
control plane and cannot fall back to a local CE server. Token loading,
refresh, login-cache handling, discovery, and regional routing belong to the
``kumiho`` Python SDK.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


OFFICIAL_CONTROL_PLANE_URL = "https://control.kumiho.cloud"
OFFICIAL_DISCOVERY_CACHE_DIRNAME = "official-cloud"
SHARED_HOME_HANDOFF_ENV = "KUMIHO_PLUGIN_SHARED_HOME"
BACKEND_BOUND_SENTINEL = "_KUMIHO_ADAPTER_BOUND"
_CLOUD_ROUTE_ENV = (
    "KUMIHO_AUTO_CONFIGURE",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_DISCOVERY_CACHE_FILE",
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
    "KUMIHO_LOCAL_SERVER_PORT",
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
)


class _CloudUnavailableClient:
    """Non-None SDK default that prevents any implicit local bootstrap."""

    def __getattr__(self, _name):
        raise RuntimeError("Kumiho Cloud is not configured")


def _discover_cloud_client(kumiho, *, force_refresh: bool = False):
    """Run SDK-owned auth and official Cloud discovery without CE fallback."""
    if not os.getenv("KUMIHO_AUTH_TOKEN", "").strip():
        # Refresh SDK-managed login credentials before consulting a still-valid
        # route cache. The plugin deliberately ignores the returned credential;
        # discovery reloads it through the SDK's normal precedence.
        from kumiho.auth_cli import ensure_token

        ensure_token(interactive=False)
    return kumiho.client_from_discovery(
        id_token=None,
        control_plane_url=OFFICIAL_CONTROL_PLANE_URL,
        cache_path=os.environ["KUMIHO_DISCOVERY_CACHE_FILE"],
        force_refresh=force_refresh,
    )


def _prepare_environment() -> Path:
    """Pin official Cloud routing while leaving authentication to the SDK."""
    trusted_handoff = os.environ.pop(SHARED_HOME_HANDOFF_ENV, "").strip()
    handed_off = Path(trusted_handoff) if trusted_handoff else None
    shared_root = (
        handed_off
        if handed_off is not None and handed_off.is_absolute()
        else Path.home() / ".kumiho"
    )
    for key in _CLOUD_ROUTE_ENV:
        os.environ.pop(key, None)

    # Claude, Codex, Kumiho Desktop, kumiho-auth, and kumiho-cli all share the
    # SDK-owned credential/cache root. An explicit KUMIHO_AUTH_TOKEN is left
    # untouched so the SDK can apply its documented token-first precedence.
    os.environ["KUMIHO_CONFIG_DIR"] = str(shared_root)
    os.environ.pop("KUMIHO_CODEX_CONFIG_ROOT", None)
    os.environ["KUMIHO_CONTROL_PLANE_URL"] = OFFICIAL_CONTROL_PLANE_URL
    os.environ["KUMIHO_DISCOVERY_CACHE_FILE"] = str(
        shared_root / OFFICIAL_DISCOVERY_CACHE_DIRNAME / "discovery-cache.json"
    )
    os.environ["KUMIHO_REQUIRE_TLS"] = "1"

    return shared_root


def _configure_cloud(*, force_refresh: bool = False) -> bool:
    """Ask public SDK APIs to own auth, discovery, and regional routing."""
    import kumiho

    try:
        client = _discover_cloud_client(kumiho, force_refresh=force_refresh)
        kumiho.configure_default_client(client)
    except Exception as exc:
        # kumiho.mcp_server asks the SDK to auto-configure again on each tool
        # call. In an explicitly selected Cloud process that retry must not
        # silently probe local CE when Cloud auth is unavailable.
        def refuse_ce_fallback(*_args, **_kwargs):
            raise RuntimeError("Kumiho Cloud is not configured")

        kumiho.auto_configure_from_discovery = refuse_ce_fallback
        # Several SDK MCP tools ignore _ensure_configured()'s False result and
        # call get_client() anyway. A non-None fail-closed default prevents
        # get_client() from invoking bootstrap_default_client(), which probes CE.
        kumiho.configure_default_client(_CloudUnavailableClient())
        print(
            "[kumiho-memory] Kumiho Cloud is not ready "
            f"({type(exc).__name__}); set KUMIHO_AUTH_TOKEN or run "
            "kumiho-auth login / kumiho-cli login, then restart the host.",
            file=sys.stderr,
        )
        return False

    # Keep every MCP tool call on official SDK discovery so cache refresh and
    # regional reassignment continue to work in long-lived hosts. Calling the
    # public direct-discovery API also prevents SDK auto-configure from probing
    # local CE when Cloud was explicitly selected.
    def refresh_cloud_client(*_args, **_kwargs):
        nonlocal client
        try:
            client = _discover_cloud_client(
                kumiho,
                force_refresh=bool(_kwargs.get("force_refresh", False)),
            )
        except Exception:
            # An expired/unusable Cloud route must fail closed. A later tool
            # call retries official discovery and can recover automatically.
            kumiho.configure_default_client(_CloudUnavailableClient())
            raise RuntimeError("Kumiho Cloud discovery refresh failed") from None
        kumiho.configure_default_client(client)
        return client

    kumiho.auto_configure_from_discovery = refresh_cloud_client
    return True


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
        runpy.run_path(
            script,
            run_name="__main__",
            init_globals={BACKEND_BOUND_SENTINEL: True},
        )
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
    _prepare_environment()
    auth_check = sys.argv[1:] == ["--auth-check"]
    # Resolve once at process startup so an account/API-token switch cannot
    # inherit another account's still-valid regional route. Later MCP calls
    # use the SDK's normal cache policy through refresh_cloud_client().
    authenticated = _configure_cloud(force_refresh=True)
    if auth_check:
        if authenticated:
            print("[kumiho-memory] Kumiho Cloud authentication verified by the SDK.")
            return
        raise SystemExit(1)
    if not authenticated and len(sys.argv) > 1 and sys.argv[1] in {
        "--module", "--script", "--code",
    }:
        # Auxiliary jobs call SDK clients directly and never enter mcp_server's
        # guarded auto-configure hook. Never execute their payload after Cloud
        # bootstrap failed, or the SDK default client could probe local CE.
        raise SystemExit(1)
    _run_target()


if __name__ == "__main__":
    main()
