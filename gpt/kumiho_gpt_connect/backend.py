"""Backend wiring for the inner ``kumiho-mcp``.

Backend selection reuses the SDK's own rule (token present → Cloud discovery;
no token → tokenless loopback CE probe). We just assemble the environment the
inner server inherits.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

DEFAULT_CE_REDIS = "redis://127.0.0.1:6379"
CE_ENDPOINT = "127.0.0.1:9190"


def resolve_cloud_token() -> Optional[str]:
    """A Kumiho API token from env or the standard credential cache."""
    env = (os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip()
    if env:
        return env
    cache = Path.home() / ".kumiho" / "kumiho_authentication.json"
    if not cache.exists():
        return None
    try:
        body = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for key in ("control_plane_token", "id_token", "api_token"):
        val = body.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def backend_env(backend: str) -> Dict[str, str]:
    """Environment the inner kumiho-mcp process should run with."""
    if backend == "cloud":
        token = resolve_cloud_token()
        if not token:
            raise RuntimeError(
                "Cloud backend selected but no Kumiho token found. Run "
                "`kumiho-auth login` or set KUMIHO_AUTH_TOKEN."
            )
        # Token present → SDK routes to Cloud via control-plane discovery.
        return {"KUMIHO_AUTH_TOKEN": token}

    # CE: no token → SDK probes the loopback CE server; working memory needs a
    # local Redis URL (Cloud gets this via the control-plane proxy, CE does not).
    env = {
        "KUMIHO_LOCAL_SERVER_ENDPOINT": CE_ENDPOINT,
        "UPSTASH_REDIS_URL": os.getenv("UPSTASH_REDIS_URL", DEFAULT_CE_REDIS),
    }
    # Make sure no stray token flips us into Cloud mode.
    env["KUMIHO_AUTH_TOKEN"] = ""
    return env
