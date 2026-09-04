# Kumiho Memory — Plugin for Claude Code

Persistent graph-native memory plugin for Claude. Runs a local Kumiho MCP
server with `kumiho-memory` so Claude **remembers you across sessions**.

Version: **0.21.0** | Requires: `kumiho>=0.12.2`, `kumiho-memory>=1.4.0`
(reused or installed automatically in `~/.kumiho/venv` — nothing to `pip install`)

**OpenAI Codex** uses the parallel native package under `../codex`,
selected by the repo's `.agents/plugins/marketplace.json`. This Claude
package keeps independent commands, hooks, and backend settings while sharing
Kumiho Desktop's package runtime; see
[`codex/README.md`](../codex/README.md).

## Quick install

```bash
claude plugin marketplace add KumihoIO/kumiho-plugins
claude plugin install kumiho-memory@kumiho-plugins
```

Or inside Claude Code: run `/plugin`, **Add marketplace** →
`KumihoIO/kumiho-plugins`, then install **kumiho-memory**.

Then pick a backend — **Cloud** (managed, API token) or **self-hosted CE**
(your own `kumiho-server`, no token):

```bash
/kumiho-onboard                 # interactive: asks Cloud vs CE, then wires everything
/kumiho-onboard ce              # self-hosted CE (defaults to 127.0.0.1:9190)
```

Run onboarding once before expecting the MCP server on a fresh install. It
adopts Kumiho Desktop's existing `~/.kumiho/venv` when present (no reinstall),
creates Claude's persistent compatibility alias, and asks you to open a new
session. Existing onboarded installations keep using that same alias.
The bare `python` spelling in manual POSIX examples below is shorthand; on
Windows use `/kumiho-onboard` so the OS-account venv or a native PE launcher is
resolved by absolute path instead of a Windows App Execution Alias.

For non-interactive Cloud setup, keep the token out of chat and shell history:

```bash
python ./claude/scripts/setup.py --token-stdin --yes
```

See [Choosing a backend](#choosing-a-backend-cloud-vs-ce) for the CE
neo4j / redis / embedding details.

## What it does

- Bootstraps user identity and preferences at session start
- Recalls context from previous sessions via semantic graph search
- Stores decisions, preferences, and project facts automatically
- **Decision Memory** — mines your git commits *and* your sessions into a
  git-anchored decision graph: ask `kumiho_code_why` why code is the way it
  is and get the decision, its rationale, the rejected alternatives, and the
  measurements that decided it (opt-in; see [Decision Memory](#decision-memory))
- **History Backfill** — `/kumiho-backfill` mines your existing **Claude Code,
  Codex, and ChatGPT** history into ontology-typed memory, so the graph knows
  your work from day one (local-first, keyless, review before upload)
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

When Kumiho plugins are configured for the same backend and Cloud tenant (or
the same CE deployment), they share the `CognitiveMemory` graph and
skill-ingestion pipeline, so memories stored by one agent are recallable by
another. Hosts configured for different backends or tenants remain isolated.
Cross-agent parity exists at the data model and discoverable-skill layer;
host-side automation still differs by platform.

| Capability        | Claude Code                                 | OpenClaw                                      |
| ----------------- | ------------------------------------------- | --------------------------------------------- |
| Tool syntax       | `kumiho_memory_recall(...)`                 | `memory_search(...)` / `creative_capture(...)` |
| Behavioral rules  | Discovery-first SKILL.md + SessionStart context | TypeScript hooks + injected memory instructions |
| Session bootstrap | SessionStart hook + SKILL bootstrap         | TypeScript identity bootstrap in `before_prompt_build` |
| Recall behavior   | Agent-triggered recall guided by SKILL      | Automatic `before_prompt_build` hook           |
| Capture behavior  | Agent-triggered `store` / `add_response`    | Automatic `agent_end` buffering + capture      |
| Consolidation     | Keyless: agent- or subagent-written summary via `kumiho_memory_consolidate(summary=…)`; host-counted 20-turn floor + manual tool | Threshold + idle timer + manual tool           |
| Dream State       | `/dream-state` command                      | Config schedule + manual tool                  |
| Setup wizard      | `python scripts/setup.py`                   | `npx kumiho-setup`                             |
| Skill ingestion   | Local SKILL + bundled references            | Claude canonical SKILL + bundled references |
| Privacy model     | Raw transcripts stay local                  | Raw transcripts stay local + PII redaction     |
| Creative memory   | Via graph skills                            | Built-in `creative_capture` / `creative_recall` |
| Local artifacts   | SessionEnd hook                             | Built-in artifact manager                      |

## Installation

### Claude Code (marketplace)

```bash
claude plugin marketplace add KumihoIO/kumiho-plugins
claude plugin install kumiho-memory@kumiho-plugins
```

`marketplace update` refreshes to the latest published version:

```bash
claude plugin marketplace update kumiho-plugins
```

### Local development

Run ad hoc from a checkout without installing (point at the `claude/`
plugin directory in this repo):

```bash
claude --plugin-dir ./claude
```

## Getting started

1. **Install** the plugin (marketplace commands above)
2. **Choose a backend** — see [Choosing a backend](#choosing-a-backend-cloud-vs-ce):
   - **Cloud**: sign up at [kumiho.io](https://kumiho.io) (free tier), mint an API token under **API Keys**
   - **CE**: stand up your own [`kumiho-server` Community Edition](https://github.com/KumihoIO/kumiho-server-community) (Neo4j + Redis + embedding)
3. **Run `/kumiho-onboard`** inside Claude — the wizard handles backend selection, auth, MCP config, and skill ingestion
4. **Start chatting** — Claude now remembers you across sessions

## Choosing a backend (Cloud vs CE)

| | **Cloud** (managed) | **Self-hosted CE** |
|---|---|---|
| Auth | API token (`eyJ…` JWT) | none — the CE server enforces its own |
| Backend store | managed Neo4j + Redis | **you run** Neo4j + Redis (+ an embedding/LLM) |
| Setup | `/kumiho-onboard cloud`, then use the masked local-terminal prompt | stand up `kumiho-server-community`, then `/kumiho-onboard ce` |
| Data | summaries in Kumiho Cloud; raw transcripts stay local | everything on your machine/VPC |

**Cloud** — the token is all you need; everything else is managed:

```bash
/kumiho-onboard cloud
# local terminal: python ./claude/scripts/setup.py --token-stdin --yes
```

**CE** — the graph store (Neo4j) and the embedding model live **inside the
CE server**, which you deploy first from
[kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
(its installer runs the Docker one-shot for Neo4j + Redis and configures the
embedding provider). The plugin only needs to know where to reach it:

```bash
/kumiho-onboard ce
# or, non-interactive, with a non-default endpoint / redis / local LLM:
python ./claude/scripts/setup.py --ce --yes \
  --ce-endpoint 127.0.0.1:9190 \
  --ce-redis-url redis://127.0.0.1:6379 \
  --ce-llm-base-url http://127.0.0.1:11434/v1
```

| Plugin-side CE flag | What it points at | Default |
|---|---|---|
| `--ce-endpoint` | the CE server's gRPC endpoint (fronts Neo4j) | `127.0.0.1:9190` |
| `--ce-redis-url` | working-memory Redis | `redis://127.0.0.1:6379` |
| `--ce-llm-base-url` | OpenAI-compatible LLM for the LLM-backed paths: summarizer-written consolidation, Dream State (`/dream-state`), LLM edge discovery (Ollama, llama.cpp, vLLM, …) | fail-fast (reflect and keyless consolidation, i.e. `kumiho_memory_consolidate` with `summary`, need none) |

Plain `http://` LLM URLs are accepted only for loopback hosts such as
`127.0.0.1` or `localhost`. Use `https://` for a model endpoint on another
machine so memory/code payloads and provider keys are never sent in clear.
Plain `redis://` is likewise loopback-only; a Redis service on another machine
must use `rediss://` so working-memory contents are encrypted in transit.

No API token is involved in CE mode. Full CE environment details are in
[Self-hosted (Community Edition)](#self-hosted-community-edition) below.

On your first session the plugin will ask a few questions (name, language,
communication style) to set up your identity. After that, it picks up where
you left off automatically.

## Hooks

The plugin registers these hooks; all run automatically:

| Hook | Script | Purpose |
|------|--------|---------|
| `SessionStart` | `session-bootstrap.py` | Injects the session card (identity lookup with the CE fallback, mandatory onboarding when identity is missing, the two reflexes, the live `session_id`), persists the session facts for the reflex, repairs a stale Desktop server entry |
| `UserPromptSubmit` | `memory-reflex.py` | Injects prefetched recall from the local cache, the reflect floor, and the keyless consolidation floor (`KUMIHO_REFLEX_CONSOLIDATE_FLOOR`) |
| `SubagentStart` | `memory-reflex.py --subagent` | Hands subagents the memory rules and the live `session_id` |
| `Stop` | `reflex-observe.py` | Ledgers the completed turn and spawns the detached recall prefetch |
| `PostToolUse` | `reflex-observe.py`, `code-capture-hook.py` | Ledgers engage / reflect / consolidate calls (consolidate with an `ok` flag); queues commits for Decision Memory after `git commit` |
| `SessionEnd` | `save-session-artifact.py`, `code-capture-hook.py` | Saves the conversation as a local Markdown artifact; drains queued commit captures |
| `PermissionRequest` | `auto-approve-memory.py` | Auto-approves Kumiho memory MCP tool calls (`kumiho_*`) |

Every hook runs under `${CLAUDE_PLUGIN_DATA}/venv/bin/pythonw`, a compatibility
link to the shared `~/.kumiho/venv` used by Kumiho Desktop, Claude, and Codex.
On Windows its `bin` junction exposes the console-less `pythonw.exe`; on POSIX
the launcher links `bin/pythonw` to `bin/python`. Background workers hide their
child consoles for the same reason. On upgrade, an old per-plugin venv is kept
as `venv.pre-shared*` before the compatibility link is installed.

## Slash commands

| Command | Description |
|---------|-------------|
| `/kumiho-onboard` | Onboarding wizard — venv setup, auth, MCP config, skill ingestion |
| `/memory-capture` | Capture a specific fact or preference into long-term memory |
| `/dream-state` | Run Dream State consolidation (review, enrich, prune stored memories) |

## Decision Memory

Git records *what* changed and *when*; it never records *why*. Decision
Memory captures the why — decisions, their rationale, the alternatives that
were rejected, and the measurements that decided them — as a graph anchored
to git (the code is never copied; nodes point at `{repo, commit, file,
line}`, so the memory never rots across rebases). Opt-in via
`KUMIHO_MEMORY_CODE=1` (on by default for this plugin; set `0` to disable).

| Tool | Ask |
|---|---|
| `kumiho_code_why` | *Why is this code the way it is?* — the decision, rationale, and verbatim evidence for a file/commit, plus whether it was later reversed (`superseded_by`) |
| `kumiho_code_ingest` | Mine a commit range into decision nodes (idempotent) |
| `kumiho_code_mine_session` | Mine the conversation itself — rejected alternatives + measurements the commit message never captured |

**Automatic capture:** on `git commit` (and at session end) the plugin
mines the commit into the graph with zero action. **Session mining** —
mining the whole conversation, not just commits — closes the loop but is a
full-transcript LLM pass, so it is a **second opt-in**:

```dotenv
KUMIHO_MEMORY_CODE=1            # commit capture (default on)
KUMIHO_MEMORY_CODE_AUTOMINE=1   # also mine sessions at session end (default off)
```

Before editing unfamiliar code, ask `kumiho_code_why` for the file first —
never re-litigate a decision the graph already explains.

## Runtime model

The bootstrap script (`scripts/run_kumiho_mcp.py`) reuses or creates the shared
`~/.kumiho/venv`, installs required Python packages only when its current
versions do not satisfy the plugin, resolves the Kumiho
gRPC endpoint via control-plane discovery, and launches the MCP server.

- **Runtime home:**
  - Windows: `%LOCALAPPDATA%\kumiho-claude`
  - macOS/Linux: `$XDG_CACHE_HOME/kumiho-claude` or `~/.cache/kumiho-claude`
- **Shared package runtime:** `~/.kumiho/venv` (also used by Kumiho Desktop and Codex)
- **Override runtime state home:** `KUMIHO_CLAUDE_HOME` (logs, markers, reflex state only)
- **Override package spec:** `KUMIHO_CLAUDE_PACKAGE_SPEC`

Default package spec:

```text
kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0
```

## Self-hosted (Community Edition)

By default the launcher resolves a **cloud** Kumiho endpoint via control-plane
discovery and fails fast if no auth token is present. If you run your own
[`kumiho-server` Community Edition](https://github.com/KumihoIO/kumiho-server-community)
you can point the plugin at it instead — **opt-in**, so cloud users are
unaffected.

**Easiest path — the wizard.** Run `/kumiho-onboard` and pick
**Self-hosted (Community Edition)**, or from a clone of this repo:

```bash
python ./claude/scripts/setup.py --ce --yes
# non-default endpoint: --ce-endpoint HOST:PORT
```

The wizard writes the CE config below to `.env.local`, your OS user environment,
and the Claude Desktop config, then ingests skills and probes the server — no
API token involved.

**Manual path.** Enable CE mode by setting **either** of:

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
- Builds an explicit tokenless CE client for the configured endpoint (default
  `127.0.0.1:9190`) without the SDK's loopback-only auto-discovery. Remote TLS
  endpoints such as `grpcs://ce.example.com:7443` retain their secure scheme.
- Provides a local working-memory Redis URL (`UPSTASH_REDIS_URL`, default
  `redis://127.0.0.1:6379`) — cloud gets this via the control-plane proxy; CE
  does not. Plain `redis://` is loopback-only; remote Redis requires
  `rediss://`.
- Logs the selected mode and resolved endpoint on startup so
  "why is my memory empty / not connecting" is answerable at a glance.

Reflect and keyless consolidation (the agent passes `summary`) need no LLM.
For summarizer-written consolidation in CE mode (calls without `summary`),
point at a local or self-provided LLM instead of the fail-fast dead-port
fallback:

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
python ./claude/scripts/setup.py
```

The token is stored under the `api_token` key in
`~/.kumiho/kumiho_authentication.json`. It does **not** overwrite session
tokens from `kumiho-auth login`.

### Method B — CLI login (email + password)

```bash
kumiho-auth login
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

In Claude Code, put custom Cloud routing only in the user-global
`~/.claude/settings.local.json` (project-local settings may still provide the
token or select CE):

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

Project-local Claude settings continue to supply `KUMIHO_AUTH_TOKEN`,
`KUMIHO_CLAUDE_MODE`, and `KUMIHO_CLAUDE_SERVER_ENDPOINT`. For safety, a
custom `KUMIHO_CONTROL_PLANE_URL` or `KUMIHO_TENANT_HINT` is honored only from
user-global `~/.claude/settings*.json`. Host-launched MCP/setup processes also
ignore an ambient `KUMIHO_CONFIG_DIR`; its user-global value must be absolute.
This avoids pairing a repository-controlled Cloud route with the shared
credential cache or executing a repository-selected virtualenv. Direct
maintenance runs outside Claude/Codex continue to honor environment overrides.

### LLM provider for summarization

Consolidation is keyless when the agent passes `summary`. For summarizer-written
summaries (calls without `summary`), set either:

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
| `KUMIHO_CONTROL_PLANE_URL` | `https://control.kumiho.cloud` | Control plane URL; custom values for host launches must be in user-global `~/.claude/settings*.json` |
| `KUMIHO_TENANT_HINT` | *(auto)* | Tenant slug or UUID for multi-tenant setups; user-global settings only for host launches |
| `KUMIHO_MCP_LOG_LEVEL` | `INFO` | MCP server log level |
| `KUMIHO_CLAUDE_HOME` | *(platform default)* | Override the runtime state directory (logs, markers, reflex state); it does not move the shared venv. |
| `KUMIHO_CONFIG_DIR` | `~/.kumiho` | Override Kumiho's shared config/credential/runtime root, including `venv`. For a Claude host launch this must be an absolute path in user-global `~/.claude/settings*.json`; direct maintenance runs may use the environment. Use the same value for Desktop, Claude, and Codex. |
| `KUMIHO_CLAUDE_PACKAGE_SPEC` | *(see above)* | Override pip install spec |
| `KUMIHO_REFLEX_CONSOLIDATE_FLOOR` | `20` | Completed turns after which the UserPromptSubmit hook asks the agent to consolidate the session (keyless: the agent or a subagent writes the summary). `0` disables the nudge. |
| `KUMIHO_WORKING_MEMORY_TTL` | `3600` (CE mode: `86400`) | Idle TTL in seconds of the Redis session buffer (working memory). kumiho-memory re-arms it on every write **and** read, so a live session keeps its buffer; a pause longer than this loses it and the next reflect reports `created_bucket: true`. |
| `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` | *(unset)* | Set to `1` to disable local no-key LLM fallback |
| `KUMIHO_CLAUDE_DISCOVERY_USER_AGENT` | `kumiho-claude/<plugin version>` | Override discovery HTTP User-Agent. The default is derived from `.claude-plugin/plugin.json` at startup, so it always reports the running version — there is no pinned copy to bump. |
| `KUMIHO_ARTIFACT_DIR` | `~/.kumiho/artifacts/` | Override conversation artifact directory |

### Self-hosted (Community Edition)

| Variable | Default | Description |
|----------|---------|-------------|
| `KUMIHO_CLAUDE_MODE` | *(unset)* | Set to `ce` (or `community` / `self-hosted` / `local`) to target a self-hosted CE server instead of cloud discovery |
| `KUMIHO_CLAUDE_SERVER_ENDPOINT` | `127.0.0.1:9190` | CE gRPC endpoint; setting it also enables CE mode. `grpc://`/`http://` and bare `host:port` are loopback-only; remote endpoints require `grpcs://` or `https://`. |
| `UPSTASH_REDIS_URL` | `redis://127.0.0.1:6379` | CE working-memory Redis URL; `redis://` is loopback-only, otherwise use `rediss://` (CE only) |
| `KUMIHO_WORKING_MEMORY_TTL` | `86400` | Working-memory idle TTL in CE mode. A local Redis holds a day of buffer for nothing, and the package's one-hour default lost the buffer whenever a turn or a pause ran past an hour |
| `KUMIHO_LLM_BASE_URL` | *(unset)* | OpenAI-compatible LLM endpoint for the LLM-backed paths: summarizer-written consolidation (calls without `summary`), Dream State (`/dream-state`) and LLM edge discovery; reflect and keyless consolidation need none. When set, replaces the fail-fast dead-port fallback. HTTP is loopback-only; remote endpoints require HTTPS. |

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
python ./claude/scripts/setup.py
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

These run from a clone of this repo, where the plugin lives in `claude/`:

```bash
git clone https://github.com/KumihoIO/kumiho-plugins && cd kumiho-plugins
```

```bash
# Claude Code — validate plugin manifest:
claude plugin validate ./claude/.claude-plugin/plugin.json

# Provision runtime and verify required modules:
export KUMIHO_AUTH_TOKEN=YOUR_KUMIHO_BEARER_JWT
python ./claude/scripts/run_kumiho_mcp.py --self-test

# Test discovery with .env.local:
python ./claude/scripts/test_discovery_env.py --env-file .env.local

# Offline checks for self-hosted CE mode (no network/venv needed):
python ./claude/scripts/test_ce_mode.py
```

## Structure

```text
.
├── .claude-plugin/
│   └── plugin.json            # Plugin manifest (name, version, entry points)
│                              # (the marketplace manifest lives at the repo
│                              #  root, not here — see ../.claude-plugin/)
├── .mcp.json                  # MCP server definition (kumiho-memory stdio)
├── .env.local.example         # Template for local auth config
├── commands/
│   ├── kumiho-onboard.md      # /kumiho-onboard slash command (setup wizard)
│   ├── memory-capture.md      # /memory-capture slash command
│   └── dream-state.md         # /dream-state slash command
├── hooks/
│   └── hooks.json             # SessionStart, UserPromptSubmit, SubagentStart, Stop, PostToolUse, SessionEnd, PermissionRequest hooks
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
│   ├── bounded_proc.py           # Subprocess helper whose timeout is a real bound
│   ├── session-bootstrap.py      # SessionStart hook
│   ├── memory-reflex.py          # UserPromptSubmit + SubagentStart hook
│   ├── reflex-observe.py         # Stop + PostToolUse observation hook
│   ├── code-capture-hook.py      # PostToolUse (Bash) + SessionEnd commit capture
│   ├── save-session-artifact.py  # SessionEnd hook
│   ├── auto-approve-memory.py    # PermissionRequest hook
│   ├── code_capture_pending.py   # Keyless pending-commit queue (agent drains it)
│   ├── code_ingest_worker.py     # Detached commit-ingest worker
│   ├── session_mine_worker.py    # Detached session-mining worker
│   ├── reflex_prefetch_worker.py # Detached recall prefetch for the reflex
│   ├── reflex_state.py           # Shared reflex state/config helpers
│   ├── backfill_inventory.py     # History Backfill stage-1 tooling (no LLM)
│   ├── backfill_ingest.py        # History Backfill ingest driver
│   ├── test_discovery_env.py     # Discovery smoke test (needs .env.local)
│   ├── test_ce_mode.py           # Self-hosted CE mode offline checks
│   ├── setup.py                  # Interactive setup wizard
│   └── ingest-skills.py          # Skill ingestion into CognitiveMemory/Skills graph
├── CONNECTORS.md                 # MCP connector details and env reference
└── README.md
```

## License

MIT
