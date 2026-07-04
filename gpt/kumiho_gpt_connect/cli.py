"""kumiho-gpt-connect — command-line entry point.

    kumiho-gpt-connect install [--token T] [--tunnel cloudflare|ngrok]
                               [--tunnel-token TT] [--cloudflare-hostname H]
                               [--no-service]
    kumiho-gpt-connect serve         # run the gateway (what the service runs)
    kumiho-gpt-connect url           # print the ChatGPT connector URL + PIN
    kumiho-gpt-connect status        # health of gateway / CE / service
    kumiho-gpt-connect rotate-pin    # mint a new consent PIN
    kumiho-gpt-connect uninstall     # remove the auto-launch service
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, config as cfgmod


def _cmd_install(args: argparse.Namespace) -> int:
    from . import ce as cemod
    from . import service
    from .backend import resolve_cloud_token

    cfg = cfgmod.load()
    token = (args.token or "").strip()
    cfg.backend = "cloud" if (token or resolve_cloud_token()) else "ce"
    if token:
        # Persist so the (headless) service can reach Cloud. Standard cache.
        _store_cloud_token(token)
    cfg.tunnel_provider = args.tunnel
    if args.tunnel_token:
        cfg.tunnel_token = args.tunnel_token.strip()
    if args.cloudflare_hostname:
        cfg.cloudflare_hostname = args.cloudflare_hostname.strip()
    if args.gateway_port:
        cfg.gateway_port = args.gateway_port
    if args.inner_port:
        cfg.inner_port = args.inner_port
    pin = cfgmod.ensure_pin(cfg)
    # Derive the stable connector URL up front when we can (named cloudflare).
    if cfg.tunnel_provider == "cloudflare" and cfg.cloudflare_hostname:
        cfg.public_base_url = f"https://{cfg.cloudflare_hostname}"
    cfgmod.save(cfg)

    print(f"kumiho-gpt-connect {__version__}")
    print(f"  backend:  {cfg.backend}")
    print(f"  tunnel:   {cfg.tunnel_provider}")

    if cfg.backend == "ce":
        cemod.ensure_ce(wait=0)  # guidance only; serve waits for it
    else:
        print("  cloud:    paid/managed tier — this local tunnel is a bridge; the\n"
              "            recommended paid path is Kumiho's hosted managed connector.")

    if not args.no_service:
        service.install_service()
    else:
        print("  service:  skipped (--no-service); run `kumiho-gpt-connect serve` yourself")

    print("\nNext:")
    if cfg.connector_url:
        print(f"  1. Add this custom connector in ChatGPT (Developer mode):\n     {cfg.connector_url}")
    else:
        print("  1. Start the gateway once to obtain the connector URL:\n"
              "     kumiho-gpt-connect serve   (then: kumiho-gpt-connect url)")
    print(f"  2. When ChatGPT's browser consent asks for a PIN, enter: {pin}")
    print("  3. ChatGPT ▸ Settings ▸ Connectors ▸ Advanced ▸ Developer mode ▸ Add custom connector")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .gateway.serve import serve

    return serve()


def _cmd_url(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    if not cfg.connector_url:
        print("No connector URL yet. Start the gateway once (`kumiho-gpt-connect serve`) "
              "so the tunnel URL is known, then re-run this.", file=sys.stderr)
        return 1
    print(f"Connector URL: {cfg.connector_url}")
    print(f"Consent PIN:   {cfg.pin}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    import httpx

    from . import ce as cemod
    from . import service

    cfg = cfgmod.load()
    gw = "down"
    try:
        r = httpx.get(f"http://127.0.0.1:{cfg.gateway_port}/", timeout=1.0)
        gw = "up" if r.status_code < 500 else f"http {r.status_code}"
    except httpx.HTTPError:
        gw = "down"
    print(f"gateway (127.0.0.1:{cfg.gateway_port}): {gw}")
    print(f"backend: {cfg.backend}")
    if cfg.backend == "ce":
        print(f"CE server: {'up' if cemod.probe_ce() else 'down'}")
    print(f"service: {service.service_status()}")
    print(f"connector URL: {cfg.connector_url or '(unknown — run serve once)'}")
    return 0


def _cmd_rotate_pin(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    cfg.pin = cfgmod.new_pin()
    cfgmod.save(cfg)
    print(f"New consent PIN: {cfg.pin}")
    print("Restart the gateway/service for it to take effect.")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from . import service

    service.remove_service()
    print("Removed the auto-launch service. Config left in ~/.kumiho/gpt (delete it to fully reset).")
    return 0


def _store_cloud_token(token: str) -> None:
    import json
    from pathlib import Path

    cache = Path.home() / ".kumiho" / "kumiho_authentication.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    body = {}
    if cache.exists():
        try:
            body = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            body = {}
    body["api_token"] = token
    cache.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kumiho-gpt-connect", description=__doc__)
    parser.add_argument("--version", action="version", version=f"kumiho-gpt-connect {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="set up backend, tunnel, PIN, and auto-launch service")
    p_install.add_argument("--token", default="", help="Kumiho API token → use Cloud (paid) instead of CE")
    p_install.add_argument("--tunnel", choices=["cloudflare", "ngrok"], default="cloudflare")
    p_install.add_argument("--tunnel-token", default="", help="Cloudflare tunnel token or ngrok authtoken")
    p_install.add_argument("--cloudflare-hostname", default="", help="public hostname for a named Cloudflare tunnel")
    p_install.add_argument("--gateway-port", type=int, default=0)
    p_install.add_argument("--inner-port", type=int, default=0)
    p_install.add_argument("--no-service", action="store_true", help="do not install the auto-launch service")
    p_install.set_defaults(func=_cmd_install)

    sub.add_parser("serve", help="run the gateway (tunnel + OAuth + MCP proxy)").set_defaults(func=_cmd_serve)
    sub.add_parser("url", help="print the ChatGPT connector URL and PIN").set_defaults(func=_cmd_url)
    sub.add_parser("status", help="report gateway / CE / service health").set_defaults(func=_cmd_status)
    sub.add_parser("rotate-pin", help="mint a new consent PIN").set_defaults(func=_cmd_rotate_pin)
    sub.add_parser("uninstall", help="remove the auto-launch service").set_defaults(func=_cmd_uninstall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
