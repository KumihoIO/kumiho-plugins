# Belief insight integration

Claude/Codex plugin **0.22.0** and OpenClaw **0.7.0** integrate the published
[Kumiho Memory 1.5.0](https://pypi.org/project/kumiho-memory/1.5.0/) core.
Local provisioning requires `kumiho-memory[all]>=1.5.0`. Remote deployments
must advertise `include_insights` on `kumiho_memory_engage`; installing a local
plugin does not update a remote server.

## User behavior

The default experience is **automatic host selection**. Users ask normal
questions; the agent chooses the needed depth as part of its existing reasoning,
without a separate classifier/model call or a per-question permission prompt.
Explicit user instructions about memory use and answer depth still take priority
for host reasoning and host-initiated tool calls.
The API booleans are internal controls, not a user-facing on/off workflow.

| Need | Host behavior | Full insight request |
| --- | --- | --- |
| A fact or earlier choice | Answer from visible context, or ordinary recall when needed | No |
| A small connection | Use available/ordinarily recalled evidence and state the relevant condition briefly | No |
| A comparison that needs deeper evidence review | Request the supported synthesis packet on the first engage; compare conditions, alternatives and uncertainty | Yes |

These are reasoning choices, not new API modes. A question containing
"decision", "old", or "insight" does not by itself justify a full packet. An
old experience can support either a short answer or detailed review depending
on what the user needs and what evidence is already available. Missing evidence
alone does not require escalation when it would not help answer the question.

Choose before the turn's one engage. If an ordinary engage already ran, use the
returned evidence, disclose material gaps, and do not issue another engage just
to change depth. Reuse an exact matching prefetched packet when available. The
host may give a concise answer from a detailed packet; that does not recover
input tokens already spent. Do not load lifecycle references for a plain lookup.

This update changes host guidance, not the core packet format. It does not add
an adaptive packet compressor or remove fields from a fingerprinted request.

Old experiences are not invalid merely because they are old. Compare event
time, applicability conditions, observed results, and explicit supersession or
conflict markers. A newer storage timestamp is not evidence that an older
lesson became false. No age-based retrieval/ranking change is implemented in
this plugin; the host reasoning guidance makes this distinction explicit.

A detailed insight response contains `insight_brief` and `synthesis_request`. The latter keeps
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

Claude's advanced background cache controls are independent of the default
automatic host selection. They do not require the user to turn on insight for
normal questions. These knobs are resolved by the launcher and
snapshotted for hooks, including Desktop's literal environment placeholders:

| Setting | Default | Meaning |
| --- | --- | --- |
| `KUMIHO_REFLEX_INSIGHTS` | `0` | Enable question-bound insight prefetch when the local core advertises support. Automatic foreground host selection works with this flag off. |
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

The runtime floor now names the published core 1.5.0 across the launchers,
setup paths, and distribution declarations. Capability checks remain active for
older remote servers. CI requires `KUMIHO_REQUIRE_INSIGHT_CONTRACT=1`: the real
SDK stdio matrix fails if any of the six lifecycle tools or their expected
read/write annotations is absent in either host's Cloud/CE adapter. These checks
do not write memories or certify response quality.

The Claude/Codex startup adapters fill missing SDK annotation entries from the
installed core's own insight tool declarations. This supports SDK versions whose
separate annotation registry does not yet include the six new tools. Existing
SDK entries and unrelated tool defaults remain untouched. The same patched SDK
module starts the server; the required stdio tests verify the metadata actually
reaches the host.


Offline regression uses synthetic packets, old/new schema variants, scope and
budget checks, and stale/different-question cache cases. A real Codex-to-Claude
user study or production write/read lifecycle test is not implied by those
checks. The core PR's eight-question pilot is preliminary, not a cross-model
benchmark.

## Cost and selection evaluation

The core eight-case pilot measured mean **2,938.25 additional serialized guidance
characters**. Approximately 735 tokens was only `characters / 4`, not a tokenizer
measurement, a fixed per-turn charge, or total transport/answer cost. This plugin
update does not claim a measured token saving or validated routing accuracy.

Review these contrasting situations when assessing host selection:

| Situation | Expected behavior |
| --- | --- |
| "Which library did we choose?" | Factual recall, even though it mentions a decision. |
| The answer is already visible in the current conversation | Answer directly without a new memory call. |
| One recalled failure and an explicitly unchanged condition | A brief conditional connection may suffice; no full packet merely because the experience is old. |
| Several prior outcomes conflict and current operating conditions changed | Detailed review on the first supported engage; learned sources only if needed. |
| "Check the current branch/PID" | Use live tools; memory is not live-state verification. |
| "Answer briefly" with materially conflicting evidence | Preserve necessary evidence/uncertainty; a short final answer can still need detailed review. |
| A useful exact-matching packet was already injected | Reuse it without another engage; shorten the answer as appropriate. |
| The user forbids memory access, or the backend lacks insight support | Make no host-initiated memory call, or use supported ordinary recall respectively; background prefetch is separate (see below). |

For a measured study, hold model/version and source availability fixed, record
actual tool arguments, and judge answer usefulness and evidence support alongside
unnecessary insight activations, missed helpful activations, tokenizer-measured
input/output tokens, and latency. Evaluate concise useful connections as well as
detailed answers. The table is an acceptance protocol, not completed benchmark
results or proof that every host follows its instructions.

The host-selection policy governs agent reasoning and agent-initiated calls.
Existing automatic hooks may retrieve/inject context before the agent reads the
current prompt; they do not interpret free-form per-turn no-memory requests.
This change does not add such a pre-hook privacy gate. Deployments requiring no
background memory access must control the existing auto-recall/prefetch settings.

## Release tags

The synchronized Claude/Codex release uses `kumiho-memory-v0.22.0`, with
`claude-v0.22.0` and `codex-v0.22.0` host aliases. OpenClaw uses
`openclaw-v0.7.0`. All four tags identify the same verified integration commit.
A Git tag does not itself publish the OpenClaw package to npm.
