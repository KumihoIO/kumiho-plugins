# Kumiho Memory — directory submission

Updated 2026-09-16. **Portal draft prepared; not submitted or published.**
Tool-only remote MCP app, with no custom widget.

## Listing and publisher

| Field | Prepared portal value |
| --- | --- |
| Name / publisher | Kumiho Memory / Kumiho Inc. |
| Category / version | Productivity / 0.1.0 |
| Availability | South Korea (`KR`); global expansion later |
| Languages | English base (`en-US`), Korean translation (`ko-KR`) |
| Support | support@kumiho.io; https://kumiho.io/en/contact |
| Website | https://kumiho.io/ |
| Privacy and terms | https://kumiho.io/en/legal |
| MCP URL | https://mcp.kumiho.cloud/mcp |
| Authentication | OAuth through https://control.kumiho.cloud |
| Business identity | Verified Kumiho business selected in the portal |

Private organization/draft identifiers and review instructions are in ignored
`.local/` handoff files. Credentials are entered only in the portal's protected
reviewer field. KR listing availability does not imply Korea-only processing:
MCP and the review workspace are in Seoul; the control plane is in US East.
The historical `PRIVACY.md` is an engineering draft, not approved policy.

## Submission contents

- `chatgpt-app-submission.json`: 18 tools, 54 annotation justifications,
  five positive scenarios and three negative scenarios. Portal test prompts
  are reconciled with observed ChatGPT paths.
- `submission-assets/kumiho-fox.png`: original user-supplied fox PNG, uploaded
  in both icon fields. The white wordmark remains a separate brand asset.
- Three Korean starter prompts, release notes and Korean listing translation.
- Portal-issued domain proof deployed and verified; live scan found 18 tools.
- Dedicated review account containing only synthetic fixtures in
  `CognitiveMemory/review-demo`, on Seoul's existing paid Neo4j instance.
- A 90-second edited walkthrough made from actual ChatGPT browser frames:
  `worker/public/review/7bf5db6c415e4fb6a7a5dc5c0d2b9e61/`. It identifies itself
  as recorded frames with waiting time shortened, not continuous recording.
  It contains no reviewer password or private conversation history.

## Deployment and verification

The SDK fix isolates credential-bound project and bundle handles per tool call,
preventing reuse of old credentials after token rotation or another user's call.
Signing keys and regional database assignments were not changed.

- Hosted suite: **209 passed, 11 skipped**; Ruff passed.
- Actual reviewer OAuth and hosted server: **47/47 checks passed**, including
  nonempty reflect capture, separate buffers, cleanup, refresh and revocation.
- Actual ChatGPT: all five positive workflows and three negative scenarios
  exercised; see `DIRECTORY-ACCEPTANCE.md` for observed tools and limitations.
- Summary and clear used `kumiho_memory_store` then `kumiho_chat_clear`; the
  portal lists this observed path rather than an unobserved consolidate call.
- Final runtime source `78b4209`, ECS revision **23**, completed with one task.
  Image manifest digest:
  `sha256:51a9ae649d7b53a89f8d518d026dfab5906f471a864868bddbcac4dc4050ac6f`.
- PR #102 is merged; follow-up fixes and assets are in PR #103.
- Worker TypeScript and all five proxy/asset tests passed. Public demo, MCP
  health and domain verification paths return HTTP 200. The asset binding
  returned the complete 973 KB MP4 even for a Range request; browser loading
  succeeded without media errors.
- No additional compute, database, signing key, subscription or R2 bucket.

## Remaining submission steps

1. Public demo URL is live, loads as a 90-second video without media errors,
   and is entered in the draft. Protected review notes contain final evidence.
2. Check required fields. Legal attestations remain unchecked until the owner
   confirms them at submission time.
3. Submit and verify a receipt/status. Business verification, app review
   approval and public publishing are separate states.

## Known limitations

- All 18 tools omit `outputSchema`; add schemas paired with actual structured
  success/error output. The portal recommends them; none are invented here.
- The final Linux image scan reports one HIGH zlib finding, CVE-2026-85091.
  Debian still marks the installed trixie package unfixed on 2026-09-16. The
  affected nonblocking gzip formatted-write API was not found in the hosted
  request path; that is not proof of non-reachability. Track before public
  activation; see `ADVERSARIAL-REVIEW.md`.
- A finite acceptance run does not guarantee future model tool choices.

## References

- [Official submission flow](https://developers.openai.com/plugins/deploy/submission)
- [App guidelines](https://developers.openai.com/plugins/app-guidelines)
- [Debian zlib finding](https://security-tracker.debian.org/tracker/CVE-2026-85091)
