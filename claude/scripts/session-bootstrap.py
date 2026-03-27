#!/usr/bin/env python3
"""SessionStart hook — inject memory skill invocation instruction.

This hook fires at the beginning of every session (Claude Code or
Cowork) and injects additionalContext that tells Claude to invoke the
kumiho-memory skill before responding to the user.  Without this hook
the skill's SKILL.md content is never loaded into context and the
memory bootstrap cannot run.

The context also reminds Claude about the recall-before-respond rule
so it persists across the full session.
"""

from __future__ import annotations

import json
import sys

CONTEXT = (
    "SESSION-START INSTRUCTION (kumiho-memory plugin)\n"
    "\n"
    "=== EVERY TURN AFTER THE FIRST ===\n"
    "The bootstrap is DONE.  On turn 2 and beyond, follow ONLY these "
    "rules:\n"
    "  - Do NOT invoke the kumiho-memory skill.\n"
    "  - Do NOT call kumiho_get_revision_by_tag.  Identity is already "
    "loaded.\n"
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
    "  2. Call kumiho_memory_engage ONCE with a broad query.\n"
    "  3. Only greet if the user's message is itself a greeting (hi, hey, "
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

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": CONTEXT,
            }
        }
    )
)
sys.exit(0)
