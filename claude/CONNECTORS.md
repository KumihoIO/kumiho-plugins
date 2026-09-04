# Connectors

This plugin uses one local MCP server:

- `kumiho-memory` (stdio)
  - command: `${CLAUDE_PLUGIN_DATA}/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/scripts/run_kumiho_mcp.py`
  - bootstrap: reuses or creates `~/.kumiho/venv` and installs
    `kumiho[mcp]` + `kumiho-memory[all]` only when needed

## Cloud authentication

- `KUMIHO_AUTH_TOKEN` (Cloud-mode JWT bearer token for authenticated
  memory/graph calls; CE does not use it)
  - set this in `.env.local` at the plugin root, or in `.claude/settings.local.json` (Claude Code only)
  - if omitted in Cloud mode, MCP tools still load but authenticated operations
    fail until onboarding completes
  - raw JWT or `Bearer <jwt>` are both accepted
- in Claude Code, the launcher auto-loads token and CE selection values from
  nearby `.claude/settings*.json` when not already present in env; custom Cloud
  routes and tenant hints are accepted only from user-global settings or the
  process environment
- in Claude Cowork, use `.env.local` or the credential cache (`~/.kumiho/kumiho_authentication.json`) for authentication
- if no env token is present, launcher falls back to
  `~/.kumiho/kumiho_authentication.json` token cache

## Optional environment

- `KUMIHO_CONTROL_PLANE_URL` (default: `https://control.kumiho.cloud`)
- `KUMIHO_MCP_LOG_LEVEL` (default: `INFO`)
- `KUMIHO_CLAUDE_HOME` (override host state/log directory; packages remain in
  `~/.kumiho/venv` unless `KUMIHO_CONFIG_DIR` changes the Kumiho home)
- `KUMIHO_CLAUDE_PACKAGE_SPEC` (override package install spec)
- `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` (disable local no-key LLM fallback mode)
- `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT` (override discovery HTTP User-Agent)

`KUMIHO_SERVER_ENDPOINT` and `KUMIHO_SERVER_ADDRESS` are intentionally ignored by
the launcher to enforce control-plane discovery routing in cloud mode. For
self-hosted routing use `KUMIHO_CLAUDE_SERVER_ENDPOINT` (see below).

## Self-hosted Community Edition (CE)

Opt-in mode that targets a local `kumiho-server` CE instead of cloud discovery.
Cloud behavior is unchanged unless one of these is set:

- `KUMIHO_CLAUDE_MODE` = `ce` (or `community` / `self-hosted` / `local`) — enable CE mode
- `KUMIHO_CLAUDE_SERVER_ENDPOINT` (default `127.0.0.1:9190`) — CE gRPC
  endpoint; setting it also enables CE mode. Plaintext/bare endpoints are
  loopback-only; remote endpoints require `grpcs://` or `https://`
- `UPSTASH_REDIS_URL` (default `redis://127.0.0.1:6379`) — CE working-memory
  Redis; plaintext `redis://` is loopback-only, otherwise use `rediss://`
- `KUMIHO_WORKING_MEMORY_TTL` (default `86400` in CE mode, `3600` otherwise) — idle TTL of the working-memory buffer, re-armed on every read and write
- `KUMIHO_LLM_BASE_URL` — OpenAI-compatible LLM endpoint for summarization
  (replaces the dead-port fallback); HTTP is loopback-only and remote
  endpoints must use HTTPS

In CE mode the launcher skips control-plane discovery and cloud auth (no
`KUMIHO_AUTH_TOKEN` / `kumiho-auth login` needed; any inherited token is cleared),
binds an explicit tokenless SDK client to `KUMIHO_SERVER_ENDPOINT`, and logs
the selected mode and resolved endpoint on startup. The CE server enforces its
own auth.

## Verify connection

1. Install and enable plugin.
2. Start a session.
3. Confirm Kumiho tools appear, for example:
   - `kumiho_chat_add`
   - `kumiho_chat_get`
   - `kumiho_chat_clear`
   - `kumiho_memory_ingest`
   - `kumiho_memory_recall`
   - `kumiho_memory_consolidate`
   - `kumiho_memory_dream_state`

If memory calls fail with `invalid_id_token`, refresh `KUMIHO_AUTH_TOKEN`
and verify `/api/memory/redis` is deployed with control-plane token verification.

If direct memory-store calls fail with `StatusCode.UNAVAILABLE` to
`127.0.0.1:8080`, discovery did not resolve cloud routing. Ensure
`/api/discovery/tenant` is deployed with control-plane token verification.

If discovery returns Cloudflare `error code: 1010`, your edge rules are likely
blocking the default Python user-agent. The launcher uses a custom user-agent;
you can override it with `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT`.

You can validate discovery directly with:
`python ./claude/scripts/test_discovery_env.py --env-file .env.local`
