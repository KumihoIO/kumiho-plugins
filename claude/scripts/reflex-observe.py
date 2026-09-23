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
  PostToolUse   -> one {"kind":"tool"} line per engage/reflect/consolidate call
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
    # Decode the payload as UTF-8 EXPLICITLY. The hook wire format is UTF-8
    # regardless of locale, but sys.stdin on a Windows pipe uses the ambient
    # codepage (cp949 here) with surrogateescape -- which does not raise, so
    # json.loads happily returns mojibake carrying LONE SURROGATES that blow up
    # on the next .encode("utf-8"). One em dash was enough to silently kill both
    # the ledger line and the prefetch spawn. errors="replace" keeps it fail-open
    # and, unlike surrogateescape, can never emit a surrogate downstream.
    try:
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read().decode("utf-8", "replace") if buf else sys.stdin.read()
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
        # errors="replace": a lone surrogate from a mis-decoded payload must not
        # raise here -- hashing is bookkeeping, and it once took the whole hook down.
        "resp_sha12": hashlib.sha256(stripped.encode("utf-8", "replace")).hexdigest()[:12] if stripped else "",
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
              "stderr": subprocess.DEVNULL, "cwd": cwd,
              "env": rs.secured_hook_child_env()}
    if os.name == "nt":
        kwargs["creationflags"] = 0x8 | 0x200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, "-I", str(worker), cwd, session_id], **kwargs
        )
    except OSError as exc:
        rs.log("prefetch spawn failed: %s" % exc)


def _tool_ok(payload: dict) -> bool:
    """Best-effort success flag from the tool response; unknown counts as ok.

    Only the consolidate floor reads it: a consolidate that answered
    success:false left the buffer full, and resetting the count on it would
    silence the nudge for another full floor -- the silent failure the ledger
    exists to surface. No response text is stored, only the flag."""
    resp = payload.get("tool_response")
    if resp is None:
        return True  # nothing reached the hook: unknown, not a failure
    try:
        text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=True)
    except (TypeError, ValueError):
        return True
    low = text.lower()
    # POSITIVE detection: a consolidate that stored something always answers
    # success:true. success:false, a bare {"error": ...} from an exception,
    # or an MCP error envelope all lack it, and none of those drained the
    # buffer -- so none of them may reset the floor.
    return any(m in low for m in (
        '"success": true', '"success":true',
        '\\"success\\": true', '\\"success\\":true',
    ))


def _result_dicts(value, depth: int = 0) -> list:
    """Every JSON object in a tool response, whatever envelope carried it.

    MCP results reach the hook as a dict, a content-block list, or JSON text
    inside a text block, depending on the host version; walking them all keeps
    the check shape-agnostic, the way _tool_ok's substring search is."""
    if depth > 4:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, list):
        return [d for item in value[:32] for d in _result_dicts(item, depth + 1)]
    if not isinstance(value, dict):
        return []
    found = [value]
    for key in ("content", "text", "structuredContent", "result"):
        if key in value:
            found += _result_dicts(value[key], depth + 1)
    return found


def _reflect_ok(payload: dict) -> bool:
    """Did the reflect return a receipt for every capture it was given?

    Mirrors the Codex lifecycle receipt check: an error envelope, a queued or
    backend-errored write, or fewer stored krefs than captures is not a reflect,
    so it must not reset the reflect floor. A response the hook never saw is
    unknown, not a failure, same as _tool_ok."""
    resp = payload.get("tool_response")
    if resp is None:
        return True
    rows = _result_dicts(resp)
    if any(d.get("isError") is True or d.get("is_error") is True for d in rows):
        return False
    receipts = [d for d in rows if "captures_stored" in d or "buffered" in d]
    if not receipts:
        return False
    receipt = receipts[0]
    if (receipt.get("error") or receipt.get("backend_error")
            or receipt.get("queued") is True or receipt.get("success") is False):
        return False
    args = payload.get("tool_input")
    captures = args.get("captures") if isinstance(args, dict) else None
    if isinstance(captures, list):
        refs = receipt.get("stored_krefs")
        return (receipt.get("captures_stored") == len(captures)
                and isinstance(refs, list) and len(refs) == len(captures))
    return True


def _on_tool(payload: dict, session_id: str) -> None:
    tool = str(payload.get("tool_name") or "")
    if not tool:
        return
    short = tool[-40:]
    for name in ("engage", "reflect", "consolidate"):
        if tool.endswith(name):
            short = name
            break
    entry = {
        "kind": "tool",
        "session_id": session_id,
        "tool": short,
        "prompt_id": str(payload.get("prompt_id") or ""),
        "ts": int(time.time()),
    }
    if short == "consolidate":
        entry["ok"] = _tool_ok(payload)
    elif short == "reflect":
        entry["ok"] = _reflect_ok(payload)
    rs.append_jsonl(_ledger_path(session_id), entry)


def main() -> int:
    try:
        # Same name memory-reflex.py gates on, and the one declared in .mcp.json.
        # These used to differ (KUMIHO_MEMORY_REFLEX here, declared nowhere), so
        # KUMIHO_REFLEX=0 stopped injection while the observer and the detached
        # worker kept running.
        if rs.off() or not rs.gate("KUMIHO_REFLEX"):
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
            # Spawn FIRST: it is the load-bearing side effect (no worker -> no
            # recall cache -> nothing to inject next turn). Bookkeeping gets its
            # own handler so a ledger failure can never cancel it -- these used to
            # share one try/except, so one bad byte cost both.
            if not payload.get("stop_hook_active"):
                _spawn_prefetch(payload, session_id)
            try:
                _on_stop(payload, session_id)
            except BaseException:  # noqa: BLE001
                pass
        elif event == "PostToolUse":
            _on_tool(payload, session_id)
    except BaseException:  # noqa: BLE001 - observation must never fail a session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
