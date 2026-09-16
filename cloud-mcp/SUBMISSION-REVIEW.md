# Submission source review

Updated 2026-09-16. Inspected the hosted MCP wrapper plus the locked
`kumiho==0.13.0` / `kumiho-memory==1.5.0` implementations and called helpers.
The importer JSON matches the current local descriptors. The live portal has
accepted its 18 tools and 54 justifications; actual submission is still pending.

## Corrections included

- **Sensitive input:** `kumiho_search_items.auth_token` was a stdio-only override
  still present in the hosted catalog. The SDK ignored it in hosted mode, but
  advertising it could solicit credentials in chat. It is now absent from
  discovery, and stale/direct calls supplying it return an error without echoing
  the value. Account authorization continues through OAuth.
- **Destructive hints:** `kumiho_memory_consolidate` clears the working buffer;
  `kumiho_memory_store` and `kumiho_memory_reflect` can move an existing published
  memory tag; `kumiho_memory_decompose` can demote a superseded fact and invalidate
  dependent evidence. Their `destructiveHint` values are now explicitly true,
  including when the upstream SDK already supplied different annotations.
  Previous memory revisions remain accessible in history.
- **Descriptions/data use:** Hosted store, reflect and decompose descriptions
  disclose these modes. Instructions limit input to brief relevant information,
  forbid credentials and full-transcript collection, and direct account linking
  to OAuth. These instructions are not a universal sensitive-data classifier.
- **Open-world hints:** All 18 tools operate within the authorized private
  workspace. Artifact locations are registered as pointers; hosted artifact
  reading/writing is disabled. No arbitrary web browsing or public publishing
  tool is exposed. `openWorldHint: false` remains accurate.
- **Widget CSP:** Not applicable: this server exposes no widget resources.
  There is no widget wildcard allowlist to narrow.
- **Naming:** `kumiho_deprecate_item` retires an item and can reverse retirement;
  the submission copy and test 5 explicitly distinguish this from erasure.

No remaining explicit input field was found that asks for passwords, tokens,
MFA codes, payment-card details, government IDs or precise location. Free-text
memory fields can contain personal information; users should supply only what
is needed for the requested memory. The published policy must cover returned
workspace metadata, summaries, temporary buffers and provider processing.

## Output-schema warning — all 18 tools

Add an `outputSchema` so models can use each tool's results more reliably.
The SDK currently returns JSON in text content for these tools; schemas must be
based on real success/error shapes and paired with matching structured output.
Do not invent schemas or claim they already exist. This warning does not block
generation of the submission-import JSON.

[MCP tool output schema reference](https://modelcontextprotocol.io/specification/draft/server/tools#tool)

- `kumiho_list_projects`
- `kumiho_get_spaces`
- `kumiho_get_item`
- `kumiho_search_items`
- `kumiho_memory_store`
- `kumiho_memory_retrieve`
- `kumiho_get_revision_by_tag`
- `kumiho_get_provenance_summary`
- `kumiho_create_space`
- `kumiho_deprecate_item`
- `kumiho_chat_get`
- `kumiho_chat_clear`
- `kumiho_memory_consolidate`
- `kumiho_memory_recall`
- `kumiho_memory_engage`
- `kumiho_memory_reflect`
- `kumiho_memory_space_profile`
- `kumiho_memory_decompose`

## Public-release gates

- The dedicated synthetic reviewer account passed 47 hosted OAuth/MCP checks.
  Actual ChatGPT evidence and the final scenario retest are documented in
  `DIRECTORY-ACCEPTANCE.md`; the edited demo uses captured browser frames.
  Keep legal attestations pending until owner confirmation at submission time.
- Availability is KR-only initially. Do not claim every service or account
  record is processed exclusively in Korea; the control plane is in US East.
- Public policy exists at `https://kumiho.io/en/legal`; compare its promises
  against current deployment settings, deletion behavior and actual retention.
  The historical `PRIVACY.md` draft contains unconfirmed retention/residency
  statements and must not be treated as approved policy.
- The portal verified the exact domain token and the Kumiho business identity
  is selected. The final runtime is deployed as ECS revision 23.
- The final image scan still reports one HIGH zlib dependency finding; see
  `CHATGPT-SUBMISSION.md` for the digest and current assessment. No direct
  affected API use was identified; this does not establish non-reachability.

## Verification

- JSON structure, 18 exact action names, 54 explicit boolean hint values,
  descriptor equality, subtitle length and exact 5/3 scenario counts checked.
- Python Ruff passed; full hosted suite including credential-cache regressions: **209 passed, 11 skipped**.
  The skipped checks require optional live CE/Redis or a fallback-only SDK path.
- Original icons copied without modification; PNG headers and hashes checked.

## PR #102 adversarial review

See `PR102-ADVERSARIAL-REVIEW.md` for the reproduced developer-runner failures,
fixes, regression evidence, and limits. This source review does not substitute
for GitHub's required approving review or an OpenAI submission receipt.
