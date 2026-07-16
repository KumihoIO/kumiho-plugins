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
/kumiho-onboard <API-TOKEN>   # Kumiho Cloud (kumiho.io › Dashboard › API Keys)
/kumiho-onboard ce            # self-hosted kumiho-server Community Edition
```

Full guide, hooks, Decision Memory, and Cloud-vs-CE details:
**[claude/README.md](claude/README.md)**.

## What you get

- **Cross-session memory** — identity bootstrap at session start, semantic
  recall of past sessions, automatic capture of decisions and preferences.
- **Decision Memory** — `kumiho_code_why` answers *why is this code the way
  it is?* with the decision, its rationale, the alternatives that were
  rejected, and the measurements that decided them. Commits are mined
  automatically; sessions can be mined too (opt-in).
- **Your data, your rules** — Cloud stores only summaries; raw transcripts
  stay on your machine. Or run the whole stack yourself on CE.

## Backends

| | Cloud (managed) | Self-hosted CE |
|---|---|---|
| Auth | API token | none (CE enforces its own) |
| Store | managed Neo4j + Redis | **you run** Neo4j + Redis + embedding via [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community) |
| Setup | `/kumiho-onboard <TOKEN>` | `/kumiho-onboard ce` |

## Install for OpenAI Codex

Since the July 2026 ChatGPT×Codex merger, Codex ships the same plugin
model — and it reads this repo's marketplace manifest directly (verified
on codex-cli 0.134.0):

```bash
codex plugin marketplace add KumihoIO/kumiho-plugins
codex plugin add kumiho-memory@kumiho-plugins
```

Details, the Codex-tailored native plugin, and the legacy setup script:
**[codex/README.md](codex/README.md)**.

## All integrations

| Host | Directory | What it is |
|---|---|---|
| **Claude Code / Desktop / Cowork** | [`claude/`](claude/README.md) | Full plugin: skills (two-reflex protocol), MCP server, hooks, Decision Memory, History Backfill |
| **OpenAI Codex** (CLI · IDE · merged ChatGPT app) | [`codex/`](codex/README.md) | Codex plugin (`.codex-plugin` native + Claude-manifest compat) with Codex-tailored skill and vendored launcher |
| **ChatGPT** (remote connector) | [`gpt/`](gpt/README.md) | `kumiho-gpt-connect` — OAuth 2.1 remote MCP connector gateway (developer mode / Apps SDK contract) |
| **OpenClaw** | [`openclaw/`](openclaw/README.md) | Full code plugin (`@kumiho/openclaw-kumiho`) with tools + setup wizard |
| **n8n** | [`n8n/`](n8n/README.md) | `n8n-nodes-kumiho` workflow nodes |
| **ComfyUI** | [`comfyui/`](comfyui/README.md) | Custom nodes for asset/graph workflows |

## Links

- Docs: [docs.kumiho.io](https://docs.kumiho.io)
- Cloud & API keys: [kumiho.io](https://kumiho.io)
- Community Edition server: [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
- License: MIT
