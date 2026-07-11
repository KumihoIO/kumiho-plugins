# Kumiho Memory for OpenAI Codex CLI

Graph-native persistent memory (working memory + consolidation + ontology
+ **Decision Memory**) for [Codex CLI](https://github.com/openai/codex),
backed by the same `kumiho-memory` MCP server the Claude plugin uses.

Requires a full `kumiho-plugins` checkout (the launcher and ingest worker
are shared with `claude/` — one source of truth, monorepo-relative paths).

## Setup

### Cloud (kumiho.cloud tenant)

```bash
kumiho-auth login                       # once; caches control-plane auth
python codex/scripts/setup_codex.py    # registers [mcp_servers.kumiho-memory]
```

### Self-hosted CE

```bash
KUMIHO_CLAUDE_MODE=ce \
KUMIHO_CLAUDE_SERVER_ENDPOINT=127.0.0.1:50051 \
python codex/scripts/setup_codex.py
```

Codex does not expand `${VAR}` placeholders in `config.toml`, so the setup
script materializes auth/mode values at setup time. Re-run it after
rotating credentials (delete the `[mcp_servers.kumiho-memory]` block
first — an existing block is never overwritten).

An LLM key in the environment at setup time (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `KUMIHO_LLM_*`) is captured for consolidation and
decision mining; without one, summarization falls back to fail-fast mode
and decision capture skips quietly.

## Agent protocol

Append [`AGENTS.md`](AGENTS.md) to your project's `AGENTS.md`. It wires the
two-reflex protocol (engage → reflect) and the Decision Memory rule:
*ask `kumiho_code_why` before modifying unfamiliar code.*

## Decision auto-capture (recommended)

Codex has no lifecycle hooks, so capture rides on git itself:

```bash
python codex/scripts/install_git_hook.py /path/to/your/repo
```

This installs a `post-commit` hook that spawns a detached worker mining
the new commit into decision nodes — incremental (already-captured commits
are marker-skipped at zero LLM cost), never blocks or slows the commit,
and editor-agnostic: the same hook serves any tool that commits. Logs land
in the plugin state dir (`code-ingest.log`).

## Tools

Everything the Claude plugin exposes: `kumiho_memory_engage` /
`kumiho_memory_reflect` (composite), chat working memory, consolidation,
recall, graph operations — plus, with `KUMIHO_MEMORY_CODE=1` (set by the
setup script):

| Tool | Description |
| --- | --- |
| `kumiho_code_why` | Why is this code the way it is? — git-anchored decisions + verbatim evidence chains |
| `kumiho_code_ingest` | Mine a git commit range into decision nodes (idempotent) |
