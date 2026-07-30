#!/usr/bin/env python3
"""Detached Decision Memory session-mining worker (Phase 2 loop-closer).

Spawned by ``code-capture-hook.py`` at ``SessionEnd``.  Where the commit
worker mines *what* landed in git, this worker mines the *why* the session
produced — rejected alternatives, measurements in their original form, and
decisions that never reached a commit — closing the loop the design left
open (commit capture alone never touched the conversation).

Runs OUTSIDE the hook's lifetime, so it affords the full env hydration +
venv + LLM pass:

1. Reuses the launcher's environment pipeline (dotenv / cached auth /
   CE-mode endpoint / LLM fallback) — same server the MCP tools use.
2. **Double opt-in gate**: session mining runs a full-transcript LLM pass
   at every session end (real recurring cost + raw-conversation privacy),
   so it is OFF by default.  ``KUMIHO_MEMORY_CODE_AUTOMINE=1`` (in addition
   to the master ``KUMIHO_MEMORY_CODE``) turns it on — the same gate the SDK
   consolidation chain uses.
3. Runs ``python -m kumiho_memory code-mine-session <session_id>
   --transcript <path> --repo <repo>``.  Idempotent: a completed session is
   marker-skipped at zero LLM cost, so a re-fire costs nothing.

A state-dir lock keeps concurrent workers off the same session; output goes
to ``<state-dir>/session-mine.log``.  Failures are logged, never raised into
the session.
"""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

LOCK_STALE_S = 900


def _load_launcher():
    path = Path(__file__).resolve().parent / "run_kumiho_mcp.py"
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() is __main__-guarded
    return module


def _automine_enabled() -> bool:
    """Double opt-in: master gate ON *and* AUTOMINE explicitly set."""
    master = (os.getenv("KUMIHO_MEMORY_CODE", "1") or "1").strip().lower()
    if master in ("0", "false", "no", "off"):
        return False
    return (os.getenv("KUMIHO_MEMORY_CODE_AUTOMINE", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def main() -> int:
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    session_id = sys.argv[2] if len(sys.argv) > 2 else ""
    transcript = sys.argv[3] if len(sys.argv) > 3 else ""

    launcher = _load_launcher()
    state_dir = launcher._state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "session-mine.log"

    def log(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    if not session_id:
        return 0
    if not transcript or not Path(transcript).exists():
        log(f"skip: no transcript for session {session_id}")
        return 0

    # Not a git repo -> no anchors to correlate against.
    probe = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return 0

    lock = state_dir / f"session-mine-{session_id}.lock"
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) < LOCK_STALE_S:
            log(f"skip: another session mine is running (lock {lock})")
            return 0
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass  # lock is best-effort

    try:
        launcher._sanitize_placeholder_env_vars()
        launcher._hydrate_env_from_local_config()
        # Gate AFTER hydration, not before. Evaluated earlier, the flag could only
        # ever be read from a real process env var, so declaring
        # KUMIHO_MEMORY_CODE_AUTOMINE in .mcp.json was inert and this worker was
        # unreachable in practice. Default is still OFF (double opt-in).
        if not _automine_enabled():
            log("skip: AUTOMINE off (set KUMIHO_MEMORY_CODE_AUTOMINE=1 to enable)")
            return 0
        if not launcher._ce_mode_enabled():
            launcher._validate_auth_token()
        try:
            launcher._bootstrap_server_endpoint()
        except RuntimeError as exc:
            log(f"skip: endpoint bootstrap failed ({exc})")
            return 0
        launcher._configure_llm_fallback()
        if (os.getenv("KUMIHO_LLM_BASE_URL", "") or "").startswith("http://127.0.0.1:9"):
            log("skip: no LLM configured (fail-fast fallback active)")
            return 0

        python_path = launcher._ensure_runtime()
        env = dict(os.environ)
        env["KUMIHO_MEMORY_CODE"] = "1"
        log(f"session mine start: session={session_id} repo={repo_dir}")
        proc = subprocess.run(
            [str(python_path), "-m", "kumiho_memory", "code-mine-session",
             session_id, "--transcript", transcript, "--repo", repo_dir],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=600,
        )
        tail = (proc.stdout or "").strip().splitlines()[-14:]
        log(f"session mine done rc={proc.returncode}: " + " | ".join(tail))
        if proc.returncode != 0 and proc.stderr:
            log("stderr: " + (proc.stderr or "").strip()[-500:])
        return 0  # mining failures never propagate
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
