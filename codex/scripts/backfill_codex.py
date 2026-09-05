#!/usr/bin/env python3
"""Codex adapter for the unchanged, bundled history-backfill engine.

Use run_kumiho_mcp.mjs --backfill for shared-venv Python discovery.
Extraction is stdlib-only; ingest never provisions or upgrades the runtime.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))  # trusted siblings under python -I


def inventory(argv: list[str]) -> int:
    from backfill import inventory as engine

    # Honor Codex's history root without changing HOME or Claude discovery.
    codex_root = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").expanduser()
    engine.discover_codex_files = lambda: sorted((codex_root / "sessions").glob("**/*.jsonl"))
    if argv[:1] in (["scan"], ["manifest"]) and not any(
        arg == "--source" or arg.startswith("--source=") for arg in argv
    ):
        argv = [*argv, "--source", "codex"]
    if argv[:1] == ["packetize"] and not any(
        arg == "--top" or arg.startswith("--top=") for arg in argv
    ):
        argv = [*argv, "--top", "5"]
    sys.argv = [str(SCRIPT_DIR / "backfill" / "inventory.py"), *argv]
    return engine.main()


def _pin_keyless_env() -> None:
    # Same replay-only policy as Claude backfill_ingest._pin_keyless_env.
    os.environ["KUMIHO_AUTO_ASSESS"] = "0"
    os.environ["KUMIHO_GRAPH_AUGMENTED_RECALL"] = "0"
    os.environ["KUMIHO_LLM_PROVIDER"] = "openai"
    os.environ["KUMIHO_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["OPENAI_API_KEY"] = "kumiho-backfill-disabled"
    for key in ("KUMIHO_LLM_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(key, None)


def ingest(argv: list[str]) -> int:
    runner = SCRIPT_DIR / "backfill" / "ingest_runner.py"
    _pin_keyless_env()
    # Preview/refusal/help must not require Cloud login or a running CE.
    os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)
    if "--yes" not in argv or "--dry-run" in argv or "--help" in argv or "-h" in argv:
        sys.argv = [str(runner), *argv]
        runpy.run_path(str(runner), run_name="__main__")
        return 0

    import run_kumiho_mcp as codex

    codex._apply_codex_config()
    ce_mode = os.environ[codex.CODEX_BACKEND_ENV] == "ce"
    if ce_mode:
        os.environ["KUMIHO_SERVER_ENDPOINT"] = os.environ[codex.CODEX_ENDPOINT_ENV]
    os.environ["KUMIHO_CLAUDE_HOST"] = "codex"
    os.environ["KUMIHO_CLAUDE_DISCOVERY_USER_AGENT"] = codex._codex_user_agent()
    # Config may contain an LLM URL; pin again after hydration.
    _pin_keyless_env()
    adapter = SCRIPT_DIR / ("run_kumiho_ce.py" if ce_mode else "run_kumiho_cloud.py")
    sys.argv = [str(adapter), "--script", str(runner), *argv]
    runpy.run_path(str(adapter), run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path,
                        default=Path.home() / ".kumiho" / "backfill" / "codex",
                        help="local staging/packet directory (separate from Claude)")
    parser.add_argument("command", choices=("inventory", "ingest"))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    os.environ["KUMIHO_BACKFILL_HOME"] = str(args.state_dir.expanduser().resolve())
    try:
        return inventory(args.args) if args.command == "inventory" else ingest(args.args)
    except ImportError:
        print("[kumiho-codex] Backfill dependencies unavailable; run $kumiho-onboard, then retry.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
