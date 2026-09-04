#!/usr/bin/env python3
"""Run kumiho.mcp_server against an explicit tokenless remote CE endpoint."""

from __future__ import annotations

import ipaddress
import os
import sys
from urllib.parse import urlsplit


_ENDPOINT_ENV = "KUMIHO_OPENCLAW_REMOTE_CE_ENDPOINT"
_TLS_SCHEMES = {"https", "grpcs"}


def _validated_endpoint() -> str:
    raw = (os.getenv(_ENDPOINT_ENV) or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"Invalid {_ENDPOINT_ENV}") from exc

    if (
        parsed.scheme.lower() not in _TLS_SCHEMES
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError(
            f"{_ENDPOINT_ENV} must be a grpcs:// or https:// endpoint without credentials or a path"
        )
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError(f"Invalid {_ENDPOINT_ENV} port")

    host = parsed.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if loopback:
        raise RuntimeError(f"{_ENDPOINT_ENV} is reserved for non-loopback CE endpoints")
    return raw


def main() -> None:
    endpoint = _validated_endpoint()

    # token="" is intentionally distinct from None in the SDK: it prevents
    # loading ~/.kumiho credentials. The explicit target and use_discovery=False
    # keep every MCP request on this TLS-protected CE endpoint.
    import kumiho

    client = kumiho.connect(
        endpoint=endpoint,
        token="",
        enable_auto_login=False,
        use_discovery=False,
    )
    kumiho.configure_default_client(client)

    # kumiho.mcp_server calls this before every tool invocation. Pin it to the
    # explicit client so even a model-supplied auth_token cannot re-enable Cloud
    # discovery after startup.
    def explicit_remote_ce_client(*_args: object, **_kwargs: object):
        kumiho.configure_default_client(client)
        return client

    kumiho.auto_configure_from_discovery = explicit_remote_ce_client

    from kumiho.mcp_server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[kumiho-openclaw] remote CE bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
