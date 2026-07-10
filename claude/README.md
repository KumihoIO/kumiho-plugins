# Kumiho Memory — Plugin for Claude Code

Persistent graph-native memory plugin for Claude. Runs a local Kumiho MCP
server with `kumiho-memory` so Claude **remembers you across sessions**.

Version: **0.9.1** | Requires: `kumiho>=0.9.16`, `kumiho-memory>=0.3.16`

## What it does

- Bootstraps user identity and preferences at session start
- Recalls context from previous sessions via semantic graph search
- Stores decisions, preferences, and project facts automatically
- Generates local conversation artifacts (BYO-storage — raw transcripts stay on your machine)
- Runs Dream State consolidation for memory hygiene
- Auto-approves Kumiho memory tool calls (no permission prompts)

## Platform compatibility

| Feature | Claude Code (CLI + VS Code) | Claude Desktop |
| ------- | --------------------------- | -------------- |
| MCP memory tools | Yes | Yes |
| Session bootstrap hook | Yes | Yes |
| Session-end artifact hook | Yes | Yes |
| `/kumiho-onboard` command | Yes | Yes |
| `/memory-capture` command | Yes | Yes |
| `/dream-state` command | Yes | Yes |
| Auto-approve memory ops | Yes | No (Desktop manages permissions differently) |
| `.claude/settings.json` env | Yes | No (use `.env.local` instead) |

## Cross-Agent Compatibility

The Kumiho plugins share the same Neo4j + Redis backend, `CognitiveMemory` graph, and skill-ingestion pipeline. Memories stored by one agent are recallable by any other. Cross-agent parity exists at the data model and discoverable-skill layer; host-side automation still differs by platform.

| Capability        | Claude Code                                 | OpenClaw                                      |
| ----------------- | ------------------------------------------- | --------------------------------------------- |
| Tool syntax       | `kumiho_memory_recall(...)`                 | `memory_search(...)` / `creative_capture(...)` |
| Behavioral rules  | Discovery-first SKILL.md + SessionStart context | TypeScript hooks + injected memory instructions |
| Session bootstrap | SessionStart hook + SKILL bootstrap         | TypeScript identity bootstrap in `before_prompt_build` |
| Recall behavior   | Agent-triggered recall guided by SKILL      | Automatic `before_prompt_build` hook           |
| Capture behavior  | Agent-triggered `store` / `add_response`    | Automatic `agent_end` buffering + capture      |
| Consolidation     | Agent-triggered                             | Threshold + idle timer + manual tool           |
| Dream State       | `/dream-state` command                      | Config schedule + manual tool                  |
| Setup wizard      | `python scripts/setup.py`                   | `npx kumiho-setup`                             |
| Skill ingestion   | Local SKILL + bundled references            | Claude canonical SKILL + bundled references |
| Privacy model     | Raw transcripts stay local                  | Raw transcripts stay local + PII redaction     |
| Creative memory   | Via graph skills                            | Built-in `creative_capture` / `creative_recall` |
| Local artifacts   | SessionEnd hook                             | Built-in artifact manager                      |

## Installation

### Claude Code

Install from GitHub:

```bash
claude plugin add github:kumihoclouds/kumiho-claude
```

### Local development

Run ad hoc without installing:

```bash
claude --plugin-dir ./kumiho-claude
```

## Getting started

1. **Sign up** at [kumiho.io](https://kumiho.io) (free tier available)
2. **Mint an API token** from the dashboard under **API Keys**
3. **Run `/kumiho-onboard`** inside Claude — the wizard handles auth, setup, and skill ingestion
4. **Start chatting** — Claude now remembers you across sessions

On your first session the plugin will ask a few questions (name, language,
communication style) to set up your identity. After that, it picks up where
you left off automatically.

## Hooks

The plugin registers three hooks that run automatically:

| Hook | Script | Purpose |
|------|--------|---------|
| `SessionStart` | `session-bootstrap.py` | Loads auth token, runs control-plane discovery, hints the agent to load user identity |
| `SessionEnd` | `save-session-artifact.py` | Saves conversation as a local Markdown artifact |
| `PermissionRequest` | `auto-approve-memory.py` | Auto-approves Kumiho memory MCP tool calls (`kumiho_*`) |

## Slash commands

| Command | Description |
|---------|-------------|
| `/kumiho-onboard` | Onboarding wizard — venv setup, auth, MCP config, skill ingestion |
| `/memory-capture` | Capture a specific fact or preference into long-term memory |
| `/dream-state` | Run Dream State consolidation (review, enrich, prune stored memories) |

## Runtime model

The bootstrap script (`scripts/run_kumiho_mcp.py`) creates an isolated
virtualenv, installs the required Python packages, resolves the Kumiho
gRPC endpoint via control-plane discovery, and launches the MCP server.

- **Runtime home:**
  - Windows: `%LOCALAPPDATA%\kumiho-claude`
  - macOS/Linux: `$XDG_CACHE_HOME/kumiho-claude` or `~/.cache/kumiho-claude`
- **Override runtime home:** `KUMIHO_CLAUDE_HOME`
- **Override package spec:** `KUMIHO_CLAUDE_PACKAGE_SPEC`

Default package spec:

```text
kumiho[mcp]>=0.9.16 kumiho-memory[all]>=0.3.16
```

## Self-hosted (Community Edition)

By default the launcher resolves a **cloud** Kumiho endpoint via control-plane
discovery and fails fast if no auth token is present. If you run your own
[`kumiho-server` Community Edition](https://github.com/KumihoIO/kumiho-server-community)
you can point the plugin at it instead — **opt-in**, so cloud users are
unaffected.

Enable CE mode by setting **either** of:

```dotenv
# .env.local (plugin root) or .claude/settings.local.json env block
KUMIHO_CLAUDE_MODE=ce
# or, equivalently, just name the endpoint (this alone turns CE mode on):
KUMIHO_CLAUDE_SERVER_ENDPOINT=127.0.0.1:9190
```

In CE mode the launcher:

- **Skips** control-plane discovery and cloud auth entirely — no
  `kumiho-auth login` and no `KUMIHO_AUTH_TOKEN` are required (any inherited
  token is cleared so it cannot flip routing back to cloud). The CE server
  enforces its own auth.
- Routes the SDK to the CE gRPC endpoint via `KUMIHO_LOCAL_SERVER_ENDPOINT`
  (default `127.0.0.1:9190`), so it does **not** fall back to `127.0.0.1:8080`.
- Provides a local working-memory Redis URL (`UPSTASH_REDIS_URL`, default
  `redis://127.0.0.1:6379`) — cloud gets this via the control-plane proxy; CE
  does not.
- Logs the selected mode and resolved endpoint on startup so
  "why is my memory empty / not connecting" is answerable at a glance.

For consolidation/reflect summarization in CE mode, point at a local or
self-provided LLM instead of the fail-fast dead-port fallback:

```dotenv
# OpenAI-compatible local server (Ollama, llama.cpp, vLLM, …)
KUMIHO_LLM_BASE_URL=http://127.0.0.1:11434/v1
# or a real key: OPENAI_API_KEY / ANTHROPIC_API_KEY (+ KUMIHO_LLM_PROVIDER=anthropic)
```

Start the CE server first (the community installer runs the Docker one-shot for
Neo4j + Redis and an onboarding wizard); see the
[kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
releases.

## Authentication

Run `/kumiho-onboard` inside Claude — the wizard walks you through authentication
as part of the full onboarding flow. Alternatively, authenticate manually:

### Method A — Dashboard API token (recommended)

Mint a long-lived API token from the [kumiho.io dashboard](https://kumiho.io)
under **API Keys**. Then run the onboarding wizard:

```text
/kumiho-onboard
```

Or cache it from the command line:

```bash
python ./kumiho-claude/scripts/setup.py
```

The token is stored under the `api_token` key in
`~/.kumiho/kumiho_authentication.json`. It does **not** overwrite session
tokens from `kumiho-cli login`.

### Method B — CLI login (email + password)

```bash
kumiho-cli login
```

This creates `~/.kumiho/kumiho_authentication.json` with `id_token` and
`control_plane_token`. These are session tokens and expire — refresh with
`kumiho-cli refresh` when they do.

### Alternative: environment variable

Set `KUMIHO_AUTH_TOKEN` in `.env.local` at the plugin root:

```dotenv
KUMIHO_AUTH_TOKEN=YOUR_KUMIHO_BEARER_JWT
KUMIHO_CONTROL_PLANE_URL=https://control.kumiho.cloud
```

In Claude Code, you can also set it in `.claude/settings.local.json`:

```json
{
  "env": {
    "KUMIHO_AUTH_TOKEN": "YOUR_KUMIHO_BEARER_JWT",
    "KUMIHO_CONTROL_PLANE_URL": "https://control.kumiho.cloud"
  }
}
```

### Token resolution order

The launcher resolves authentication from these sources (first match wins):

1. Process environment variable `KUMIHO_AUTH_TOKEN`
2. `.env.local` at the plugin root
3. `.claude/settings.local.json` then `.claude/settings.json` *(Claude Code only)*
4. `.mcp.json` env block
5. `~/.kumiho/kumiho_authentication.json` credential cache — checks keys in order: `control_plane_token`, `id_token`, `api_token`

Both raw JWT and `"Bearer <jwt>"` formats are accepted.
The plugin starts without a token so tools remain visible, but authenticated
memory/graph operations require a valid token.

### LLM provider for summarization

For higher-quality summarization during memory consolidation, set either:

- `OPENAI_API_KEY` (default provider path), or
- `ANTHROPIC_API_KEY` with `KUMIHO_LLM_PROVIDER=anthropic`.

If no LLM key is set, the launcher enables a local fail-fast fallback so MCP
tools still initialize without external LLM credentials. In Claude Code, the
host agent (Claude itself) handles query reformulation and edge discovery
natively — no external LLM key needed for those features.

## Conversation artifacts

The plugin follows a **BYO-storage** model: raw conversation content is stored
locally as Markdown files; the cloud graph stores only metadata and artifact
pointers.

Artifacts are saved to `~/.kumiho/artifacts/{YYYY-MM-DD}/` by default.
Override with `KUMIHO_ARTIFACT_DIR`:

```bash
export KUMIHO_ARTIFACT_DIR=.kumiho/artifacts
```

Each session with 2+ meaningful exchanges produces a Markdown artifact with
YAML frontmatter (session_id, date, topics, summary) and structured
`## Exchange N` sections.

## Environment variables

### Required

| Variable | Description |
|----------|-------------|
| `KUMIHO_AUTH_TOKEN` | JWT bearer token for authenticated memory/graph calls (cloud mode; not required in CE mode) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `KUMIHO_CONTROL_PLANE_URL` | `https://control.kumiho.cloud` | Control plane URL |
| `KUMIHO_TENANT_HINT` | *(auto)* | Tenant slug or UUID for multi-tenant setups |
| `KUMIHO_MCP_LOG_LEVEL` | `INFO` | MCP server log level |
| `KUMIHO_CLAUDE_HOME` | *(platform default)* | Override runtime/venv directory |
| `KUMIHO_CLAUDE_PACKAGE_SPEC` | *(see above)* | Override pip install spec |
| `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` | *(unset)* | Set to `1` to disable local no-key LLM fallback |
| `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT` | `kumiho-claude/0.9.1` | Override discovery HTTP User-Agent |
| `KUMIHO_ARTIFACT_DIR` | `~/.kumiho/artifacts/` | Override conversation artifact directory |

### Self-hosted (Community Edition)

| Variable | Default | Description |
|----------|---------|-------------|
| `KUMIHO_CLAUDE_MODE` | *(unset)* | Set to `ce` (or `community` / `self-hosted` / `local`) to target a self-hosted CE server instead of cloud discovery |
| `KUMIHO_CLAUDE_SERVER_ENDPOINT` | `127.0.0.1:9190` | CE gRPC endpoint; setting it also enables CE mode |
| `UPSTASH_REDIS_URL` | `redis://127.0.0.1:6379` | CE working-memory Redis URL (CE only) |
| `KUMIHO_LLM_BASE_URL` | *(unset)* | OpenAI-compatible LLM endpoint for summarization; when set, replaces the fail-fast dead-port fallback |

`KUMIHO_SERVER_ENDPOINT` and `KUMIHO_SERVER_ADDRESS` are intentionally
ignored by the launcher to enforce control-plane discovery routing in cloud
mode. For self-hosted routing use `KUMIHO_CLAUDE_SERVER_ENDPOINT` (see
[Self-hosted (Community Edition)](#self-hosted-community-edition)).

## Troubleshooting

### Token not picked up

If the bootstrap logs:

```text
[kumiho-claude] Searched N settings paths; none contained a usable env block.
```

Run `/kumiho-onboard` to set up authentication, or run:

```bash
python ./kumiho-claude/scripts/setup.py
```

### Auth error (401)

If you see:

```text
Memory proxy error 401: {"error":"invalid_id_token"}
```

Fix options:

1. Use a fresh dashboard-minted token via `/kumiho-onboard`.
2. Ensure control-plane `/api/memory/redis` is deployed with control-plane token verification.

### Connection refused

If you see:

```text
StatusCode.UNAVAILABLE ... 127.0.0.1:8080 ... Connection refused
```

Then Kumiho SDK discovery did not resolve a cloud gRPC endpoint.

Fix options:

1. Ensure `KUMIHO_CONTROL_PLANE_URL` points to your deployed control plane.
2. Ensure `/api/discovery/tenant` is deployed with control-plane token verification.

If you see DNS failures for `us-central.kumiho.cloud`, a stale endpoint override is
likely present. This plugin ignores `KUMIHO_SERVER_ENDPOINT`/`KUMIHO_SERVER_ADDRESS`
and resolves endpoint from control-plane on every startup.

### Cloudflare 1010 error

If discovery returns Cloudflare `error code: 1010`, edge rules are blocking
the default Python user-agent. Override with `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT`.

## Validation and smoke test

```bash
# Claude Code — validate plugin manifest:
claude plugin validate ./kumiho-claude/.claude-plugin/plugin.json

# Provision runtime and verify required modules:
export KUMIHO_AUTH_TOKEN=YOUR_KUMIHO_BEARER_JWT
python ./kumiho-claude/scripts/run_kumiho_mcp.py --self-test

# Test discovery with .env.local:
python ./kumiho-claude/scripts/test_discovery_env.py --env-file .env.local

# Offline checks for self-hosted CE mode (no network/venv needed):
python ./kumiho-claude/scripts/test_ce_mode.py
```

## Structure

```text
.
├── .claude-plugin/
│   ├── plugin.json            # Plugin manifest (name, version, entry points)
│   └── marketplace.json       # Marketplace metadata
├── .mcp.json                  # MCP server definition (kumiho-memory stdio)
├── .env.local.example         # Template for local auth config
├── commands/
│   ├── kumiho-onboard.md      # /kumiho-onboard slash command (setup wizard)
│   ├── memory-capture.md      # /memory-capture slash command
│   └── dream-state.md         # /dream-state slash command
├── hooks/
│   └── hooks.json             # SessionStart, SessionEnd, PermissionRequest hooks
├── skills/
│   └── kumiho-memory/
│       ├── SKILL.md           # Core behavioral instructions
│       └── references/
│           ├── artifacts.md               # Agent output artifact guidelines
│           ├── bootstrap.md               # Session bootstrap procedure
│           ├── edges-and-traversal.md     # Graph edge types and traversal
│           ├── onboarding.md             # First-session onboarding flow
│           └── privacy-and-trust.md      # Privacy guarantees and data handling
├── scripts/
│   ├── run_kumiho_mcp.py         # Bootstrap launcher (venv, install, discovery, MCP)
│   ├── session-bootstrap.py      # SessionStart hook
│   ├── save-session-artifact.py  # SessionEnd hook
│   ├── auto-approve-memory.py    # PermissionRequest hook
│   ├── test_discovery_env.py     # Discovery smoke test
│   ├── test_ce_mode.py           # Self-hosted CE mode offline checks
│   ├── setup.py                  # Interactive setup wizard
│   └── ingest-skills.py          # Skill ingestion into CognitiveMemory/Skills graph
├── CONNECTORS.md                 # MCP connector details and env reference
└── README.md
```

## License

MIT
