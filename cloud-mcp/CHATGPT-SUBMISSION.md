# Kumiho Memory — ChatGPT / Codex submission package

Status: **prepared locally; not submitted or published**. Updated 2026-09-16.
Archetype: submission-ready preparation for a tool-only remote MCP app.

## Confirmed publisher and launch choices

| Field | Value |
| --- | --- |
| Display name | Kumiho Memory |
| Publisher / verified identity to select | Kumiho Inc. |
| Public support email | support@kumiho.io |
| Initial availability | South Korea (`KR`) only |
| Expansion | Global later, after country-specific readiness review |
| Primary listing language | Korean |
| Category | Productivity |
| Submitter role | Owner — user-confirmed |
| Business verification | Approved — user-confirmed |

The organization identifier and review contact are in the local, gitignored
`.local/submission-private.md` handoff. Select that same verified organization
in the portal. Owner status has not been independently inspected in the portal.
Country availability is a listing choice; it does not promise Korea-only data
processing. The MCP task is in Seoul, while the existing control plane is in
US East. Verify the full data-processing locations before any residency claim.

## Files to use

- `chatgpt-app-submission.json`: importable app info, 18 tool justifications,
  exactly five positive scenarios and three negative scenarios.
- `submission-assets/kumiho-fox.png`: selected app icon, original transparent
  PNG, 1254 × 1254, 844,363 bytes. Confirm the portal accepts the dimensions;
  no portal upload or automatic resize has been performed.
- `submission-assets/kumiho-white.png`: original white wordmark, transparent
  PNG, 949 × 326; use on dark backgrounds, not as the square directory icon.
- `SUBMISSION-REVIEW.md`: source inspection, remaining output-schema warnings
  and launch gates. The JSON does not contain findings or credentials.

Both images were supplied by the user and copied byte-for-byte. No generated
logo, visual redesign or image transformation was applied.

## Portal fields not included in the JSON importer

| Field | Prepared value / status |
| --- | --- |
| Submission type | With MCP; remote MCP only |
| URL type | Universal |
| MCP server URL | `https://mcp.kumiho.cloud/mcp` — live for developer testing; see DEVELOPER-TEST.md |
| Authentication | OAuth through `https://control.kumiho.cloud` |
| Website | https://kumiho.io/ |
| Support URL | https://kumiho.io/en/contact |
| Privacy policy URL | https://kumiho.io/en/legal — Privacy Policy section |
| Terms URL | https://kumiho.io/en/legal — Terms of Service section |
| Domain challenge | Waiting for the portal-issued token; never invent a token |
| Custom UI / CSP | No widget is exposed; no widget CSP domains to declare |
| Review credentials | Dedicated demo account and workspace still need provisioning and live verification |
| Screenshots | Capture real connected ChatGPT workflows after the endpoint is live |

The public contact/legal pages were fetched on 2026-09-16. They already name
Kumiho Inc. and cover AI memory. The root `/privacy` and `/terms` URLs could not
be verified; do not submit those guessed URLs. Review the actual policy against
the hosted service before attesting. `PRIVACY.md` is an older engineering draft,
not an approved replacement for the published policy.

## Reviewer setup — required before running the supplied scenarios

Provision a **dedicated, isolated demo workspace** with synthetic data only.
Do not give reviewers the publisher's own account or production workspace.
The following fixtures are specified here, not yet created:

1. A project named `CognitiveMemory` with a `review-demo` space.
2. A decision titled `서울 파일럿 리전 결정`: the pilot uses Seoul to reduce
   latency for its initial Korean users. Include that rationale in the summary.
3. A disposable item named `검토용 테스트 기억`, to retire in test 5; record its
   actual item reference in the private reviewer instructions after creation.
4. A prepared conversation buffer for test 4, created through the hosted
   conversation-ID flow. Retain its actual ID in that test conversation only.
5. A second demo conversation with a distinct buffer, to verify that clearing
   the first one does not touch the second. Provide no second-tenant credentials.

Use a review account that can complete sign-in without reviewer-side MFA, SMS,
email confirmation or private-network access. Enter its credentials only in the
portal's protected review fields, not in this repository, test prompts or chat.
Reset the disposable fixture between review attempts using normal authorized
workspace operations. Do not bypass tenant checks or disable authentication.

The scenarios in the JSON are **review instructions, not completed ChatGPT
acceptance tests**. Run all eight through the final deployed endpoint, recording
screenshots and expected results. The Python fixtures do not replace this step.

## Suggested starter prompts

- 전에 저장한 프로젝트 결정과 그 이유를 찾아줘.
- 이 결정을 기억해 줘: 서울 파일럿은 응답 지연을 줄이기 위해 서울 리전을 사용한다.
- 이번 대화의 핵심 결정을 요약해 저장하고 임시 버퍼를 정리해 줘.

## Initial release notes

Kumiho Memory brings private-workspace memory storage, contextual recall,
provenance and conversation summarization to ChatGPT and Codex through OAuth and
MCP 2.x. This is the initial public submission, available first in South Korea.
Writes and destructive modes are explicitly labeled; retiring a memory is not
permanent erasure. Review credentials use a dedicated synthetic-data workspace.

## Remaining release sequence

1. Review and merge the submission metadata changes; publish an image from that
   final commit. The pre-review `0af330b` image must not be used.
2. Re-scan the image and assess the previously reported upstream zlib finding.
3. Deploy the MCP sidecar into the existing Seoul ECS task, verify both old and
   new target mappings and rollback, then activate the public MCP Worker.
4. Verify real Firebase/OAuth linking, signed tool calls, two-chat separation,
   destructive confirmations and the dedicated review account.
5. Open the portal in the confirmed organization, import the JSON, upload the
   fox icon, fill publisher/policy/test fields and select South Korea only.
6. Install the exact generated domain-challenge token, scan the live tools,
   reconcile the scan with this JSON and attach real screenshots.
7. Complete factual attestations and submit. App review approval and the later
   publish action remain separate from business verification.

## Official references

- [Submission flow and required fields](https://developers.openai.com/plugins/deploy/submission)
- [Tool descriptions, annotations and data minimization](https://developers.openai.com/plugins/app-guidelines)
- [Public Kumiho legal page](https://kumiho.io/en/legal)
- [Public Kumiho support page](https://kumiho.io/en/contact)
