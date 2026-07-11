#!/usr/bin/env python3
"""Kumiho memory MCP launcher for OpenAI Codex CLI.

Thin shim over the Claude launcher: the environment pipeline (venv
provisioning, dotenv/cached-auth hydration, control-plane discovery, CE
mode, LLM fallback) is identical for every MCP host — only the discovery
user-agent differs.  The shim keeps one source of truth in
``claude/scripts/run_kumiho_mcp.py``; this repo ships as a monorepo, so
the relative path is stable.
"""

import os
import runpy
import sys
from pathlib import Path

CODEX_USER_AGENT = "kumiho-codex/0.1.0"

_LAUNCHER = (
    Path(__file__).resolve().parent.parent.parent
    / "claude" / "scripts" / "run_kumiho_mcp.py"
)


def main() -> None:
    if not _LAUNCHER.exists():
        print(
            f"[kumiho-codex] Shared launcher not found at {_LAUNCHER} — "
            "the codex integration requires the full kumiho-plugins checkout.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    os.environ.setdefault("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", CODEX_USER_AGENT)
    sys.argv[0] = str(_LAUNCHER)
    runpy.run_path(str(_LAUNCHER), run_name="__main__")


if __name__ == "__main__":
    main()
