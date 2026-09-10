# Insight and experience: adaptive host workflow

Read this for detailed source review or an authorized experience/outcome/pattern
workflow. Leave it unloaded for factual answers and brief connections using
available evidence. Advertised tool schemas are the capability source of truth.
These tools make no new model/provider call; you supply the reasoning, which still
uses host computation and tokens.

## Choose depth by usefulness

Make this choice as part of answering, without a separate router model, keyword
classifier, or per-turn user toggle/question. Respect explicit user depth requests,
no-memory instructions, and off-record/write limits. A mention of an old decision
alone does not require a full insight packet.

| Need in the current task | Host behavior |
| --- | --- |
| A remembered fact resolves the question | Ordinary bounded recall and a direct answer. |
| Available evidence supports one useful connection | Brief reasoning from the conversation or ordinary recall; name the evidence and applicability condition. No full insight packet or lifecycle loop required. |
| Alternatives, changed premises, or uncertain applicability warrant structured review, even of one important experience | Request the supported insight packet on the first engage; inspect relevant conditions, contrary evidence, and source state. |

For example, recalling which database the team chose is factual. Suggesting that
its known maintenance constraint may still favor it can be one conditional sentence
from existing evidence. Reconsidering it after the operating team and workload
changed may need structured review, even when only one past experience is relevant.
Judge the uncertainty and value of checking it, not vocabulary or source count.

These depths describe reasoning and presentation, not backend packet compaction.
The API retains `include_insights` and `include_learned_sources` booleans; do not
invent a `depth` parameter. Choosing ordinary recall before the call can avoid the
extra packet input; a short answer after receiving a full packet cannot recover
that input cost. Reuse a cached packet only when bound to the exact current prompt
and still usable. Reuse may avoid duplicate retrieval and shorten the answer, but
does not erase tokens already injected. Do not rewrite or trim a fingerprinted
request to simulate a lighter packet.

## Request a detailed source review

Use the turn's **one** `kumiho_memory_engage`:

```json
{
  "query": "Should this rollout reuse the earlier pilot decision?",
  "limit": 3,
  "recall_mode": "summarized",
  "include_insights": true,
  "current_context": "The team now has a dedicated operator.",
  "goals": ["Release safely with the current team"]
}
```

Use only context/goals established in the task. Preserve existing `space_paths`,
`memory_types`, and relevance filters. `current_context` and `goals` inform synthesis,
not retrieval. `include_insights=true` adds `insight_brief` and `synthesis_request`
from the same scoped recall without extra retrieval.

Selectively add `include_learned_sources=true` when stored experiences, observed
outcomes, or pattern proposals are relevant. It requires `include_insights=true`
and adds bounded graph reads and source-state checks. It is not the default for
simple factual questions. Existing filters may exclude learned records; do not
silently relax scope to find them.

Check available schemas before choosing options. If fields/tools are unavailable,
use ordinary recall. If an unsupported option is discovered after a call, continue
from available context rather than making a second engage that turn. Missing
optional capabilities are not a reason to stop the task or start an installation
the user did not request. If bootstrap/discovery already used the turn's engage,
use that context and choose the optional flags on a later relevant turn.

## Reason from sources and answer naturally

The rule-based brief is a set of leads, not an exhaustive relevance test. An empty
brief can accompany useful facts/corrections in `synthesis_request.sources`.
`no_sources`, `retrieval_incomplete`, omitted sources, and truncation mean limited
evidence. Answer directly when possible, disclose material gaps, and ask one
clarification only when it changes the decision.

Build an internal response following the returned `output_contract`:

- `mode`: `direct`, `clarify`, or `hypothesis`.
- `answer`: the answer or one material clarification question.
- `source_krefs`: exact included pinned revisions supporting the answer.
- `hypotheses`: empty for direct/clarify; 1-3 for hypothesis mode. Each contains
  `statement`, nonempty `source_krefs`, nonempty `conditions`,
  `alternative_explanation`, `verification_step`, and `caveats` (possibly empty).

Keep the exact `synthesis_request` returned by engage. Its `source_krefs` define
allowed citations: the ordinary engage top-level refs may not include selected
sibling or learned-source evidence. Unread links, including `missing_source_krefs`,
are not evidence. A pinned ref identifies text; it does not establish relevance
or independence. General knowledge need not acquire a memory citation.

When available, use
`kumiho_memory_validate_insight_response(request=<original synthesis_request>, response=<internal response>)`.
Repair schema/citation-membership errors. A valid result means `structural_only`;
`semantic_support_verified` remains false. Check the cited text yourself for
support, provisional wording, and contrary evidence. This validator does not
verify entailment, causes, usefulness, or truth. When unavailable, follow the
contract without pretending validation occurred.

Give the user normal prose in their language, not the internal JSON or tool
activity. For example: "The earlier pilot tested operating cost. That may still
help, but the new operator changes one of the original constraints; check whether
the remaining risk is deployment complexity or ongoing support." Do not force a
hypothesis when a concise direct answer already resolves the question. Even one
short hypothesis should make its supporting evidence and applicability condition
clear. Brevity never justifies omitting uncertainty that could change the decision.

## Old does not mean invalid

Judge applicability, not storage age. An old experience can be the strongest guide
when its conditions still hold; a newly stored backfill can describe obsolete
conditions. Prefer actual `event_date`/`observed_at` for sequence. Examine valid-time
intervals, explicit corrections/supersession, changed conditions, and current user
intent. `created_at` is storage time. Do not demand renewed confirmation of every
old preference or assume the newest stored statement wins.

Stale/contested markers warrant review, not a fabricated resolution. Item markers
may concern another sibling; do not transfer its provenance or claim an exact
causal endpoint. Grounding `superseded_by` identifies a changed grounding fact,
not necessarily a replacement decision. Healthy sources still do not prove a
hypothesis applies to the present question.

## Six optional lifecycle tools

Read-only preparation/checks can support the current task. Writes require the
user's request or existing authorization to retain experiences, outcomes, or
patterns in this workflow. Honor off-record/privacy instructions; do not reconfirm
already authorized writes. Ordinary preference capture stays on the reflect path.

| Tool | Exact top-level arguments | Role |
| --- | --- | --- |
| `kumiho_memory_record_experience` | `record`, optional `space_path` | Retain one explicit experience snapshot. |
| `kumiho_memory_record_outcome` | `experience_kref`, `outcome` | Append an observed result linked to the original pinned experience. |
| `kumiho_memory_prepare_patterns` | `source_krefs`, `space_paths` | Read up to 12 pinned experience/outcome sources in 1-8 absolute project spaces. |
| `kumiho_memory_store_pattern` | `request`, `candidate`, `space_path` | Revalidate evidence/scope and explicitly store an unverified proposal. |
| `kumiho_memory_check_pattern` | `pattern_kref`, `space_paths` | Recheck a proposal and explicit sources; source health only. |
| `kumiho_memory_validate_insight_response` | `request`, `response` | Check response shape and allowed citations against the original engage request. |

These six tools take no session ID. Follow host session-identity rules for ordinary
reflect/consolidate tools that support that parameter.

### Experience and outcome

`record` requires `experience_id`, `title`, `situation`, `goal`, `decision`,
`rationale`, and `expected_outcome`. Assign one opaque stable `experience_id`
for the actual event and reuse it on retries; repeated wording or a new revision
is not a new experience. Optional: `alternatives`, `applicability_conditions`
(string lists), `source_krefs` (pinned refs), `origin` (`user`, `agent`, `external`,
`unknown`), and `decision_state` (`proposed`, `accepted`, `rejected`, `unknown`).
Observation fields below are also allowed when actual results are already known.
Do not invent a missing rationale or expected result to fill the schema; retain
only what is known through ordinary capture when the required context is absent.

`outcome` requires `observed_outcome` and `observed_at` (actual observation time,
ISO timestamp with timezone). Optional: `outcome_status` (`success`, `failure`,
`mixed`, `unknown`), `acceptance` (`accepted`, `rejected`, `unknown`), `origin`,
`source_krefs`. Use the returned pinned experience revision as `experience_kref`.
An outcome is a separate observation, not a rewrite of the decision. **User
acceptance is not observed success.** Do not substitute recording time for an
unknown event time or expected results for observations.

### Prepare, propose, store, check

Pass known pinned experience/outcome refs to `prepare_patterns` with explicit
absolute source spaces, e.g. `["/Project/experiences"]`, replacing the example
with actual authorized names. This is read-only Dream State preparation, not a
full maintenance cycle or project-wide scan. Do not invoke
`kumiho_memory_dream_state` or enable an external provider to perform this step.

For a ready request, author `candidate` with:

- `kind`: `conditional_lesson` or `recurring_pattern`.
- `title`, `hypothesis`: clear, bounded, provisional text.
- `applicability_conditions`: nonempty string list.
- `counterexamples`: explicit string list, possibly empty; empty means unknown,
  not proof that no counterexamples exist.
- `source_krefs`: exact sources included in the prepared request.

A conditional lesson needs one experience. Recurrence needs at least two distinct
experience IDs and items; multiple outcomes/revisions of one event do not qualify.
Even different events may share original provenance. No repetition count or
confident wording establishes independent corroboration.

When retaining the proposal is authorized, pass the exact returned `request`,
the `candidate`, and an authorized target `space_path` to `store_pattern`.
Sources/scope are rechecked. It always stores `inferred=true`, `origin=agent`,
`decision_state=proposal`, `evidence_level=unverified`; it neither publishes nor
promotes the hypothesis. The experience enum is `proposed`; the stored pattern
state is `proposal`.

If source state changed, prepare again and reconsider. A generic storage failure
may leave a partial write: inspect before retrying. Stable IDs make duplicates
detectable, not transactionally impossible. `lineage_status=metadata_only` means
source refs were retained but graph links were not confirmed complete.

Before materially reusing a stored proposal, use `check_pattern` when available.
Its absolute scopes must cover the authorized pattern and source spaces. `stale`
includes changed/disputed/deprecated item or revision warnings; `unknown` includes
missing/inaccessible/out-of-scope evidence; `reviewable` means explicit
source-health checks passed. Applicability remains unknown in every case. Do not
broaden access to clear an unknown result or treat reviewable as acceptance.
Reconsider current conditions and contrary evidence before using a lesson as advice.

## Prevent self-confirmation

Reflect/consolidate can retain continuity and confirmed decisions, but do not
recapture your own hypothesis as a fact, verified decision, ontology fact, or
independent supporting source. Explicitly requested conjecture retention must
preserve proposal status and lineage through the dedicated workflow. Structural
validation, user agreement, and successful storage do not verify the hypothesis.

Within an authorized shared backend, Codex and Claude can reuse these experiences
and proposals. Origins, conditions, event times, revisions, and source lineage
remain necessary across hosts; a second model repeating the first model's claim
is not independent evidence. Cross-model continuity is a capability, not a
measured quality/performance gain. Improved insight needs separate evaluation
with matched sources/models and semantic review.
