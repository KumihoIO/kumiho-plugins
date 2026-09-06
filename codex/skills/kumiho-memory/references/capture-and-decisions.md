# Capture, recall, ontology, and Decision Memory (Codex)

Operation detail for the READ_ONLY / WRITE_ONLY / READ_WRITE routes. The main
`SKILL.md` carries the turn gate and the mandatory exceptions; load this only on
the route you actually chose, not every turn.

## Engage — the read reflex (READ_ONLY / READ_WRITE)

When the turn needs history (see the gate), call `kumiho_memory_engage` with a
query derived from the current message, at most once per turn (the server
deduplicates within 5 seconds). Never say "I don't know" without engaging first.
Hold the returned `source_krefs` for reflect.

Recall is a working context, not a transcript dump. The normal shape is
`kumiho_memory_engage(query=<current request>, limit=3, recall_mode="summarized")`;
keep the query short and specific. Do not fetch whole spaces, session
transcripts, or old artifacts just to be safe. Keep only the few returned memories that
constrain the current answer (their short title/summary and krefs); treat roughly
1,200 characters or three items as the working-context budget. If the result is
empty, continue from the code, docs, and git history — do not ask the user to
repeat context. Broaden retrieval only when a relevant decision is missing or two
memories conflict; resolve a code-specific question with `kumiho_code_why` for the
file rather than another broad engage. Never repeat an identical query in a turn.

Before a material change, keep a small internal receipt: the relevant
decision(s), the constraints that must not be violated, the files in scope, and
the source krefs. It is a reasoning checkpoint, not text to repeat. If the user
corrects a premise, discard the receipt and rebuild it from the correction.

## Reflect — the write reflex (WRITE_ONLY / READ_WRITE)

After a substantive response that established something durable, call
`kumiho_memory_reflect` with your response text and structured captures
(decisions, preferences, facts, corrections). Use absolute dates in capture
titles ("on Jul 11", never "today"); pass `source_krefs` from engage for
provenance. A buffer-only reflect with no captures is NOT required merely because
you produced a reply — on a CONTEXT_ONLY turn, skip it entirely.

Give every capture a `space_hint`. Without one it is filed at the project root,
and reflect's automatic revision stacking then searches that whole bucket for
something to stack onto — that is how an unrelated capture becomes a new revision
of a months-old item. Reuse a space name you have already seen in an engage kref
(`kref://<project>/<space>/<item>.<kind>`), copied exactly: reflect forwards the
hint verbatim, so `Skills` and `skills` are two different spaces. Fall back to the
capture type (`decisions`, `facts`, `preferences`, `corrections`) when none fits.

Optionally set applicability metadata the recall layer surfaces (kumiho-memory#28):
`origin` (who asserted it — `user` / `agent` / `imported` / `observed`) and, for a
decision, `decision_state` (`proposal` vs `accepted`). A floated option is a
`proposal`, not an accepted decision.

### Every answer you had to ask for gets stored

If you had to ask the user, the answer was not in the code, the docs, or the git
history — the most valuable thing in the session and the most expensive to lose
(kumiho-plugins#73). Capture every non-secret question you put to the user as
`type: "decision"`, in the same turn, keeping the question WITH the answer
("Asked whether the queue should back onto Postgres or Redis for the ingest path;
chose Postgres — one datastore to operate", not just "Postgres"). Route it with a
`space_hint`. A short answer to a question you asked is not a trivial exchange —
it is WRITE_ONLY, never CONTEXT_ONLY. Recall before you ask: if a question could
have been settled before, engage on it first and use what comes back naturally
rather than re-asking.

## Typed ontology (keyless)

After a substantive exchange — a settled decision, a durable fact, or a new named
entity that will recur — call `kumiho_memory_decompose` on the kref returned by
reflect: pass the entities (reusable named hubs), facts (each ABOUT its entities),
and entity→entity relations you distilled. No external LLM key; keep it lean and
reuse existing entity names so hubs merge across sessions. Decompose only a
successfully stored, meaningful capture — never force an engage just to
manufacture `source_krefs`.

`decompose` records a belief change, the only route you have for it:

- `supersedes: [{ statement, replaces, reason }]` — the new claim wins.
  `statement` is a fact from THIS call; `replaces` is the prior statement or its
  kref. Decomposition demotes what it replaces and ripples grounding staleness to
  whatever depended on it.
- `contradicts: [{ statement, conflicts_with, reason }]` — the two claims
  disagree and you cannot say which is right. Records the conflict without picking
  a winner, so recall surfaces both.

Do not reach for a SUPERSEDES edge instead: `kumiho_create_edge` does not
advertise that type and the call is rejected; even if accepted, a bare edge write
does neither the demotion nor the staleness ripple. When the user corrects you or
a fact changes, capture the new fact via reflect and declare the revision here.

## Decision Memory (code work)

Before modifying unfamiliar code, ask `kumiho_code_why` for the file first —
prior decisions, rationale, verbatim evidence, and whether they were later
reversed (`superseded_by`) come back in one call. Never re-litigate a decision the
graph already explains; if you change it anyway, say why, and capture the new
rationale with `kumiho_code_capture` right after the commit (capturing the why is
YOUR job; this plugin installs no git hook). Capture a commit that embodies a real
choice (an alternative picked, a default set, a reversal, a measured trade-off)
with the decision you already understand. To backfill a repo's history:
`kumiho_code_ingest` (idempotent; captured commits are skipped at zero LLM cost).

## Skill discovery

For an unfamiliar procedure not covered locally, use the turn's one engage with
`space_paths=["CognitiveMemory/Skills"]`, `limit=3`, `recall_mode="summarized"`.
If engage was already used, reuse relevant returned skills or continue from local
docs — do not spend a second call on discovery. Read the published revision of
only the selected skill by its kref; prefer `agent_compat` containing `codex` or
host-neutral guidance; skip quarantined/unpublished material and Claude-only hook
instructions. Reuse the loaded guide within the session.
