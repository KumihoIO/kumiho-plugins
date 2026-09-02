#!/usr/bin/env python3
"""SessionStart hook — inject memory skill invocation instruction.

This hook fires at the beginning of every session (Claude Code or
Cowork) and injects additionalContext that tells Claude to invoke the
kumiho-memory skill before responding to the user.  Without this hook
the skill's SKILL.md content is never loaded into context and the
memory bootstrap cannot run.

The context also reminds Claude about the recall-before-respond rule
so it persists across the full session.

It ALSO persists the host-provided session facts to
``<state>/reflex/<session_id>.session.json``.  Claude Code hands every hook
``session_id``, ``source``, ``cwd`` and ``transcript_path`` on stdin; this hook
used to discard all four, which is why nothing downstream could name the session
it was in -- the model was left to guess a session_id it has no channel to learn.
Sibling hooks (``save-session-artifact.py``, ``code-capture-hook.py``) already
read the same payload.

Run: fed a JSON hook payload on stdin by Claude Code; prints one
``hookSpecificOutput`` envelope on stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CONTEXT = (
    "SESSION-START INSTRUCTION (kumiho-memory plugin)\n"
    "\n"
    "=== EVERY TURN AFTER THE FIRST ===\n"
    "The bootstrap is DONE.  On turn 2 and beyond, follow ONLY these "
    "rules:\n"
    "  - You MAY consult the kumiho-memory skill if the protocol is "
    "unclear.  Do not re-run bootstrap.\n"
    "  - Do NOT repeat the identity lookup (kumiho_get_revision_by_tag) "
    "or the onboarding once either has completed.  If onboarding is "
    "still waiting on the user's answers, finish it before anything "
    "else.\n"
    "  - Do NOT greet the user unless they greeted you first.  If their "
    "message is a question or task, answer directly.\n"
    "  - TWO REFLEXES — Use kumiho_memory_engage (before responding) and "
    "kumiho_memory_reflect (after responding).  At most one engage per "
    "response.  The server deduplicates within 5 seconds.\n"
    "  - ENGAGE: Call kumiho_memory_engage ONCE if the topic might have "
    "history.  Your query MUST derive from the user's current message.  "
    "Hold the returned source_krefs for reflect.\n"
    "  - REFLECT: After a substantive response, call "
    "kumiho_memory_reflect with your response text and any structured "
    "captures (decisions, preferences, facts, corrections).  This "
    "buffers your response AND stores captures with provenance links.  "
    "Skip captures for trivial exchanges.\n"
    "  - ROUTE EVERY CAPTURE — give each capture a space_hint.  An "
    "unrouted capture is filed at the project root, and reflect's "
    "automatic revision stacking then searches that whole bucket for "
    "something to stack onto, which fuses unrelated memories.  Reuse a "
    "space name you saw in an engage kref "
    "(kref://<project>/<space>/<item>.<kind>); fall back to the capture "
    "type (decisions, facts, preferences, corrections).\n"
    "__SESSION_ID_RULE__"
    "  - EXPLICIT REMEMBER REQUESTS — When the user says 'remember "
    "this', 'keep this in mind', 'note that', or similar, you MUST "
    "capture it via kumiho_memory_reflect.  Do NOT rely on Claude's "
    "auto-memory — Kumiho MCP tools are the canonical memory store.\n"
    "  - Do NOT narrate memory operations.\n"
    "  - Do NOT repeat content you already showed the user.  Refer to "
    "it briefly (e.g. 'the draft above') instead of reproducing it.\n"
    "  - Do NOT re-ask questions already answered in this conversation.\n"
    "  - Do NOT re-execute tasks already completed.\n"
    "  - If you need user input, ask and STOP.  Never simulate the "
    "user's answer.\n"
    "\n"
    "=== FIRST MESSAGE ONLY ===\n"
    "Skip this block on all subsequent messages.\n"
    "  1. Invoke the kumiho-memory:kumiho-memory skill.\n"
    "  2. LOAD IDENTITY — kumiho_get_revision_by_tag(item_kref="
    "\"kref://CognitiveMemory/agent.instruction\", tag=\"published\").  "
    "If not found (or the kref is rejected), retry ONCE with "
    "item_kref=\"kref://CognitiveMemory/personal/agent.instruction\" — "
    "self-hosted CE resolves only the space-qualified form.  Adopt the "
    "metadata of whichever resolved.\n"
    "  3. NOT FOUND ON BOTH = FIRST MEETING.  Before answering, run the "
    "onboarding in the skill's references/onboarding.md: ask the "
    "identity questions with AskUserQuestion (plain chat if unavailable), "
    "STOP and wait for the answers, then persist them (project and "
    "'personal' space if missing, agent.instruction item under "
    "CognitiveMemory/personal, revision, 'published' tag).  Never skip "
    "it, never invent answers, never answer as if an identity were "
    "loaded.  An auth or connection error is NOT a first meeting: say "
    "memory is not connected (run /kumiho-onboard) and continue without "
    "memory.\n"
    "  4. Call kumiho_memory_engage ONCE with a broad query.\n"
    "  5. Only greet if the user's message is itself a greeting (hi, hey, "
    "good morning, etc.).  If they open with a question or task, skip "
    "the greeting and answer directly.  Never narrate the bootstrap "
    "(no 'Memory connected!' or similar).\n"
    "\n"
    "=== ALWAYS ===\n"
    "TEMPORAL AWARENESS — When using engage results, compare each "
    "result's created_at against today's date and the user's timezone.  "
    "Express memory age naturally ('earlier today', 'yesterday', "
    "'last Tuesday', 'about two weeks ago').  Recent memories take "
    "precedence over stale ones when they conflict.  When capturing "
    "memories via reflect, always use absolute dates in titles "
    "('on Feb 24', not 'today') — relative time becomes meaningless "
    "when recalled in a future session.\n"
    "\n"
    "STORE COMPACT SUMMARIES — When context is compacted (/compact or "
    "auto-compression), capture the summary via kumiho_memory_reflect "
    "with a capture of type='summary' and tags ['compact', "
    "'session-context'].\n"
    "\n"
    "SKILL DISCOVERY — When you need specialized behavioral guidance "
    "(creative output tracking, graph traversal, privacy rules, session "
    "management) beyond what the SKILL.md provides inline, search for "
    "skills: kumiho_memory_engage with query about what you need and "
    "space_paths=['CognitiveMemory/Skills'].  Cache discovered skills "
    "in your working context for the rest of the session."
)

#: Used when the host handed us the real session_id on stdin -- which is every
#: Claude Code session, per the hook contract.
#:
#: Why hand the model the id at all, when the package's whole point is that
#: ``session_id`` is optional? Because the env tier it would otherwise resolve
#: through does not exist for a large part of the install base. Claude Desktop
#: spawns ONE MCP server from ``claude_desktop_config.json`` at app start; it
#: has no CLAUDE_CODE_SESSION_ID to inherit, so ``_normalize_host_session_id``
#: has nothing to publish, and that single long-lived process is shared by every
#: conversation afterwards -- no env var can name a per-conversation session. On
#: 2026-07-31 a reflect on that path failed outright with "no session identity
#: available". The SessionStart hook is the only per-session channel there is,
#: and ``memory-reflex._subagent_card`` already uses it for exactly this reason.
#:
#: This is not the fabrication the omit-convention was written against: the id
#: below came from the host on stdin, is the top resolution tier (explicit arg),
#: and is echoed back as session_id_source="argument" for verification.
_SESSION_ID_RULE_KNOWN = (
    "  - SESSION ID — whenever a memory tool accepts a session_id (reflect, "
    "consolidate, and the chat/ingest tools), pass session_id=%s.  Tools "
    "without that parameter — engage among them — take no session_id at "
    "all; adding one is an input error.  This is the real id, handed to "
    "the plugin by the host; do NOT invent one and do NOT substitute a "
    "different value.  Results echo back the "
    "session_id used plus a session_id_source (expect \"argument\"); if a "
    "result reports created_bucket=true on a turn that is not the first, "
    "say so — the call addressed a session that did not exist.\n"
)

#: Fallback for hosts that hand the hook no session_id. Then the model genuinely
#: has no channel to learn one, and inventing a value fragments the buffer --
#: so it must let the server resolve and report instead.
_SESSION_ID_RULE_UNKNOWN = (
    "  - SESSION ID — OMIT it on every memory tool call.  The server "
    "resolves the session and reports the session_id it used plus a "
    "session_id_source; a value you invent fragments the conversation "
    "buffer.  If a result reports created_bucket=true on a turn that is "
    "not the first, say so — the call addressed a session that did not "
    "exist.  If a call reports that no session identity is available, "
    "tell the user instead of inventing one.\n"
)


def _context(session_id: str) -> str:
    """The session card, with the session-id rule the host's payload supports."""
    rule = (_SESSION_ID_RULE_KNOWN % session_id) if session_id else _SESSION_ID_RULE_UNKNOWN
    return CONTEXT.replace("__SESSION_ID_RULE__", rule)

def _state_dir() -> Path:
    """Mirror ``run_kumiho_mcp._state_dir`` -- duplicated, like
    ``code_capture_pending._state_dir``, so this hook stays import-free and fast
    on the session-start critical path."""
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def _read_hook_input() -> dict:
    """Most-defensive stdin read (the idiom from save-session-artifact.py):
    blank or unparseable input degrades to {}, never raises."""
    # Explicit UTF-8: the hook wire format is UTF-8, but sys.stdin on a Windows
    # pipe decodes with the ambient codepage (cp949) and surrogateescape.
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


def _session_id(payload: dict) -> str:
    """The host's session id, or "" if it is absent or unusable.

    It becomes a filename AND is interpolated into the instruction card, so it
    is rejected on both counts: path separators (never trust it as a path
    component) and control characters. A newline would let the value close the
    SESSION ID bullet and open forged ones -- Claude Code sends a uuid and never
    that, so this is a guard on the channel rather than a fix for a live bug.
    """
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return ""
    if any(c in session_id for c in '\\/:*?"<>|'):
        return ""
    if any(c < " " or c == "\x7f" for c in session_id):
        return ""
    return session_id


def _persist_session(payload: dict) -> None:
    """Record the host-provided session facts for downstream reflex components.

    Best-effort by construction: SessionStart must never fail a session, so every
    error is swallowed. Writing nothing simply leaves downstream code to fall back
    to its own resolution."""
    session_id = _session_id(payload)
    if not session_id:
        return
    try:
        d = _state_dir() / "reflex"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.session.json" % session_id)).write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "source": str(payload.get("source") or ""),
                    "cwd": str(payload.get("cwd") or ""),
                    "transcript_path": str(payload.get("transcript_path") or ""),
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        pass


def _repair_stale_desktop_entry() -> None:
    """Hand the CURRENT launcher a chance to fix Desktop's server entry.

    The launcher self-heals that config, but the check runs inside whichever
    launcher the config already names -- so once the entry goes stale, the only
    code that could repair it is the stale code, and a fix shipped later never
    runs. Hit four times on one machine in a day. This hook is the way out: the
    host substitutes CLAUDE_PLUGIN_ROOT from the INSTALLED plugin, so it is
    always the current version regardless of what the config says.

    It spawns unconditionally rather than checking first. An earlier version
    read the config here and only spawned on a mismatch, which meant this file
    had to know where the config lives -- and it got that wrong: it missed
    XDG_CONFIG_HOME (CI caught it) and the Windows MSIX location, so the hook
    would see drift the launcher then repaired somewhere else, or miss drift
    entirely. Two implementations of one path list is the same drift class this
    function exists to fix. The launcher owns the paths; the child is detached
    and exits immediately when nothing is wrong.
    """
    root = (os.getenv("CLAUDE_PLUGIN_ROOT", "") or "").strip()
    if not root or "${" in root:
        return
    launcher = Path(root) / "scripts" / "run_kumiho_mcp.py"
    if not launcher.is_file():
        return
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, str(launcher), "--repair-desktop-entry"],
                         **kwargs)
    except OSError:
        pass  # best-effort; SessionStart must never fail a session


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass  # piped children report cp949 on Windows; best-effort
    payload = _read_hook_input()
    _persist_session(payload)
    _repair_stale_desktop_entry()
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": _context(_session_id(payload)),
                }
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
