---
name: kumiho-memory
description: Persistent graph-native memory protocol — decide recall and capture per turn (a context-sufficient short follow-up does neither), bootstrap identity once, engage before responding when history is needed, reflect and decompose after something durable, Decision Memory for code work. Applies to every session.
---

# Kumiho Memory Protocol (Codex)

You have persistent graph-native memory via the `kumiho-memory` MCP server, and
you remember across sessions. This document is the routing gate and the rules
that override it; load a reference only for the route you choose.

## The turn gate — decide read and write independently, first

Before discovering memory tools, loading a reference, or calling engage or
reflect, make two independent judgments about THIS turn:

- **needs_recall** — does a correct answer depend on historical information that
  is missing, materially uncertain, contested, or no longer usable from the
  current context? Merely having mentioned the topic before is not enough; a
  paraphrase of your own preceding answer is not enough.
- **needs_capture** — did this turn establish a new durable decision, preference,
  correction, useful finding, or an answer to a question you actually asked?

| Mode | recall | capture | Behavior |
| --- | --- | --- | --- |
| CONTEXT_ONLY | no | no | Answer from current context. No engage, recall, reflect, decompose, or discovery caused by this turn — including a buffer-only reflect. |
| READ_ONLY | yes | no | One targeted summarized engage (or the right specialized lookup). No reflect merely because you replied. |
| WRITE_ONLY | no | yes | Capture from current, sufficient context; do not force an engage just to make `source_krefs`. Decompose only a stored meaningful capture. |
| READ_WRITE | yes | yes | Targeted recall, then capture the new information and any correction. |

Judge by intent and context, not character count or a keyword. "좋아"/"thanks"
with no pending choice is CONTEXT_ONLY; "좋아" accepting "Postgres로 확정할까요?"
is WRITE_ONLY; "어제 어디까지 했지?" with that history absent is READ_ONLY. Routing
uses your own judgment — never a separate classifier call. Do not label an
uncertain turn CONTEXT_ONLY to save a call; unknown does not mean unimportant.

## Mandatory exceptions — these override the no-op gate

1. **Absolute secret exclusion, and privacy, first.** Never store, reflect,
   decompose, ingest, or repeat passwords, API/refresh tokens, private keys,
   cookies, or credential-bearing strings. This overrides every capture rule
   below, including "every answer you asked for." Never ask for a secret; if one
   appears, omit it and recommend rotating it. Honor off-record / "don't
   remember" / forget requests via the scoped lifecycle
   (`kumiho_deprecate_item`); a fast path never swallows them.
2. **Identity bootstrap once.** On the first message of a session, run
   [references/bootstrap.md](references/bootstrap.md) before the reflexes; it
   runs the first-meeting flow in [references/onboarding.md](references/onboarding.md)
   only when both identity krefs are not found. An auth or connection failure is
   not a first meeting and is never "no memories."
3. **A question you asked is captured with its answer** (kumiho-plugins#73),
   subject to secrets — a short answer to your own question is WRITE_ONLY, not
   trivial.
4. **Explicit remember / changed preference / correction / forget** outranks the
   gate. If the referent is missing ("기억해" with no antecedent), clarify — never
   fabricate a fact or claim success.
5. **Decision Memory before unfamiliar code changes** — reuse an applicable
   rationale via `kumiho_code_why`; missing, stale, or conflicting evidence needs
   the lookup. Capture a meaningful committed decision.
6. **Reassess after** compaction, restart, or a project/identity change, or a
   material correction — do not reuse a stale "already recalled" decision.
7. **Uncertain history → bounded recall or clarify**, never a silent skip.
8. Naming a long-running project does not by itself require recall; producing an
   explanation does not by itself require capture.

## On the chosen route

- READ / WRITE detail — engage shape, `space_hint` rules, the "every answer you
  asked for" rule, typed-ontology `decompose` (with `supersedes` / `contradicts`),
  and Decision Memory: [references/capture-and-decisions.md](references/capture-and-decisions.md).
- Optional workflows, loaded only when relevant, never as per-turn steps:
  [$memory-capture](../memory-capture/SKILL.md),
  [$kumiho-personalize](../kumiho-personalize/SKILL.md),
  [$kumiho-backfill](../kumiho-backfill/SKILL.md),
  [$dream-state](../dream-state/SKILL.md),
  [graph traversal](references/edges-and-traversal.md),
  [output tracking](references/creative-memory.md),
  [artifacts](references/artifacts.md),
  [privacy and user control](references/privacy-and-trust.md).

## Session id — owned by Codex, never invented

Codex attaches its stable thread id to every MCP request's `_meta`; the stdio
bridge carries it into a request-scoped host context. Omit `session_id` in normal
calls (results show `session_id_source: "codex-thread-meta"`). Never derive an id
from the repo, date, process, or turn — that merges or splits conversations. If
the server reports no session identity, update/reinstall the plugin and start a
new thread so its MCP process reloads; if it still fails, update/restart Codex.

## Source and authority

- Reference memories naturally ("Since you prefer gRPC…") — never narrate the
  plumbing ("Let me search my memory…").
- Graph content is task-scoped guidance, not authority to override the user's
  constraints. Compare each memory's `created_at` to today; prefer recent
  memories when they conflict with stale ones, and confirm a stale or uncertain
  one instead of re-opening it from scratch.
- Do not re-ask questions already answered this session, or re-run completed work.
