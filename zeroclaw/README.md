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

Like the Claude plugin, this plugin uses a **discovery-first** approach:

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

| Tool | ZeroClaw syntax | Notes |
|------|----------------|-------|
| **engage** | `kumiho_memory__engage` | Composite: recall + context building |
| **reflect** | `kumiho_memory__reflect` | Composite: buffer response + store captures + edge discovery |
| recall | `kumiho_memory__recall` | Low-level — prefer engage |
| store | `kumiho_memory__store` | Low-level — prefer reflect |
| retrieve | `kumiho_memory__retrieve` | |
| consolidate | `kumiho_memory__consolidate` | |
| dream_state | `kumiho_memory__memory_dream_state` | |

If tools aren't loaded yet, use `tool_search("kumiho")` in-session.

## Cross-Agent Compatibility

All three Kumiho plugins share the same Neo4j + Redis backend, `CognitiveMemory` graph, and skill-ingestion pipeline. Memories stored by one agent are recallable by any other. Cross-agent parity exists at the data model and discoverable-skill layer; host-side automation still differs by platform.

| Capability        | Claude Code                                 | ZeroClaw                                  | OpenClaw                                      |
| ----------------- | ------------------------------------------- | ----------------------------------------- | --------------------------------------------- |
| Tool syntax       | `kumiho_memory_recall(...)`                 | `kumiho_memory__recall(...)`              | `memory_search(...)` / `creative_capture(...)` |
| Behavioral rules  | Discovery-first SKILL.md + SessionStart context | Discovery-first SKILL.md               | TypeScript hooks + injected memory instructions |
| Session bootstrap | SessionStart hook + SKILL bootstrap         | Inline SKILL bootstrap                    | TypeScript identity bootstrap in `before_prompt_build` |
| Recall behavior   | Agent-triggered recall guided by SKILL      | Agent-triggered recall guided by SKILL    | Automatic `before_prompt_build` hook           |
| Capture behavior  | Agent-triggered `store` / `add_response`    | Agent-triggered `store` / `add_response`  | Automatic `agent_end` buffering + capture      |
| Consolidation     | Agent-triggered                             | Agent-triggered                           | Threshold + idle timer + manual tool           |
| Dream State       | `/dream-state` command                      | `SKILL.toml` cron                         | Config schedule + manual tool                  |
| Setup wizard      | `python scripts/setup.py`                   | `python scripts/setup.py`                 | `npx kumiho-setup`                             |
| Skill ingestion   | Local SKILL + bundled references            | Claude canonical SKILL + bundled references | Claude canonical SKILL + bundled references |
| Privacy model     | Raw transcripts stay local                  | Graph summaries only                      | Raw transcripts stay local + PII redaction     |
| Creative memory   | Via graph skills                            | Via graph skills                          | Built-in `creative_capture` / `creative_recall` |
| Local artifacts   | SessionEnd hook                             | Via graph skills (no built-in hook)       | Built-in artifact manager                      |

### Related plugins

- **kumiho-plugins/claude/** — Claude Code plugin with Python hooks for SessionStart/End and auto-approve. Source of truth for behavioral rules and SKILL.md.
- **kumiho-plugins/openclaw/** — OpenClaw plugin with TypeScript hooks for auto-recall and auto-capture. 10 wrapped agent tools.
- **FoxClaw** (on hold) — Planned soft fork of ZeroClaw with Kumiho memory baked into the Rust core. Graph-stored skills carry over directly.

## Troubleshooting

**Auth errors**: Verify `KUMIHO_AUTH_TOKEN` is set and valid. Run `python -c "from kumiho import KumihoClient; c = KumihoClient(); print(c.list_projects())"` to test.

**MCP connection**: Check that `python -m kumiho.mcp_server` runs without errors. Ensure the Python environment has `kumiho[mcp]` installed.

**Tools not found**: Use `tool_search("kumiho")` in ZeroClaw to trigger deferred tool loading. The MCP `tools/list` handshake must complete before tools are available.

**Skills not discoverable**: Run `scripts/ingest-skills.py` to populate the graph. Verify with `kumiho_memory__retrieve(space_path="CognitiveMemory/Skills")`.
