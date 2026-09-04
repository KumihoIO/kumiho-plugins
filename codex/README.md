# Kumiho Memory for OpenAI Codex

Graph-native persistent memory (working memory + consolidation + ontology
+ **Decision Memory**) for [Codex](https://github.com/openai/codex) —
the Codex CLI and Codex in the ChatGPT desktop app — backed by the same
`kumiho-memory` MCP server the Claude plugin uses. The Codex IDE extension
does not currently load native plugins; use the legacy/manual MCP registration
at the end of this guide for IDE sessions.

## Native Codex plugin

This repository exposes two parallel host packages:

- `.agents/plugins/marketplace.json` selects `./codex` for Codex.
- `.claude-plugin/marketplace.json` selects `./claude` for Claude.

Installing or updating the Codex plugin does not replace the Claude plugin or
change its commands, hooks, or settings. Codex loads its own
`.codex-plugin/plugin.json`, skill, and MCP definition from `./codex`.

## Requirements

- codex-cli 0.153.2 or newer (the native marketplace layout is verified on
  0.153.2; older plugin-capable builds may still select the Claude package)
- Node.js on `PATH`
- Python 3.10 or newer
- Network access only when the shared runtime still needs package installation

The MCP entry runs `node scripts/run_kumiho_mcp.mjs`. The Node launcher uses
the shared `~/.kumiho/venv` first, then `KUMIHO_PYTHON`, then these fallbacks:

| Platform | Discovery order |
| --- | --- |
| Windows | `~/.kumiho/venv`, then absolute `py.exe` / `python*.exe` files resolved from absolute PATH entries |
| macOS / Linux | `~/.kumiho/venv`, then `python3`, `python`, `py -3` |

If discovery fails, set `KUMIHO_PYTHON` to the full path of a Python 3.10+
executable before starting Codex. On Windows this must be an absolute path to
an existing, valid PE `.exe`; command aliases such as `python` are rejected
before launch. Windows Store App Execution Aliases are ignored.
The shared path is derived from the operating-system account record, so a
project-level `HOME`, `USERPROFILE`, or `KUMIHO_CONFIG_DIR` override cannot
redirect Codex to a repository-supplied interpreter or credential directory.

## Install

```bash
codex plugin marketplace add KumihoIO/kumiho-plugins
codex plugin add kumiho-memory@kumiho-plugins
```

Prefer the `owner/repo` form. A local-path marketplace copies the working
tree into Codex's plugin cache, so do not keep credentials or `.env.local`
files in that tree.

## Update or uninstall

Refresh the Git marketplace, reinstall the plugin snapshot, and then start a
new Codex session:

```bash
codex plugin marketplace upgrade kumiho-plugins
codex plugin remove kumiho-memory@kumiho-plugins
codex plugin add kumiho-memory@kumiho-plugins
```

To uninstall, run only the `codex plugin remove` command. The non-secret
`~/.kumiho/codex.json` backend preference is deliberately left intact for a
later reinstall. For a complete Codex-only cleanup, remove only
`~/.kumiho/codex.json` after uninstalling. Do not remove the rest of
`~/.kumiho`, including its shared runtime, SDK-owned credentials, or discovery
cache: Kumiho Desktop and Claude use the same state root.

`marketplace upgrade` refreshes Git marketplaces only. Contributors testing a
local-path marketplace should use Codex's plugin cachebuster helper before
reinstalling, then restore the release version in the source manifest before
committing:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py" codex
codex plugin add kumiho-memory@kumiho-plugins
```

PowerShell:

```powershell
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$kumihoPython = Join-Path $HOME ".kumiho\venv\Scripts\python.exe"
& $kumihoPython (Join-Path $codexRoot "skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py") codex
codex plugin add kumiho-memory@kumiho-plugins
```

## Automatic backend setup

Start Codex and invoke `$kumiho-onboard`, or ask Codex to set up Kumiho Memory.
It does not ask which backend to use: it reuses an existing Codex choice,
otherwise selects a valid cached Cloud login, otherwise detects local CE, and
finally defaults to Cloud. The installed skill resolves its own versioned
plugin path and runs five stages: runtime provisioning,
backend/authentication, Codex configuration, skill ingestion, and read-only
backend verification.

The only Codex-specific backend preference/config file the wizard writes is
`~/.kumiho/codex.json`; it contains a backend name and non-secret CE endpoints,
and Claude does not read it. Kumiho Desktop, Claude, and Codex share the
`~/.kumiho` state root and its `~/.kumiho/venv` Python runtime. The Kumiho
Python SDK owns all Cloud token parsing, credential-cache loading and refresh,
login fallback, discovery, and regional routing in that shared root. Official
discovery state is shared at
`~/.kumiho/official-cloud/discovery-cache.json`; the plugin never selects the
legacy, origin-ambiguous `~/.kumiho/discovery-cache.json`. The Codex wizard
never edits Claude Desktop config, Claude settings, or Claude hooks.

Backend support is symmetric and independent: Codex can use either Cloud or
CE, and Claude can separately use either Cloud or CE. The host-specific config
prevents selection bleed; it does not restrict either host's backend choices.
Codex pins Cloud discovery to `https://control.kumiho.cloud`; the SDK then uses
only the regional endpoint returned by that official discovery service. The
plugin clears `KUMIHO_CONTROL_PLANE_API_URL` instead of setting an authentication
route; the SDK authentication CLI owns its official default.

### Kumiho Cloud

Request Cloud explicitly only when overriding auto-detection. An explicit
`KUMIHO_AUTH_TOKEN` is passed through and has first priority. Otherwise the
Python SDK uses the shared `~/.kumiho` credential cache, refreshes it when
possible, and requests login when needed. Setup continues without a prompt when
the SDK can authenticate; otherwise Codex gives you an exact
`node ... --onboard cloud` command to run in your own terminal. That command
uses the SDK's masked login and never places a password or token in chat,
process arguments, plugin configuration, or memory. The same login can also be
performed directly with `kumiho-auth login` or `kumiho-cli login` from the
shared environment.

Do not paste credentials into Codex. The memory skill treats every secret as
an absolute no-capture exception. To force a fresh login from a checkout, run
the secure terminal wizard with `--reauth`:

```bash
node codex/scripts/run_kumiho_mcp.mjs --onboard cloud --reauth
```

### Self-hosted CE

Start your Community Edition server first. CE mode needs no Kumiho Cloud token.
Request CE explicitly only when overriding auto-detection; its endpoint defaults to
`127.0.0.1:9190`. From a checkout, the equivalent command is:

```bash
node codex/scripts/run_kumiho_mcp.mjs --onboard ce
```

For a non-default local server:

```bash
node codex/scripts/run_kumiho_mcp.mjs --onboard ce \
  --ce-endpoint 127.0.0.1:9190 \
  --ce-redis-url redis://127.0.0.1:6379
```

The wizard rejects endpoints and URLs containing embedded credentials. CE is a
strictly local backend: its server, Redis, and optional LLM URLs must use an
actual loopback host, such as `localhost`, `127.0.0.1`, or `::1`. TLS schemes
such as `grpcs://`, `rediss://`, and `https://` do not make a non-loopback host
eligible.

An external LLM key is optional: agent-written reflection, code capture, and
consolidation with an explicit summary are keyless. When configured for CE, the
LLM endpoint must remain on one of the same loopback hosts.

## First run and provisioning

The Python packages live in the shared `~/.kumiho/venv` runtime also used by
Kumiho Desktop and Claude. If its installed versions already satisfy the
plugin, no pip command runs. Otherwise the first MCP start begins background
provisioning (about 150 MB on a fresh machine) and exits intentionally;
installing dependencies inside Codex's
MCP startup window would be killed partway through.

Wait for provisioning to finish, invoke `$kumiho-onboard`, and then open a
**new Codex session**. Subsequent starts reuse the prepared runtime. Progress
and errors are written to:

- Windows: `%LOCALAPPDATA%\kumiho-claude\provision.log`
- macOS / Linux: `${XDG_CACHE_HOME:-~/.cache}/kumiho-claude/provision.log`

From a full checkout, `--doctor` diagnoses Node and Python discovery. The other
two commands below build and test the same shared runtime. Use
`$kumiho-onboard` inside Codex for the complete installed-plugin workflow.

```bash
node codex/scripts/run_kumiho_mcp.mjs --doctor
node codex/scripts/run_kumiho_mcp.mjs --provision
node codex/scripts/run_kumiho_mcp.mjs --self-test
```

## Verify the installation

```bash
codex plugin list
codex mcp get kumiho-memory
```

Check that:

- `kumiho-memory@kumiho-plugins` is version `0.21.0` or newer and
  its source ends in `codex`, not `claude`.
- The MCP command is `node`, its argument is
  `scripts/run_kumiho_mcp.mjs`, and no command or argument contains a
  literal `${...}` placeholder.
- After provisioning, a new Codex session exposes tools such as
  `kumiho_memory_engage`, `kumiho_memory_reflect`, and
  `kumiho_code_why`.

## Agent protocol

The native plugin loads two Codex-specific skills automatically; plugin users
do not need to copy [`AGENTS.md`](AGENTS.md). `$kumiho-onboard` owns setup and
repair. The memory skill loads the published identity once per session,
performs first-meeting identity onboarding only after two definitive not-found
lookups, recalls relevant context, reflects durable decisions and preferences,
and asks `kumiho_code_why` before modifying unfamiliar code.

Capture is **agent-driven by default**:

- `kumiho_memory_reflect` stores decisions, preferences, facts, and
corrections surfaced during the conversation.
- `kumiho_code_capture` records a meaningful implementation decision and
  its rationale after the agent makes it.

This native package does not silently install a repository git hook.
`AGENTS.md` remains useful for legacy/manual setups that do not load the
bundled skill.

Codex supplies its stable thread id in each MCP request's per-call `_meta`.
The Node stdio bridge recognizes the supported metadata spellings and carries
it into a Python request context, preserving
Kumiho's post-consolidation session rotation without a process-global
environment race. Normal memory calls therefore omit `session_id`, remain
stable across turns, and fail loudly instead of inventing an id if an older
host supplies neither per-call metadata nor an environment fallback.

## Optional post-commit hook (full checkout only)

If you explicitly want repository-wide post-commit capture, clone this repo and
install its hook into the target repository:

```bash
python codex/scripts/install_git_hook.py /path/to/your/repo
```

The hook is an opt-in complement to agent-driven `kumiho_code_capture`.
It is available only from a full checkout because plugin snapshots do not
include the shared ingest worker. It queues/mines new commits in a detached
worker; logs go to the plugin state directory as `code-ingest.log`.

## Tools

The server exposes working memory, consolidation, semantic recall, graph
operations, and Decision Memory. Core tools include:

| Tool | Description |
| --- | --- |
| `kumiho_memory_engage` | Recall relevant memories and build context for the current request |
| `kumiho_memory_reflect` | Buffer the response and store selected durable memories |
| `kumiho_code_why` | Why is this code the way it is? — git-anchored decisions + verbatim evidence chains |
| `kumiho_code_capture` | Store a decision the current agent made, including rationale and code anchors |
| `kumiho_code_ingest` | Mine a git commit range into decision nodes (idempotent) |

## Troubleshooting

### Old source, stale cache, or missing `$kumiho-onboard`

Codex sessions and plugin snapshots are immutable once loaded. If
`$kumiho-onboard` is absent, `codex plugin list` still shows `0.20.5` (or any
older version), or the
source ends in `claude`, remove the stale plugin and marketplace snapshot, then
add them again:

```bash
codex plugin remove kumiho-memory@kumiho-plugins
codex plugin marketplace remove kumiho-plugins
codex plugin marketplace add KumihoIO/kumiho-plugins
codex plugin add kumiho-memory@kumiho-plugins
```

Run the two verification commands again and start a new Codex session. An
already-open session cannot discover skills or MCP tools added by a reinstall.

### MCP entry still contains `${...}`

This indicates a stale plugin or a legacy global MCP block. Reinstall the
plugin as above. If you previously ran `setup_codex.py`, inspect
`~/.codex/config.toml` and remove only its old
`[mcp_servers.kumiho-memory]` block before reinstalling; do not keep a
global entry and a plugin-scoped entry with the same name.

### Node or Python is not found

```bash
node --version
python3 --version
```

On Windows, the launcher resolves PATH candidates to absolute `.exe` files,
rejects Windows Store aliases, and validates the PE header before launch.
Install Python 3.10+ or point `KUMIHO_PYTHON` at its absolute path, then run
`--doctor` from a checkout.

### Tools are absent just after installation

A cold first start exits while provisioning continues. Check `provision.log`,
wait for it to finish, and open a new session. If provisioning failed, fix the
reported Python, network, or `venv` error and invoke `$kumiho-onboard` again.
Running a checkout's `--provision` command also targets `~/.kumiho/venv`.

### Authentication or CE connection fails

- Cloud: set an explicit `KUMIHO_AUTH_TOKEN`, or authenticate in your own
  terminal with `kumiho-auth login` or `kumiho-cli login`, then ask Codex to
  rerun `$kumiho-onboard` for Cloud.
- CE: confirm the server is running, then ask Codex to rerun
  `$kumiho-onboard` for CE with the correct endpoint. Codex's choice is stored
  in `~/.kumiho/codex.json`, not in Claude settings or shell-only environment
  variables.

## Legacy/manual registration

`scripts/setup_codex.py` remains for older Codex installations without
native plugin support and for the Codex IDE extension, and requires a full
checkout. Do not run it alongside the native plugin in the same Codex home
because both register an MCP server named `kumiho-memory`.

```bash
# Cloud
node codex/scripts/run_kumiho_mcp.mjs --onboard cloud
python codex/scripts/setup_codex.py

# CE (macOS/Linux example)
KUMIHO_CLAUDE_MODE=ce \
KUMIHO_CLAUDE_SERVER_ENDPOINT=127.0.0.1:9190 \
python codex/scripts/setup_codex.py
```

The legacy script does not overwrite an existing
`[mcp_servers.kumiho-memory]` block. Native plugin installation is the
recommended path.
