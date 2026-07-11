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

## Other hosts

This repo also ships Kumiho memory integrations for other agents —
[`codex/`](codex/) (OpenAI Codex CLI), [`gpt/`](gpt/), [`openclaw/`](openclaw/),
[`n8n/`](n8n/), [`comfyui/`](comfyui/). The Claude Code plugin lives in
[`claude/`](claude/).

## Links

- Docs: [docs.kumiho.io](https://docs.kumiho.io)
- Cloud & API keys: [kumiho.io](https://kumiho.io)
- Community Edition server: [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community)
- License: MIT
