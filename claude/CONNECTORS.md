# Connectors

This plugin uses one local MCP server:

- `kumiho-memory` (stdio)
  - command: `${CLAUDE_PLUGIN_DATA}/venv/bin/python -I ${CLAUDE_PLUGIN_ROOT}/scripts/run_kumiho_mcp.py`
  - bootstrap: reuses or creates `~/.kumiho/venv` and installs
    `kumiho[mcp]` + `kumiho-memory[all]` only when needed

## Cloud authentication

- The active Cloud adapter pins discovery to
  `https://control.kumiho.cloud`, then delegates token loading, refresh,
  discovery, and assigned-region routing to the installed Python SDK.
- An explicit `KUMIHO_AUTH_TOKEN` is preserved as the preferred SDK credential.
  Configure it persistently in the OS/user or trusted host environment before
  Claude starts; CE clears it and never uses Cloud authentication. The plugin
  does not save tokens to `.env.local`, Claude/Desktop config, or SDK caches.
- Without an explicit token, authenticate the shared SDK store locally with
  either `kumiho-auth login` or `kumiho-cli login`. Claude, Codex, and Kumiho
  Desktop use the same SDK-owned `~/.kumiho` credential/cache root.
- Official discovery records use the origin-scoped
  `~/.kumiho/official-cloud/discovery-cache.json`; a legacy generic/custom
  discovery cache is never reused for the pinned Cloud origin.
- Each MCP configuration check re-enters official SDK discovery so a long-lived
  host can refresh its assigned region. Refresh failure is fail-closed and never
  probes CE.
- Legacy `setup.py --token` / `--token-stdin` flags remain for one-run
  compatibility verification only. The value is passed to the SDK for that
  setup process and is not persisted for the next host restart.
- If authentication is unavailable, MCP tools still load, but authenticated
  operations fail until a token or SDK login is available and the host restarts.

## Optional environment

- `KUMIHO_CONTROL_PLANE_URL` is pinned by the plugin to
  `https://control.kumiho.cloud`; inherited overrides are ignored
- `KUMIHO_MCP_LOG_LEVEL` (default: `INFO`)
- `KUMIHO_CLAUDE_HOME` (override host state/log directory; packages remain in
  `~/.kumiho/venv` unless `KUMIHO_CONFIG_DIR` changes the Kumiho home; host
  launches accept only an absolute path from user-global Claude settings)
- `KUMIHO_CLAUDE_PACKAGE_SPEC` (override package install spec; host launches
  accept it only from a persistent OS-user environment or user-global Claude
  settings; Claude and Codex honor the same shared spec). Host provisioning ignores
  project/ambient pip, uv, proxy, certificate, and Python startup variables;
  configure a private index or enterprise proxy in the user's pip config
- `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` (disable automatic local no-key LLM
  fallback setup; this never permits a remote or provider-key-only CE LLM)

`KUMIHO_CONTROL_PLANE_API_URL`, Firebase auth-project variables, discovery
cache paths, and tenant routing are SDK internals, not plugin configuration.
The Cloud adapter removes inherited overrides and leaves the auth API unset so
the SDK's official default applies.

`KUMIHO_SERVER_ENDPOINT` and `KUMIHO_SERVER_ADDRESS` are intentionally ignored by
the launcher to enforce control-plane discovery routing in cloud mode. For
self-hosted routing use `KUMIHO_CLAUDE_SERVER_ENDPOINT` (see below).

## Self-hosted Community Edition (CE)

Opt-in mode that targets a local `kumiho-server` CE instead of cloud discovery.
Cloud behavior is unchanged unless one of these is set:

- `KUMIHO_CLAUDE_MODE` = `ce` (or `community` / `self-hosted` / `local`) — enable CE mode
- `KUMIHO_CLAUDE_SERVER_ENDPOINT` (default `127.0.0.1:9190`) — CE gRPC
  endpoint; setting it also enables CE mode. The host must be loopback even
  when the URL uses `grpcs://` or `https://`
- `UPSTASH_REDIS_URL` (default `redis://127.0.0.1:6379`) — CE working-memory
  Redis; both `redis://` and `rediss://` must use a loopback host
- `KUMIHO_WORKING_MEMORY_TTL` (default `86400` in CE mode, `3600` otherwise) — idle TTL of the working-memory buffer, re-armed on every read and write
- `KUMIHO_LLM_BASE_URL` — OpenAI-compatible LLM endpoint for summarization
  (replaces the dead-port fallback); in CE mode its host must be loopback for
  every scheme. The `KUMIHO_MEMORY_CODE_AUTOMINE=1` transcript opt-in is
  accepted only from a persistent OS-user environment or user-global
  `~/.claude/settings*.json`

A provider API key or provider-specific base URL does not opt CE into a remote
model. CE enrichment requires an explicit loopback `KUMIHO_LLM_BASE_URL`;
otherwise the plugin keeps the keyless fail-fast path.

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

If memory calls fail with `invalid_id_token`, set a fresh explicit token or run
`kumiho-auth login` / `kumiho-cli login` locally, then restart the host so the
SDK reloads or refreshes its credential cache.

If direct memory-store calls fail with `StatusCode.UNAVAILABLE` to
`127.0.0.1:8080`, the SDK did not resolve a regional endpoint from the official
control plane. Verify authentication and network access to
`https://control.kumiho.cloud`; do not configure a regional endpoint manually.

If discovery returns Cloudflare `error code: 1010`, verify that the Python SDK
is current and that the network permits the official Kumiho Cloud service.
Discovery transport and routing are SDK-owned.
