# @kumiho/openclaw-kumiho

[![npm version](https://img.shields.io/npm/v/@kumiho/openclaw-kumiho)](https://www.npmjs.com/package/@kumiho/openclaw-kumiho)
[![license](https://img.shields.io/npm/l/@kumiho/openclaw-kumiho)](LICENSE)

Long-term cognitive memory for [OpenClaw](https://openclaw.ai) agents, powered by [Kumiho.io](https://kumiho.io).

Your agent forgets everything between sessions. This plugin fixes that — it watches conversations, extracts what matters, and brings it back the next time it's relevant. Raw chats never leave your machine; only structured summaries reach the cloud.

```text
┌─────────────────────────────────────────────────────────┐
│ OpenClaw Gateway (Node.js)                              │
│                                                         │
│  @kumiho/openclaw-kumiho plugin                         │
│    ├── Auto-Recall / Auto-Capture hooks                 │
│    ├── Idle consolidation timer                         │
│    ├── Dream State scheduler                            │
│    ├── 10 agent tools                                   │
│    └── McpBridge ──stdin/stdout──► kumiho-mcp (Python)  │
│                                       ├── kumiho-memory │
│                                       ├── Redis buffer  │
│                                       ├── LLM summary   │
│                                       └── Neo4j graph   │
└─────────────────────────────────────────────────────────┘
```

> **Cloud mode** is also available — HTTPS calls to Kumiho Cloud, no Python process needed. See [Configuration](#configuration).

## Quick Start

```bash
# 1. Install the plugin
openclaw plugins install @kumiho/openclaw-kumiho

# 2. Set up the Python backend and authenticate
npx --package=@kumiho/openclaw-kumiho kumiho-setup

# 3. (Optional) Populate shared behavioral skills
python scripts/ingest-skills.py

# 4. Let kumiho-setup update openclaw.json, or paste the printed snippet
```

```json5
{
  "plugins": {
    "entries": {
      "openclaw-kumiho": {
        "enabled": true,
        "config": { "userId": "your-user-id" }
      }
    }
  }
}
```

```bash
# 4. Verify
openclaw kumiho stats
```

That's it. `kumiho-setup` now offers to merge the plugin entry into `~/.openclaw/openclaw.json` automatically. If that file is missing or you skip the merge, it prints the exact `plugins.entries.openclaw-kumiho` block to paste manually. Mode defaults to `"local"`, the venv is auto-detected, and Dream State schedule is loaded from `~/.kumiho/preferences.json`.

The setup wizard now includes GPT-5-sized OpenAI presets for Dream State and Consolidation, plus a custom model entry so you can type host-specific model IDs such as `gpt-5.3-codex-spark`.

## Features

| Feature | Description |
| ------- | ----------- |
| **Auto-Recall** | Relevant memories injected into context before each agent response |
| **Zero-Latency Recall** | Stale-while-revalidate prefetch — recall adds 0ms on turn 2+ |
| **Auto-Capture** | Facts extracted and stored after each agent response |
| **Two-Track Consolidation** | Session flushed to graph on message threshold *or* idle timeout |
| **Creative Memory** | Track creative outputs (docs, code, plans) with full graph lineage |
| **Cross-Channel** | Memories follow the user across WhatsApp, Slack, Telegram, etc. |
| **Privacy-First** | Raw conversations and media stay local; only summaries go to graph DB |
| **PII Redaction** | Emails, phone numbers, SSNs redacted before upload |
| **10 Agent Tools** | Explicit memory operations the AI can invoke |
| **Dream State** | Scheduled memory maintenance and edge discovery |
| **Local Artifacts** | Conversation logs and media stored on your filesystem |
| **Local-first** | Everything runs on your machine — no server to deploy |

## How It Works

### Per-Turn Lifecycle

```text
User sends message
    │
    ├── before_prompt_build
    │     ├── Cancel idle consolidation timer (user is active)
    │     ├── Consume prefetched recall instantly (0ms) — or wait up to 1.5s on cold start
    │     └── Inject <kumiho_memory> / <kumiho_project> context into prompt
    │
    ├── Agent generates response
    │
    └── agent_end
          ├── Store assistant response to Redis buffer
          ├── Check consolidation threshold (messageCount >= 20) → flush to graph if met
          ├── Start background prefetch for next turn (stale-while-revalidate)
          └── Arm idle consolidation timer (default: 5 min)
```

### Zero-Latency Recall

After the first turn, memory recall adds **zero milliseconds** to agent startup:

1. **Turn 1 (cold start):** Recall runs in parallel with working memory storage. Up to 1500ms timeout — the agent starts regardless.
2. **`agent_end`:** While the user reads the response, a background prefetch quietly fetches memories relevant to the current topic.
3. **Turn 2+:** `before_prompt_build` finds the prefetched result ready instantly — no round-trip. A new background prefetch starts for turn 3.

### Two-Track Consolidation

Working memory (Redis) is flushed to long-term graph storage (Neo4j) by whichever track fires first:

| Track           | Trigger                                            | Default                |
| --------------- | -------------------------------------------------- | ---------------------- |
| **Threshold**   | `messageCount >= consolidationThreshold`           | 20 messages (10 turns) |
| **Idle**        | No activity for `idleConsolidationTimeout` seconds | 300 s (5 min)          |

The idle track ensures short sessions (3–5 turns) still get consolidated. Both tracks reset the counter and start a fresh session.

### Privacy Model

```text
YOUR DEVICE (OpenClaw)              KUMIHO CLOUD / NEO4J
========================            ========================
Raw chat transcripts     ----X----> (never uploaded)
Voice recordings         ----X----> (never uploaded)
Images / screenshots     ----X----> (never uploaded)

Structured summaries     ----------> Stored in graph DB
Extracted facts          ----------> Stored in graph DB
Topic classifications    ----------> Stored in graph DB
Artifact pointers        ----------> Stored (paths, not content)
```

### Cross-Channel Continuity

Session IDs are user-centric, not channel-specific:

```text
alice-personal:user-7f3a9b:20260203:001
```

A user starting on WhatsApp at 9 AM and continuing on Slack at 2 PM seamlessly shares context.

### Project & Space Auto-Creation

The `CognitiveMemory` project (or any name you configure) is automatically created on first use — no manual setup required.

## Configuration

### Minimal Config

```json5
// openclaw.json
{
  "plugins": {
    "entries": {
      "openclaw-kumiho": {
        "enabled": true,
        "config": {
          "userId": "your-user-id"
        }
      }
    }
  }
}
```

### Local Mode — Custom Python Environment

If `kumiho-mcp` is in a virtualenv or a non-default Python path:

```json5
{
  "plugins": {
    "entries": {
      "openclaw-kumiho": {
        "enabled": true,
        "config": {
          "userId": "your-user-id",
          "local": {
            "pythonPath": "/home/user/.venvs/kumiho/bin/python",
            "command": "kumiho.mcp_server"
          }
        }
      }
    }
  }
}
```

### Cloud Mode

Skip `kumiho-setup` and use an API key instead:

```json5
{
  "plugins": {
    "entries": {
      "openclaw-kumiho": {
        "enabled": true,
        "config": {
          "mode": "cloud",
          "apiKey": "${KUMIHO_API_TOKEN}",
          "userId": "your-user-id"
        }
      }
    }
  }
}
```

### Environment Variables

```bash
# Cloud mode
export KUMIHO_API_TOKEN="kh_live_abc123..."

# LLM keys — set by OpenClaw during onboarding; forwarded to the Python process automatically

# Both modes
export KUMIHO_MEMORY_ARTIFACT_ROOT="~/.kumiho/artifacts"
```

> **Redis auto-discovery:** In local mode, the Python SDK automatically discovers the Upstash Redis connection through the Kumiho control plane. If discovery fails (e.g. offline), it falls back to the server-side memory proxy. Override with `UPSTASH_REDIS_URL` if needed.

### Full Configuration Reference

```json5
{
  "plugins": {
    "entries": {
      "openclaw-kumiho": {
        "enabled": true,
        "config": {
          // Mode selection
          "mode": "local",               // "local" or "cloud"

          // Cloud mode auth (not needed for local)
          "apiKey": "${KUMIHO_API_TOKEN}",
          "endpoint": "https://api.kumiho.cloud",

          // Project & User
          "project": "CognitiveMemory",  // Auto-created if missing
          "userId": "your-user-id",

          // Automation
          "autoCapture": true,           // Extract facts after each turn
          "autoRecall": true,            // Inject memories before each turn
          "localSummarization": true,    // Summarize locally before upload

          // Memory behavior
          "consolidationThreshold": 20,    // Messages before threshold consolidation
          "idleConsolidationTimeout": 300, // Seconds idle before consolidation (0 = off)
          "sessionTtl": 3600,              // Working memory TTL (1 hour)
          "topK": 5,                       // Max memories per recall
          "searchThreshold": 0.3,          // Min similarity score (0–1)

          // Privacy
          "piiRedaction": true,
          "artifactDir": "~/.kumiho/artifacts",
          "privacy": {
            "uploadSummariesOnly": true, // Never send raw text
            "localArtifacts": true,      // Media stays on device
            "storeTranscriptions": true  // Upload voice/image transcriptions
          },

          // Dream State (auto-loaded from ~/.kumiho/preferences.json if omitted)
          "dreamStateSchedule": "0 3 * * *",   // Cron — "off" to disable
          "dreamStateModel": { "provider": "openai", "model": "gpt-5-mini" },
          "consolidationModel": { "provider": "openai", "model": "gpt-5-codex" },

          // LLM for summarization (optional, uses agent default)
          "llm": {
            "provider": "openai",
            "model": "gpt-5-mini"
          },

          // Local mode subprocess settings
          "local": {
            "pythonPath": "python",  // Python executable
            "command": "kumiho-mcp", // CLI entry point or module name
            "args": [],              // Extra CLI args
            "env": {},               // Extra env vars for the Python process
            "cwd": null,             // Working directory
            "timeout": 30000         // Request timeout (ms)
          }
        }
      }
    }
  }
}
```

## Agent Tools

The plugin exposes 10 tools the AI can invoke during conversations:

| Tool | Description |
|------|-------------|
| `memory_search` | Query memories by natural language |
| `memory_store` | Explicitly save a fact, decision, or summary |
| `memory_get` | Retrieve a specific memory by kref |
| `memory_list` | List recent memories |
| `memory_forget` | Delete or deprecate a memory |
| `memory_consolidate` | Trigger session consolidation immediately |
| `memory_dream` | Trigger Dream State maintenance |
| `creative_capture` | Save a creative output (doc, code, plan) with graph lineage |
| `creative_job_status` | Check the status and resulting krefs of an async creative capture |
| `creative_recall` | List creative outputs for a project space |

### Example Interactions

```text
User:  "Remember that I prefer dark mode in all my editors"
Agent: calls memory_store → type: "fact", content: "User prefers dark mode..."

User:  "What do I prefer for my editor theme?"
Agent: calls memory_search → query: "editor theme preferences"

User:  "Save this blog draft to my blog-jan26 project"
Agent: calls creative_capture → title: "Blog Draft", project: "blog-jan26", kind: "document"
```

## CLI Commands

```bash
# Search memories
openclaw kumiho search "what languages does the user know"

# Show memory stats (mode, session, message count)
openclaw kumiho stats

# Manually consolidate current session
openclaw kumiho consolidate

# Trigger Dream State maintenance
openclaw kumiho dream

# Capture last response as a creative output
openclaw kumiho capture "Blog Draft" my-blog --kind document

# List creative outputs for a project
openclaw kumiho project my-blog --query "drafts"
```

## Auto-Reply Commands

In any chat channel:

```text
/memory stats                              # Show memory status
/capture <title> | <project> [| <kind>]   # Capture last response as creative output
```

Example:

```text
/capture Blog Draft | my-blog | document
```

Available kinds: `document`, `code`, `design`, `plan`, `analysis`, `other`

## Programmatic API

Use Kumiho memory outside the OpenClaw plugin system:

```typescript
import { createKumihoMemory } from "@kumiho/openclaw-kumiho";

// Local mode (default) — uses kumiho-mcp Python subprocess
const memory = createKumihoMemory({ mode: "local" });
await memory.start(); // Spawns Python process + MCP handshake

// Cloud mode — uses Kumiho Cloud HTTPS API
// const memory = createKumihoMemory({
//   mode: "cloud",
//   apiKey: "kh_live_abc123...",
// });

// Both modes share the same API:

// Recall memories
const results = await memory.recall("user preferences");

// Store a fact
await memory.store("User prefers async communication", {
  type: "fact",
  topics: ["communication", "work-style"],
});

// Working memory
const sessionId = await memory.newSession("user-1");
await memory.addMessage(sessionId, "user", "Hello!");
await memory.addMessage(sessionId, "assistant", "Hi there!");
const messages = await memory.getMessages(sessionId);

// Auto-recall/capture hooks
const recalled = await memory.autoRecallHook("What did I say about meetings?");
console.log(recalled.contextInjection);
await memory.autoCaptureHook("You mentioned you prefer async...");

// Dream State
const stats = await memory.dream();
console.log(`Deprecated: ${stats.deprecated}, Edges: ${stats.edges_created}`);

// Clean up — important in local mode to stop the Python process
await memory.close();
```

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

### Skill Ingestion

Populate the shared `CognitiveMemory/Skills` graph with behavioral skills (one-time, after setup):

```bash
python scripts/ingest-skills.py          # ingest all
python scripts/ingest-skills.py --dry-run  # preview only
python scripts/ingest-skills.py --list     # list sections
```

This uses the Claude plugin's SKILL.md as the canonical source. All three agents discover the same skills.

## Troubleshooting

### `kumiho-mcp` not found / Python process fails to start

Run the setup wizard to install the Python backend:

```bash
npx --package=@kumiho/openclaw-kumiho kumiho-setup
```

Or install manually into an existing environment:

```bash
pip install "kumiho[mcp]" "kumiho-memory[all]"
python -c "from kumiho.mcp_server import main; print('kumiho-mcp OK')"
```

If `kumiho-mcp` is in a virtualenv, point the plugin at it:

```json5
"local": {
  "pythonPath": "/path/to/venv/bin/python",
  "command": "kumiho.mcp_server"
}
```

### Plugin not loading / no memory injected

1. Confirm `enabled: true` is set in `openclaw.json`.
2. Run `openclaw kumiho stats` — if it errors, the service isn't running.
3. Check OpenClaw logs for `Kumiho:` prefixed lines — startup errors are logged there.
4. Confirm `userId` is set; without it no session ID can be generated.

### Redis / working memory errors

The Python SDK discovers the Upstash Redis connection automatically via the Kumiho control plane. If you see Redis errors:

- Make sure you completed `kumiho-setup` and ran `kumiho-auth login`.
- If you're offline, the SDK falls back to the server-side memory proxy automatically.
- To override: `export UPSTASH_REDIS_URL="rediss://..."` before starting OpenClaw.

### Dream State not running

- Verify the schedule in `~/.kumiho/preferences.json` (`dreamState.schedule`) is not `"off"`.
- The scheduler arms on plugin start — restart OpenClaw after changing the schedule.
- Test manually: `openclaw kumiho dream` (or call the `memory_dream` tool in chat).

### Memories not persisting across sessions

Working memory (Redis) is flushed to the graph by consolidation. If you end a session abruptly — before 5 minutes of idle time or 20 messages — run:

```bash
openclaw kumiho consolidate
```

Or lower `idleConsolidationTimeout` in your config (e.g. `60` seconds).

## Comparison with mem0

| Feature | @kumiho/openclaw-kumiho | @mem0/openclaw-mem0 |
| ------- | ----------------------- | ------------------- |
| Backend | MCP stdio to Python SDK (local) or HTTPS (cloud) | Cloud API or self-hosted (Open-Source Mode) |
| Storage | Graph DB (Neo4j) | Vector DB (24+ options) |
| Privacy | Raw data stays local | Cloud by default; local with Open-Source Mode |
| PII Redaction | Built-in (TypeScript + Python) | Not built-in |
| Cross-channel | Session ID strategy | User ID only |
| Memory types | fact, decision, action, error, summary | Flat memories |
| Recall latency | 0ms on turn 2+ (prefetch) | Per-request |
| Consolidation | Threshold + idle timer | Manual |
| Creative outputs | Full graph lineage tracking | Not available |
| Dream State | Automated maintenance | Not available |
| Tool execution | Tracks successes/failures | Not available |
| Local artifacts | File pointers, never uploaded | Not available |
| Local mode | Python SDK via MCP stdio | Self-hosted (separate deploy) |

## Development

```bash
npm install
npm run build
npm run typecheck
npm test
npm run dev       # Watch mode
```

### Python requirements (local mode development)

```bash
# Recommended: use the setup script
npm run setup

# Manual
pip install "kumiho[mcp]" "kumiho-memory[all]"
python -c "from kumiho.mcp_server import main; print('kumiho-mcp OK')"

# Ingest shared behavioral skills (one-time)
python scripts/ingest-skills.py
```

## Related Packages

- [`kumiho`](https://pypi.org/project/kumiho/) — Core Python SDK
- [`kumiho-memory`](https://pypi.org/project/kumiho-memory/) — Python memory provider (used by local mode)
- [`kumiho-server`](https://github.com/kumihoclouds/kumiho-server) — Rust gRPC server

## License

MIT
