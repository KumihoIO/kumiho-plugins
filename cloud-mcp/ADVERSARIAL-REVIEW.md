# PR #101 adversarial review

Reviewed 2026-09-16. Baseline: `0af330b2254c6d10dfef88637769602aa2654360`.
Scope: hosted MCP authentication, credential/context isolation, tool exposure,
request lifecycle, edge routing, locked image and CI. The resulting fixes and
regression tests are part of this PR. No unresolved code merge blocker was
identified after these fixes; deployment and public app submission have the
separate gates listed below. This is not a guarantee of no remaining defects.

## Findings resolved

| Priority | Finding and trigger | Resolution / regression evidence |
| --- | --- | --- |
| P1 | Expired JWKS cache still returned old keys after a failed refresh, and ignored an authoritative empty key set. Removed signing keys could remain trusted indefinitely. | Enforce cache freshness after refresh, accept empty replacement sets, reject malformed key documents/IDs. Tests reproduce stale-key acceptance before the fix and reject it after. Cold refresh failures now obey cooldown (12 concurrent requests previously made 12 outbound calls; now one). |
| P1 | Missing conversation IDs used the SDK's active-user pointer; concurrent conversations could share working memory. Explicit raw buffer IDs were not scoped to the authenticated user/client. | Require a host conversation ID or an explicitly echoed server-issued ID before touching a buffer. Namespace by tenant, user, OAuth client and host context; reject cross-identity issued IDs and host/argument conflicts. Real SDK handlers verify concurrent same-user buffers, isolated reads/clears, identity changes and token rotation. |
| P2 | Client reuse depended on optional/repeated `jti`; two distinct bearer credentials could reuse the first credential's client. | Include the full credential's SHA-256 fingerprint and user in the cache key. Both missing-jti and repeated-jti cases failed before the fix. |
| P2 | Cancellation interrupted lease release after its released flag was set, leaving retired channels permanently leased. Acquisition/eviction also had ownership-transfer cancellation gaps. | Shield ownership transfer and cleanup from the request cancellation scope. A cancelled request now releases and closes its retired channel. |
| P2 | Body buffering ran outside the deadline middleware, so a stalled upload bypassed the request time limit. | Put the deadline around body buffering too. A receive operation that never completes now returns 504 instead of waiting past the regression test's outer deadline. |
| P2 | Optional legacy SSE only bound a session to its tenant, permitting another user in that workspace to post to the same session. It also depended on a private SDK stream map. | Remove legacy `/sse` and `/messages/` and reject enabling the old flag at startup. Supported transport is MCP 2.x Streamable HTTP at `/mcp`. |
| P2 | The native profile path trusted any tool the SDK listed. A later SDK upgrade could expose unreviewed destructive tools. | Intersect both native and shim catalogs with the local reviewed 18-tool allowlist, and enforce it at dispatch. The native fake profile includes `kumiho_delete_project`; discovery and invocation now reject it. |
| P2 | The image workflow was manual-only, so the green repository PR checks did not run any hosted MCP tests. Manual publication also accepted non-main refs. | Run locked Python/Worker checks on relevant pull requests with read-only permissions; restrict image publication to manual dispatch on main. |

## Validation

- Windows, Python 3.12: **177 passed, 11 skipped**; Ruff passed.
- Newly built Linux/amd64 runtime image, Python 3.11, MCP 2.2.0, non-root,
  512 MiB: **177 passed, 11 skipped**. The tests import the installed image
  package, with only tests and pytest configuration mounted read-only.
- The suite includes real MCP 2.x HTTP clients in both supported initialization
  modes, AS/RS JWT contract tests, real SDK tenant-scoped writes and the new
  conversation buffer regressions. Backends for these checks are fixtures.
- Worker TypeScript check and **3 tests passed**.
- 29 new tests plus a strengthened native-profile test. The 11 skips are ten
  explicitly opt-in live CE/Redis checks and one fallback-only compatibility
  check; real production Firebase/ChatGPT login was not exercised.
- The sole warning comes from a deliberately short HMAC secret in a negative
  non-ES256 token test, which verifies rejection.

## Deployment / submission gates retained

- Build and publish a fresh image from the reviewed merge commit. The earlier
  ECR candidate from `0af330b` does not contain these security fixes and must
  not be used for rollout. This review does not update ECS or publish the Worker.
- Run a signed account-linking flow and two simultaneous ChatGPT conversations.
  Without a host conversation header, the client must echo the newly issued
  ID. This protocol is covered with real SDK handlers; actual ChatGPT retention
  of the ID still needs acceptance testing.
- Long-term graph memories remain workspace-scoped by design; the new namespace
  isolates temporary conversation buffers, not all workspace content.
- The previous ECR scan reported one HIGH zlib finding, CVE-2026-85091. Debian's
  tracker still marks trixie unfixed as of this review. It concerns nonblocking
  gzwrite/gzprintf buffer handling. No direct call to that API was identified
  in the hosted request path; this is an assessment, not proof of non-reachability.
  Re-scan the final deployment image and resolve or explicitly assess this
  dependency risk before public activation. [Debian advisory](https://security-tracker.debian.org/tracker/CVE-2026-85091).
- OAuth control PR #14 and server deployment-support PR #65 are already merged.
  OpenAI business verification is approved; app-directory approval is separate.
