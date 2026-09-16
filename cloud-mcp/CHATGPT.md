# Kumiho Memory: ChatGPT / Codex hosted MCP

Status: OAuth control plane deployed; MCP production endpoint not yet deployed.
Updated 2026-09-16. PR #101 includes the PR #80 implementation and adversarial
review fixes; see [ADVERSARIAL-REVIEW.md](ADVERSARIAL-REVIEW.md).

## Implemented

- Native **MCP SDK 2.x** server integration (`mcp>=2.2.0,<3`), locked to **2.2.0**
  in the Linux image. Kumiho 0.13.0 / kumiho-memory 1.5.0 are locked with hashes.
- MCP 2.x constructor handlers and public `get_request_handler` API, full result
  models and snake_case fields; no old `request_handlers` maps or global monkey patches.
- The Kumiho SDK's existing v2 input validation and tenant-aware execution remain
  in use. Calls to tools absent from the published profile are refused, including
  project deletion. Hosted resources/prompts are not exposed.
- All 18 tools publish OAuth `memory` scope in `_meta.securitySchemes`. Public v2
  response middleware mirrors this at the top-level `securitySchemes` after core
  protocol validation, preserving the OpenAI extension that MCP 2.x typed models
  otherwise remove. Both forms are checked over the real HTTP wire.
- `KUMIHO_MCP_HOST_CONTEXT=chatgpt` is selected by the deployment. It is not derived
  from client-supplied headers or used to select the authenticated tenant.
- ChatGPT/Claude browser origins, current MCP request headers, OAuth challenges,
  uncached streaming and origin port 8443 are supported by the Worker.
- The Worker keeps forwarding pinned to its configured origin even for paths
  starting `//`; `/healthz` now reaches the MCP origin instead of reporting
  Worker-only health.
- PR CI runs the hosted Python/Worker suites. Manual main-only image publication
  produces a Seoul ECR candidate; ECS rollout is owned
  by `kumiho-server`, with a second NLB listener and the existing task size.

No OpenAI model API call or API key is needed for this server preparation.

## Test evidence

| Check | Result |
| --- | --- |
| Windows / Python 3.12 / MCP 2.2.0 | 177 passed, 11 skipped |
| Linux image / Python 3.11 / locked MCP 2.2.0, 512 MiB | 177 passed, 11 skipped |
| Real MCP 2.x HTTP client | Legacy initialization and 2026-07-28 modes passed |
| Profile and input validation | 18 tools, OAuth metadata, malformed arguments rejected, hidden delete rejected |
| Worker | TypeScript check + 3 tests passed |
| Deployment tooling in kumiho-server | 7 tests passed; AWS accepted network template validation |

The 11 skips are optional live CE/Redis tests and a fallback-only hosted-mode
test. The suite explicitly requires `KUMIHO_MCP_RUN_LIVE_CE=1` for disposable live
backends. It will not automatically use a developer's already-running CE/Redis.
The expected warning is from a deliberately invalid HS256 token in a negative
auth test. It does not indicate the ES256 production path accepts that token.

### Earlier container resource probe (before the review fixes)

Image `kumiho-cloud-mcp:sidecar-mcp2`, Linux/amd64, non-root `10001:10001`,
512 MiB memory limit, local 0.125 CPU hard limit, port 8081 bound to loopback only:

- Startup exposed 18 tools and strong-only stacking.
- 100 requests, concurrency 8: `/healthz` and unauthenticated `/mcp` all returned
  the expected 200 / 401 responses. OAuth discovery matched the public MCP URL.
- p95 93.23 ms, maximum 98.30 ms for this local light workload.
- Docker working-set sample: 89.27 MiB. Cgroup peak: 121.94 MiB, including the
  diagnostic Python process and cgroup-accounted cache.

These are **not** authenticated graph-operation benchmarks or proof of combined
production task capacity. Perform signed store/retrieve/consolidate tests and
measure the original Kumiho server during the deployment canary.

## Run locally

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e '.[dev]'
.venv/Scripts/python.exe -m pytest -q
```

```powershell
docker build --platform linux/amd64 -t kumiho-cloud-mcp:mcp2 .
docker run --rm --memory 512m --publish 127.0.0.1:18081:8081 --env PORT=8081 --env KUMIHO_MCP_HOST_CONTEXT=chatgpt kumiho-cloud-mcp:mcp2
```

`/healthz` and OAuth protected-resource metadata work without credentials.
Tenant tools require a valid bearer token and a functioning control plane.
Never enable CE dev mode on the public deployment.

## Remaining before a working public connection

1. **End-to-end OAuth**: control PR #14 is merged and the existing App Runner
   and control Worker serve OAuth discovery/JWKS/consent. Firebase authorized
   domain, OAuth DB tables/RLS and cleanup are configured. Public metadata and
   negative auth probes passed; a real Firebase login followed by signed
   ChatGPT tool calls remains to be validated.
2. **Origin and public route**: the Seoul sidecar network stack exists and
   server PR #65 preserves the sidecar on future deployments. ECS still runs
   the original task without MCP, and the MCP Worker is not deployed. Rebuild
   the ECR candidate from the final reviewed merge commit, then perform the
   ECS canary, TLS 8443 check, public Worker activation and rollback checks.
   The earlier `0af330b` image predates the review fixes and must not be rolled out.
3. **Submission behavior**: the user confirmed OpenAI business verification is
   approved. Complete privacy/data-deletion review, publisher/support details,
   screenshots, review account, domain challenge and app-directory submission.
   Business verification is distinct from app approval.

Authentication is currently enforced at the HTTP boundary with an OAuth challenge,
including before tools/list. This is the account-linking path being prepared.
Anonymous tool discovery and tool-result-level `_meta["mcp/www_authenticate"]`
relink UX have not been implemented/tested; do not describe this as that flow.

### Conversation identity

The hosted wrapper prevents the SDK's active-user fallback for all four buffer
tools. If the host supplies `X-Kumiho-Session-Id`, it is bound to the authenticated
tenant, user, OAuth client and host context. Without it, the first call returns
`session_required` and a fresh ID without accessing memory; the client retries
with that ID and reuses it only within the current conversation. Same-user
concurrent conversations, different users, host-ID conflicts and token rotation
are regression-tested through real SDK handlers. This requires a real ChatGPT
conversation test before submission: the server cannot infer host conversation
boundaries from an OAuth token, and an agent must retain the issued ID.

Legacy HTTP+SSE routes are removed. Enabling `KUMIHO_MCP_ENABLE_SSE` fails startup;
MCP 2.x Streamable HTTP at `/mcp` is the supported transport.

## Deployment files

In the sibling `kumiho-server` worktree on `codex/chatgpt-mcp-sidecar`:

- `scripts/aws/mcp_sidecar.py`: candidate task + original mapping rollback renderer
- `scripts/aws/mcp-sidecar-network.json`: new listener/target/logs/ingress only
- `scripts/aws/MCP-SIDECAR.md`: prerequisites, rollout and rollback commands
- `scripts/aws/render-task-definition.py` and `.github/workflows/deploy-aws.yml`:
  preserve the live MCP container on later server releases

The old App Runner bootstrap script and Claude-specific DIRECTORY.md are retained
as PR #80 history. Use this document and the ECS runbook for the selected rollout.

## References

- [MCP 2.x migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [OpenAI MCP server guide](https://developers.openai.com/plugins/build/mcp-server)
- [OpenAI authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI submission](https://developers.openai.com/plugins/deploy/submission)
