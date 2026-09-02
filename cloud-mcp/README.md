# kumiho-cloud-mcp

The hosted MCP **resource server** behind `https://mcp.kumiho.cloud/mcp` — the
thing a Claude user connects to when they add **Kumiho Memory** from the
Connectors Directory, and the thing a Team admin points a custom connector at.

It is deliberately small. It does not mint tokens, hold a database, or know
anything about a user beyond what a request's token says. Everything it does
happens per request:

```
Claude.ai / Desktop / Cowork / Claude Code
   │  Authorization: Bearer <ES256 access JWT>   or   x-api-key: <service token>
   ▼
mcp.kumiho.cloud/mcp        ← this service
   │  1. verify the token against control.kumiho.cloud's JWKS
   │  2. build a RequestContext + a tenant-scoped kumiho client
   │  3. run the Kumiho MCP tools in-process, wrapped in that context
   ▼
kumiho-server (gRPC, per-region)   ·   Redis via the control-plane proxy
```

The authorization server lives in the control plane (`control.kumiho.cloud`),
which already owns the ES256 key, Firebase verification and the tenant
directory. See `docs/CLOUD-CONNECTOR-PLAN.md` for the whole design; this README
covers running and deploying WP-C.

---

## Run it locally

### Dev mode against a local CE server

The fast loop. No control plane, no OAuth: auth is skipped, a fixed fake tenant
is used, and the backend is a self-hosted Kumiho CE server on loopback.

```bash
cd cloud-mcp
python -m pip install -e ".[dev]"

# Windows PowerShell
$env:KUMIHO_MCP_DEV_MODE = "ce"
python -m uvicorn kumiho_cloud_mcp.app:app --host 127.0.0.1 --port 8080

# Git Bash / POSIX
KUMIHO_MCP_DEV_MODE=ce python -m uvicorn kumiho_cloud_mcp.app:app --host 127.0.0.1 --port 8080
```

Then:

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/.well-known/oauth-protected-resource

# Add it to Claude Code
claude mcp add --transport http kumiho-dev http://127.0.0.1:8080/mcp

# Or drive it with the MCP Inspector
npx @modelcontextprotocol/inspector
```

Dev mode expects a CE server on `127.0.0.1:9190` (override with
`KUMIHO_LOCAL_SERVER_ENDPOINT`) and Redis on `127.0.0.1:6379`. Check the server
is up with `Test-NetConnection 127.0.0.1 -Port 9190` on Windows.

**Dev mode and Redis.** `KUMIHO_MCP_HOSTED=1` is set even in dev mode, because
several SDK guards read the process flag between requests and a dev run that
left it unset would exercise a different code path than production. Hosted mode
otherwise treats the control-plane Redis proxy as the *only* route to Redis —
that is what namespaces keys per tenant and authenticates per request — and dev
mode has no control plane at all.

`kumiho-memory` 1.4.0 adds the escape hatch for exactly this case, and dev mode
arms it: `KUMIHO_HOSTED_LOCAL_REDIS=1` plus `KUMIHO_LOCAL_REDIS_URL`
(`KUMIHO_MCP_DEV_REDIS_URL`, default `redis://127.0.0.1:6379`). The hatch only
fires when `KUMIHO_MCP_HOSTED=1` is *also* set, so a stray env var can never
redirect a plugin user's working memory to localhost, and keys stay namespaced
by tenant and user, so two dev tenants sharing one Redis still cannot see each
other. It logs a `WARNING` on every manager build. Never set it on a deployment
serving real tenants — the per-request token is not checked by anything.

**Two dev tenants.** In dev mode *only*, `x-kumiho-dev-tenant: <label>` picks a
different fake tenant for that request: the label is hashed into a stable
UUID-shaped `tenant_id` with its own `user_id`, so two client sessions get
separate manager-cache entries, Redis key prefixes and active-session pointers.
Outside `KUMIHO_MCP_DEV_MODE=ce` the header is not read at all — a caller's
tenant comes from their verified token and nothing else. It exists so
`tests/e2e/` can drive real isolation checks through one process.

### Against the real control plane

```bash
export KUMIHO_MCP_PUBLIC_URL=https://mcp.kumiho.cloud/mcp
export KUMIHO_AS_ISSUER=https://control.kumiho.cloud
export KUMIHO_CONTROL_PLANE_URL=https://control.kumiho.cloud
export KUMIHO_CONTROL_PLANE_INTERNAL_KEY=...   # service-token introspection
python -m uvicorn kumiho_cloud_mcp.app:app --port 8080
```

A dashboard API key is the easiest way to exercise a real tenant:

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "x-api-key: $KUMIHO_SERVICE_TOKEN" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Tests

```bash
cd cloud-mcp
python -m pytest -q            # everything
python -m pytest -q -k auth    # just the token branches
python -m pytest tests/test_live_ce.py -v -s   # real round trip, needs CE on :9190
python -m pytest tests/e2e -v -s               # full live stack, needs CE + Redis
```

The suite is hermetic apart from `test_live_ce.py` and `tests/e2e/`, which skip
themselves when no CE server (`:9190`) or Redis (`:6379`) answers. The control
plane (JWKS, introspection, discovery) is an `httpx.MockTransport`; the gRPC
client is a stub. `test_concurrency.py` runs several tenants through `/mcp` at
once and asserts that a tool handler — reached through the same
`asyncio.to_thread` hop production uses — never sees another tenant's client,
context or Redis token.

`tests/e2e/` is the opposite: nothing is stubbed. It spawns a dev-mode server
(or reuses one already on the port), drives it with the published `mcp`
streamable-HTTP client, and asserts the whole contract end to end — the 18-tool
profile with annotations, an out-of-profile `kumiho_delete_project` refused as a
tool error, engage → reflect → chat → consolidate → deprecate against the real
graph, and two `x-kumiho-dev-tenant` tenants that never see each other's
sessions, Redis keys or buffered messages. Run it with `-s`: every check prints
the payload it asserted on.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `KUMIHO_MCP_PUBLIC_URL` | `https://mcp.kumiho.cloud/mcp` | The URL clients enter. Must match the PRM `resource` exactly, and determines the mount path. |
| `KUMIHO_AS_ISSUER` | `https://control.kumiho.cloud` | Expected `iss`, and `authorization_servers[0]` in the PRM. |
| `KUMIHO_JWKS_URL` | `{issuer}/.well-known/kumiho-jwks.json` | ES256 verification keys. |
| `KUMIHO_CONTROL_PLANE_URL` | `https://control.kumiho.cloud` | Discovery + service-token introspection. |
| `KUMIHO_CONTROL_PLANE_INTERNAL_KEY` | — | `x-control-plane-key` for introspection. **Without it `x-api-key` auth fails closed.** |
| `KUMIHO_MCP_AUDIENCE` | `kumiho-server` | Expected `aud`. |
| `KUMIHO_MCP_HOSTED` | `1` | Shared-server rules: no `~/.kumiho`, no env credentials, per-tenant caches. |
| `KUMIHO_MCP_DEV_MODE` | — | `ce` disables auth and pins a fake tenant. **Never set in production.** |
| `KUMIHO_LOCAL_SERVER_ENDPOINT` | `127.0.0.1:9190` | CE backend for dev mode. |
| `KUMIHO_MCP_DEV_REDIS_URL` | `redis://127.0.0.1:6379` | Direct Redis for dev mode; becomes `KUMIHO_LOCAL_REDIS_URL`. |
| `KUMIHO_MCP_ALLOW_SHIM` | `0` | **Dev only.** Start even on `kumiho`/`kumiho-memory` older than the connector contract. See *The startup contract*. |
| `PORT` | `8080` | Listen port. |
| `KUMIHO_MCP_MAX_BODY_BYTES` | `2097152` | Request body cap (413 above it). |
| `KUMIHO_MCP_REQUEST_TIMEOUT_SECONDS` | `120` | Wall clock cap on a POST/DELETE; `0` disables. GET streams are exempt. Sized for `kumiho_memory_consolidate` — one tool call, a whole session's worth of graph writes. |
| `KUMIHO_MCP_JWKS_CACHE_SECONDS` | `3600` | JWKS cache lifetime. |
| `KUMIHO_MCP_JWKS_COOLDOWN_SECONDS` | `30` | Minimum gap between refreshes triggered by an unknown `kid`. |
| `KUMIHO_MCP_INTROSPECTION_CACHE_SECONDS` | `60` | Service-token revocation cache. |
| `KUMIHO_MCP_DISCOVERY_CACHE_SECONDS` | `600` | Per-tenant routing cache. |
| `KUMIHO_MCP_CLIENT_CACHE_MAX` | `1024` | gRPC clients held, keyed by `(tenant_id, token_id)`. |
| `KUMIHO_MCP_ENABLE_SSE` | `0` | Serve the legacy `/sse` + `/messages` transport. Off by default — see below. |
| `KUMIHO_MCP_JSON_RESPONSE` | `0` | Answer POSTs with JSON instead of SSE (tests use this). |
| `KUMIHO_MCP_LOG_LEVEL` | `INFO` | Root log level. |

Variables that must **never** be set on a hosted box, because they would give
every tenant one ambient identity: `KUMIHO_AUTH_TOKEN`, `KUMIHO_SERVICE_TOKEN`,
`UPSTASH_REDIS_URL` (production), `KUMIHO_HOSTED_LOCAL_REDIS` /
`KUMIHO_LOCAL_REDIS_URL` (dev-only direct Redis), `KUMIHO_MCP_ALLOW_SHIM`, and
`KUMIHO_MEMORY_DECISIONS` (Decision Memory assumes a local git checkout; the
service drops it with a warning if present).

### The SSE fallback is off by default

`KUMIHO_MCP_ENABLE_SSE` defaults to `0`. Claude connects over streamable HTTP,
and the deprecated HTTP+SSE transport roughly doubles the authenticated surface:
a long-lived `GET /sse` stream plus a `POST /messages/` whose only binding
between a message and a tenant is an in-process session map. Turn it on
knowingly, per deployment, if some legacy client ever needs it — and note that
`/messages/` then rejects a session id belonging to a different tenant with 403.

### The startup contract

Outside dev mode the service **refuses to start** if `kumiho < 0.13.0`,
`kumiho-memory < 1.4.0`, or `create_mcp_server` has no `profile=` parameter. The
`_compat` shim was written so this service could be built before WP-A and WP-B
landed, and it degrades gracefully — which is the danger. A deploy that picked
up an old `kumiho` would come up healthy while serving a *different* tool set
than the one the Claude directory listing was reviewed against, without the
SDK's tenant-keyed caches. That has to fail the deploy, not the tenants.
`KUMIHO_MCP_ALLOW_SHIM=1` bypasses the check and is for local development only.

## Routes

| Route | Auth | Notes |
|---|---|---|
| `GET /` | none | Human-readable pointer to the docs. |
| `GET /healthz` | none | `{"status":"ok", "tools": n, "expected_tools": 18, "tenant_managers": {...}, "clients": n, "sdk": {...}}`. `tenant_managers.count` is how many tenants hold a live memory manager, and `tenant_managers.process_singleton` must stay `false`. |
| `GET /.well-known/oauth-protected-resource` | none | RFC 9728. `Access-Control-Allow-Origin: *`. |
| `GET /.well-known/oauth-protected-resource/mcp` | none | Same document, path-suffixed form. |
| `GET\|POST\|DELETE /mcp` | required | Streamable HTTP, `stateless=True`. |
| `GET /sse` + `POST /messages/` | required | Legacy SSE transport. **Not mounted unless `KUMIHO_MCP_ENABLE_SSE=1`.** |

Every response carries `Cache-Control: no-store` and `X-Robots-Tag: noindex`.

## The 401 challenge

An unauthenticated request to `/mcp` is how a client discovers where to
authenticate, so the header shape is load-bearing:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://mcp.kumiho.cloud/.well-known/oauth-protected-resource", scope="memory"
```

`error="invalid_token"` is appended when a credential *was* presented and failed
— absent when none was presented at all, because "you did not authenticate" and
"your token is bad" are different signals to a client deciding whether to
refresh.

## Auth in one paragraph

Both credentials are the same ES256 JWT format, minted by the control plane with
the same key. An **OAuth access token** (`token_use == "mcp_access"`) is
short-lived, so signature + `iss` + `aud` + `exp` (+ `resource` when present) is
the whole check. A **service token** (`type == "service_token"`) is the
dashboard API key: it lives a year and deleting it in the dashboard does not
invalidate the JWT, so every request additionally introspects its `token_id`
against `POST {cp}/api/control-plane/service-token/introspect` (cached 60 s).
That check fails closed — if the control plane cannot be reached, or no
introspection key is configured, the request is refused rather than trusted.

## Deploy

```powershell
# One time: ECR repo, IAM roles, App Runner service, autoscaling
pwsh ./deploy/bootstrap-apprunner.ps1 -UpdateGitHubSecret
```

Then `.github/workflows/deploy-cloud-mcp.yml` builds the image, pushes to ECR,
updates App Runner, smoke-tests `/healthz` + the PRM + the 401 challenge, and
deploys the edge worker. **The workflow is committed but not wired to secrets** —
it only runs on `workflow_dispatch` until Morpheus creates them; the header of
the file lists exactly which.

`worker/` is the Cloudflare edge for `mcp.kumiho.cloud`: CORS for `claude.ai`
with the MCP transport headers, a per-IP brake, and never any caching. See
`worker/README.md`.

## What is where

```
cloud-mcp/
  kumiho_cloud_mcp/
    app.py                 Starlette app, routes, per-request wiring
    auth.py                JWKS cache, token verification, introspection, challenge
    clients.py             discovery routing + the tenant-scoped client pool
    settings.py            every env var, read once
    middleware.py          body cap, request timeout, no-store/noindex
    connector_profile.py   local mirror of the 18-tool profile + annotations
    _compat.py             shims for kumiho / kumiho-memory versions in flight
    logging_setup.py       JSON logs that cannot serialise a token
  tests/                   pytest + httpx ASGI; test_live_ce.py needs a CE server
  worker/                  Cloudflare edge worker
  deploy/                  App Runner bootstrap
  DIRECTORY.md             Claude Connectors Directory submission pack
  PRIVACY.md               privacy policy draft
```

## Compatibility shims

`_compat.py` exists because two sibling work packages were landing in parallel.
Each shim prefers the real implementation and degrades quietly:

- **Request context.** `kumiho.request_context` is canonical, but
  `kumiho-memory` vendors its own copy under `_request_context` so it can ship
  ahead of the SDK. Those are *different* ContextVars until the SDK version pin
  can be raised, so the app binds every provider it can find — binding only one
  would leave the other reading ambient credentials on every request.
- **Tool profile.** `create_mcp_server(profile="connector", instructions=...)`
  when the signature accepts it; otherwise the full server with `tools/list`
  filtered locally and annotations attached from `connector_profile.py`.
- **Call guard.** Applied on *both* paths. Filtering `tools/list` hides a tool
  from the model but leaves the JSON-RPC method reachable by name, and the full
  surface includes `kumiho_delete_project`. A public resource server enforces its
  own profile rather than trusting the list to be the boundary.
- **Redis token.** `kumiho_memory.redis_token_override_var` is set to the
  caller's token around every request.

Delete a shim when its version floor is raised, not before.
