#!/usr/bin/env python3
"""History Backfill ingest wrapper — env hydration + keyless pinning + venv exec.

Invoked by /kumiho-backfill (stage 2). Reuses the launcher's environment
pipeline exactly like session_mine_worker.py, then — unlike any other worker —
**pins a keyless environment** before spawning the venv runner, so a power
user with an API key in .env.local still gets zero LLM calls during ingest
(docs/BACKFILL_DESIGN.md, "keyless by enforcement"):

* KUMIHO_AUTO_ASSESS=0 and KUMIHO_GRAPH_AUGMENTED_RECALL=0
* the LLM endpoint forced (not defaulted) to the launcher's fail-fast dead
  port, with provider API keys scrubbed from the child env

All CLI arguments are forwarded verbatim to backfill/ingest_runner.py, and
the runner's stdio flows straight through — the consent payload must reach
whoever invoked us. Exit code is the runner's.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def _load_launcher():
    path = Path(__file__).resolve().parent / "run_kumiho_mcp.py"
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() is __main__-guarded
    return module


def _pin_keyless_env(env: dict) -> None:
    env["KUMIHO_AUTO_ASSESS"] = "0"
    env["KUMIHO_GRAPH_AUGMENTED_RECALL"] = "0"
    env["KUMIHO_LLM_PROVIDER"] = "openai"
    env["KUMIHO_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"  # launcher's fail-fast dead port
    env["OPENAI_API_KEY"] = "kumiho-claude-fallback"
    for key in ("KUMIHO_LLM_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)


def main() -> int:
    launcher = _load_launcher()

    launcher._sanitize_placeholder_env_vars()
    launcher._hydrate_env_from_local_config()
    if not launcher._ce_mode_enabled():
        launcher._validate_auth_token()
    try:
        launcher._bootstrap_server_endpoint()
    except RuntimeError as exc:
        print(f"[backfill-ingest] endpoint bootstrap failed: {exc}", file=sys.stderr)
        print("[backfill-ingest] run /kumiho-onboard first (auth + venv), then retry.",
              file=sys.stderr)
        return 1

    python_path = launcher._ensure_runtime()
    env = dict(os.environ)
    _pin_keyless_env(env)

    runner = Path(__file__).resolve().parent / "backfill" / "ingest_runner.py"
    argv = sys.argv[1:]
    if "--log-file" not in argv:
        argv += ["--log-file", str(launcher._state_dir() / "backfill-ingest.log")]
    command = [str(python_path), str(runner), *argv]
    if launcher._ce_mode_enabled():
        adapter = Path(__file__).resolve().parent / "run_kumiho_ce.py"
        command = [str(python_path), str(adapter), "--script", str(runner), *argv]
    proc = subprocess.run(command, env=env)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
