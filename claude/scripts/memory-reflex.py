#!/usr/bin/env python3
"""UserPromptSubmit hook: inject recalled memory before the model answers.

This is the one hook on the critical path, so it does almost nothing: reads two
small JSON files, tails a bounded ledger, writes one small JSON file, prints one
envelope. **No network. No venv. No launcher import. No subprocess.** The
expensive part -- actually calling the memory backend -- happened after the
PREVIOUS turn ended, in a detached worker (``reflex_prefetch_worker.py``), during
the user's thinking time.

That inversion is the whole point. Claude Code's own memory never asks the model
to call a recall tool: relevant context is injected and the model simply has it.
Recall here works the same way, so a turn where the model is absorbed in another
task still gets its memories.

The print happens BEFORE any other side effect, deliberately: an exception in
bookkeeping must never cost the injection it was bookkeeping for.

Run: fed a JSON hook payload on stdin by Claude Code; prints at most one
``hookSpecificOutput`` envelope on stdout. Pass ``--subagent`` for SubagentStart.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reflex_state as rs  # noqa: E402

_DEFAULT_TTL_S = 900
_DEFAULT_FLOOR = 3
_DEFAULT_BUDGET_CHARS = 6000
_FLOOR_COOLDOWN_TURNS = 5
_DEFAULT_CONSOLIDATE_FLOOR = 20
_CONSOLIDATE_COOLDOWN_TURNS = 5
_QUEUE_COOLDOWN_TURNS = 20
_QUEUE_MIN_PENDING = 10
# The recall query is capped at 200 chars anyway, so storing more prompt than
# this buys nothing and only lengthens what sits on disk.
_PROMPT_MAX_CHARS = 2000


def _int_env(name: str, default: int) -> int:
    """Via reflex_state.conf so .mcp.json-declared values actually reach this
    hook; a real process env var still wins."""
    return rs.conf_int(name, default)


def _read_stdin() -> dict:
    # Explicit UTF-8: sys.stdin on a Windows pipe uses the ambient codepage
    # (cp949) with surrogateescape, which silently mojibakes the payload and
    # persists a corrupted prompt into turn.json -- which is exactly what the
    # prefetch worker builds its recall query from.
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


def _emit(event: str, context: str) -> None:
    """Exactly one top-level key.

    The CLI strips unrecognized top-level keys with a "Did you mean
    hookSpecificOutput.additionalContext (with a hookEventName)?" warning, so a
    drifted envelope injects NOTHING while looking like it worked. Pinned by test.
    """
    sys.stdout.write(json.dumps(
        {"hookSpecificOutput": {"hookEventName": event,
                                "additionalContext": context}},
        ensure_ascii=True,
    ))


def _turns_since_reflect(ledger_path) -> int:
    """Count completed turns since the last reflect, from the observation ledger.

    Counted BEFORE the current turn is appended, so the number names what already
    happened rather than including the turn being started."""
    n = 0
    for line in reversed(rs.tail_lines(ledger_path, max_lines=200)):
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("kind") == "tool" and row.get("tool") == "reflect":
            break
        if row.get("kind") == "stop" and not row.get("tool_only"):
            n += 1
    return n


def _turns_since_consolidate(ledger_path) -> int:
    """Completed, non-tool-only turns since the last SUCCESSFUL consolidate.

    A consolidate that came back success:false left the buffer full, so it
    does not reset the count (the observer stamps ``ok`` on the row). No
    consolidate in the ledger means the count runs from the first turn the
    ledger saw: the buffer has never been drained this session."""
    n = 0
    for line in reversed(rs.tail_lines(ledger_path, max_lines=400)):
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("kind") == "tool" and row.get("tool") == "consolidate" and row.get("ok", True):
            break
        if row.get("kind") == "stop" and not row.get("tool_only"):
            n += 1
    return n


def _idle_expiry_phrase() -> str:
    """'an hour', '24 hours', '90 minutes': the buffer idle TTL as configured.

    The launcher resolves KUMIHO_WORKING_MEMORY_TTL into the reflex snapshot
    (86400 in CE mode), and the consolidation line must name that, not a
    hard-coded hour."""
    ttl = _int_env("KUMIHO_WORKING_MEMORY_TTL", 3600)
    if ttl <= 0:
        ttl = 3600
    if ttl % 3600 == 0:
        hours = ttl // 3600
        return "an hour" if hours == 1 else "%d hours" % hours
    if ttl % 60 == 0:
        return "%d minutes" % (ttl // 60)
    return "%d seconds" % ttl


def _consolidate_line(n: int, floor: int, session_id: str) -> str:
    """The keyless consolidation instruction.

    Consolidation used to be an adjective ("after 20+ exchanges") with no
    counter behind it, and the tool hard-failed keyless anyway. kumiho-memory
    now takes an agent-written ``summary`` and skips its summarizer, so the
    floor is a counted fact and the line carries the one thing the model must
    not get wrong: the summary is ITS job (or a subagent's), never an external
    LLM's."""
    return (
        "Completed turns since this session was last consolidated: %d (floor %d). "
        "Consolidate now, keyless: write the session summary yourself from the "
        "conversation, or delegate it to a subagent (Agent tool, e.g. model "
        "sonnet) fed the transcript from kumiho_chat_get(session_id=%s, "
        "limit=1000). Then "
        "call kumiho_memory_consolidate(session_id=%s, summary={title, summary, "
        "events, knowledge: {facts, decisions, actions, open_questions}, "
        "classification: {topics, entities}}, implications=[...]) -- only "
        "summary is required. Never call it without summary: that path needs an "
        "external LLM and fails keyless. The working memory expires after %s "
        "idle, so do not defer this past a long pause."
        % (n, floor, session_id, session_id, _idle_expiry_phrase())
    )


def _pending_count() -> int:
    """Depth of the keyless capture queue. Bounded at 200 by the queue itself,
    so counting lines is cheap enough for the critical path."""
    try:
        p = rs.state_dir() / "pending-code-captures.jsonl"
        if not p.exists():
            return 0
        return sum(1 for ln in rs.tail_lines(p, max_lines=1000) if ln.strip())
    except OSError:
        return 0


def _subagent_card(session_id: str) -> str:
    """Subagents inherit every memory tool and none of the protocol."""
    return (
        "KUMIHO MEMORY (subagent)\n"
        "- Recall is injected host-side in the PARENT session, not here. Do not "
        "call kumiho_memory_engage speculatively.\n"
        "- If your work settles a decision, preference, fact or correction, call "
        "kumiho_memory_reflect with typed captures before you finish.\n"
        # Pass it, do not omit it. Omitting resolves through KUMIHO_SESSION_ID in
        # the MCP server's env, which goes stale after /clear (Claude Code rotates
        # the session id without respawning the server), so captures would land in
        # the PREVIOUS conversation's bucket. This card holds the live id.
        "- Pass session_id=%s explicitly wherever a memory tool accepts one "
        "(reflect, consolidate, chat/ingest). Do not omit it here, do not "
        "invent one, and do not add it to tools that take no session_id "
        "(engage among them) — that is an input error.\n"
        "- Do not narrate memory operations." % session_id
    )


def main(argv: list) -> int:
    try:
        if rs.off() or not rs.gate("KUMIHO_REFLEX"):
            return 0
        payload = _read_stdin()
        event = str(payload.get("hook_event_name") or "")
        session_id = rs.safe_id(payload.get("session_id"))
        if not session_id:
            return 0

        if "--subagent" in argv or event == "SubagentStart":
            _emit("SubagentStart", _subagent_card(session_id))
            return 0
        if event and event != "UserPromptSubmit":
            return 0

        d = rs.reflex_dir()
        turn_path = d / ("%s.turn.json" % session_id)
        turn = rs.read_json(turn_path, {}) or {}
        now = int(time.time())
        parts = []

        # --- recalled memories ------------------------------------------------
        # Served only from cache. No cold-start poll: a debounced producer may
        # legitimately never have written, and blocking to wait for a file that
        # may never appear is how a recall feature becomes a latency bug.
        cache = rs.read_json(d / ("%s.recall.json" % session_id), {}) or {}
        block = str(cache.get("block") or "")
        sha = str(cache.get("content_sha12") or "")
        generated_at = cache.get("generated_at") or 0
        fresh = block and (now - int(generated_at)) < _int_env("KUMIHO_REFLEX_TTL_S", _DEFAULT_TTL_S)

        budget = _int_env("KUMIHO_REFLEX_SESSION_BUDGET_CHARS", _DEFAULT_BUDGET_CHARS)
        spent = int(turn.get("injected_chars") or 0)
        # Cross-turn dedup is mandatory, not an optimisation: additionalContext is
        # pushed into message history and re-sent on every later request, so
        # re-emitting the same block would accrue duplicate tokens every turn and
        # accelerate the very compaction this exists to survive.
        if fresh and sha and sha != str(turn.get("last_sha") or "") and spent + len(block) <= budget:
            parts.append(block)
            turn["last_sha"] = sha
            spent += len(block)

        # --- the floor: a counted fact, never an adjective --------------------
        n_turn = int(turn.get("n") or 0) + 1
        floor = _int_env("KUMIHO_REFLEX_FLOOR", _DEFAULT_FLOOR)
        n_since = _turns_since_reflect(d / ("%s.turns.jsonl" % session_id))
        last_floor = int(turn.get("last_floor_turn") or -99)
        if n_since >= floor and (n_turn - last_floor) >= _FLOOR_COOLDOWN_TURNS:
            parts.append(
                "Turns since your last kumiho_memory_reflect: %d. session_id=%s. "
                "If a decision, preference, fact or correction landed, call "
                "kumiho_memory_reflect with typed captures and pass any Kref "
                "values above as source_krefs. Otherwise ignore this line."
                % (n_since, session_id))
            turn["last_floor_turn"] = n_turn

        # --- consolidation floor: keyless, counted from the same ledger -------
        floor_c = _int_env("KUMIHO_REFLEX_CONSOLIDATE_FLOOR", _DEFAULT_CONSOLIDATE_FLOOR)
        last_c = int(turn.get("last_consolidate_turn") or -99)
        if floor_c > 0 and (n_turn - last_c) >= _CONSOLIDATE_COOLDOWN_TURNS:
            n_c = _turns_since_consolidate(d / ("%s.turns.jsonl" % session_id))
            if n_c >= floor_c:
                parts.append(_consolidate_line(n_c, floor_c, session_id))
                turn["last_consolidate_turn"] = n_turn

        # --- pending keyless capture queue ------------------------------------
        last_q = int(turn.get("last_queue_turn") or -99)
        if (n_turn - last_q) >= _QUEUE_COOLDOWN_TURNS:
            pending = _pending_count()
            if pending >= _QUEUE_MIN_PENDING:
                # Absolute paths, because $CLAUDE_PLUGIN_ROOT is empty in the
                # agent's shell -- which is why the drain command documented in
                # SKILL.md never actually ran.
                queue_cmd = "%s -I %s --claude-host" % (
                    shlex.quote(sys.executable),
                    shlex.quote(str(
                        Path(__file__).resolve().parent / "code_capture_pending.py"
                    )))
                parts.append(
                    "%d commits are queued for keyless Decision Memory capture. "
                    "To drain: %s list. After each captured or intentionally "
                    "skipped entry: %s done <commit>" % (
                        pending, queue_cmd, queue_cmd
                    ))
                turn["last_queue_turn"] = n_turn

        # Print FIRST. Bookkeeping below must never cost the injection.
        if parts:
            _emit("UserPromptSubmit", "\n\n".join(parts))

        # The prompt is stored ONLY because the prefetch worker builds the recall
        # query from it on the next Stop -- minutes later at most. Everywhere else
        # this design records a hash and a length rather than text (see the
        # observation ledger), so a verbatim prompt sitting on disk until prune()
        # is an inconsistency, not a feature. It is therefore capped, and can be
        # turned off entirely: with KUMIHO_REFLEX_STORE_PROMPT=0 the worker falls
        # back to the transcript tail it already reads for prior-turn context, so
        # recall degrades in quality rather than breaking.
        turn.update({
            "n": n_turn,
            "injected_chars": spent,
            "prompt": (str(payload.get("prompt") or "")[:_PROMPT_MAX_CHARS]
                       if rs.gate("KUMIHO_REFLEX_STORE_PROMPT") else ""),
            "prompt_id": str(payload.get("prompt_id") or ""),
            "cwd": str(payload.get("cwd") or ""),
            "ts": now,
        })
        rs.write_json_atomic(turn_path, turn)
    except BaseException:  # noqa: BLE001 - never fail or stall a turn
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
