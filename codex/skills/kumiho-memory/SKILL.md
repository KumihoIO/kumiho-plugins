---
name: kumiho-memory
description: Persistent graph-native memory protocol — identity bootstrap, engage before responding, reflect after, Decision Memory for code work, typed-ontology decomposition, and session consolidation. Applies to every session; trigger whenever the user's topic might have history.
---

# Kumiho Memory Protocol (Codex)

You have persistent graph-native memory via the `kumiho-memory` MCP server.
You remember across sessions. Follow this protocol every session.

## Session bootstrap — once

On the first user message of a new session, follow
[references/bootstrap.md](references/bootstrap.md) before the normal reflexes.
It loads the published identity or, only when both supported identity krefs are
not found, runs the first-meeting flow in
[references/onboarding.md](references/onboarding.md). Authentication and
connection failures are not a first meeting.

## Absolute secret exclusion

Never store, reflect, decompose, ingest, or repeat passwords, API tokens,
refresh tokens, private keys, session cookies, connection strings containing
credentials, or raw secret-bearing environment/config values. This overrides
every capture rule below, including “every answer you had to ask for.” Never
ask for a secret in chat. If one appears, omit it from all memory calls and
recommend rotating it.

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

Give every capture a `space_hint`. Without one it is filed at the project
root, and reflect's automatic revision stacking then searches that whole
bucket for something to stack onto — that is how an unrelated capture
becomes a new revision of a months-old item. Reuse a space name you have
already seen in an engage kref (`kref://<project>/<space>/<item>.<kind>`),
copied exactly: reflect forwards the hint as a space path verbatim, so
`Skills` and `skills` are two different spaces.
Fall back to the capture type (`decisions`, `facts`, `preferences`,
`corrections`) when none fits.

## Every answer you had to ask for gets stored

If you had to ask the user, the answer was not in the code, the docs or
the git history — which makes it the most valuable thing in the session
and the most expensive to lose. So:

**Capture it, always, except secrets.** Every non-secret question you put to the user — a structured
prompt or a line of prose — gets its answer stored via
`kumiho_memory_reflect` as `type: "decision"`, in the same turn you
receive it. Not "if it seems substantive": you asked because you could
not derive it, and that is the whole test.

**Keep the question with the answer.** "Postgres" is not a memory; "Asked
whether the queue should back onto Postgres or Redis for the ingest path;
chose Postgres — one datastore to operate" is. Put the question in the
`content` (and in the `title`, as the choice it settled), or a later
session recalls a word with nothing to attach it to.

**Route it.** Give the capture a `space_hint` naming the topic it belongs
to — `architecture`, `deployment`, the space you saw in an engage kref —
so it lands where the next session will look, not at the project root
with everything else. `decisions` when nothing better fits.

**Recall before you ask.** If a question could plausibly have been settled
before, `kumiho_memory_engage` on it first and use what comes back —
naturally, as something you know ("you had this on Postgres for the ingest
path") — rather than asking again. Confirm a stale or uncertain answer
instead of re-opening it from scratch; re-asking a question the graph
already answers is a memory failure, not diligence.

## Session id — owned by Codex, never invented by the agent

Codex attaches its stable thread id to every MCP tool request in per-call
`_meta`. The plugin's stdio bridge recognizes the supported Codex metadata
spellings and carries that value into a
request-scoped Kumiho host context for session-aware tools.
This per-call route stays correct even when one MCP process outlives a thread;
`CODEX_THREAD_ID` / `CODEX_SESSION_ID` are only compatibility fallbacks for
hosts that explicitly export them. Omit `session_id` in normal calls. Results
report the id and normally show `session_id_source: "codex-thread-meta"`.

Never derive an id from the repository, date, process, or turn. Such ids
either merge separate conversations or split one conversation into several
working-memory buckets. A non-empty explicit id remains available only for a
deliberate historical/backfill target. If the server reports that no session
identity is available, first update or reinstall the Kumiho Memory plugin and
start a new Codex thread so its MCP process reloads. If it still fails,
update/restart Codex; do not guess one.

## Typed ontology (keyless)

After a substantive exchange — a settled decision, a durable fact, or a
new named entity that will recur — call `kumiho_memory_decompose` on the
kref returned by reflect: pass the entities (reusable named hubs), facts
(claims, each ABOUT its entities), and entity→entity relations you
distilled yourself. No external LLM key; keep it lean (a handful of each)
and reuse existing entity names so hubs merge across sessions.

`decompose` takes two more lists, and they are the only route you have to
record that a belief changed:

- `supersedes: [{ statement, replaces, reason }]` — the new claim wins.
  `statement` is a fact from THIS call, `replaces` is the prior statement
  or its kref. Decomposition demotes what it replaces and ripples
  grounding staleness to whatever depended on it.
- `contradicts: [{ statement, conflicts_with, reason }]` — the two claims
  disagree and you cannot say which is right. Records the conflict
  without picking a winner, so recall surfaces both.

Do not reach for a SUPERSEDES edge instead. `kumiho_create_edge` does not
advertise that type and the call is rejected; even if it were accepted, a
bare edge write does neither the demotion nor the staleness ripple, which
is how recall ends up serving a dependent decision as though its grounding
were still sound. When the user corrects you or a fact changes, capture
the new fact via reflect and declare the revision here.

## Decision Memory (code work)

**Before modifying unfamiliar code, ask `kumiho_code_why` for the file
first** — prior decisions, their rationale, verbatim evidence, and whether
they were later reversed (`superseded_by`) come back in one call. Never
re-litigate a decision the graph already explains; if you change it
anyway, say why — and capture the new rationale yourself with
`kumiho_code_capture` right after the commit (capturing the why is YOUR
job; this plugin installs no git hook, so nothing happens automatically).

When you commit code that embodies a real choice — an alternative picked
over another, a default set, a reversal, a measured trade-off — call
`kumiho_code_capture` right after the commit with the decision you already
understand (title, decision, rationale, why_question, files, evidence).
Keyless, exactly like reflect.

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
  `kumiho_memory_consolidate` with a `summary` you wrote yourself from the
  conversation. Omit `session_id`; the bridge supplies the Codex thread id as
  above. This is keyless — without `summary` the call needs an external LLM
  and fails.
