#!/usr/bin/env python3
"""Local discovery smoke test using .env.local credentials.

Usage (from kumiho-claude/ or repo root):
    python kumiho-claude/scripts/test_discovery_env.py --env-file .env.local

Expected env keys in file:
    KUMIHO_AUTH_TOKEN=eyJ...
    KUMIHO_CONTROL_PLANE_URL=https://control.kumiho.cloud   # optional
    KUMIHO_TENANT_HINT=your-tenant-slug                      # optional
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import run_kumiho_mcp as bootstrap


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        return text[1:-1]
    return text


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")

    loaded: dict[str, str] = {}
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"Invalid env line {idx}: {raw}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env line {idx}: {raw}")
        loaded[key] = _strip_wrapping_quotes(value)
    return loaded


def _apply_loaded_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value


def _is_localhost_target(target: str) -> bool:
    host = target.rsplit(":", 1)[0].strip().lower()
    return host in {"localhost", "127.0.0.1", "::1", "[::1]"}


def _check_dns(target: str) -> tuple[bool, str]:
    host, _, port_text = target.rpartition(":")
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not port_text.isdigit():
        return False, f"invalid port in target: {target}"
    try:
        infos = socket.getaddrinfo(host, int(port_text), proto=socket.IPPROTO_TCP)
    except Exception as exc:
        return False, str(exc)
    return True, f"{len(infos)} addrinfo record(s)"


def _request_discovery(token: str, discovery_url: str, tenant_hint: str, timeout: float) -> dict:
    user_agent = (os.getenv("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", "") or "").strip() or "kumiho-claude/0.4.0"
    payload: dict[str, str] = {}
    if tenant_hint:
        payload["tenant_hint"] = tenant_hint
    request = urllib.request.Request(
        discovery_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate control-plane discovery from .env.local")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Path to env file (default: .env.local)",
    )
    parser.add_argument(
        "--tenant-hint",
        default="",
        help="Optional tenant hint override (slug or tenant id)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP timeout seconds (default: 8)",
    )
    parser.add_argument(
        "--skip-dns-check",
        action="store_true",
        help="Skip DNS resolution check for the resolved target",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser()
    loaded = _load_env_file(env_path)
    _apply_loaded_env(loaded)

    token = bootstrap._clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if not token or bootstrap._looks_like_placeholder(token):
        print("FAIL: KUMIHO_AUTH_TOKEN missing/invalid in env file", file=sys.stderr)
        return 2

    if token.count(".") != 2:
        print("FAIL: KUMIHO_AUTH_TOKEN is not a JWT (expected 3 sections)", file=sys.stderr)
        return 2

    control_plane_url = bootstrap._load_control_plane_url()
    discovery_url = bootstrap._build_discovery_url(control_plane_url)
    tenant_hint = args.tenant_hint or (os.getenv("KUMIHO_TENANT_HINT", "") or "").strip()
    discovery_user_agent = (os.getenv("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", "") or "").strip() or "kumiho-claude/0.4.0"

    print(f"env_file: {env_path}")
    print(f"control_plane_url: {control_plane_url}")
    print(f"discovery_url: {discovery_url}")
    print(f"user_agent: {discovery_user_agent}")
    print(f"tenant_hint: {tenant_hint or '(none)'}")

    try:
        body = _request_discovery(token, discovery_url, tenant_hint, args.timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8").strip()
        except Exception:
            pass
        print(f"FAIL: discovery HTTP {exc.code} {exc.reason}", file=sys.stderr)
        if detail:
            print(f"detail: {detail}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"FAIL: discovery request error: {exc}", file=sys.stderr)
        return 3

    region = body.get("region") if isinstance(body, dict) else None
    if not isinstance(region, dict):
        print("FAIL: discovery response missing region object", file=sys.stderr)
        print(json.dumps(body, indent=2, ensure_ascii=True), file=sys.stderr)
        return 4

    raw_target = ""
    grpc_authority = region.get("grpc_authority")
    if isinstance(grpc_authority, str) and grpc_authority.strip():
        raw_target = grpc_authority
    else:
        server_url = region.get("server_url")
        if isinstance(server_url, str) and server_url.strip():
            raw_target = server_url

    resolved_target = bootstrap._normalize_server_target(raw_target or "")
    if not resolved_target:
        print("FAIL: could not normalize discovery target", file=sys.stderr)
        print(json.dumps(body, indent=2, ensure_ascii=True), file=sys.stderr)
        return 5

    print(f"region_code: {region.get('region_code')}")
    print(f"server_url: {region.get('server_url')}")
    print(f"grpc_authority: {region.get('grpc_authority')}")
    print(f"resolved_target: {resolved_target}")

    if _is_localhost_target(resolved_target):
        print("FAIL: discovery resolved localhost target (fallback/unconfigured route)", file=sys.stderr)
        return 6

    if not args.skip_dns_check:
        ok, detail = _check_dns(resolved_target)
        if not ok:
            print(f"FAIL: target DNS check failed: {detail}", file=sys.stderr)
            return 7
        print(f"dns_check: ok ({detail})")

    print("PASS: discovery returned non-local Kumiho server endpoint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
