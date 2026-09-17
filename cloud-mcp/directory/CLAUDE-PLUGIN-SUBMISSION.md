# Kumiho Memory — Claude plugin directory submission

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

This URL becomes valid once the branch `feat/claude-directory-plugin` merges to
`main`. Before that, the directory on `main` has no `.claude-plugin/plugin.json`
or `.mcp.json`. The official marketplace lists subdirectory plugins as
`{"source": "git-subdir", "url": "https://github.com/KumihoIO/kumiho-plugins.git", "path": "cloud-mcp/directory/kumiho-memory", ...}`,
the same shape it uses for Airtable's `plugins/airtable`.

### Plugin homepage

```
https://kumiho.io/docs/connect/claude
```

Returned 200 on 2026-09-17 (redirects to `/en/docs/connect/claude`).

### Plugin name *

```
kumiho-memory
```

Display name in the manifest: **Kumiho Memory**.

Availability check, 2026-09-17, against
`anthropics/claude-plugins-official` `.claude-plugin/marketplace.json` at
`ea0a38e1d671aa18a30431c9160e31193dc9860b` (308 plugins): no plugin is named
`kumiho-memory` or `kumiho`, no entry mentions "kumiho" anywhere, and no plugin
name or display name contains "memory". "Kumiho" is Kumiho Inc.'s own brand.

The same name is used by the local plugin in Kumiho's own marketplace
(`kumiho-memory@kumiho-plugins`). Plugin IDs include the marketplace, so the
directory listing would be `kumiho-memory@claude-plugins-official`. Users should
enable only one of the two in Claude Code; the README and the onboarding skill
say so.

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
- **Tools.** The connector exposes 18 tools. The skills reference 8 of them, all
  inside that set: `kumiho_memory_engage`, `kumiho_memory_retrieve`,
  `kumiho_memory_reflect`, `kumiho_memory_consolidate`, `kumiho_get_revision_by_tag`,
  `kumiho_deprecate_item`, `kumiho_list_projects`, `kumiho_get_spaces`.
  `cloud-mcp/tests/test_directory_bundle.py` enforces this.
- **No hooks, agents or commands.** The plugin is skills plus one remote MCP
  server. The only script is the backfill helper, which reads only files passed to
  it, makes no network calls and writes only inside the batch directory it
  creates.
- **Dream State** runs its assessment with Claude itself; the server-side
  `kumiho_memory_dream_state` tool is not part of the connector.
- **Do not claim** that the service is free or that data is never used for
  training. The published policy at `https://kumiho.io/en/legal` makes no training
  statement.
