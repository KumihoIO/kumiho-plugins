#!/usr/bin/env python3
"""Decision Memory auto-capture hook (kumiho-plugins#10).

Fires on two Claude Code hook events and, when a git commit has plausibly
landed, spawns the DETACHED ingest worker so decisions are mined into the
graph with zero user action:

* ``PostToolUse`` (matcher: ``Bash``) — acts only when the tool command
  contains a ``git ... commit`` invocation.  Commit grain, not edit grain:
  anchors need a commit hash, and per-edit capture would burn LLM calls on
  noise (see the issue).
* ``SessionEnd`` — batch safety net for commits made during the session.

Design constraints (all hard):
- The hook must NEVER block or fail the commit/session: the worker is
  spawned fully detached and this process exits 0 immediately, always.
- Re-runs are free: the SDK's incremental mode marker-skips captured
  commits with zero LLM cost, so over-triggering is harmless.
- Gate: ``KUMIHO_MEMORY_CODE`` — on by default for the plugin (the plugin
  IS the integration layer); set ``0``/``false`` to disable capture.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_COMMIT_RE = re.compile(r"\bgit\b[^|;&]*\bcommit\b")


def _gate_enabled() -> bool:
    return (os.getenv("KUMIHO_MEMORY_CODE", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _spawn_worker(repo_dir: str) -> None:
    worker = Path(__file__).resolve().parent / "code_ingest_worker.py"
    if not worker.exists():
        return
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": repo_dir,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives hook exit.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, str(worker), repo_dir], **kwargs)


def main() -> None:
    if not _gate_enabled():
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return

    event = data.get("hook_event_name", "")
    cwd = data.get("cwd") or os.getcwd()

    if event == "PostToolUse":
        if data.get("tool_name") != "Bash":
            return
        command = str((data.get("tool_input") or {}).get("command", ""))
        if not _COMMIT_RE.search(command):
            return
    elif event != "SessionEnd":
        return

    try:
        _spawn_worker(cwd)
    except Exception:  # noqa: BLE001 — capture must never break the session
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
