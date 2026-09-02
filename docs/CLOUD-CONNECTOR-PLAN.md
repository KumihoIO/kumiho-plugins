# Kumiho Memory — Hosted Claude Connector Plan

Status: DRAFT v1 · 2026-09-02 · owner: Morpheus · authored by Miho (Fable 5.1), implemented by Opus 5 work packages.

Goal: a Kumiho Cloud user opens Claude → Connectors → Directory → "Kumiho Memory" → clicks **+** → signs in to Kumiho (or pastes an API key) → done. Same server also works as a *custom connector* (Team admin with `x-api-key`) and in Claude Code (`claude mcp add --transport http`). No local Python, no venv, no hooks.

This is a **lite tier**. The Claude Code / Cowork plugin (`kumiho-plugins/claude`) stays the **pro tier** (host-side prefetch, session rotation on `/clear`, Decision Memory over git, transcript artifacts). The docs must say so.

---

## 0. Ground truth (verified 2026-09-02)

### What exists

| Piece | Where | State |
|---|---|---|
| MCP server (stdio only) | `kumiho-SDKs/python/python/kumiho/mcp_server.py` (4330 lines, low-level `mcp.server.Server`, mcp 1.x/2.x shim). `create_mcp_server()` exists. | 45 core tools + 18 memory tools = **63 tools**, no profile mechanism, no annotations, no `instructions`. `run_server()` is `stdio_server()` only; `main()` installs an orphan watchdog + `os._exit`. Process-global caches `_project_cache`, `_known_spaces`, `_bundle_cache`, `_space_registry_cache` keyed by project name only (tenant-unsafe). `os.environ["KUMIHO_AUTH_TOKEN"]=…` mutation at :786/:831. |
| Memory logic (client-side!) | `kumiho-memory/kumiho_memory/mcp_tools.py` `_build_manager()` :176-363 builds a **process singleton** `UniversalMemoryManager` from `os.environ`. Session resolution :410-435 (explicit → `KUMIHO_SESSION_ID` env → shared active-session pointer → generated). | Existing per-request seam: `redis_memory._token_override_var` (ContextVar) :31-33, exported as `kumiho_memory.redis_token_override_var`. Redis buffer already supports the control-plane proxy `{cp}/api/memory/redis` :750-761 with Bearer = override token. Embeddings are server-side. LLM is optional (keyless core). |
| SDK request scoping | `kumiho/__init__.py:190` `_client_context_var`, `use_client(client)` ctx manager, `get_client()` prefers the contextvar. `kumiho.connect(...)` builds an explicit client. | Good seam. `mcp_server.py:3752` already uses `asyncio.to_thread` so contextvars propagate. |
| Existing hosted MCP (prior art) | `kumiho-FastAPI/app/core/mcp.py` :31-60 runs `create_mcp_server()` in-process under `StreamableHTTPSessionManager(stateless=True)` at `/api/v1/mcp/tools`, wrapped in `with kumiho.use_client(client)`. Per-request client from `app/dependencies.py:537 get_kumiho_client` (Firebase→CP token exchange, discovery routing, `kumiho.connect`). | Repo is stale (last commit 2026-04-17). Bugs: `/list` and `/invoke` call `configure_default_client` (cross-tenant leak); **no `_token_override_var` bridge** so memory tools use ambient creds; no `x-api-key`. Playground does the bridge correctly at `app/apps/playground/providers.py:93-115`. |
| Existing OAuth AS (prior art) | `kumiho-plugins/gpt/kumiho_gpt_connect/gateway/oauth.py` (452 lines): RFC 8414 + RFC 9728 metadata, `/register` (DCR), `/authorize`, `/token`, PKCE S256, RS256, rotated refresh tokens. | Single-operator PIN auth, redirect allowlist = chatgpt.com/openai.com, state in `~/.kumiho/gpt/`. Shape is right, identity is wrong. |
| Control plane | `kumiho-control` (private). Worker = CF edge proxy for `control.kumiho.cloud` → Origin = Next.js 15 API-only on AWS App Runner us-east-1 (ECR `kumiho-control-plane`). Supabase Postgres via service-role. | Endpoints: `POST /api/control-plane/token` (Firebase ID token → **ES256 15-min JWT**, `iss=https://control.kumiho.cloud`, `aud=kumiho-server`, `sub`=firebase uid, claims `tenant_id, tenant_slug, tenant_tier, neo4j_db_name, roles[], guardrails{…}, memory_enabled, memory_tier, memory_add_on, region_code, neo4j_instance_id, is_trial, trial_ends_at, jti`); picks `memberships[0]` (TODO at :98). `POST/GET/DELETE /api/control-plane/service-token` = the dashboard "API Keys": **1-year ES256 JWT**, `sub`=tenant id, `type:'service_token'`, `token_id`, `jti=token_id`; metadata row in `service_tokens` (id, tenant_id, name, token_prefix, created_by, expires_at); **deletion does not revoke** (no jti check anywhere). Public JWKS `https://control.kumiho.cloud/.well-known/kumiho-jwks.json` (kid `kumiho-cp-key-1`). `POST /api/discovery/tenant` (Firebase or CP token, `tenant_hint`). `POST /api/memory/redis` (Firebase-or-CP dual auth :70-100; keys `kumiho:memory:{tenant_id}:…`, per-tenant only). `lib/firebase/verifyIdToken.ts`, `lib/control-plane/verifyControlPlaneToken.ts`, `lib/control-plane/tenantLookup.ts`. **No OAuth, no login UI, no sessions.** Worker CORS allowlist lacks `https://claude.ai`, `x-api-key`, `mcp-session-id`, `mcp-protocol-version`. CF WAF challenges non-browser UAs and rate-limits `/api/control-plane/token` 10/min/IP (`packages/origin/docs/cloudflare-config.md:55-83`). |
| Backend | kumiho-server (Rust, gRPC) verifies CP JWTs via `control_plane_jwks_url` (sig + iss + aud + exp only). Neo4j + embeddings server-side. Upstash Redis per region via the proxy. | Any ES256 JWT with the CP claim shape is accepted; extra claims are ignored. |

### What Claude requires (claude.com/docs/connectors/building/authentication + /submission, fetched 2026-09-02)

- Directory listing → **OAuth 2.0 required** for authenticated services. Auth types: `oauth_dcr` (out of box), `oauth_cimd` (out of box; Claude picks CIMD only if AS metadata has `client_id_metadata_document_supported: true` **and** `"none"` in `token_endpoint_auth_methods_supported`), `oauth_anthropic_creds` (email mcp-review@anthropic.com), `static_headers` (beta; org admin enters `authorization` or `x-api-key` once, shared by the org), `none`.
- Prefer **CIMD or anthropic_creds over DCR** for directory traffic (DCR registers a new client per fresh connection).
- PKCE `S256` on every request; metadata must advertise `code_challenge_methods_supported: ["S256"]`.
- Redirect URIs: hosted surfaces `https://claude.ai/api/mcp/auth_callback`; Claude Code = loopback `http://localhost/callback` and `http://127.0.0.1/callback` **port-agnostic** (CIMD doc `https://claude.ai/oauth/claude-code-client-metadata`). Consent screen must display the redirect host; warn when only loopback.
- RS must answer unauthenticated requests with **`401` + `WWW-Authenticate: Bearer resource_metadata="<PRM url>"`** (optionally `scope="…"`). PRM (RFC 9728) `resource` must equal the MCP URL exactly as entered; `authorization_servers[0]` is used (no fallback). AS serves RFC 8414 metadata at its own `/.well-known/oauth-authorization-server`.
- Claude appends `offline_access` if listed in `scopes_supported`; refresh reactively on 401 and proactively 5 min before expiry; rotate refresh tokens for public clients; RFC 6749 error codes (`invalid_grant`); `/token` accepts `application/x-www-form-urlencoded`; `/register` is JSON.
- Latency: discovery/registration/token ≤ 10 s, refresh ≤ 30 s. Egress `160.79.104.0/21`.
- Submission: Claude.ai **Team/Enterprise org**, portal `claude.ai/admin-settings/directory/submissions/new`. Every tool needs `title` + `readOnlyHint` or `destructiveHint`. Listing: name ≤100, tagline ≤55, description ≤2000, 1–5 categories, docs URL, privacy policy URL, support contact, icon, permanent slug, use cases, data-handling answers, test account, 7 compliance acknowledgments. Server URL must be `https://`, transport streamable HTTP or SSE.

---

## 1. Architecture

```
Claude.ai / Desktop / Cowork / Claude Code
   │  OAuth 2.1 (code + PKCE S256; DCR or CIMD)          ┌──────────────────────────────┐
   ├────────────────────────────────────────────────────▶│ AS = control.kumiho.cloud     │ (kumiho-control, TS)
   │  /.well-known/oauth-authorization-server            │ /oauth/authorize (consent UI) │
   │  /api/oauth/{register,token,revoke}                 │ Firebase sign-in or API key   │
   │                                                     │ mints ES256 access JWT        │
   │  Bearer <access JWT>  or  x-api-key: <service tok>  └──────────────────────────────┘
   ▼
mcp.kumiho.cloud/mcp  (RS = kumiho-plugins/cloud-mcp, Python/Starlette)        ← NEW service
   • verifies JWT via CP JWKS (ES256, iss, aud, exp, token_use|type)
   • builds RequestContext(tenant, user, token, session) → contextvars
   • kumiho.mcp_server.create_mcp_server(profile="connector") in-process,
     StreamableHTTPSessionManager(stateless=True)
   • gRPC → kumiho-server (Bearer + x-tenant-id)      • Redis via control-plane proxy (Bearer)
```

Decisions (do not re-litigate; write down objections in the PR instead):

1. **AS lives in the control plane (TypeScript)**, not in Python. It already owns the ES256 key, JWKS, Firebase verification, tenant directory, Supabase. Cross-host AS is explicitly supported by Claude.
2. **Access token = CP-format ES256 JWT** (same key, `iss=https://control.kumiho.cloud`, `aud="kumiho-server"` as a single string for kumiho-server compatibility) with extra claims `token_use:"mcp_access"`, `client_id`, `scope`, `resource:"https://mcp.kumiho.cloud/mcp"`, `sub`=firebase uid, plus the full tenant claim set. Lifetime **3600 s**. kumiho-server and the Redis proxy accept it unchanged, so the RS never exchanges tokens.
3. **Refresh token = opaque, hashed at rest, rotated (single use), 90 days**, family-revoked on reuse. Refresh re-runs tenant lookup so tier/guardrail changes propagate.
4. **RS is a new minimal service** (`mcp.kumiho.cloud`), not kumiho-FastAPI: small attack surface, independent pinning of `kumiho`/`kumiho-memory`, same deploy template (ECR + App Runner + CF Worker).
5. **Both DCR and CIMD** supported from day one; `oauth_anthropic_creds` is a later email to mcp-review.
6. **`x-api-key` = existing service token** (dashboard "API Keys"). Requires a revocation check → new internal introspection endpoint; RS caches results 60 s.
7. **Scopes**: `memory` (all tools) and `offline_access`. `scopes_supported` on both metadata docs = `["memory","offline_access"]`.
8. **Multi-tenancy is per-request via contextvars**, never process env. Hosted mode is opt-in (`KUMIHO_MCP_HOSTED=1`) so the stdio plugin path is untouched.
9. **Tool profile `connector`** exposes ~19 curated tools with annotations + server `instructions` carrying the engage/reflect protocol (there is no skill or hook remotely).
10. Memory identity in hosted mode: `user_id = sub` (OAuth) or `service:<token_id>` (API key); `context = "claude"`. Session = explicit arg → active-session pointer per (context, user) → generated. No local artifacts, no LLM fallback in v1 (keyless core only; enable per-tenant later).

---

## 2. Shared contract (all work packages code against this)

### 2.1 `kumiho.request_context` (NEW, owned by WP-A; WP-B/WP-C import it with a vendored fallback until A lands)

```python
# kumiho/request_context.py
from __future__ import annotations
import contextvars
from dataclasses import dataclass, field
from typing import Iterator, List, Optional
from contextlib import contextmanager

@dataclass(frozen=True)
class RequestContext:
    tenant_id: str            # UUID from token claims
    user_id: str              # firebase uid (OAuth) or "service:<token_id>" (API key)
    auth_token: str           # the raw bearer/api-key JWT presented by the caller
    context: str = "claude"   # memory context namespace (active-session pointer key)
    session_id: Optional[str] = None
    client_id: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    tenant_slug: Optional[str] = None
    region_code: Optional[str] = None
    token_id: Optional[str] = None   # jti

_request_var: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar("kumiho_request", default=None)

def current_request() -> Optional[RequestContext]:
    return _request_var.get()

@contextmanager
def request_context(ctx: RequestContext) -> Iterator[RequestContext]:
    token = _request_var.set(ctx)
    try:
        yield ctx
    finally:
        _request_var.reset(token)

def hosted_mode() -> bool:
    import os
    return os.environ.get("KUMIHO_MCP_HOSTED", "").strip().lower() in ("1", "true", "yes")
```

Rules when `current_request()` is not None:
- never read `~/.kumiho/*`, never mutate `os.environ`, never use the machine-id discovery cache file;
- all per-process caches must be keyed by `tenant_id`;
- `kumiho.get_client()` must resolve via `use_client` (WP-C wraps every request in `with kumiho.use_client(client)`), and `kumiho_memory` must set `redis_token_override_var` from `ctx.auth_token` if unset.

### 2.2 `kumiho.mcp_server` additions (WP-A)

```python
create_mcp_server(profile: str | None = None, instructions: str | None = None) -> Server
# profile: None/"full" = today's 63 tools; "connector" = curated list below. Env fallback KUMIHO_MCP_TOOL_PROFILE.
# instructions default for "connector" = kumiho.mcp_server.CONNECTOR_INSTRUCTIONS
TOOL_ANNOTATIONS: dict[str, dict]   # name -> {"title","readOnlyHint","destructiveHint","idempotentHint","openWorldHint": False}
```

Connector profile tool list (exact names, all existing):

| Read (readOnlyHint=true) | Write (readOnlyHint=false, destructiveHint=false) | Destructive (destructiveHint=true) |
|---|---|---|
| kumiho_memory_engage, kumiho_memory_recall, kumiho_memory_retrieve, kumiho_chat_get, kumiho_search_items, kumiho_get_item, kumiho_get_revision_by_tag, kumiho_list_projects, kumiho_get_spaces, kumiho_get_provenance_summary | kumiho_memory_reflect, kumiho_memory_store, kumiho_memory_consolidate, kumiho_memory_decompose, kumiho_memory_space_profile (persists profile items unless dry_run), kumiho_create_space | kumiho_deprecate_item (title "Forget a memory"), kumiho_chat_clear |

Revision 2026-09-02 (after WP-A): **18 tools**. `kumiho_memory_dream_state` is out of the connector profile for v1 because it needs an LLM key and hosted tenants are keyless (§1.10); it stays annotated (`destructiveHint=true`) in the full profile. `kumiho_memory_space_profile` is a write, not a read.

Every one of the 63 tools gets an annotation entry (the directory syncs whatever is exposed; the plugin benefits too).

### 2.3 `kumiho_memory` behavior in hosted mode (WP-B)

- `mcp_tools._get_manager()` → when `current_request()` is set: return a manager from an LRU cache keyed by `tenant_id` (max 256, idle TTL 30 min), built with the request token for the Redis proxy and **no** env token / no local artifact dir / no LLM assessor unless `KUMIHO_HOSTED_LLM=1`. Otherwise: existing singleton (unchanged).
- Session resolution order: explicit arg → `ctx.session_id` → active-session pointer keyed `(ctx.context, ctx.user_id)` → generate + set pointer. `user_id`/`context` defaults come from ctx. Results keep reporting `session_id` + `session_id_source` (`"argument" | "request" | "active_session" | "generated"`).
- `memory_manager` session lookup prefers the contextvar over `KUMIHO_SESSION_ID`.
- Engage/recall dedup guard (5 s) keyed by `(tenant_id, user_id, session_id)` not process-global.
- `RedisMemoryBuffer`: if `redis_token_override_var` is unset and `current_request()` is set, use `ctx.auth_token`. In hosted mode never fall back to `UPSTASH_REDIS_URL` env.
- `_write_artifact` and any filesystem writes: no-op when hosted.
- All new behavior covered by tests that run two fake tenants concurrently and assert no cross-talk (manager identity, session ids, redis token).

### 2.4 RS service (WP-C) — `kumiho-plugins/cloud-mcp`, package `kumiho_cloud_mcp`

Env: `KUMIHO_MCP_PUBLIC_URL=https://mcp.kumiho.cloud/mcp`, `KUMIHO_AS_ISSUER=https://control.kumiho.cloud`, `KUMIHO_JWKS_URL=https://control.kumiho.cloud/.well-known/kumiho-jwks.json`, `KUMIHO_CONTROL_PLANE_URL`, `KUMIHO_CONTROL_PLANE_INTERNAL_KEY` (introspection), `KUMIHO_MCP_HOSTED=1`, `PORT=8080`, dev-only `KUMIHO_MCP_DEV_MODE=ce` (no auth; CE backend at `KUMIHO_LOCAL_SERVER_ENDPOINT`, local Redis; a fixed fake tenant/user).

Routes:
- `GET /healthz` (200 JSON), `GET /` (short HTML/JSON pointer to docs).
- `GET /.well-known/oauth-protected-resource` and `GET /.well-known/oauth-protected-resource/mcp` → `{"resource": PUBLIC_URL, "authorization_servers": [ISSUER], "scopes_supported": ["memory","offline_access"], "bearer_methods_supported": ["header"], "resource_documentation": "https://kumiho.io/docs/connect/claude"}`.
- `GET|POST|DELETE /mcp` → auth middleware → `StreamableHTTPSessionManager(create_mcp_server(profile="connector"), stateless=True, json_response=False)`. Also mount `/sse` + `/messages` (SSE fallback) if cheap with mcp 1.26; otherwise skip and document.
- Auth: `Authorization: Bearer <jwt>` or `x-api-key: <jwt>`. Verify ES256 with JWKS (cache 1 h, refresh on unknown kid), `iss == ISSUER`, `aud` contains `kumiho-server`, `exp`, then either `token_use == "mcp_access"` (OAuth) or `type == "service_token"` (API key → introspect `token_id` via control plane, cache 60 s, reject if inactive). Missing/invalid → `401` with `WWW-Authenticate: Bearer resource_metadata="<PUBLIC_ORIGIN>/.well-known/oauth-protected-resource", scope="memory"` (+ `error="invalid_token"` when a token was present). Wrong `resource` claim (when present) → 401.
- Per request: build `RequestContext`, resolve routing via `POST {cp}/api/discovery/tenant` (Bearer = token, cache per tenant 10 min), `client = kumiho.connect(...)` per FastAPI `get_kumiho_client` pattern (cache clients per `(tenant_id, token_id)` with TTL ≤ token exp), then `with kumiho.use_client(client), request_context(ctx): await session_manager.handle_request(scope, receive, send)`.
- Security: body limit 2 MB, request timeout 60 s, structured JSON logs with `tenant_id`/`jti` never the token, `Cache-Control: no-store` on all auth-bearing responses, `X-Robots-Tag: noindex`.
- Packaging: `pyproject.toml` (deps `kumiho[mcp]`, `kumiho-memory[all]`, `starlette`, `uvicorn[standard]`, `PyJWT[crypto]` or `python-jose`, `httpx`), `Dockerfile` (python:3.11-slim, non-root, `uvicorn kumiho_cloud_mcp.app:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips *`), `worker/` Cloudflare edge worker copied from kumiho-FastAPI's (route `mcp.kumiho.cloud/*`, CORS allow `https://claude.ai` + MCP headers, never cache), `deploy/bootstrap-apprunner.ps1` adapted from kumiho-control `scripts/aws/bootstrap-apprunner.ps1`, GitHub Actions workflow `deploy-cloud-mcp.yml` (ECR + App Runner us-east-1) — workflow committed but **not** wired to secrets (Morpheus does that).
- Tests: JWT verification (good/expired/wrong aud/wrong iss/unknown kid), 401 shape, PRM document, API-key introspection cache, two-tenant concurrency through `/mcp` with a fake kumiho client, MCP `initialize` → `tools/list` shows exactly the connector profile with annotations, `tools/call kumiho_memory_engage` round-trips in dev mode against CE if reachable (skip otherwise).
- Docs: `cloud-mcp/README.md` (run locally, dev mode, deploy), `cloud-mcp/DIRECTORY.md` (the full submission pack: listing copy, tool table, use cases, data handling, test-account script, custom-connector + Claude Code instructions, pro-vs-lite matrix), `cloud-mcp/PRIVACY.md` draft.

### 2.5 Control plane (WP-D) — `kumiho-control/packages/origin`

New files under `src/app/`:
- `.well-known/oauth-authorization-server` via `next.config.mjs` rewrite → `api/oauth/metadata/route.ts`: `issuer`, `authorization_endpoint: {issuer}/oauth/authorize`, `token_endpoint: {issuer}/api/oauth/token`, `registration_endpoint: {issuer}/api/oauth/register`, `revocation_endpoint: {issuer}/api/oauth/revoke`, `jwks_uri: {issuer}/.well-known/kumiho-jwks.json`, `scopes_supported: ["memory","offline_access"]`, `response_types_supported: ["code"]`, `grant_types_supported: ["authorization_code","refresh_token"]`, `code_challenge_methods_supported: ["S256"]`, `token_endpoint_auth_methods_supported: ["none","client_secret_post"]`, `client_id_metadata_document_supported: true`, `service_documentation: https://kumiho.io/docs/connect/claude`. Public CORS `*`, cache ≤ 5 min.
- `api/oauth/register/route.ts` (RFC 7591, JSON): public clients (`token_endpoint_auth_method: "none"`), require `redirect_uris` all `https://` **or** loopback (`http://localhost/...`, `http://127.0.0.1/...`, any port); store in `oauth_clients` (client_id = `dcr_<random>`, redirect_uris jsonb, client_name, client_uri, created_at, last_used_at). Rate-limit friendly. GC job note for stale clients.
- CIMD: `client_id` that is an `https://` URL → fetch `application/json` metadata (timeout 5 s, no redirects to private IPs, size ≤ 64 KB, cache 1 h in-memory + Supabase `oauth_cimd_cache`), require `client_id` in doc == URL, take `redirect_uris` from the doc. Loopback match ignores port. Explicitly test `https://claude.ai/oauth/claude-code-client-metadata`.
- `oauth/authorize/page.tsx` + `api/oauth/authorize/route.ts`: GET validates `response_type=code`, `client_id` (DCR row or CIMD), `redirect_uri` ∈ registered (exact, loopback port-agnostic), `code_challenge` + `code_challenge_method=S256` (required), `state`, `scope` ⊆ supported (default `memory`), optional `resource` (if present must equal `https://mcp.kumiho.cloud/mcp`, else `invalid_target`). Persist `oauth_pending_authorizations` row (id, expires 10 min, all params) and render the consent page: Kumiho branding, "**Claude** wants to read and write your Kumiho memory", **redirect host displayed prominently** (extra warning for loopback), Firebase sign-in (email/password + Google, using `NEXT_PUBLIC_FIREBASE_*`), tenant selector when the user has >1 membership, and an "Use an API key instead" field accepting a service-token JWT. Approve/Deny buttons. Invalid `redirect_uri`/`client_id` → render error page, never redirect.
- `api/oauth/authorize/decision/route.ts` (POST JSON): `{pending_id, approve, id_token? | api_key?, tenant_id?}` → verify identity (`verifyFirebaseIdToken` or `verifyControlPlaneToken` + `type==='service_token'` + introspection), resolve membership for the chosen tenant (must be a member), mint `oauth_authorization_codes` row (code = random 32 B url-safe, hashed at rest, single-use, 10 min, bound to client_id/redirect_uri/code_challenge/scope/resource/user/tenant), respond `{redirect_to: redirect_uri?code=…&state=…}`; deny → `error=access_denied`.
- `api/oauth/token/route.ts` (POST **form-urlencoded**, also accept JSON): `authorization_code` → verify code (unexpired, unused, client_id match, redirect_uri match, PKCE `S256(code_verifier) == code_challenge`) → issue access JWT (see §1.2, via shared `buildTenantClaims`) + rotated refresh token; `refresh_token` → look up by hash, check family/rotation, re-resolve tenant claims, issue new pair, invalidate old; errors as RFC 6749 JSON (`invalid_grant`, `invalid_client`, `invalid_request`, `unsupported_grant_type`) with `Cache-Control: no-store`. Response `{access_token, token_type:"Bearer", expires_in:3600, refresh_token, scope}`. Must answer well under 10 s.
- `api/oauth/revoke/route.ts` (RFC 7009): revoke refresh token family; access tokens simply expire (1 h).
- `api/control-plane/service-token/introspect/route.ts` (POST, `x-control-plane-key`): `{token_id}` → `{active, tenant_id, expires_at}` from `service_tokens`.
- Refactor `lib/control-plane/claims.ts`: `buildTenantClaims({user, membership, tenant, tier})` shared by `token/route.ts`, `service-token/route.ts`, and the new token endpoint; add optional `tenant_id` selection to `/api/control-plane/token` (replaces `memberships[0]`, keeps old behavior when absent).
- Supabase migration `supabase/migrations/<ts>_oauth.sql`: `oauth_clients`, `oauth_pending_authorizations`, `oauth_authorization_codes`, `oauth_refresh_tokens` (token_hash, family_id, client_id, firebase_uid, tenant_id, scope, expires_at, rotated_at, revoked_at), `oauth_cimd_cache`. Indexes on hashes and expiries.
- Worker (`packages/worker`): CORS add origin `https://claude.ai` and headers `x-api-key, mcp-session-id, mcp-protocol-version, last-event-id`; never-cache `/oauth`, `/api/oauth`, `/.well-known/oauth-authorization-server`. `next.config.mjs`: keep `X-Frame-Options: DENY` on `/api/*`, add CSP for the consent page. Update `packages/origin/docs/cloudflare-config.md`: exempt Anthropic egress `160.79.104.0/21` from the UA challenge and from the 10/min limit on the new `/api/oauth/*` paths; add a 60/min limit on `/api/oauth/token`.
- Tests (vitest, as the repo already uses): metadata shape, DCR validation, CIMD fetch + loopback matching, full code+PKCE happy path, PKCE mismatch, code reuse, refresh rotation + reuse detection, introspection, tenant selection.

---

## 3. Work packages (Opus 5 agents)

| WP | Repo / branch | Owner | Depends on |
|---|---|---|---|
| A | kumiho-SDKs `feat/mcp-connector-profile` | Opus | — |
| B | kumiho-memory `feat/hosted-request-context` | Opus | contract §2.1 (vendored fallback) |
| C | kumiho-plugins `feat/cloud-mcp` (`cloud-mcp/`) | Opus | contract §2.1–2.3 (code against it; pin local paths in dev extras) |
| D | kumiho-control `feat/oauth-authorization-server` | Opus | — |
| E | integration: run A+B+C together in dev mode against CE, MCP Inspector flow, fix contract drift, write `docs/CLOUD-CONNECTOR-STATUS.md` | Opus | A, B, C, D |

Rules for every agent: feature branch only, never push, never touch `main`; if the working tree is dirty, use a `git worktree` beside the repo; keep the stdio plugin path byte-for-byte compatible (run existing tests); no secrets in code; Windows host (Git Bash + PowerShell available, `python` = 3.11).

---

## 4. Things only Morpheus can do

1. Claude.ai **Team/Enterprise org** for the submission portal (Owner role).
2. DNS `mcp.kumiho.cloud` → CF worker route; App Runner service + ECR repo + Secrets Manager entries (`KUMIHO_CONTROL_PLANE_INTERNAL_KEY`, etc.); CF WAF rule changes from §2.5.
3. Supabase migration apply; `NEXT_PUBLIC_FIREBASE_*` env for the consent page; Google sign-in enabled in Firebase for the origin domain.
4. Privacy policy + docs pages on kumiho.io; icon; support contact; test account with populated memory.
5. Optional: email `mcp-review@anthropic.com` with a static `client_id`/`client_secret` for `oauth_anthropic_creds`.

---

## 5. Out of scope for v1 (tracked)

Server-side auto-consolidation / Dream State scheduler (needs LLM metering), per-user Redis namespacing inside the control-plane proxy (currently per tenant; the RS passes `user_canonical_id`), MCP Apps UI, enterprise managed auth, usage metering per connector, Codex/ChatGPT reuse of the same AS (the `gpt/` gateway becomes redundant once this ships).
