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
    "  - You MAY call kumiho_memory_recall ONCE if the topic might "
    "have history.  One call per response, maximum.  The server "
    "returns empty for duplicates within 5 seconds.  Your query MUST "
    "match the user's current message.\n"
    "  - Answer the user's actual question first.  Only surface "
    "recalled memories if directly relevant.\n"
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
    "  2. Call kumiho_memory_recall ONCE with a broad query.\n"
    "  3. Only greet if the user's message is itself a greeting (hi, hey, "
    "good morning, etc.).  If they open with a question or task, skip "
    "the greeting and answer directly.  Never narrate the bootstrap "
    "(no 'Memory connected!' or similar).\n"
    "\n"
    "=== ALWAYS ===\n"
    "TEMPORAL AWARENESS — When using recall results, compare each "
    "result's created_at against today's date and the user's timezone.  "
    "Express memory age naturally ('earlier today', 'yesterday', "
    "'last Tuesday', 'about two weeks ago').  Recent memories take "
    "precedence over stale ones when they conflict.  When STORING "
    "memories, always use absolute dates in summaries and titles "
    "('on Feb 24', not 'today') — relative time becomes meaningless "
    "when recalled in a future session.\n"
    "\n"
    "BUFFER SUBSTANTIVE RESPONSES — After generating a substantive "
    "response (drafts, analyses, plans, decisions, creative output, "
    "anything longer than a few sentences), call "
    "kumiho_memory_add_response with your reply text.  This keeps the "
    "session buffer current for consolidation.  Skip only for trivial "
    "acknowledgements.\n"
    "\n"
    "STORE COMPACT SUMMARIES — When context is compacted (/compact or "
    "auto-compression), store the compact summary via kumiho_memory_store "
    "with memory_type='summary' and tags ['compact', 'session-context'], "
    "then call kumiho_memory_discover_edges on the result.\n"
    "\n"
    "STORE & LINK — When storing any memory via kumiho_memory_store, "
    "pass relevant recall krefs as source_revision_krefs, then call "
    "kumiho_memory_discover_edges on the returned revision_kref."
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
