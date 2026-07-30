#!/usr/bin/env python3
"""Observation-only hook: make the memory reflexes countable.

Before this, no code anywhere called, counted, observed, retried or logged
``kumiho_memory_engage`` / ``kumiho_memory_reflect``. A turn where memory silently
did nothing was indistinguishable from one where it worked, which is why the
failure was only ever found by a user asking afterwards.

This writes a per-session ledger of what happened, and nothing else. It costs no
context, makes no network call, and stores NO conversation text -- only a length
and a sha256 prefix of the assistant's final message, which is enough to tell
"a substantive turn produced no reflect" from "a tool-only turn" without ever
recording what was said.

Wired to two events, both ``async: true`` so neither blocks the turn:
  Stop          -> one {"kind":"stop"} line per completed turn
  PostToolUse   -> one {"kind":"tool"} line per engage/reflect call
                   (matcher-scoped to the kumiho memory tools)

Exits 0 on every path, including BaseException: an observer that can fail a
session is worse than no observer.

Run: fed a JSON hook payload on stdin by Claude Code. Prints nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reflex_state as rs  # noqa: E402


def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not (raw or "").strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ledger_path(session_id: str) -> Path:
    return rs.reflex_dir() / ("%s.turns.jsonl" % session_id)


def _on_stop(payload: dict, session_id: str) -> None:
    # A Stop hook that re-fires inside its own continuation would double-count.
    if payload.get("stop_hook_active"):
        return
    msg = payload.get("last_assistant_message")
    text = msg if isinstance(msg, str) else ""
    stripped = text.strip()
    entry = {
        "kind": "stop",
        "session_id": session_id,
        "prompt_id": str(payload.get("prompt_id") or ""),
        "ts": int(time.time()),
        "resp_len": len(stripped),
        # sha of the TEXT, never the text: enough to correlate turns, useless
        # for reconstructing anything.
        "resp_sha12": hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:12] if stripped else "",
        # openclaw's empty-response guard (hooks.ts): a turn that ended with only
        # tool calls is not a missed capture, it is a turn with nothing to say.
        "tool_only": not stripped,
    }
    rs.append_jsonl(_ledger_path(session_id), entry)


def _spawn_prefetch(payload: dict, session_id: str) -> None:
    """Refresh the recall cache during the user's thinking time.

    This is the whole stale-while-revalidate trade: the expensive call happens
    HERE, after the turn ended, so the blocking hook on the next turn spawns
    nothing and only reads a file. Fully detached -- it outlives this hook's
    timeout by design.

    The query text is never passed in argv: process command lines are captured by
    Windows 4688 / Sysmon 1 and forwarded off-machine by EDR. The worker reads it
    from the state files instead.
    """
    if not rs.gate("KUMIHO_REFLEX_PREFETCH"):
        return
    worker = Path(__file__).resolve().parent / "reflex_prefetch_worker.py"
    if not worker.exists():
        return
    # _spawn uses args[0] as the child's cwd, so it must be a real directory.
    cwd = str(payload.get("cwd") or "").strip()
    if not cwd or not os.path.isdir(cwd):
        cwd = os.getcwd()
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "cwd": cwd}
    if os.name == "nt":
        kwargs["creationflags"] = 0x8 | 0x200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, str(worker), cwd, session_id], **kwargs)
    except OSError as exc:
        rs.log("prefetch spawn failed: %s" % exc)


def _on_tool(payload: dict, session_id: str) -> None:
    tool = str(payload.get("tool_name") or "")
    if not tool:
        return
    short = "engage" if tool.endswith("engage") else ("reflect" if tool.endswith("reflect") else tool[-40:])
    rs.append_jsonl(_ledger_path(session_id), {
        "kind": "tool",
        "session_id": session_id,
        "tool": short,
        "prompt_id": str(payload.get("prompt_id") or ""),
        "ts": int(time.time()),
    })


def main() -> int:
    try:
        if rs.off() or not rs.gate("KUMIHO_MEMORY_REFLEX"):
            return 0
        payload = _read_stdin()
        session_id = rs.safe_id(payload.get("session_id"))
        if not session_id:
            return 0
        # Dispatch on hook_event_name ONLY. Never on agent_id: it is a base field
        # present on every payload, so branching on it silently zeroes the hook.
        # Subagent turns fire SubagentStop, which we deliberately do not observe.
        event = str(payload.get("hook_event_name") or "")
        if event == "Stop":
            _on_stop(payload, session_id)
            if not payload.get("stop_hook_active"):
                _spawn_prefetch(payload, session_id)
        elif event == "PostToolUse":
            _on_tool(payload, session_id)
    except BaseException:  # noqa: BLE001 - observation must never fail a session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
