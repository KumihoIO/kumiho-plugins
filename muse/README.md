# Kumiho Memory — Meta Muse app connector

This integration connects the **Muse consumer app** to the existing hosted MCP
service. It is not a Muse Code extension. No new server, bridge or client secret
is shipped here. Status: prepared, not submitted or approved.

## Submission values

| Field | Value |
| --- | --- |
| Name / publisher | Kumiho Memory / Kumiho Inc. |
| Short description | Remember decisions and context across conversations. |
| Endpoint / transport | https://mcp.kumiho.cloud/mcp / Streamable HTTP |
| Environment | Production service; reviewer uses an isolated test workspace |
| Authentication | OAuth; issuer https://control.kumiho.cloud; discovery, CIMD/DCR and PKCE S256 |
| Requested scope | `memory`: workspace reads and writes; no separate read-only OAuth scope currently |
| Website | https://kumiho.io |
| Privacy | https://kumiho.io/en/legal#privacy |
| Product terms | https://kumiho.io/en/legal |
| Support | support@kumiho.io |
| Authorized icon | assets/logo.png |
| Submission | https://muse.ai/platform |

[connection.json](connection.json) is a copyable Kumiho reference document, not
an invented Meta upload format. [tools.json](tools.json) contains the real hosted
SDK schemas/descriptions, MCP annotations, and proposed Muse classifications.
Meta reviews and enforces its own permissions; the JSON does not enforce them.

## Listing copy

Kumiho Memory helps people carry useful context between conversations: decisions
and their reasons, preferences, project facts, and selected experiences. Ask Muse
to recall what you previously decided, save a specific fact, correct an earlier
memory, or retire an item from normal recall. References and revisions keep the
source and history available. Unlike browsing public pages, the connector reads
and updates the user's authenticated private Kumiho workspace through structured
tools, and returns verifiable references for saved items.

A trial or paid Kumiho Cloud workspace is required. The 14-day trial offers one
seat, CognitiveMemory only and 2,000 nodes, with Google sign-in and no card; one
trial per account. Start at https://kumiho.io/signup?intent=trial. After expiration,
recall/export remain for 30 days while saves stop; subscribing continues the same
workspace. Deletion is announced beforehand. This connector does not access a
self-hosted Community Edition instance. Confirm actual supported regions during
submission; this dossier does not assert worldwide service availability.

Example prompts:
- "Remember this decision: keep Friday afternoons free for research."
- "What did I decide about my weekly schedule, and why?"
- "Change that preference to Thursday afternoons."
- "Retire that old schedule preference from normal recall."
- "Review up to five memories about my research project; propose changes only."

## Permissions, errors and side effects

The catalog proposes **10 Read, 6 Write and 2 Sensitive write** tools. Treat all
operations as subject to Muse approval, including reads. A denied call stops that
operation. Installation or account linking is not blanket consent for captures.

`kumiho_chat_clear` irreversibly clears the current buffer;
`kumiho_memory_consolidate` stores a summary AND clears it. Both are proposed as
sensitive writes requiring approval on every use. `kumiho_deprecate_item`
reversibly retires an item; it is not permanent deletion. Store/reflect may update
a published revision, decomposition writes graph relationships, and space
profiling persists results. Their destructive MCP hints remain visible alongside
this proposed classification. Meta may classify additional operations sensitive.

Use the exact input schemas in tools.json. Outputs are MCP tool results containing
application JSON/text, including references and application errors; callers must
inspect both MCP errors and the payload. A 200 HTTP response alone is not proof
of success. A backend error with zero matches is not proof of no memories.

Session-aware calls initially omit session_id. On `session_required`, use the
returned ID for this conversation only. Never invent an ID or use another chat's
buffer. Check returned capture_results/krefs and verify a saved revision before
claiming success. On uncertain writes, inspect known results rather than replay
blindly. Do not turn partial capture failures into an all-success answer.

Expired/revoked authorization requires the host reconnect flow. Honor 401/403,
429 (and Retry-After if supplied), and service errors; no fixed numeric rate limit
is promised here. Tool invocations must remain in the token's authorized workspace.
Read-only OAuth scope is not currently available; Muse per-tool read-only choices
must not be described as server-enforced read-only OAuth credentials.

## Data processing draft — facts and open attestations

Tool inputs include a narrow recall query, authorized memory text, references,
space filters and optional provenance. Tool responses return matching workspace
memories/revisions and, when requested, the conversation buffer. Reflect can store
a short response summary and approved captures. Buffer consolidation changes both
durable memory and the buffer. OAuth credentials belong in the secure host login
flow, never in chat, this repository, tool arguments or reviewer documentation.

The endpoint uses HTTPS. Authorization is workspace-scoped. User-selected history
is distilled before capture; connecting does not initiate whole-history upload.
Retirement is not erasure. Account/record erasure and authorization revocation need
separate verification in the reviewer environment.

Before submitting factual/legal attestations, the publisher must verify the
current production retention/deletion periods, subprocessors and processing
locations, sensitive-data-at-rest protection, handling after access revocation,
and the published policy's consistency with Muse's prohibition on secondary use
(including model training). The existing directory documentation explicitly did
not establish a training statement. Do not invent one for this submission.
Confirm security/maintenance contacts and ability to meet the required 48-hour
incident notification obligation, and accept the applicable Meta terms as the
company's authorized representative. No such attestations have been accepted by
this package or carried over from the earlier OpenAI submission.

## Reviewer setup and QA

Provide a dedicated, isolated Kumiho Cloud test account through the portal's
secure credential fields. Do not reuse the publisher's personal workspace or
commit credentials. Keep the test account available throughout review. Seed only
synthetic records, for example a fictional Project Lantern decision with its
reason. A test account and completed host QA are still required.

1. Connect with OAuth, approve scope, list projects/spaces; verify the test tenant.
2. Read known synthetic items using each read tool (search, retrieve, item,
   revision, provenance, recall, engage and current-buffer read). Check that a
   fresh unrelated query is distinguished from an actual backend error.
3. Approve create-space, reflect and store calls individually. Verify returned
   references, then correct the exact item using capture.revises and verify its
   new revision. Reject one requested write and confirm no mutation occurs.
4. Approve a profile and decomposition on synthetic test memories and inspect
   their persisted effects; do not label these read-only.
5. Retire a specific synthetic item and verify ordinary recall excludes it; do
   not claim deletion. Verify the existing service's recovery/erasure routes.
6. In separate disposable conversations, test buffer clear and consolidation.
   Require Muse approval every time, verify summary persistence before/alongside
   buffer removal, and test Deny leaves the buffer unchanged.
7. Test expired/revoked login, no-workspace account, wrong-tenant reference,
   uncertain/partial writes and plan limits. Record errors without credentials.
8. Disconnect, verify access stops, and record the implemented data deletion or
   inaccessibility behavior for the questionnaire. Do not claim these tests ran
   merely because source/package checks passed.

From the repository root, regenerate/check the catalog with the locked cloud-mcp dependencies:

```sh
python muse/export_tools.py
python muse/export_tools.py --check
```

Official review requirements: https://muse.ai/platform/docs
