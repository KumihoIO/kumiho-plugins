# PR #102 adversarial review

Reviewed on 2026-09-16 against main `26dbdceb2d2e173d3097c167bb23495263204e8a`.
The starting PR head was `953bdb1dd680ade6fdf6324bd4166868e87cd225`.
This review was performed by the implementing agent; it is not an independent
GitHub reviewer approval.

## Reproduced findings and fixes

| Severity | Failure before the fix | Resolution |
| --- | --- | --- |
| P2 | An empty memory search passed the live test because only the absence of a tool error was checked. Any nonempty item payload also passed readback. | Readback must return the created item's exact kref and retrieval must contain that item. An empty-result regression failed before the fix. |
| P2 | If the first buffer cleanup raised, the second buffer cleanup was skipped. | Attempt every issued buffer ID, then fail and record cleanup status if any attempt failed. Fault injection proves the second buffer is still cleared. |
| P2 | A 200 refresh response with no successor refresh token passed rotation (`None != old_token`), discarded the old token, and skipped revocation. | Require a nonempty access/refresh pair, Bearer token type and a changed refresh token before replacing the old pair. Retain ownership of the old refresh token for cleanup on invalid issuance. |
| P2 | Any HTTP 400 after revocation counted as successful rejection, including `invalid_request`. | Require HTTP 400 plus OAuth `invalid_grant`. The wrong-error regression failed before the fix. |

The four original failure cases were reproduced against the unmodified runner.
Additional coverage exercises empty credentials, incorrect token type, unchanged
refresh tokens, successful rotation/revocation, and successful buffer cleanup.
A non-ASCII callback state is rejected without a string-comparison exception.
The optional CE test's stale two-destructive-tool expectation is updated to the
six reviewed tools; that optional suite was not executed against a live CE.

## Source and catalog checks

- Local reviewed annotations override upstream defaults in discovery; all 54
  submission hints now have an automated comparison with the real hosted MCP
  response using the locked SDK.
- The stdio-only search credential field is removed from discovery, and direct
  calls containing it are rejected without echoing the value.
- Store/reflect/decompose descriptions disclose supersession effects and the
  consolidation description discloses buffer deletion. Hosted tools remain
  limited to the caller's authorized workspace.
- Submission assets, private local reports and the test runner are excluded
  from the runtime image; no credentials were added to the PR.
- The Worker origin matches the verified Seoul listener. No new infrastructure
  sizing change or authentication bypass is introduced by this PR.

## Validation

- Full hosted Python suite: **202 passed, 11 skipped**; Ruff passed.
- Developer-runner suite: **22 passed** with in-memory fake OAuth/MCP responses.
- Worker typecheck passed; **3 proxy/CORS/health tests passed**.
- The previous real OAuth run completed 63 checks. The stricter runner introduced
  by this review has not yet been rerun with a real login; do not retroactively
  treat those 63 checks as proving the new assertions.

No unresolved merge-blocking defect was found in the reviewed PR changes after
these fixes. This is bounded assurance, not a guarantee of no vulnerabilities.

## Public submission still requires

A dedicated reviewer account and synthetic workspace, the complete five positive
and three negative ChatGPT scenarios and real screenshots, exact portal domain
verification, and a submission receipt. Business verification and a working
private developer connection do not mean directory approval.

All 18 tools still lack outputSchema (see SUBMISSION-REVIEW.md). The existing
image scan's upstream zlib HIGH finding remains a release assessment item; this
PR does not resolve it. Do not attest unverified data-residency or retention
claims from the old PRIVACY.md draft.
