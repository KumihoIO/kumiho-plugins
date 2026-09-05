#!/usr/bin/env python3
"""Start the Kumiho MCP server with an explicit tokenless CE client."""

from __future__ import annotations

import ipaddress
import os
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"
BACKEND_BOUND_SENTINEL = "_KUMIHO_ADAPTER_BOUND"


def _validated_endpoint(raw: str) -> str:
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("CE endpoint is empty or invalid")
    has_scheme = "://" in value
    parsed = urlsplit(value if has_scheme else f"//{value}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme not in {"", "http", "https", "grpc", "grpcs"}:
        raise ValueError("CE endpoint has an unsupported scheme")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CE endpoint has unsupported URL components")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CE endpoint has an invalid port") from exc
    if port is None:
        if not scheme:
            raise ValueError("CE endpoint must include a port")
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    loopback_host = host.rstrip(".").lower()
    loopback = loopback_host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(loopback_host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise ValueError("CE endpoints must use a loopback host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    return f"{scheme}://{authority}" if scheme else authority


def _validated_redis_url(raw: str) -> str:
    """Allow CE working memory only on loopback."""
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("CE Redis URL is empty or invalid")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("CE Redis URL has an unsupported scheme or host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CE Redis URL contains unsupported credentials or components")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("CE Redis URL has an invalid port") from exc
    host = parsed.hostname.rstrip(".").lower()
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise ValueError("CE Redis must use a loopback host")
    return value


def _install_codex_thread_context() -> None:
    if os.getenv("KUMIHO_CLAUDE_HOST") != "codex":
        return
    from codex_thread_context import install_codex_thread_context

    install_codex_thread_context()


def main() -> None:
    try:
        endpoint = _validated_endpoint(
            (os.getenv("KUMIHO_SERVER_ENDPOINT", "") or "").strip()
        )
        redis_url = _validated_redis_url(
            (os.getenv("UPSTASH_REDIS_URL", "") or "").strip()
            or DEFAULT_CE_REDIS_URL
        )
    except ValueError:
        print(
            "[kumiho-ce] Explicit CE routing is invalid; server and Redis "
            "endpoints must use loopback hosts.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    # The SDK can perform Cloud discovery during ``import kumiho`` when this
    # switch is inherited.  Scrub every discovery route before that import;
    # CE must be explicit and tokenless from the first SDK instruction.
    for key in (
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
    scheme = endpoint.partition("://")[0].lower() if "://" in endpoint else ""
    if scheme in {"grpcs", "https"}:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
        os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    else:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    os.environ["UPSTASH_REDIS_URL"] = redis_url

    try:
        import kumiho

        client = kumiho.connect(
            endpoint=endpoint,
            # ``None`` reloads ~/.kumiho Cloud credentials in supported SDKs.
            # Empty is explicitly tokenless and must remain so for CE.
            token="",
            enable_auto_login=False,
            use_discovery=False,
        )
        kumiho.configure_default_client(client)

        def keep_explicit_ce_client(*_args, **_kwargs):
            kumiho.configure_default_client(client)
            return client

        # kumiho.mcp_server validates configuration for each tool call. Keep
        # that safety check, but bind it to this already-validated CE client so
        # it cannot load Cloud auth or enter control-plane discovery.
        kumiho.auto_configure_from_discovery = keep_explicit_ce_client
    except Exception as exc:
        print(
            f"[kumiho-ce] Could not configure the CE client "
            f"({type(exc).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    _install_codex_thread_context()

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
        exec(compile(args[1], "<kumiho-ce-code>", "exec"), {"__name__": "__main__"})
        return

    from kumiho.mcp_server import main as run_server

    run_server()


if __name__ == "__main__":
    main()
