# Kumiho Memory — Plugin for Claude Code

Persistent graph-native memory plugin for Claude. Runs a local Kumiho MCP
server with `kumiho-memory` so Claude **remembers you across sessions**.

Version: **0.21.3** | Requires: `kumiho>=0.12.2`, `kumiho-memory>=1.4.0`
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

Cloud is the automatic default; an existing explicit backend setting is reused.
Select **self-hosted CE** explicitly when you run your own local
`kumiho-server` (no Cloud token):

```bash
/kumiho-onboard                 # reuses explicit config; otherwise defaults to Cloud
/kumiho-onboard ce              # self-hosted CE (defaults to 127.0.0.1:9190)
```

Run onboarding once before expecting the MCP server on a fresh install. It
adopts Kumiho Desktop's existing `~/.kumiho/venv` when present (no reinstall),
creates Claude's persistent compatibility alias, and asks you to open a new
session. Existing onboarded installations keep using that same alias.
The bare `python` spelling in manual POSIX examples below is shorthand; on
Windows use `/kumiho-onboard` so the OS-account venv or a native PE launcher is
resolved by absolute path instead of a Windows App Execution Alias.

Cloud credentials are owned by the Python SDK. Before starting Claude, either
configure a persistent `KUMIHO_AUTH_TOKEN` in the OS/user or trusted host
environment, or authenticate the shared SDK store from a local terminal with
`kumiho-auth login` / `kumiho-cli login`. Then run setup without a token:

```bash
python -I ./claude/scripts/setup.py --yes
```

Legacy `--token` and `--token-stdin` flags remain for one-run compatibility
verification only. Their value is not saved to the SDK cache, Desktop config,
or `.env.local`, so they do not configure the next Claude restart.

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
| `.claude/settings.json` env | Yes | No (use the Desktop host environment/config) |

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
| Setup wizard      | `python -I scripts/setup.py`                | `npx kumiho-setup`                             |
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
   - **Cloud**: reuse an explicit API token, or sign in locally with `kumiho-auth login` / `kumiho-cli login`
   - **CE**: stand up your own local [`kumiho-server` Community Edition](https://github.com/KumihoIO/kumiho-server-community) (Neo4j + Redis + embedding)
3. **Run `/kumiho-onboard`** inside Claude — the wizard handles backend selection, SDK auth verification, MCP config, and skill ingestion
4. **Start chatting** — Claude now remembers you across sessions

## Choosing a backend (Cloud vs CE)

| | **Cloud** (managed) | **Self-hosted CE** |
|---|---|---|
| Auth | Explicit API token, or SDK-managed CLI login/cache | none — the CE server enforces its own |
| Backend store | managed Neo4j + Redis | **you run** Neo4j + Redis (+ an embedding/LLM) |
| Setup | configure persistent `KUMIHO_AUTH_TOKEN` before host start, or use SDK login; then `/kumiho-onboard cloud` | stand up `kumiho-server-community`, then `/kumiho-onboard ce` |
| Data | summaries in Kumiho Cloud; raw transcripts stay local | everything on the same machine as the plugin |

**Cloud** — the plugin pins `https://control.kumiho.cloud`, then the Python SDK
owns credential loading/refresh, discovery, and routing to the assigned region.
An explicit `KUMIHO_AUTH_TOKEN` is passed through as the preferred credential;
otherwise use either supported local login command:

```bash
# Authenticate once in a local terminal when no persistent token is set:
kumiho-auth login              # kumiho-cli login is also supported
# Then, inside Claude:
/kumiho-onboard cloud
```

**CE** — the graph store (Neo4j) and the embedding model live **inside the
CE server**, which you deploy first from
[kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
(its installer runs the Docker one-shot for Neo4j + Redis and configures the
embedding provider). The plugin only needs to know where to reach it:

```bash
/kumiho-onboard ce
# or, non-interactive, with a non-default endpoint / redis / local LLM:
python -I ./claude/scripts/setup.py --ce --yes \
  --ce-endpoint 127.0.0.1:9190 \
  --ce-redis-url redis://127.0.0.1:6379 \
  --ce-llm-base-url http://127.0.0.1:11434/v1
```

| Plugin-side CE flag | What it points at | Default |
|---|---|---|
| `--ce-endpoint` | the CE server's gRPC endpoint (fronts Neo4j) | `127.0.0.1:9190` |
| `--ce-redis-url` | working-memory Redis | `redis://127.0.0.1:6379` |
| `--ce-llm-base-url` | OpenAI-compatible LLM for the LLM-backed paths: summarizer-written consolidation, Dream State (`/dream-state`), LLM edge discovery (Ollama, llama.cpp, vLLM, …) | fail-fast (reflect and keyless consolidation, i.e. `kumiho_memory_consolidate` with `summary`, need none) |

Project-provided CE routes are restricted to loopback hosts. A CE endpoint in
user-global Claude settings may instead use a remote `https://` or `grpcs://`
host; plaintext remote endpoints remain rejected. Redis stays local, and its
URL may include credentials for password-protected local Redis.

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

Because AUTOMINE sends the raw session transcript through the configured LLM
path, Claude host launches accept this opt-in only from the OS user's persistent
environment or user-global `~/.claude/settings*.json`, never from a project.

Before editing unfamiliar code, ask `kumiho_code_why` for the file first —
never re-litigate a decision the graph already explains.

## Runtime model

### Shared runtime ownership contract

`~/.kumiho/venv` is the single shared runtime for Kumiho Desktop, Claude, and
Codex. Desktop owns initial creation and its platform Python. The plugins may
verify the interpreter, repair only the `CLAUDE_PLUGIN_DATA` compatibility
alias, and install the declared Kumiho package set while holding the shared
provision lock. Neither side replaces a healthy runtime or silently changes
its Python major/minor version. If it is unusable, a dated `venv.broken-*`
backup is created before replacement, and Desktop re-reads the runtime on its
next launch. Plugin data never becomes a second package runtime.

The shared install marker is the package-identity authority. A lock holder
wins concurrent writes; the other host waits briefly or reconnects. This
contract is required for Desktop upgrades and plugin upgrades to coexist.

The bootstrap script (`scripts/run_kumiho_mcp.py`) reuses or creates the shared
`~/.kumiho/venv` and installs required Python packages only when its current
versions do not satisfy the plugin. In Cloud mode it launches the narrow Cloud
adapter, which pins discovery to `https://control.kumiho.cloud` and delegates
token loading, refresh, discovery, and regional routing to the Python SDK. The
plugin does not implement a second Cloud authentication stack.

- **Runtime home:**
  - Windows: `%LOCALAPPDATA%\kumiho-claude`
  - macOS/Linux host launches: `~/.cache/kumiho-claude` from the OS account
  - direct maintenance runs may use `$XDG_CACHE_HOME/kumiho-claude`
- **Shared package runtime:** `~/.kumiho/venv` (also used by Kumiho Desktop and Codex)
- **Override runtime state home:** `KUMIHO_CLAUDE_HOME` (logs, markers, reflex state only;
  host launches accept it only as an absolute user-global Claude setting)
- **Override package spec:** `KUMIHO_CLAUDE_PACKAGE_SPEC` (host launches accept
  it only from a persistent OS-user environment or user-global Claude settings;
  Claude and Codex both honor the same value because they share the venv;
  direct maintenance keeps the environment override)

Host runtime networking restores proxy/certificate values only from the OS
user's persistent environment or user-global Claude settings, not an opened
project. Provisioning children additionally ignore pip/uv/proxy/certificate and
Python-startup variables; put private-index policy in the OS user's pip config.

Default package spec:

```text
kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0
```

## Self-hosted (Community Edition)

By default the launcher asks the Python SDK to resolve a **cloud** Kumiho
endpoint through the official control plane. The SDK uses an explicit token or
its shared login cache; if neither is available, authenticated calls fail
without falling back to CE. If you run your own
[`kumiho-server` Community Edition](https://github.com/KumihoIO/kumiho-server-community)
you can point the plugin at it instead — **opt-in**, so cloud users are
unaffected.

**Easiest path — the wizard.** Run `/kumiho-onboard` and pick
**Self-hosted (Community Edition)**, or from a clone of this repo:

```bash
python -I ./claude/scripts/setup.py --ce --yes
# non-default endpoint: --ce-endpoint HOST:PORT
```

The wizard writes the CE config below to trusted user-global Claude settings,
`.env.local`, your OS user environment, and the Claude Desktop config, then
ingests skills and probes the server — no API token involved.

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
  `127.0.0.1:9190`) without entering Cloud discovery. Project routes are
  loopback-only; user-global TLS CE endpoints may be remote.
- Provides a local working-memory Redis URL (`UPSTASH_REDIS_URL`, default
  `redis://127.0.0.1:6379`) — cloud gets this via the control-plane proxy; CE
  does not. `redis://` and `rediss://` must name a loopback host, with optional
  username/password credentials.
- Logs the selected mode and resolved endpoint on startup so
  "why is my memory empty / not connecting" is answerable at a glance.

Reflect and keyless consolidation (the agent passes `summary`) need no LLM.
For summarizer-written consolidation in CE mode (calls without `summary`),
point at a loopback OpenAI-compatible LLM instead of the fail-fast dead-port
fallback. A provider key without a loopback base URL is not a CE configuration
and is ignored so CE enrichment cannot send memory content off-machine:

```dotenv
# OpenAI-compatible local server (Ollama, llama.cpp, vLLM, …)
KUMIHO_LLM_BASE_URL=http://127.0.0.1:11434/v1
```

Project settings may select a loopback LLM. In CE mode, a remote LLM base is
rejected regardless of scheme or configuration source. The AUTOMINE transcript
opt-in must be placed in the persistent OS-user environment or the user-global
`~/.claude/settings.local.json` / `settings.json` env block.

Start the CE server first (the community installer runs the Docker one-shot for
Neo4j + Redis and an onboarding wizard); see the
[kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
releases.

## Authentication

Cloud authentication is owned by the installed `kumiho` Python SDK. The plugin
pins only the official discovery origin and invokes the SDK. If
`KUMIHO_AUTH_TOKEN` is explicitly configured, it is preserved and the SDK uses
it as the preferred credential. Otherwise the SDK loads and refreshes its
standard `~/.kumiho` credential cache.

Configure authentication before Cloud onboarding with one of these methods.
Never paste a token into chat or put it on a command line.

### Method A — Dashboard API token (recommended)

Mint a long-lived API token from the [kumiho.io dashboard](https://kumiho.io)
under **API Keys**, then configure it as a persistent `KUMIHO_AUTH_TOKEN` in
the OS/user environment or trusted host configuration **before Claude starts**.
Fully restart Claude so the MCP process inherits it, then run:

```text
/kumiho-onboard
```

The plugin and `/kumiho-onboard` do not collect or persist this token. Legacy
`setup.py --token` / `--token-stdin` invocations only pass a token to the SDK
for that setup process; they neither update the SDK credential store nor make
the token available after Claude restarts.

### Method B — CLI login (email + password)

```bash
kumiho-auth login
# or
kumiho-cli login
```

Both commands populate the SDK-owned credential store under `~/.kumiho`. The
SDK loads and refreshes those credentials when Cloud starts.

### Trusted host environment example

An explicit host env block may provide the token. Configure it outside the
plugin before Claude starts; setup will verify it but will not write it:

```json
{
  "env": {
    "KUMIHO_AUTH_TOKEN": "YOUR_KUMIHO_BEARER_JWT"
  }
}
```

### Runtime authentication contract

The active Cloud adapter preserves an explicit `KUMIHO_AUTH_TOKEN`, removes
inherited endpoint, tenant, Firebase, cache-path, and auth-API overrides, pins
`KUMIHO_CONTROL_PLANE_URL=https://control.kumiho.cloud`, and calls public SDK
APIs with `id_token=None`. That lets the SDK apply its own token-first loading,
refresh, discovery, and assigned-region routing. Long-lived MCP hosts ask the
SDK to refresh official discovery on each configuration check; a failed refresh
fails closed instead of probing CE. `KUMIHO_CONTROL_PLANE_API_URL` is an SDK
auth-CLI internal, not plugin routing configuration; the plugin leaves it unset
so the SDK's official default applies. Official discovery records are kept in
the origin-scoped `~/.kumiho/official-cloud/discovery-cache.json`, never a
legacy generic cache.

The plugin starts even when SDK authentication is unavailable so tools remain
visible. Configure persistent `KUMIHO_AUTH_TOKEN` before host startup or run
`kumiho-auth login` / `kumiho-cli login` locally, then restart Claude for
authenticated memory and graph operations.
Standalone ingestion, prefetch, mining, and backfill targets fail closed and
are not executed when the SDK cannot establish the selected Cloud backend.
Host-launched MCP/setup processes ignore ambient `KUMIHO_CONFIG_DIR` and
`KUMIHO_CLAUDE_HOME`; their user-global values must be absolute. This prevents
a repository from selecting a credential/runtime root. Direct maintenance runs
outside Claude/Codex retain the documented path overrides.

### LLM provider for summarization

Consolidation is keyless when the agent passes `summary`. For summarizer-written
summaries (calls without `summary`) in Cloud mode, set either:

- `OPENAI_API_KEY` (default provider path), or
- `ANTHROPIC_API_KEY` with `KUMIHO_LLM_PROVIDER=anthropic`.

If no LLM key is set, the launcher enables a local fail-fast fallback so MCP
tools still initialize without external LLM credentials. In Claude Code, the
host agent (Claude itself) handles query reformulation and edge discovery
natively — no external LLM key needed for those features.

In CE mode, an enrichment LLM must instead be selected with a loopback
`KUMIHO_LLM_BASE_URL`; a provider key alone must never select a remote service.

## Conversation artifacts

The plugin follows a **BYO-storage** model: raw conversation content is stored
locally as Markdown files; the cloud graph stores only metadata and artifact
pointers.

Artifacts are saved to `~/.kumiho/artifacts/{YYYY-MM-DD}/` by default. Because
they contain raw conversation text, an automatic Claude hook accepts an
override only as an absolute local path in user-global
`~/.claude/settings.local.json` or `settings.json` (UNC/network paths and
project environment overrides are ignored):

```json
{
  "env": {
    "KUMIHO_ARTIFACT_DIR": "/absolute/local/path/kumiho-artifacts"
  }
}
```

Direct manual runs retain the legacy `KUMIHO_ARTIFACT_DIR` environment and
agent-preferences-cache behavior.

Each session with 2+ meaningful exchanges produces a Markdown artifact with
YAML frontmatter (session_id, date, topics, summary) and structured
`## Exchange N` sections.

## Environment variables

### Cloud credential

| Variable | Description |
|----------|-------------|
| `KUMIHO_AUTH_TOKEN` | Preferred explicit JWT bearer token for Cloud memory/graph calls. Instead of setting it, you may use the shared SDK store populated by `kumiho-auth login` or `kumiho-cli login`. CE does not use it. |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `KUMIHO_CONTROL_PLANE_URL` | `https://control.kumiho.cloud` | Cloud discovery origin, pinned by the plugin; inherited overrides are ignored |
| `KUMIHO_MCP_LOG_LEVEL` | `INFO` | MCP server log level |
| `KUMIHO_CLAUDE_HOME` | *(platform default)* | Override the runtime state directory (logs, markers, reflex state); it does not move the shared venv. For a Claude host launch this must be an absolute path in user-global `~/.claude/settings*.json`; direct maintenance runs may use the environment. |
| `KUMIHO_CONFIG_DIR` | `~/.kumiho` | Override Kumiho's shared config/credential/runtime root, including `venv`. For a Claude host launch this must be an absolute path in user-global `~/.claude/settings*.json`; direct maintenance runs may use the environment. Use the same value for Desktop, Claude, and Codex. |
| `KUMIHO_CLAUDE_PACKAGE_SPEC` | *(see above)* | Override the shared Claude/Codex pip install spec; persistent OS-user environment or user-global Claude settings on host launches, ambient env for direct maintenance |
| `KUMIHO_REFLEX_CONSOLIDATE_FLOOR` | `20` | Completed turns after which the UserPromptSubmit hook asks the agent to consolidate the session (keyless: the agent or a subagent writes the summary). `0` disables the nudge. |
| `KUMIHO_WORKING_MEMORY_TTL` | `3600` (CE mode: `86400`) | Idle TTL in seconds of the Redis session buffer (working memory). kumiho-memory re-arms it on every write **and** read, so a live session keeps its buffer; a pause longer than this loses it and the next reflect reports `created_bucket: true`. |
| `KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK` | *(unset)* | Set to `1` to disable automatic local no-key fallback setup; it never permits a remote CE LLM or a provider-key-only CE route |
| `KUMIHO_ARTIFACT_DIR` | `~/.kumiho/artifacts/` | Override conversation artifact directory; automatic hooks require an absolute local user-global Claude setting and reject UNC/network-style paths |

`KUMIHO_CONTROL_PLANE_API_URL`, Firebase auth-project variables, discovery
cache paths, and tenant routing are SDK internals rather than plugin routing
options. The Cloud adapter scrubs inherited values; the official control plane
and SDK choose the serving region.

### Self-hosted (Community Edition)

| Variable | Default | Description |
|----------|---------|-------------|
| `KUMIHO_CLAUDE_MODE` | *(unset)* | Set to `ce` (or `community` / `self-hosted` / `local`) to target a self-hosted CE server instead of cloud discovery |
| `KUMIHO_CLAUDE_SERVER_ENDPOINT` | `127.0.0.1:9190` | CE gRPC endpoint; project values must be loopback. A user-global `https://`/`grpcs://` endpoint may be remote. |
| `UPSTASH_REDIS_URL` | `redis://127.0.0.1:6379` | CE working-memory Redis URL; loopback only, with optional credentials for password-protected local Redis |
| `KUMIHO_WORKING_MEMORY_TTL` | `86400` | Working-memory idle TTL in CE mode. A local Redis holds a day of buffer for nothing, and the package's one-hour default lost the buffer whenever a turn or a pause ran past an hour |
| `KUMIHO_LLM_BASE_URL` | *(unset)* | OpenAI-compatible LLM endpoint for the LLM-backed paths: summarizer-written consolidation (calls without `summary`), Dream State (`/dream-state`) and LLM edge discovery; reflect and keyless consolidation need none. In CE mode its host must be loopback regardless of scheme. |

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
python -I ./claude/scripts/setup.py
```

### Auth error (401)

If you see:

```text
Memory proxy error 401: {"error":"invalid_id_token"}
```

Set an explicit fresh dashboard token, or run `kumiho-auth login` /
`kumiho-cli login` in a local terminal. Restart Claude so the Python SDK can
reload or refresh its credential cache.

### Connection refused

If you see:

```text
StatusCode.UNAVAILABLE ... 127.0.0.1:8080 ... Connection refused
```

Then the Python SDK did not resolve a regional Cloud gRPC endpoint from the
official control plane. Re-authenticate locally, verify network access to
`https://control.kumiho.cloud`, and restart Claude. Do not set a custom control
plane or regional endpoint; the official discovery response owns routing.

If you see DNS failures for `us-central.kumiho.cloud`, a stale endpoint override is
likely present. This plugin ignores `KUMIHO_SERVER_ENDPOINT`/`KUMIHO_SERVER_ADDRESS`
and asks the SDK to resolve the assigned region through the official control
plane. Its official-origin cache is isolated from legacy/custom discovery data.

### Cloudflare 1010 error

If discovery returns Cloudflare `error code: 1010`, verify that the installed
Python SDK is current and that your network permits the official Kumiho Cloud
service. Discovery transport and identity are SDK-owned.

## Validation and smoke test

These run from a clone of this repo, where the plugin lives in `claude/`:

```bash
git clone https://github.com/KumihoIO/kumiho-plugins && cd kumiho-plugins
```

```bash
# Claude Code — validate plugin manifest:
claude plugin validate ./claude/.claude-plugin/plugin.json

# Provision runtime and verify required modules:
python -I ./claude/scripts/run_kumiho_mcp.py --self-test

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
├── .env.local.example         # Template for loopback CE config
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
│   ├── run_kumiho_mcp.py         # Bootstrap launcher (shared venv + backend adapter)
│   ├── run_kumiho_cloud.py       # Official Cloud SDK adapter
│   ├── run_kumiho_ce.py          # Explicit loopback-only CE SDK adapter
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
│   ├── test_ce_mode.py           # Self-hosted CE mode offline checks
│   ├── setup.py                  # Interactive setup wizard
│   └── ingest-skills.py          # Skill ingestion into CognitiveMemory/Skills graph
├── CONNECTORS.md                 # MCP connector details and env reference
└── README.md
```

## License

MIT
