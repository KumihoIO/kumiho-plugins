"""The `serve` supervisor: inner MCP server + tunnel + OAuth gateway.

This is the long-running process the auto-launch service runs. It:
  1. ensures the backend (CE probe / Cloud token),
  2. starts the inner mcp-proxy(kumiho-mcp),
  3. brings the tunnel up and records the public URL,
  4. builds the OAuth-aware gateway with that public issuer, and
  5. serves it on loopback (the tunnel forwards to it).
"""

from __future__ import annotations

import sys

import uvicorn

from .. import backend as backendmod
from .. import ce as cemod
from .. import config as cfgmod
from ..tunnels import build_tunnel
from .app import build_app
from .proxy import InnerMcp


def serve() -> int:
    cfg = cfgmod.load()
    cfgmod.ensure_pin(cfg)

    if cfg.backend == "ce" and not cemod.ensure_ce(wait=60):
        print(
            "[kumiho-gpt-connect] Starting the gateway anyway; MCP tool calls will "
            "fail until the CE server is running.",
            file=sys.stderr,
        )

    env = backendmod.backend_env(cfg.backend)
    inner = InnerMcp(cfg, env)
    tunnel = build_tunnel(cfg)

    inner.start()
    try:
        public = tunnel.start()
        cfg.public_base_url = public
        cfgmod.save(cfg)

        app = build_app(cfg, inner.base_url)
        _banner(cfg)
        uvicorn.run(app, host="127.0.0.1", port=cfg.gateway_port, log_level="info")
        return 0
    finally:
        tunnel.stop()
        inner.stop()


def _banner(cfg: cfgmod.Config) -> None:
    line = "═" * 64
    print(
        f"\n{line}\n"
        "  Kumiho Memory → ChatGPT connector is live\n"
        f"{line}\n"
        f"  Connector URL (paste into ChatGPT ▸ Settings ▸ Connectors ▸\n"
        f"  Advanced ▸ Developer mode ▸ Add custom connector):\n\n"
        f"      {cfg.connector_url}\n\n"
        f"  Auth: OAuth (the browser will ask for a PIN once).\n"
        f"  PIN:  {cfg.pin}\n"
        f"{line}\n",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(serve())
