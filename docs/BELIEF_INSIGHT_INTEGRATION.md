# Belief insight integration

This change prepares Claude/Codex plugin 0.22.0 and OpenClaw 0.7.0 for
[Kumiho Memory PR #32](https://github.com/KumihoIO/kumiho-memory/pull/32).
It does not merge or publish either package. Insight requires a backend that
advertises `include_insights` on `kumiho_memory_engage`; installing this plugin
alone cannot add the backend feature.

## User behavior

For a decision, an old experience that might apply, a changed premise, or a
conflict in remembered evidence, the host can request an insight source packet
in its one current-question engage call. The host connects the evidence to the
question and answers naturally. No extra model provider or API key is needed.
Simple lookups continue to use ordinary recall.

Old experiences are not invalid merely because they are old. Compare event
time, applicability conditions, observed results, and explicit supersession or
conflict markers. A newer storage timestamp is not evidence that an older
lesson became false. No age-based retrieval/ranking change is implemented in
this plugin; the host reasoning guidance makes this distinction explicit.

The response contains `insight_brief` and `synthesis_request`. The latter keeps
pinned evidence, uncertainty, and an internal response contract together.
Structural validation checks format and allowed reference membership, not
truth, causal support, or the usefulness of the answer. Do not turn a generated
hypothesis into an observed fact or promote a pattern automatically.

Use `include_learned_sources=true` only with `include_insights=true`, and only
when stored experience/outcome/pattern candidates are relevant. Unlike the
pure brief, learned-source discovery performs additional bounded searches and
source-health reads. Repeated revisions are not independent corroboration.
Respect the same authorized memory scope across hosts; cross-model reuse
transfers recorded evidence, not model weights or a guaranteed quality gain.

## Host integration

- **Codex:** the skill selects supported insight arguments for relevant current
  questions and keeps the detailed experience lifecycle in a portable reference.
- **Claude:** the skill provides the same on-demand flow. Automatic prefetch is
  based on an earlier turn; it never substitutes that turn's synthesis for a
  different current question. Optional cached insight must match the exact
  prompt and TTL and fit intact inside the context/session budgets.
- **OpenClaw:** typed engage arguments and responses carry the optional source
  packet. MCP schema discovery handles older deployments, and current-question
  host selection remains separate from background retrieval.

Claude's optional background insight knobs are resolved by the launcher and
snapshotted for hooks, including Desktop's literal environment placeholders:

| Setting | Default | Meaning |
| --- | --- | --- |
| `KUMIHO_REFLEX_INSIGHTS` | `0` | Enable question-bound insight prefetch when the local core advertises support. On-demand skill use does not require this flag. |
| `KUMIHO_REFLEX_LEARNED_SOURCES` | `0` | Also request learned sources; requires insight prefetch and backend support. |
| `KUMIHO_REFLEX_INSIGHT_MAX_CHARS` | `5120` | Whole cached insight block budget, additionally constrained by the shared session budget and a hard 12,000-character cap. |

Oversized packets are omitted whole, preserving their citations and snapshot
fingerprint. Hosts can use ordinary recalled evidence or their supported
current-question engage flow; partial JSON is never presented as a complete
validated source packet.

## Rollout and verification

1. Merge/release the core change and deploy it to the selected backend.
2. Verify that the live MCP schema advertises the new flags and six lifecycle
   tools. Reload host MCP sessions after updating the runtime.
3. Release these companion plugins and exercise one decision/experience query
   on each host, including an old experience whose conditions changed.

The existing published dependency floors remain unchanged deliberately: a floor
pointing at an unpublished version breaks fresh installations. Old servers keep
ordinary recall. A later release can raise the floor to the actual published
core version. Core support is determined by capabilities, not a guessed version.

The existing real-SDK stdio smoke checks the insight contract when advertised.
Set `KUMIHO_REQUIRE_INSIGHT_CONTRACT=1` for a release validation run that must fail
if the installed backend lacks it. The check covers all six lifecycle tools and
their read/write annotations through both hosts and Cloud/CE adapters. It does
not write memories or certify response quality.

Offline regression uses synthetic packets, old/new schema variants, scope and
budget checks, and stale/different-question cache cases. A real Codex-to-Claude
user study or production write/read lifecycle test is not implied by those
checks. The core PR's eight-question pilot is preliminary, not a cross-model
benchmark.
