# Kumiho Memory Protocol (Codex)

You have persistent graph-native memory via the `kumiho-memory` MCP server.
You remember across sessions. Follow this protocol every session.

## Two reflexes

**Engage — before you respond.** When the user's message touches anything
that might have history, call `kumiho_memory_engage` with a query derived
from their current message (at most once per turn; the server deduplicates
within 5 seconds). Never say "I don't know" without engaging first. Hold
the returned `source_krefs` for reflect.

**Reflect — after you respond.** After a substantive response, call
`kumiho_memory_reflect` with your response text and structured captures
(decisions, preferences, facts, corrections). Use absolute dates in
capture titles ("on Jul 11", never "today"). Skip captures for trivial
exchanges; pass `source_krefs` from engage for provenance.

## Session id — you must supply it (Codex)

Codex publishes no session identity to MCP servers, so `kumiho-memory`
>=1.2.0 cannot resolve an omitted `session_id` and **refuses the call**
rather than guessing. Derive ONE stable id at the start of the session —
`{repo}-{YYYY-MM-DD}` is a good shape — and pass that same value on every
memory call that accepts it (`kumiho_memory_engage`,
`kumiho_memory_reflect`, `kumiho_memory_consolidate`, `kumiho_chat_*`).

A fresh id per call scatters one conversation across as many
working-memory buckets as you invent. If a result reports
`created_bucket=true` on a turn that is not the first, you changed the id.

## Decision Memory (code work)

**Before modifying unfamiliar code, ask `kumiho_code_why` for the file
first** — prior decisions, their rationale, verbatim evidence, and whether
they were later reversed (`superseded_by`) come back in one call. Never
re-litigate a decision the graph already explains; if you change it
anyway, say why, and write the new rationale into your commit message —
the post-commit capture hook mines it back into the graph automatically.

To backfill a repo's history: `kumiho_code_ingest` (idempotent; re-runs
skip captured commits at zero LLM cost).

## Rules

- Reference memories naturally ("Since you prefer gRPC...") — never
  narrate the plumbing ("Let me search my memory...").
- Do not re-ask questions already answered this session; do not re-run
  completed work.
- Respect "forget X" immediately via `kumiho_deprecate_item`.
- Compare each memory's `created_at` to today's date; prefer recent
  memories when they conflict with stale ones.
- After 20+ exchanges or at session end, call
  `kumiho_memory_consolidate` with the session id you have been using all
  along (see "Session id" above).
