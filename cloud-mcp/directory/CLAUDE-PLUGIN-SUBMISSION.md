# Kumiho Memory Cloud — Claude plugin directory submission

Answers for the plugin submission form at
`claude.ai/admin-settings/directory/submissions/plugins/new` (Claude Code and
Cowork). This is the **hosted** plugin in `cloud-mcp/directory/kumiho-memory/`:
the remote Kumiho Memory connector plus six skills. It is not the local
`claude/` plugin listed in this repository's own marketplace.

The connector itself was submitted separately to the Claude Connectors Directory;
that pack is `cloud-mcp/DIRECTORY.md`.

---

## Step 1

**[Not visible from the repo — fill in from the form.]**

## Step 2 — Plugin details

### Link to plugin *

```
https://github.com/KumihoIO/kumiho-plugins/tree/main/cloud-mcp/directory/kumiho-memory
```

The bundle is on `main` since #105. The plugin name below is on `main` once
`chore/rename-hosted-plugin-cloud` merges; submit after that, or the form reads
the old name. The folder keeps its name, so this link does not change. The
official marketplace lists subdirectory plugins as
`{"source": "git-subdir", "url": "https://github.com/KumihoIO/kumiho-plugins.git", "path": "cloud-mcp/directory/kumiho-memory", ...}`,
the same shape it uses for Airtable's `plugins/airtable`.

### Plugin homepage

```
https://kumiho.io/docs/connect/claude
```

Returned 200 on 2026-09-17 (redirects to `/en/docs/connect/claude`).

### Plugin name *

```
kumiho-memory-cloud
```

Display name in the manifest: **Kumiho Memory Cloud**. The directory plugin ID
would be `kumiho-memory-cloud@claude-plugins-official`, and the Claude Code MCP
server id `plugin:kumiho-memory-cloud:kumiho-memory` (the `.mcp.json` server key
stays `kumiho-memory`).

Availability check, 2026-09-17, against
`anthropics/claude-plugins-official` `.claude-plugin/marketplace.json` at
`ea0a38e1d671aa18a30431c9160e31193dc9860b` (head of `main`, 308 plugins, no
duplicate names): no plugin is named `kumiho-memory-cloud`, `kumiho-memory` or
`kumiho`, no entry mentions "kumiho" anywhere, and no plugin name or display name
contains "memory". The names that contain "cloud" (`cloud-sql-mysql`,
`cloud-sql-postgresql`, `cloud-sql-sqlserver`, `cloudflare`, `cloudinary`,
`google-cloud-storage`, `grafana-cloud-mcp`, `netsuite-suitecloud`) are
unrelated. "Kumiho" is Kumiho Inc.'s own brand.

Why not `kumiho-memory`: the local plugin in Kumiho's own marketplace
(`kumiho-memory@kumiho-plugins`, the `claude/` directory) already has that name.
Claude Code namespaces plugin components by plugin name, not by marketplace, so
two plugins named `kumiho-memory` would register identical MCP tool names
(`mcp__plugin_kumiho-memory_kumiho-memory__kumiho_*`), the same `/mcp` server id
(`plugin:kumiho-memory:kumiho-memory`) and the same skill names
(`kumiho-memory:dream-state`, `:memory-capture`, `:kumiho-onboard`,
`:kumiho-personalize`, `:kumiho-backfill`). `kumiho-memory-cloud` removes the
collision and says what this plugin is: the OAuth connection to Kumiho Cloud.
`cloud-mcp/tests/test_directory_bundle.py` fails if the two names ever match
again.

Users should still enable only one of the two in Claude Code: each runs its own
memory protocol, so with both enabled every recall and capture would happen
twice. The README and the onboarding skill say so.

Deliberately unchanged: the folder `cloud-mcp/directory/kumiho-memory/` (the link
above and the OpenAI bundle), `.codex-plugin/plugin.json` (still named
`kumiho-memory`; already submitted to OpenAI), the skill names, and the claude.ai
upload `kumiho-memory-skill.zip`.

### Plugin description *

```
Kumiho Memory gives Claude a persistent memory that carries across conversations. Claude can recall the decisions, preferences and facts you settled earlier, save new ones you authorize, correct or retire outdated memories, and import reviewed memories from past Claude Code, Codex or ChatGPT conversations you select.

The plugin bundles the hosted Kumiho Memory MCP connector (https://mcp.kumiho.cloud/mcp, OAuth sign-in) with six skills: kumiho-memory (recall and save context), memory-capture (save one approved item), kumiho-onboard (connect and verify the workspace), kumiho-personalize (communication preferences), dream-state (bounded review and cleanup of stored memories) and kumiho-backfill (preview and import selected past conversations). Imports and cleanup changes are shown for approval before anything is written. Nothing runs locally except an optional stdlib-only helper that prepares selected history files for review.

Memories are stored in your own Kumiho Cloud workspace. Requires a paid Kumiho Cloud account; the self-hosted Community Edition is not supported by this plugin.
```

### Example use cases *

```
1. Pick up where you left off
   "What did we decide about the billing migration, and what was still blocking it?"
   Claude recalls the saved decision, its rationale and the open blocker from an earlier conversation.

2. Remember a decision with its reasoning
   "Remember that we chose Postgres over DynamoDB for the events service because the query patterns are relational."
   Claude saves one typed decision memory and confirms what was stored.

3. Stop repeating your preferences
   "Remember that I want the answer first and the reasoning after, and that I write Go and Python."
   Claude stores the preferences and applies them in later conversations.

4. Import what matters from past sessions
   "Look at these two Claude Code sessions and propose the decisions worth keeping. Don't save anything until I approve."
   Claude prepares the selected files locally, shows the proposed memories, and stores only the ones you approve.

5. Review and tidy stored memories
   "Review my memories about the events service and show me duplicates or contradictions before changing anything."
   Claude previews a bounded set of memories with evidence and applies only the corrections or retirements you approve.
```

## Step 3

**[Not visible from the repo — fill in from the form.]** Material likely to be
asked for:

| Item | Value |
|---|---|
| Icon | `cloud-mcp/directory/kumiho-memory/assets/logo.png` (512×512 PNG, downscaled from `cloud-mcp/submission-assets/kumiho-fox.png`) |
| Privacy policy | `https://kumiho.io/en/legal#privacy` (the `privacy` anchor exists on the live page) |
| Support contact | `support@kumiho.io` |
| Documentation | `https://kumiho.io/docs/connect/claude` |
| License | MIT |
| Test account | Needed: the reviewer must sign in to a paid Kumiho Cloud workspace. Reuse the account and populate script in `cloud-mcp/DIRECTORY.md` §6. **[needs publisher]** |

## Reviewer-facing facts

- **Authentication.** `.mcp.json` declares only `{"type": "http", "url": "https://mcp.kumiho.cloud/mcp"}`.
  There are no headers, secrets or `userConfig`; the client discovers the
  authorization server through the MCP authorization spec (OAuth 2.1, CIMD and
  DCR, PKCE S256). Sign-in and consent happen at `control.kumiho.cloud`.
- **Tools.** The connector exposes 18 tools. The skills reference 10 of them, all
  inside that set: `kumiho_memory_engage`, `kumiho_memory_retrieve`,
  `kumiho_memory_reflect`, `kumiho_memory_consolidate`,
  `kumiho_get_revision_by_tag`, `kumiho_deprecate_item`, `kumiho_chat_get`,
  `kumiho_chat_clear`, `kumiho_list_projects`, `kumiho_get_spaces`.
  `cloud-mcp/tests/test_directory_bundle.py` enforces this.
- **Standalone core skill.** claude.ai web and Claude Desktop chat do not pass MCP
  server `instructions` to the model
  ([anthropics/claude-ai-mcp#93](https://github.com/anthropics/claude-ai-mcp/issues/93)),
  so `skills/kumiho-memory/SKILL.md` carries the full recall, capture, session,
  forget and credential-refusal protocol itself. It is also packaged for upload
  to claude.ai by `cloud-mcp/directory/scripts/package_web_skill.py`.
- **No hooks, agents or commands.** The plugin is skills plus one remote MCP
  server. The only script is the backfill helper, which reads only files passed to
  it, makes no network calls and writes only inside the batch directory it
  creates.
- **Dream State** runs its assessment with Claude itself; the server-side
  `kumiho_memory_dream_state` tool is not part of the connector.
- **Do not claim** that the service is free or that data is never used for
  training. The published policy at `https://kumiho.io/en/legal` makes no training
  statement.
