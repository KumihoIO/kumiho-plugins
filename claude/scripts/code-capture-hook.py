#!/usr/bin/env python3
"""Decision Memory auto-capture hook (kumiho-plugins#10).

Fires on two Claude Code hook events and, when a git commit has plausibly
landed, spawns the DETACHED ingest worker so decisions are mined into the
graph with zero user action:

* ``PostToolUse`` (matcher: ``Bash``) — acts only when the tool command
  contains a ``git ... commit`` invocation.  Commit grain, not edit grain:
  anchors need a commit hash, and per-edit capture would burn LLM calls on
  noise (see the issue).
* ``SessionEnd`` — batch safety net for commits made during the session,
  AND (Phase 2 loop-closer) mines the session TRANSCRIPT into the graph:
  rejected alternatives, measurements, and decisions that never reached a
  commit.  The commit worker captures *what* landed; the session worker
  captures *why*.

Design constraints (all hard):
- The hook must NEVER block or fail the commit/session: the workers are
  spawned fully detached and this process exits 0 immediately, always.
- Re-runs are free: the SDK's incremental mode marker-skips captured
  commits AND completed sessions with zero LLM cost, so over-triggering is
  harmless.
- Gate: ``KUMIHO_MEMORY_CODE`` — on by default for the plugin (the plugin
  IS the integration layer); set ``0``/``false`` to disable commit capture.
  Session mining is additionally gated behind
  ``KUMIHO_MEMORY_CODE_AUTOMINE=1`` (OFF by default — a full-transcript LLM
  pass at every session end is real cost + raw-conversation privacy, so it
  is explicit opt-in; the session worker enforces this gate itself).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_home  # noqa: E402

_COMMIT_RE = re.compile(r"\bgit\b[^|;&]*\bcommit\b")


def _gate_enabled() -> bool:
    return (os.getenv("KUMIHO_MEMORY_CODE", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _spawn(worker_name: str, args: list) -> None:
    """Spawn a detached worker that survives this hook's exit."""
    worker = Path(__file__).resolve().parent / worker_name
    if not worker.exists():
        return
    repo_dir = args[0] if args else os.getcwd()
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": repo_dir,
        "env": state_home.secured_hook_child_env(),
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives hook exit.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-I", str(worker), *args], **kwargs)


def main() -> None:
    if not _gate_enabled():
        return
    # Decode the wire format explicitly. The hook payload is UTF-8, but on a
    # Windows pipe sys.stdin decodes with the ambient code page (cp949) and
    # surrogateescape, so a repo under a non-ASCII path came back as mojibake --
    # and this hook forwards `cwd` straight into the workers' argv. Measured:
    # the mojibake path made Popen(cwd=...) raise NotADirectoryError into the
    # `except Exception: pass` in _spawn, so every commit capture under such a
    # path was dropped with nothing logged anywhere. Same idiom as
    # session-bootstrap._read_hook_input.
    try:
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read().decode("utf-8", "replace") if buf else sys.stdin.read()
    except (OSError, ValueError):
        return
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, ValueError):
        return

    event = data.get("hook_event_name", "")
    cwd = data.get("cwd") or os.getcwd()

    if event == "PostToolUse":
        if data.get("tool_name") != "Bash":
            return
        command = str((data.get("tool_input") or {}).get("command", ""))
        if not _COMMIT_RE.search(command):
            return
        # commit grain: mine the commit that (plausibly) just landed
        try:
            _spawn("code_ingest_worker.py", [cwd])
        except Exception:  # noqa: BLE001 — capture must never break the session
            pass
        return

    if event != "SessionEnd":
        return

    # SessionEnd: commit safety net AND (opt-in) session-transcript mining.
    try:
        _spawn("code_ingest_worker.py", [cwd])
    except Exception:  # noqa: BLE001
        pass
    try:
        session_id = str(data.get("session_id", "") or "")
        transcript = str(data.get("transcript_path", "") or "")
        if session_id and transcript:
            # The session worker self-enforces the AUTOMINE double opt-in;
            # spawning is unconditional and cheap (it exits fast when off).
            _spawn("session_mine_worker.py", [cwd, session_id, transcript])
    except Exception:  # noqa: BLE001 — mining must never break the session
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
