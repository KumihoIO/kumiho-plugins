"""Tunnel factory."""

from __future__ import annotations

from .. import config as cfgmod
from .base import Tunnel, TunnelError


def build_tunnel(cfg: cfgmod.Config) -> Tunnel:
    provider = (cfg.tunnel_provider or "cloudflare").lower()
    if provider == "cloudflare":
        from .cloudflare import CloudflareTunnel

        return CloudflareTunnel(cfg.gateway_port, token=cfg.tunnel_token, hostname=cfg.cloudflare_hostname)
    if provider == "ngrok":
        from .ngrok import NgrokTunnel

        return NgrokTunnel(cfg.gateway_port, token=cfg.tunnel_token)
    raise TunnelError(f"unknown tunnel provider: {cfg.tunnel_provider!r} (use 'cloudflare' or 'ngrok')")


__all__ = ["Tunnel", "TunnelError", "build_tunnel"]
