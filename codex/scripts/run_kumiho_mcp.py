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

#: Resolution order: the monorepo-relative claude launcher (dev checkouts —
#: always the freshest), then the vendored copy shipped inside this plugin
#: (marketplace snapshots copy only the plugin directory, so ../claude does
#: not exist there). test_launcher_parity.py guards the vendored copy
#: against drifting from the canonical claude/scripts version.
_LAUNCHER_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent
    / "claude" / "scripts" / "run_kumiho_mcp.py",
    Path(__file__).resolve().parent / "_vendored_launcher.py",
)


def main() -> None:
    launcher = next((p for p in _LAUNCHER_CANDIDATES if p.exists()), None)
    if launcher is None:
        print(
            "[kumiho-codex] No launcher found (looked for the monorepo "
            f"claude launcher and the vendored copy): {_LAUNCHER_CANDIDATES}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    os.environ.setdefault("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", CODEX_USER_AGENT)
    # Identify the host: the shared launcher gates Claude-Desktop config
    # writes (bootstrap + token sync) on this, so a codex-spawned run can
    # never create or rewrite another host's config files.
    os.environ["KUMIHO_CLAUDE_HOST"] = "codex"
    sys.argv[0] = str(launcher)
    runpy.run_path(str(launcher), run_name="__main__")


if __name__ == "__main__":
    main()
