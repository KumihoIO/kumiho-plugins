# Kumiho Memory for ZeroClaw

Persistent graph-native cognitive memory for ZeroClaw agents. Recalls context across sessions, stores decisions and preferences, and discovers behavioral skills dynamically from the Kumiho memory graph.

## How it works

This plugin connects ZeroClaw to the same Kumiho backend (Neo4j + Redis) used by the Claude Code and OpenClaw plugins. All three agents share the same memory graph — memories stored by one agent are recallable by any other.

```
ZeroClaw Agent
  ↓ SKILL.md (behavioral instructions)
  ↓ MCP (stdio)
kumiho-mcp (Python process)
  ↓
Neo4j + Redis (Kumiho Cloud)
```

### Discovery-first SKILL.md

Unlike the Claude and OpenClaw plugins which list all behavioral rules inline, this plugin uses a **discovery-first** approach:

- **Inline** (~100 lines): Hard constraints, session bootstrap, per-turn memory protocol, store-link protocol, and the Skill Discovery Protocol itself
- **In the graph** (`CognitiveMemory/Skills/*`): Everything else — creative memory, edges & traversal, privacy rules, onboarding, artifacts, etc.

The agent discovers skills on-demand by searching the graph. New skills added by DreamState or any agent become immediately discoverable without changing the SKILL.md file.

## Quick setup

Run the interactive setup wizard — it handles everything:

```bash
python scripts/setup.py
```

The wizard will:

1. Find or create a Python venv with kumiho packages
2. Authenticate with Kumiho Cloud (paste your API token)
3. Patch ZeroClaw's `config.toml` with the MCP server config
4. Copy the skill into ZeroClaw's `~/.zeroclaw/skills/` directory
5. Ingest discoverable skills into the `CognitiveMemory/Skills` graph

## Prerequisites

- [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) installed
- Python 3.10+
- Kumiho account ([kumiho.io](https://kumiho.io)) — free tier available

## Manual installation

If you prefer to set up manually instead of using the wizard:

### 1. Install Python dependencies

```bash
pip install "kumiho[mcp]>=0.9.16" "kumiho-memory[all]>=0.3.16"
```

### 2. Authenticate

**Option A — API token** (recommended):

Get a token from [kumiho.io](https://kumiho.io) > Dashboard > API Keys, then:

```bash
export KUMIHO_AUTH_TOKEN=kh_live_...
```

**Option B — CLI login**:

```bash
python -m kumiho.auth_cli login
```

This creates `~/.kumiho/kumiho_authentication.json` with session tokens.

### 3. Copy the skill into ZeroClaw

```bash
cp -r kumiho-plugins/zeroclaw/ ~/.zeroclaw/skills/kumiho-memory/
```

Or for workspace-local skills:

```bash
cp -r kumiho-plugins/zeroclaw/ ./skills/kumiho-memory/
```

### 4. Add MCP server to config.toml

Add the following to your `~/.zeroclaw/config.toml`:

```toml
[mcp_servers.kumiho_memory]
transport = "stdio"
command = "python"
args = ["-m", "kumiho.mcp_server"]
tool_timeout_secs = 30

[mcp_servers.kumiho_memory.env]
KUMIHO_AUTH_TOKEN = "${KUMIHO_AUTH_TOKEN}"
```

See `config.toml.example` for the full snippet with the venv Python path.

### 5. Ingest skills into the graph (one-time)

```bash
python scripts/ingest-skills.py
```

This populates `CognitiveMemory/Skills` with 9 discoverable skill items. Use `--dry-run` to preview.

## Plugin structure

| File | Purpose |
|------|---------|
| `SKILL.toml` | ZeroClaw skill manifest — MCP server declaration, DreamState cron |
| `SKILL.md` | Discovery-first behavioral instructions loaded into agent context |
| `config.toml.example` | MCP server snippet for `~/.zeroclaw/config.toml` |
| `.env.example` | Environment variable template |
| `scripts/setup.py` | Interactive setup wizard — auth, config, skill install, ingestion |
| `scripts/ingest-skills.py` | One-time graph ingestion of discoverable skills |

## Tool naming

ZeroClaw prefixes MCP tools with the server name and double underscore:

| Tool | ZeroClaw syntax |
|------|----------------|
| recall | `kumiho_memory__recall` |
| store | `kumiho_memory__store` |
| retrieve | `kumiho_memory__retrieve` |
| discover_edges | `kumiho_memory__discover_edges` |
| consolidate | `kumiho_memory__consolidate` |
| dream_state | `kumiho_memory__memory_dream_state` |

If tools aren't loaded yet, use `tool_search("kumiho")` in-session.

## Cross-agent compatibility

| Agent | Tool syntax | Integration |
|-------|-----------|-------------|
| Claude Code | `kumiho_memory_recall(...)` | Hooks (Python) + SKILL.md + MCP |
| ZeroClaw | `kumiho_memory__recall(...)` | SKILL.md + MCP |
| OpenClaw | `memory_search(...)` | Hooks (TypeScript) + wrapped tools |

All agents share the same `CognitiveMemory` graph. Skills stored by any agent are discoverable by all others.

## Relationship to other plugins

- **kumiho-plugins/claude/** — Claude Code plugin with Python hooks for SessionStart/End and auto-approve. Source of truth for behavioral rules.
- **kumiho-plugins/openclaw/** — OpenClaw plugin with TypeScript hooks for auto-recall and auto-capture. 9 wrapped agent tools.
- **FoxClaw** (on hold) — Planned soft fork of ZeroClaw with Kumiho memory baked into the Rust core. When shipped, the graph-stored skills from this plugin carry over directly.

## Troubleshooting

**Auth errors**: Verify `KUMIHO_AUTH_TOKEN` is set and valid. Run `python -c "from kumiho import KumihoClient; c = KumihoClient(); print(c.list_projects())"` to test.

**MCP connection**: Check that `python -m kumiho.mcp_server` runs without errors. Ensure the Python environment has `kumiho[mcp]` installed.

**Tools not found**: Use `tool_search("kumiho")` in ZeroClaw to trigger deferred tool loading. The MCP `tools/list` handshake must complete before tools are available.

**Skills not discoverable**: Run `scripts/ingest-skills.py` to populate the graph. Verify with `kumiho_memory__retrieve(space_path="CognitiveMemory/Skills")`.
