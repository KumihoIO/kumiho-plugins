# Kumiho Plugins

Persistent **graph-native memory** for AI coding agents. Kumiho remembers you
across sessions, recalls prior context by meaning, and captures the *why*
behind your code — decisions, rationale, rejected alternatives — as a
git-anchored graph.

## Install for Claude Code

```bash
claude plugin marketplace add KumihoIO/kumiho-plugins
claude plugin install kumiho-memory@kumiho-plugins
```

Or inside Claude Code: run `/plugin`, **Add marketplace** →
`KumihoIO/kumiho-plugins`, then install **kumiho-memory**.

Then choose a backend and finish setup:

```bash
/kumiho-onboard cloud         # explicit API token preferred; SDK login also works
/kumiho-onboard ce            # self-hosted kumiho-server Community Edition
```

Full guide, hooks, Decision Memory, and Cloud-vs-CE details:
**[claude/README.md](claude/README.md)**.

## What you get

- **Cross-session memory** — identity bootstrap, semantic recall of past
  sessions, and durable storage of decisions and preferences. The memory skill
  drives these operations in Codex; Claude can add host-side automation through
  its hooks.
- **Decision Memory** — `kumiho_code_why` answers *why is this code the way
  it is?* with the decision, its rationale, the alternatives that were
  rejected, and the measurements that decided them. In Codex, the agent
  captures decisions while it works and a full-checkout post-commit hook is an
  opt-in; Claude's hook automation is documented separately.
- **Your data, your rules** — Cloud stores only summaries; raw transcripts
  stay on your machine. Or run the whole stack yourself on CE.

## Backends

| | Cloud (managed) | Self-hosted CE |
|---|---|---|
| Auth | API token | none (CE enforces its own) |
| Store | managed Neo4j + Redis | **you run** Neo4j + Redis + embedding via [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community) |
| Setup | Claude: `/kumiho-onboard cloud` → SDK auth<br>Codex: `$kumiho-onboard` → Cloud | Claude: `/kumiho-onboard ce`<br>Codex: `$kumiho-onboard` → CE |

Both hosts support both backends independently: Claude may use Cloud while
Codex uses CE, or the reverse. Their backend selections do not overwrite one
another. Kumiho Desktop, Claude, and Codex reuse the package runtime at
`~/.kumiho/venv` when it already satisfies the required versions.

## Install for OpenAI Codex

Codex uses this repository's native marketplace at
`.agents/plugins/marketplace.json`. Its `kumiho-memory` entry points to
`./codex`; the Claude marketplace continues to point independently to
`./claude`.

Use codex-cli 0.153.2 or newer, plus Node.js and Python 3.10+. The small Node
launcher discovers a compatible Python automatically; set `KUMIHO_PYTHON`
only when you need to override it. Install the native plugin:

```bash
codex plugin marketplace add KumihoIO/kumiho-plugins
codex plugin add kumiho-memory@kumiho-plugins
```

Start Codex and invoke `$kumiho-onboard` (or ask Codex to set up Kumiho
Memory). It auto-detects an existing backend/login/local CE without asking,
then mirrors Claude's five setup stages: shared Kumiho runtime,
host-specific backend configuration, authentication, skill ingestion, and
read-only backend verification. Cloud credentials are never accepted in chat or command
arguments; if no cached login exists, the wizard gives you one secure terminal
command. CE defaults to `127.0.0.1:9190` and needs no Cloud token.

The wizard reuses or provisions `~/.kumiho/venv` (about 150 MB on a fresh
machine). If MCP starts before onboarding, it performs any required
provision in the background and exits on purpose to avoid Codex's startup
timeout. After onboarding, start a **new Codex session**. Confirm the installed
source and server definition with:

```bash
codex plugin list
codex mcp get kumiho-memory
```

The plugin source should end in `codex`, not `claude` (using the platform's
path separator), and the MCP entry should use `node`
with `scripts/run_kumiho_mcp.mjs` without literal `${...}` placeholders.
Detailed setup, PowerShell examples, first-run diagnostics, and stale-cache
recovery:
**[codex/README.md](codex/README.md)**.

## All integrations

| Host | Directory | What it is |
|---|---|---|
| **Claude Code / Desktop / Cowork** | [`claude/`](claude/README.md) | Full plugin: skills (two-reflex protocol), MCP server, hooks, Decision Memory, History Backfill |
| **OpenAI Codex** (CLI · ChatGPT desktop; manual MCP for IDE) | [`codex/`](codex/README.md) | Native Codex plugin selected by `.agents/plugins/marketplace.json`: onboarding + memory skills, Node-to-Python MCP launcher, agent-driven capture, optional full-checkout git hook |
| **ChatGPT** (remote connector) | [`gpt/`](gpt/README.md) | `kumiho-gpt-connect` — OAuth 2.1 remote MCP connector gateway (developer mode / Apps SDK contract) |
| **OpenClaw** | [`openclaw/`](openclaw/README.md) | Full code plugin (`@kumiho/openclaw-kumiho`) with tools + setup wizard |
| **n8n** | [`n8n/`](n8n/README.md) | `n8n-nodes-kumiho` workflow nodes |
| **ComfyUI** | [`comfyui/`](comfyui/README.md) | Custom nodes for asset/graph workflows |

## Links

- Docs: [docs.kumiho.io](https://docs.kumiho.io)
- Cloud & API keys: [kumiho.io](https://kumiho.io)
- Community Edition server: [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
- License: MIT
