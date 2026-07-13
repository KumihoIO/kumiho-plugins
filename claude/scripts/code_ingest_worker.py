#!/usr/bin/env python3
"""Detached Decision Memory ingest worker (kumiho-plugins#10).

Spawned by ``code-capture-hook.py`` after a commit lands (or at session
end).  Runs OUTSIDE the hook's lifetime, so it can afford the full
environment hydration + venv provisioning + LLM mining pass:

1. Reuses the launcher's own environment pipeline (dotenv / cached auth /
   CE-mode endpoint bootstrap / LLM fallback) by importing
   ``run_kumiho_mcp`` from this directory — the worker connects to exactly
   the same server the MCP tools use, cloud or self-hosted CE alike.
2. Ensures the plugin venv (same one the MCP server runs in).
3. Runs ``python -m kumiho_memory code-ingest <repo>`` in incremental
   mode: already-captured commits are marker-skipped at zero LLM cost, so
   over-triggering costs nothing.

A state-dir lock file keeps concurrent workers (rapid commit bursts) from
mining the same repo twice; a stale lock (>10 min) is broken.  Output goes
to ``<state-dir>/code-ingest.log`` — capture failures are logged, never
raised into the session.
"""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_COMMITS = 20
LOCK_STALE_S = 600


def _load_launcher():
    path = Path(__file__).resolve().parent / "run_kumiho_mcp.py"
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() is __main__-guarded
    return module


def _load_pending():
    path = Path(__file__).resolve().parent / "code_capture_pending.py"
    spec = importlib.util.spec_from_file_location("code_capture_pending", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    launcher = _load_launcher()
    state_dir = launcher._state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "code-ingest.log"

    def log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    # Not a git repo -> nothing to capture (SessionEnd fires everywhere).
    probe = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return 0

    lock = state_dir / "code-ingest.lock"
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) < LOCK_STALE_S:
            log(f"skip: another ingest is running (lock {lock})")
            return 0
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass  # lock is best-effort

    try:
        # Same environment pipeline as the MCP server (CE mode included).
        launcher._sanitize_placeholder_env_vars()
        launcher._hydrate_env_from_local_config()
        if not launcher._ce_mode_enabled():
            launcher._validate_auth_token()
        try:
            launcher._bootstrap_server_endpoint()
        except RuntimeError as exc:
            log(f"skip: endpoint bootstrap failed ({exc})")
            return 0
        launcher._configure_llm_fallback()
        if (os.getenv("KUMIHO_LLM_BASE_URL", "") or "").startswith("http://127.0.0.1:9"):
            # No real model here (fail-fast fallback) and no agent in the loop,
            # so decisions can't be extracted now. KEYLESS fallback: queue the
            # commit so the in-loop agent captures it via kumiho_code_capture on
            # its next session (see code_capture_pending.py + SKILL.md). Never
            # drop the commit — the queue is the keyless bridge.
            try:
                _load_pending().enqueue(repo_dir)
                log("no LLM: queued commit for keyless agent capture "
                    "(pending-code-captures.jsonl)")
            except Exception as exc:  # noqa: BLE001 — never break the session
                log(f"skip: no LLM and enqueue failed: {exc}")
            return 0

        python_path = launcher._ensure_runtime()
        env = dict(os.environ)
        env["KUMIHO_MEMORY_CODE"] = "1"
        log(f"ingest start: repo={repo_dir} (newest {MAX_COMMITS}, incremental)")
        proc = subprocess.run(
            [str(python_path), "-m", "kumiho_memory", "code-ingest", repo_dir,
             "--max-commits", str(MAX_COMMITS)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=600,
        )
        tail = (proc.stdout or "").strip().splitlines()[-12:]
        log(f"ingest done rc={proc.returncode}: " + " | ".join(tail))
        if proc.returncode != 0 and proc.stderr:
            log("stderr: " + (proc.stderr or "").strip()[-500:])
        return 0  # capture failures never propagate
    except Exception as exc:  # noqa: BLE001
        log(f"worker error: {exc}")
        return 0
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
