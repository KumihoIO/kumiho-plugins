# Connectors

This plugin uses one local MCP server:

- `kumiho-memory` (stdio)
  - command: `python ${CLAUDE_PLUGIN_ROOT}/scripts/run_kumiho_mcp.py`
  - bootstrap: creates a venv and installs `kumiho[mcp]` + `kumiho-memory[all]`

## Required environment

- `KUMIHO_AUTH_TOKEN` (JWT bearer token for authenticated memory/graph calls)
  - set this in `.env.local` at the plugin root, or in `.claude/settings.local.json` (Claude Code only)
  - if omitted, MCP tools still load but authenticated operations will fail
  - raw JWT or `Bearer <jwt>` are both accepted
- in Claude Code, the launcher also auto-loads `KUMIHO_*` values from nearby `.claude/settings*.json`
  when not already present in env
- in Claude Cowork, use `.env.local` or the credential cache (`~/.kumiho/kumiho_authentication.json`) for authentication
- if no env token is present, launcher falls back to
  `~/.kumiho/kumiho_authentication.json` token cache

## Optional environment

- `KUMIHO_CONTROL_PLANE_URL` (default: `https://control.kumiho.cloud`)
- `KUMIHO_MCP_LOG_LEVEL` (default: `INFO`)
- `KUMIHO_CLAUDE_HOME` (override runtime directory)
- `KUMIHO_CLAUDE_PACKAGE_SPEC` (override package install spec)
- `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` (disable local no-key LLM fallback mode)
- `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT` (override discovery HTTP User-Agent)

`KUMIHO_SERVER_ENDPOINT` and `KUMIHO_SERVER_ADDRESS` are intentionally ignored by
the launcher to enforce control-plane discovery routing.

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
`python ./kumiho-claude/scripts/test_discovery_env.py --env-file .env.local`
