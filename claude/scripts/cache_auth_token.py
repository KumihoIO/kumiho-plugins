#!/usr/bin/env python3
"""Cache a Kumiho dashboard API token in the local credential store.

This stores the token under the ``api_token`` key in
``~/.kumiho/kumiho_authentication.json``.  It does NOT overwrite the
``id_token`` / ``control_plane_token`` keys created by ``kumiho-cli login``.

Usage:
    python cache_auth_token.py --token eyJ...
    echo 'eyJ...' | python cache_auth_token.py --stdin
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


def _credential_path() -> Path:
    config_dir = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "kumiho_authentication.json"
    return Path.home() / ".kumiho" / "kumiho_authentication.json"


def _clean_token(raw: str) -> str:
    token = raw.strip()
    if not token:
        return ""
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _decode_jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        claims = json.loads(decoded.decode("utf-8"))
        if isinstance(claims, dict):
            return claims
    except Exception:
        return None
    return None


def _write_credential(token: str, expires_at: int | None) -> Path:
    path = _credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    # Dashboard API tokens are long-lived and stored separately from
    # session tokens created by `kumiho-cli login` (id_token / control_plane_token).
    existing["api_token"] = token
    if expires_at is not None:
        existing["api_token_expires_at"] = expires_at
    else:
        existing.pop("api_token_expires_at", None)

    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cache a Kumiho API token in the local credential store."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--token", help="JWT token to cache")
    group.add_argument(
        "--stdin",
        action="store_true",
        help="Read token from stdin (avoids shell history exposure)",
    )
    args = parser.parse_args()

    raw = args.token if args.token else sys.stdin.readline()
    token = _clean_token(raw)

    if not token:
        print("Error: empty token provided.", file=sys.stderr)
        return 1

    claims = _decode_jwt_payload(token)
    if claims is None:
        print(
            "Error: token does not look like a valid JWT (expected 3 dot-separated parts "
            "with a base64url-encoded JSON payload).",
            file=sys.stderr,
        )
        return 1

    expires_at = claims.get("exp")
    if isinstance(expires_at, (int, float)):
        expires_at = int(expires_at)
    else:
        expires_at = None

    path = _write_credential(token, expires_at)
    print(f"API token cached at {path}", file=sys.stderr)
    print("Stored under the 'api_token' key (login session tokens are not affected).", file=sys.stderr)
    print("The kumiho-claude MCP server will pick this up on next restart.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
