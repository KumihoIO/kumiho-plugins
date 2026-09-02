# Kumiho hosted Claude connector — integration status

Status: **code complete on four branches, nothing pushed, nothing merged.**
Date: 2026-09-02 · Integration pass: WP-E1 · Plan: `docs/CLOUD-CONNECTOR-PLAN.md`

Everything below was run on one Windows host against a local Kumiho CE server
(gRPC `127.0.0.1:9190`) and a local Redis (`127.0.0.1:6379`), in a single venv
that installs all three Python branches from their working trees.

---

## 1. What shipped, per work package

| WP | Repo · branch | Head | State |
|---|---|---|---|
| A | `kumiho-SDKs` · `feat/mcp-connector-profile` | `8698bea` | Complete. Release 0.13.0. |
| B | `kumiho-memory` · `feat/hosted-request-context` | `faf5497` | Complete. Release 1.4.0. |
| C | `kumiho-plugins` · `feat/cloud-mcp` (`cloud-mcp/`) | `7fdf8a5` | Complete. |
| D | `kumiho-control` · `feat/oauth-authorization-server` | `9a70dee` | Complete, **not verified end to end** — see §5. |
| E | this document + the fixes below | — | Complete. |

### WP-A — kumiho 0.13.0 (SDK)

```
f808a45  Add kumiho.request_context for hosted multi-tenant deployments
a36082c  MCP: connector tool profile, tool annotations, instructions, hosted safety
a25da86  Test the connector surface and hosted-mode tenancy
285ca23  Release 0.13.0
3017dfd  Withhold dream_state from the connector profile for v1
d8fb214  Refuse out-of-profile calls as tool errors; unshadow the submodule
8698bea  fix(mcp): the SESSIONS instructions promised a session_id engage never returns   [E1]
```

`kumiho.request_context` (the §2.1 contract), `create_mcp_server(profile=,
instructions=)`, `TOOL_ANNOTATIONS` for all 63 tools, `CONNECTOR_INSTRUCTIONS`,
`ToolNotInProfileError`, tenant-keyed process caches, and hosted guards that
refuse `~/.kumiho` and refuse to mutate `os.environ`.

### WP-B — kumiho-memory 1.4.0

```
d0b0cd0  feat(hosted): vendor kumiho.request_context with a fallback shim
97bcc4c  feat(hosted): per-tenant manager, request-scoped session and Redis token
0bbeb5d  feat(hosted): silence local-disk writes and key the remaining caches by tenant
ce7d3dd  fix(hosted): carry the request context across every thread and executor hop
f9cf23d  test(hosted): two tenants concurrently, and every rule they depend on
a972b83  chore: release 1.4.0 — hosted multi-tenant mode
a5ac321  fix(hosted): cap the per-scope recall lock table
b6a940f  feat(hosted): dev-only direct-Redis escape hatch for WP-C's CE mode
eeb45ff  test(mcp): pin engage as read-only — no session_id, no pointer write            [E1]
faf5497  fix(hosted): per-tenant managers made three pieces of shared state unsafe       [E1]
```

Per-tenant manager LRU, contextvar session resolution, Redis through the
control-plane proxy, no local disk, no LLM unless `KUMIHO_HOSTED_LLM=1`.

### WP-C — `cloud-mcp` (the resource server)

```
820dcfb  feat(cloud-mcp): hosted MCP resource server for mcp.kumiho.cloud
9d8b726  test(cloud-mcp): AS<->RS contract suite, and honour introspection expires_at   [E2]
76ad1a5  feat(cloud-mcp): live end-to-end suite, dev tenants, production startup contract [E1]
7fdf8a5  chore(cloud-mcp): the profile is 18 tools, not 19; sort e2e imports            [E1]
```

E1 added, on top of WP-C:

- **`tests/e2e/`** — nothing stubbed. Spawns a dev-mode server (or reuses one
  already on the port), drives it with the published `mcp` streamable-HTTP
  client, walks the whole protocol against the real graph and the real Redis,
  and skips itself when `:9190` or `:6379` is closed.
- **`x-kumiho-dev-tenant`** — dev-mode-only header (`KUMIHO_MCP_DEV_MODE=ce`
  and nothing else) that selects a second fake tenant, so isolation can be
  tested over the real transport. Documented in `cloud-mcp/README.md`.
- **A hard startup contract.** Outside dev mode the service now refuses to
  start when `kumiho < 0.13.0`, `kumiho-memory < 1.4.0`, or
  `create_mcp_server` has no `profile=` parameter. `KUMIHO_MCP_ALLOW_SHIM=1`
  bypasses it, documented dev-only.
- **`KUMIHO_MCP_ENABLE_SSE` now defaults to `0`.**
- **`KUMIHO_MCP_REQUEST_TIMEOUT_SECONDS` 60 → 120.**
- **`/healthz` reports `tenant_managers`**, `clients`, and the installed SDK
  versions.
- **`pyproject` floor** for `kumiho-memory` moved `>=1.3.0` → `>=1.4.0`.

### WP-D — control plane (OAuth authorization server)

```
9b3234d  feat(oauth): OAuth 2.1 authorization server for the hosted Claude connector
917af98  fix(oauth): close single-use races, bind consent to the browser, tighten CORS
77e822d  test(oauth): pin the AS token and metadata contract the resource server reads
9a70dee  docs(oauth): WAF guard for the unauthenticated consent screen
```

Not exercised in this pass: the RS was driven in `KUMIHO_MCP_DEV_MODE=ce`,
which disables authentication entirely. The AS↔RS agreement is covered only by
`cloud-mcp/tests/contract/` (WP-C/E2, fixture-based) and by WP-D's own vitest
suite — **no live token has crossed between the two services.** See §5.

---

## 2. Evidence — dependency set and test suites

One venv, `cloud-mcp/.venv-e1`, built from the three working trees:

```
pip install -e H:\KumihoIO\kumiho-SDKs\python\python \
            -e H:\KumihoIO\kumiho-memory \
            -e H:\KumihoIO\kumiho-plugins.wt-cloud-mcp\cloud-mcp[dev] \
            "mcp==1.26.0"
```

```
kumiho 0.13.0
kumiho_memory 1.4.0
mcp 1.26.0
kumiho-cloud-mcp 0.1.0
ASSERTIONS OK
```

Suite summary lines, verbatim, all in that venv:

| Suite | Command | Result |
|---|---|---|
| kumiho-SDKs | `pytest -q` in `python/python` | `189 passed, 2 skipped in 4.58s` |
| kumiho-memory | `pytest -q --ignore=tests/test_skill_ingest.py` | `2 failed, 1161 passed, 2 skipped in 132.82s (0:02:12)` |
| cloud-mcp | `pytest -q` | `120 passed, 1 skipped, 1 warning in 61.31s (0:01:01)` |
| stdio plugin | `pytest -q` in `kumiho-plugins/claude/scripts` | `159 passed, 13 warnings in 39.80s` |

Notes on the two non-green lines:

- **kumiho-memory**: `tests/test_skill_ingest.py` fails to *collect* on this
  checkout layout (`Path(__file__).resolve().parents[4]` → `IndexError`), which
  is why it is ignored. The two failures —
  `test_recall_memories_with_no_retriever` and
  `test_anthropic_provider_defaults_are_current` — are the pre-existing
  environmental ones: the first picks up the host's real memory data, the
  second the host's ambient LLM provider env. Both fail on `main` too. Neither
  is in the hosted path.
- **stdio plugin**: this line originally required a hand-cleaned `HOME` —
  `test_reflex_prefetch.py::test_auth_sentinel_skips_before_any_subprocess`
  failed against the developer's real `~/.claude/settings.json`, which pins
  `KUMIHO_CLAUDE_MODE=ce`. That was a bug in the test, not the plugin, and it
  is now fixed: an autouse `hermetic_home` fixture in `claude/scripts/conftest.py`
  gives every test its own empty HOME and a working directory with no
  configuration above it, so `159 passed` holds on a machine with the plugin
  fully installed. See §5, D-7.

### stdio smoke (WP-A + WP-B, no hosted flag, no request context)

```
KUMIHO_MEMORY_DECISIONS=1, KUMIHO_MCP_HOSTED unset
kumiho 0.13.0 | kumiho_memory 1.4.0
current_request() None
hosted_mode() False
profile: full
TOOL COUNT: 63
TOOLS registry len: 63 handlers: 63
code tools: ['kumiho_code_capture', 'kumiho_code_ingest', 'kumiho_code_mine_session', 'kumiho_code_why']
has delete_project: True
tools missing annotations: []
has ListResources handler: True
has ListPrompts handler: True
instructions: None
```

The 45 core tool names are byte-identical to `main` (set diff empty). The
default server still exposes all 63 with resources and prompts and no
`instructions`. The only additive change is that every tool now carries `title`
and `annotations`, which the plugin benefits from as much as the directory
does.

---

## 3. Evidence — live end-to-end against CE

`cloud-mcp/tests/e2e`, driven by the real `mcp` Python client over streamable
HTTP against `uvicorn kumiho_cloud_mcp.app:app --port 8080` with
`KUMIHO_MCP_DEV_MODE=ce`. `9 passed in 10.07s`, reproducible (three
consecutive clean runs).

**Startup.** Dev mode sets `KUMIHO_MCP_HOSTED=1`, `KUMIHO_HOSTED_LOCAL_REDIS=1`
and `KUMIHO_LOCAL_REDIS_URL` (E1 added the third — kumiho-memory 1.4.0's escape
hatch reads that name first, and only arms at all when `KUMIHO_MCP_HOSTED=1`).
The startup smoke check logs 18:

```json
{"msg": "kumiho-cloud-mcp started", "version": "0.1.0",
 "public_url": "http://127.0.0.1:8080/mcp", "dev_mode": "ce",
 "profile_source": "native", "upstream_request_context": true,
 "request_context_providers": ["kumiho.request_context"], "hosted": true,
 "tool_count": 18, "expected_tool_count": 18}
```

- **`initialize`** → `serverInfo {"name": "kumiho-mcp", "version": "0.13.0"}`,
  and `instructions` compares **byte-identical** to
  `kumiho.mcp_server.CONNECTOR_INSTRUCTIONS` (not "looks similar" — a stale
  copy shipped by the RS would be a behaviour change nobody reviews).
- **`tools/list` = exactly 18**, every one with a `title` and a
  `ToolAnnotations` whose `title` matches, `openWorldHint` false, and at least
  one of `readOnlyHint` / `destructiveHint` set. 10 read-only; destructive is
  exactly `["kumiho_chat_clear", "kumiho_deprecate_item"]`.
- **`kumiho_delete_project`** → `isError: true`, text `Tool
  'kumiho_delete_project' is not available on the Kumiho Memory connector. Call
  tools/list to see what is.` `kumiho_list_projects` before and after is
  identical (33 KB of payload, compared whole). A tool error, not a transport
  error — a client shows the model a tool error and carries on.
- **`kumiho_memory_engage`** → real memories out of CE:
  `count: 3`, first result
  `kref://CognitiveMemory/architecture/hosted-kumiho-memory-claude-connector-architectu-bb10cffa.conversation?r=1`,
  context opening `"[2026-09-02] Hosted Kumiho Memory Claude connector
  architecture decided on 2026-09-02: …"`.
- **`kumiho_memory_reflect`** (one `fact` capture, title `Connector integration
  smoke on 2026-09-02`, `space_hint: connector-smoke`):

  ```json
  {"buffered": true, "captures_stored": 1, "edges_discovered": 0,
   "stored_krefs": ["kref://CognitiveMemory/connector-smoke/connector-integration-smoke-on-2026-09-02-125ddf48.conversation?r=3"],
   "created_bucket": true,
   "session_id": "claude:user-0ad701ccc6:20260902:003",
   "session_id_source": "generated"}
  ```

- **`kumiho_chat_get`** → `{"session_id": "claude:user-0ad701ccc6:20260902:003",
  "message_count": 1, "ttl_remaining": 3599, "session_id_source": "argument"}`,
  with the buffered assistant turn present.
- **Session continuity** → a second call in the same conversation with *no*
  `session_id` argument resolves to the same session through the active-session
  pointer: `{"session_id": "claude:user-0ad701ccc6:20260902:003",
  "session_id_source": "active_session"}`.

  Note the shape of this check. `kumiho_memory_engage` **does not report a
  session** — it is annotated `readOnlyHint: true` and resolves none, because
  resolution *writes* the active-session pointer when it generates an id. The
  continuity that matters to a connector client is therefore demonstrated with
  a session-scoped tool, and the SDK instructions were corrected to say so
  (§4, D-1).
- **`kumiho_memory_consolidate`** with a self-written summary (keyless, no LLM):

  ```json
  {"success": true,
   "store_result": {"space_path": "/CognitiveMemory/claude/dev-local-user",
     "item_kref": "kref://CognitiveMemory/claude/dev-local-user/cloud-connector-e1-integration-smoke-3ad2791f.conversation",
     "revision_kref": "…?r=2", "edges_created": [], "stacked": false},
   "session_id": "claude:user-0ad701ccc6:20260902:003"}
  ```

  The space path carries the `RequestContext`'s `context` (`claude`) and
  `user_id` — not an ambient identity.
- **`kumiho_deprecate_item`** on the smoke capture's item kref → `{"updated":
  true, "deprecated": true}`. Everything this pass wrote to the graph has been
  deprecated again (the smoke capture, both consolidation items, and one
  scratch item from a manual probe).
- **`/healthz`**:

  ```json
  {"status": "ok", "version": "0.1.0", "dev_mode": "ce",
   "profile_source": "native", "tools": 18, "expected_tools": 18,
   "tenant_managers": {"loaded": true, "count": 1, "max": 256,
                       "idle_ttl_seconds": 1800.0, "process_singleton": false},
   "clients": 1,
   "sdk": {"kumiho": "0.13.0", "kumiho_memory": "1.4.0",
           "upstream_request_context": true}}
  ```

  `process_singleton: false` is the load-bearing field: `true` would mean some
  path built a memory manager outside a request context, i.e. from ambient
  environment.
- **No `ERROR` line in the server log for the whole run**, and no traceback.

---

## 4. Evidence — tenant isolation through the real stack

Two concurrent MCP sessions against one process, `x-kumiho-dev-tenant:
iso-alpha` and `iso-beta`, each buffering its own marker and reading it back.

**Scope, stated plainly:** CE does not enforce `x-tenant-id` on graph calls the
way the managed backend does, and there is no way to obtain two *real* tenants
on a CE box. So this proves isolation at the layer where a hosted leak would
actually happen — the per-tenant manager cache, the Redis key namespace and the
active-session pointer — and **not** at the graph-authorization layer. Graph
authorization is kumiho-server's, unchanged by this work, and stays unverified
here.

- **Different sessions**, and the generated id embeds a hash of the user, which
  the dev header moves with the tenant:

  ```
  alpha: {"session_id": "claude:user-9f9c905316:20260902:001", "session_id_source": "generated",
          "chat_session_id": "claude:user-9f9c905316:20260902:001", "chat_session_id_source": "active_session"}
  beta:  {"session_id": "claude:user-fc280bb230:20260902:001", "session_id_source": "generated",
          "chat_session_id": "claude:user-fc280bb230:20260902:001", "chat_session_id_source": "active_session"}
  ```

- **Buffers never cross**: `alpha messages: ["ALPHA-ONLY-MARKER-a1b2c3"]`,
  `beta messages: ["BETA-ONLY-MARKER-d4e5f6"]`, asserted in both directions.
- **Redis keys are tenant-namespaced**, read straight out of Redis rather than
  through the API:

  ```
  alpha: kumiho:memory:2122a7c1-…:CognitiveMemory:sessions:claude:user-9f9c905316:20260902:001:messages
         kumiho:memory:2122a7c1-…:active_session:claude:dev-iso-alpha-user
         kumiho:memory:2122a7c1-…:session_seq:dev-iso-alpha-user:20260902
  beta:  kumiho:memory:35bb84ec-…:CognitiveMemory:sessions:claude:user-fc280bb230:20260902:001:messages
         kumiho:memory:35bb84ec-…:active_session:claude:dev-iso-beta-user
         kumiho:memory:35bb84ec-…:session_seq:dev-iso-beta-user:20260902
  ```

  No key belongs to both, and no key contains the other tenant's id.
- **`/healthz` after the run**: `tenant_managers.count: 3` (default dev tenant +
  alpha + beta), `clients: 3`, `process_singleton: false`. A singleton
  implementation would have sat at 1.
- **No log record names both tenants**, no `ERROR`/`CRITICAL` record, and no
  `Traceback` anywhere in the server log.

---

## 5. Defects

### Fixed in this pass

| # | Sev | Where | What | Fix |
|---|---|---|---|---|
| D-1 | Low | `kumiho/mcp_server.py` `CONNECTOR_INSTRUCTIONS` (SESSIONS) | Said "every result echoes back the `session_id`". `kumiho_memory_engage` returns neither `session_id` nor `session_id_source` — and on a remote connector engage is the **first** call of every conversation, so the model was told to expect an id from the one call that never produces one. Fixing it the other way is not available: resolution *writes* the active-session pointer, so a reporting engage would be a `readOnlyHint: true` tool writing to Redis. | SDK `8698bea` + matching guard in memory `eeb45ff`; `cloud_mcp/connector_profile.py` fallback copy corrected in `76ad1a5`. |
| D-2 | **High** | `kumiho_memory/memory_manager.py` `_last_backend_error` | A plain instance attribute on a manager that WP-B made shared per **tenant**. MCP handlers are dispatched into a thread pool, so two concurrent recalls in one tenant race: caller A's healthy result can carry the backend error produced for caller B's query — with B's query text, space paths and krefs in the 500-char message — and A's reset can erase a failure B was about to read, turning "backend down" back into "no memories". Correct while one manager served one user; not correct since. | Memory `faf5497`. One slot per calling thread (the exact span: the handler runs `asyncio.run(recall)` and reads it back on the same thread). A ContextVar cannot serve — `asyncio.run` wraps the coroutine in a `Task`, which copies the context. Both call sites unchanged. Test fails on the old implementation (verified). |
| D-3 | Low | `kumiho_memory/mcp_tools.py` `_get_manager` | A blank `ctx.tenant_id` was accepted as a cache key, so every such request would share one manager, one Redis prefix and one session pointer — the complete form of the collapse the cache prevents. | Memory `faf5497`. Refuses loudly. |
| D-4 | Low | `kumiho_memory/entity_promotion.py` `_anchor_locks` | Keyed per **entity**, not per tenant, and uncapped: grows with every entity every tenant has ever promoted, for the life of the process. `mcp_tools._recall_scope_locks` was capped for exactly this reason in the same branch. | Memory `faf5497`. Capped at 4096, same sweep shape. |
| D-5 | Low | `kumiho_memory/redis_memory.py` | The direct-Redis dev `WARNING` printed the resolved URL verbatim. An Upstash URL is `rediss://default:<token>@host` — the token *is* the credential, in a message whose whole purpose is to be found, shipped and pasted into tickets. | Memory `faf5497`. `redact_redis_url` keeps the host, drops the userinfo. |
| D-6 | Low | `cloud-mcp/kumiho_cloud_mcp/app.py` | Dev mode set `KUMIHO_UPSTASH_REDIS_URL` / `UPSTASH_REDIS_URL` but not `KUMIHO_LOCAL_REDIS_URL`, the name kumiho-memory 1.4.0's escape hatch reads first. It worked only through the `UPSTASH_*` fallback — the ambient single-tenant credential name everywhere else. | RS `76ad1a5`. |
| D-7 | Low | `claude/scripts/conftest.py` (stdio plugin tests) | The suite read the developer's real `~/.claude/settings.json` and `~/.kumiho`. `test_reflex_prefetch.py::test_auth_sentinel_skips_before_any_subprocess` failed on a machine with `KUMIHO_CLAUDE_MODE=ce` pinned in those settings — the worker took the CE branch, which resolves an endpoint instead of reaching the auth sentinel, so the assertion was about a code path the test never entered. Quieter and worse: `~/.kumiho/kumiho_authentication.json` hydrated the developer's **real bearer token** into the test process. | RS `87ab99a`. Autouse `hermetic_home` fixture: empty HOME per test, `KUMIHO_CONFIG_DIR` inside it, ambient `KUMIHO_*`/`CLAUDE_*` cleared, and a **verified** clean working directory. The last part is the subtle half — redirecting HOME alone is not enough, because the launcher also walks the cwd *and every parent* for `.claude/settings*.json`, and on Windows the pytest temp root sits inside the user profile, so a tmp cwd climbs straight back out to the file just hidden. The fixture picks the fake home only if its ancestry is clean and falls back to the filesystem anchor, then asserts. |

### Open — not fixed

| # | Sev | Where | What | Why not fixed |
|---|---|---|---|---|
| D-8 | Low | `cloud-mcp/kumiho_cloud_mcp/clients.py:130` (**E2's file**) | `ClientPool.get` returns an unpooled client when `ttl <= 0` and never closes it. A gRPC channel leaks per request for a token expiring inside the request window. Narrow (an already-expired token fails `jwt.decode` first) but unbounded over time. | E2 owns the file. |
| D-9 | Low | `cloud-mcp/kumiho_cloud_mcp/clients.py:181` (**E2's file**) | `_construct_client` drops unsupported kwargs — including `skip_auth_token_load` and `enable_auto_login` — with only a `logger.warning`, then constructs the client anyway. On an SDK without those parameters that is exactly the `~/.kumiho` fallback the docstring exists to prevent, failing **open**. Latent only: kumiho 0.13.0 accepts all six (verified), and E1's new startup contract now refuses to boot on anything older. Still, the right behaviour outside dev mode is to refuse, not warn. | E2 owns the file. |
| D-10 | Low | `cloud-mcp/kumiho_cloud_mcp/clients.py:40` (**E2's file**) | `DiscoveryRouter._cache` is unbounded and never swept — expired entries are only overwritten, never dropped. Small per entry, but unlike `ClientPool` it has no ceiling. The read at `:48` is also outside the lock the write at `:83` takes. | E2 owns the file. |
| D-11 | Low | `cloud-mcp/kumiho_cloud_mcp/auth.py:375` (**E2's file**) | For an OAuth access token, `if scopes and REQUIRED_SCOPE not in scopes` means a token with **no** `scope` claim is treated as having `memory`. The AS always sets `scope`, so this is defence-in-depth only — but the fail direction is open. | E2 owns the file. |
| D-12 | Low | `kumiho_memory/entity_promotion.py` + `mcp_tools._build_hosted_manager` | `_build_hosted_manager`'s docstring says every optional feature is spelled out as an explicit "off" "so a new opt-in feature there cannot silently become hosted default" — but `entity_promotion` is not in that list, so it defaults **on** for hosted tenants, driven by the operator's `KUMIHO_MEMORY_ONTOLOGY` / `KUMIHO_MEMORY_ENTITY_PROMOTION`. Not a leak (it writes to the request's own tenant graph through the request's client) but it is the exact class the docstring warns about, and it is an operator-wide policy applied to every tenant. | Turning it off changes what hosted users' memory does. Product decision, not an integration one. |
| D-13 | Low | `kumiho_memory/mcp_tools.py` `tool_memory_ingest` | Error type/message for a missing `user_id` changed on the stdio path (`KeyError` → `ValueError` with new text, raised earlier). The MCP wrapper turns both into `{"error": …}`, so the visible change is the string. Arguably an improvement. | Noted, not reverted. `kumiho_memory_ingest` is not in the connector profile. |
| D-14 | Low | `kumiho_memory/mcp_tools.py` `_identity_from_args_or_request` | An explicit JSON `null` for `context` (`{"context": null}`) now resolves to `"personal"` where it previously stayed `None`, so an id-less ingest lands in a different pointer bucket. Reachable from a stdio client, since MCP arguments are decoded JSON. | Narrow, and arguably the correct bucket. Flagged for WP-B's owner. |
| D-15 | Info | `kumiho_memory/mcp_tools.py` `_recall_guard_lock` | The over-cap sweep can evict a lock that has been handed out but not yet acquired, so two threads in one scope can hold different mutexes. The code comments already state this and argue the direction is benign (one duplicate recall runs instead of being suppressed; the dedup record itself is guarded separately). Verified: benign. | No change. |
| D-16 | Low | `cloud-mcp/tests/` layout | `tests/e2e/` needed an `__init__.py`: without it pytest imports both `tests/conftest.py` and `tests/e2e/conftest.py` under the bare module name `conftest`, the second wins, and the entire hermetic suite fails to collect. **If E2 ever adds a `conftest.py` under `tests/contract/`, it will hit the same trap** — that directory has none today. | Fixed for `e2e/`; flagged for `contract/`. |

### Verified clean

The five regression classes named for this pass, checked across
`git diff main...HEAD` in both packages:

1. **stdio behaviour** — 45 core tool names byte-identical to `main`; 63 tools
   with `KUMIHO_MEMORY_DECISIONS=1`; resources and prompts still registered; no
   `instructions`; `KUMIHO_HOSTED_LOCAL_REDIS` only ever consulted when
   explicitly set (and warns rather than acting without `KUMIHO_MCP_HOSTED=1`).
   The only additive change is `title` + `annotations` on every tool. Residual
   items are D-13/D-14 above.
2. **Untenanted caches under a request** — the SDK's four
   (`_project_cache`, `_known_spaces`, `_bundle_cache`,
   `_space_registry_cache`) all go through `_tenant_key`, separated by U+001F.
   kumiho-memory's are tenant-keyed too. Residual items are D-3/D-4 above, and
   the SDK caches are unbounded in the same low-severity way (tenant-prefixed,
   so growth only, never cross-serving).
3. **`os.environ` mutation on the hosted path** — one write remains in each
   package. The SDK's (`mcp_server.py:244`) is guarded by `_is_hosted()` and
   returns with a warning. kumiho-memory's (`__main__.py:93`) is unreachable in
   hosted mode because its only caller feeds it `_load_preferences()`, which
   returns `{}` when hosted — the guard is one frame away from the mutation,
   which is worth remembering but is not a live defect.
4. **Threads dropping contextvars** — every raw thread in kumiho-memory goes
   through `_bounded.start_context_thread`, which copies the context at *submit*
   time; both `run_in_executor` sites wrap in `ctx.run`; every
   `asyncio.to_thread` propagates natively. The SDK's only threads are the
   orphan watchdog, started solely from `main()`, which a hosted process never
   calls — important, because that watchdog ends in `os._exit`.
5. **Foreign identifiers in error text** — one finding, D-2 above, now fixed.
   No other log or exception interpolates an identifier sourced from shared
   state.

### Post-E1 follow-ups

Handed on rather than dropped. Each is tracked here so the hand-off is a list
someone can close, not a paragraph someone has to re-derive.

**With E2** (`cloud-mcp/kumiho_cloud_mcp/`, their files — I did not touch them):

| # | Sev | Where | Item |
|---|---|---|---|
| D-8 | Low | `clients.py:130` | `ClientPool.get` returns an unpooled client when `ttl <= 0` and never closes it — a gRPC channel leaks per request for a token expiring inside the request window. |
| D-9 | Low | `clients.py:181` | `_construct_client` drops unsupported kwargs (`skip_auth_token_load`, `enable_auto_login`) with only a warning and builds the client anyway — on an older SDK that is the `~/.kumiho` fallback the docstring exists to prevent, failing **open**. |
| D-10 | Low | `clients.py:40` | `DiscoveryRouter._cache` is unbounded and never swept; the read at `:48` is outside the lock the write at `:83` takes. |
| D-11 | Low | `auth.py:375` | An OAuth access token with **no** `scope` claim is treated as carrying `memory`. Defence-in-depth only (the AS always sets it), but the fail direction is open. |

As of this writing E2 has uncommitted work in the shared worktree that appears
to address D-8 and D-9 — a client *lease* returned from the pool, a
`ClientContractError`, and a `client_construction_problems()` check wired into
`app.py`'s startup contract. Left untouched and unstaged; **these four are E2's
to close, not mine.**

One more for E2, not a defect but a trap in the same tree: `tests/e2e/` needed
an `__init__.py`, because without one pytest imports both `tests/conftest.py`
and `tests/e2e/conftest.py` under the bare module name `conftest`, the
subdirectory one wins, and the entire hermetic suite fails to collect. If
`tests/contract/` ever grows a `conftest.py` it will hit exactly this (D-16).

**With WP-B** (`kumiho-memory`, in progress):

| # | Sev | Where | Item |
|---|---|---|---|
| D-12 | Low | `mcp_tools._build_hosted_manager` | Its docstring says every optional feature is spelled out as an explicit "off" "so a new opt-in feature there cannot silently become hosted default" — but `entity_promotion` is absent from that list and so defaults **on** for hosted tenants, driven by the operator's `KUMIHO_MEMORY_ONTOLOGY` / `KUMIHO_MEMORY_ENTITY_PROMOTION`. Not a leak (it writes to the request's own tenant graph through the request's client), but it is the exact class the docstring warns about, and it makes one operator setting a policy for every tenant. Turning it off changes what hosted users' memory does, so it is a product call. |
| D-14 | Low | `mcp_tools._identity_from_args_or_request` | An explicit JSON `null` for `context` (`{"context": null}`) now resolves to `"personal"` where it previously stayed `None`, so an id-less ingest lands in a different pointer bucket. Reachable from a stdio client, since MCP arguments are decoded JSON. |

Two further kumiho-memory items are **closed, not handed on**, and are recorded
here so the accounting is complete: D-13 (`tool_memory_ingest`'s missing-`user_id`
error changed type and text on the stdio path) is accepted as an improvement,
and D-15 (the recall-scope lock eviction race) is accepted as benign — the code
comments already state it and the failure direction is one duplicate recall, not
a wrong answer.
---

## 6. Release and deploy order

Nothing here can be reordered without something breaking.

1. **Publish `kumiho` 0.13.0 to PyPI** (`kumiho-SDKs`, merge
   `feat/mcp-connector-profile`). Everything else depends on
   `kumiho.request_context` and `create_mcp_server(profile=)`.
2. **Publish `kumiho-memory` 1.4.0 to PyPI**, after 0.13.0 is installable —
   1.4.0's `_request_context` prefers the SDK module and falls back to a
   vendored copy, and the two copies must be the same contract in the image.
3. **Build and push the `cloud-mcp` image** (merge `feat/cloud-mcp`). The
   image pins `kumiho>=0.13.0` and `kumiho-memory>=1.4.0`, and the container
   now **refuses to start** if it somehow resolves anything older, so steps 1
   and 2 really are prerequisites rather than preferences.
4. **Deploy the control plane** (merge `feat/oauth-authorization-server`) — and
   in the same window:
   - apply the Supabase migration `20260902120000_oauth.sql`;
   - add the `NEXT_PUBLIC_FIREBASE_*` build secrets (§7);
   - add `control.kumiho.cloud` to Firebase Auth → authorized domains and
     enable the Google provider;
   - apply the Cloudflare WAF changes (§7). **The Anthropic-egress skip rule
     must be rule 0**, or Claude's server-to-server OAuth calls get
     challenged and the connector cannot complete a single flow.
5. **Point DNS `mcp.kumiho.cloud`** at the Cloudflare worker route, worker
   origin → the App Runner service URL.
6. **Smoke the whole flow as a custom connector** on a Claude.ai Team org
   before anything is submitted: add `https://mcp.kumiho.cloud/mcp`, complete
   the OAuth consent, confirm `tools/list` shows 18 tools with their titles,
   run one engage → reflect → consolidate cycle, then revoke the connection and
   confirm the refresh family is dead.
7. **Submit to the directory** at
   `claude.ai/admin-settings/directory/submissions/new` with
   `cloud-mcp/DIRECTORY.md` as the source of the copy.

A rollback at any step is "redeploy the previous image / revert the branch";
the only step with a one-way component is the Supabase migration, which is
additive (new tables only, no changes to existing ones).

---

## 7. For Morpheus

Plan §4 items, plus everything the four work packages asked for.

**AWS / Secrets Manager**

- Run `cloud-mcp/deploy/bootstrap-apprunner.ps1` — it creates the ECR repo, the
  App Runner service `kumiho-cloud-mcp`, and the IAM roles, and prints the
  service ARN.
- Secrets Manager entries:
  - `kumiho/CONTROL_PLANE_INTERNAL_KEY` — the shared secret for
    `x-control-plane-key`. **Both** services need the same value: the RS sends
    it (`KUMIHO_CONTROL_PLANE_INTERNAL_KEY`), the control plane checks it
    (`CONTROL_PLANE_INTERNAL_KEY`). Without it the RS **rejects every
    `x-api-key` request**, by design — it will not trust a year-long credential
    it cannot confirm is still live.
  - `kumiho/NEXT_PUBLIC_FIREBASE_API_KEY`, `…_AUTH_DOMAIN`, `…_PROJECT_ID`,
    `…_APP_ID` — required for the consent page's Firebase sign-in. Optional:
    `…_STORAGE_BUCKET`, `…_MESSAGING_SENDER_ID`. These are public identifiers
    that `next build` inlines, so they are **build-time** inputs. Without them
    the consent page still works, but only through the "use an API key
    instead" field.
- GitHub repository secrets for `deploy-cloud-mcp.yml` (currently
  `workflow_dispatch` only, deliberately): `AWS_ROLE_ARN`, `AWS_ACCOUNT_ID`,
  `MCP_APP_RUNNER_SERVICE_ARN`, `CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_ACCOUNT_ID`. Enable the `push` trigger only after a manual run
  goes green.

**Firebase**

- Add `control.kumiho.cloud` to Auth → Settings → Authorized domains, and
  enable the Google sign-in provider. Without it the consent page fails with
  `auth/unauthorized-domain`.

**Cloudflare WAF** (`kumiho-control/packages/origin/docs/cloudflare-config.md`
has the exact expressions)

- **Rule 0, first in the list**: skip *all* custom rules for
  `160.79.104.0/21` (Anthropic egress). Claude calls
  `/.well-known/oauth-authorization-server`, `/api/oauth/register`,
  `/api/oauth/token` and `/api/oauth/revoke` server-to-server with a
  non-browser UA; a Managed Challenge there is a hard failure, because there
  is no browser to solve it.
- Verify Bot Fight Mode does not challenge those four paths — `curl` from that
  range must get `200`.
- Rate limits: `/api/oauth/token` 60/min/IP (not the 10/min that protects
  `/api/control-plane/token` — Claude refreshes reactively on 401 *and*
  proactively 5 minutes before expiry, for every connected user behind one
  egress NAT); `/api/oauth/register` 10/min; `/oauth/authorize` 30/min. All
  three exempt the Anthropic range.
- Never extend the existing `curl`-UA challenge rule to `/api/oauth/*` or
  `/.well-known/*`.

**Supabase**

- Apply `supabase/migrations/20260902120000_oauth.sql`.
- **Schedule the GC.** The migration ships `public.oauth_gc()` but does not
  schedule it. Without a schedule, `oauth_pending_authorizations`,
  `oauth_authorization_codes` and rotated `oauth_refresh_tokens` accumulate
  forever:
  ```sql
  select cron.schedule('oauth-gc', '17 * * * *', $$select public.oauth_gc()$$);
  ```
  (`pg_cron` must be enabled on the project; otherwise call it from any
  hourly maintenance job.)

**Dashboard — API key revocation gap**

- `x-api-key` bearers are 1-year service-token JWTs. Until now *deleting a key
  in the dashboard did not revoke it*: nothing checked. The RS now introspects
  `token_id` on every request and **fails closed**, and
  `introspectServiceToken` treats a missing row as inactive — so the
  dashboard's `DELETE /api/control-plane/service-token` genuinely revokes, as
  of this release.
- Two things to confirm on your side: (a) the dashboard UI actually exposes
  that delete for every key, and (b) the copy tells the user revocation takes
  up to **60 seconds** (the RS's introspection cache).
- Note also that `introspectServiceToken` reads a `revoked_at` column that
  `service_tokens` does not have — harmless today (hard delete is the
  mechanism), but if you ever want soft-revoke, that column has to be added or
  that read is a silent no-op.

**Claude.ai**

- A **Team or Enterprise** organisation with the **Owner** role, for the
  submission portal.
- Privacy policy and docs pages on kumiho.io (`cloud-mcp/PRIVACY.md` is the
  draft), an icon, a support contact, and a test account with populated memory
  — reviewers are asked to confirm every listed tool has been run.
- Optional, later: email `mcp-review@anthropic.com` for
  `oauth_anthropic_creds`, which avoids DCR registering a fresh client per
  connection.

**Before submitting — the one thing nobody has done yet**

No live OAuth token has ever crossed from the control plane to the resource
server. Both sides are tested against the same fixtures
(`cloud-mcp/tests/contract/`, WP-D's vitest suite), and the fixtures agree —
but a fixture is not a deployment. Step 6 of §6 is the real gate.
