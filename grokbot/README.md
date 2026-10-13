# Kumiho Memory for Grok Bot and Cursor

Hosted Kumiho Cloud memory over OAuth, packaged as an Agent Plugins v1 bundle.
A Cursor manifest is also included for multi-plugin marketplace discovery.
Six skills cover recall, capture, connection repair, preferences, selected-history
backfill and bounded memory review. Version 0.1.0 adapts the hosted 0.1.5 skills.

## Release status and installation

The publisher application was submitted on 2026-10-14 (KST) as Kumiho Inc.
The portal confirmed receipt with "Thanks for applying". Approval and public
listing are pending. The submitting account is an individual account; the
application explicitly requests company attribution and company contact email.
Grok Bot loads plugins through the Cursor dashboard/marketplace, not a local
`~/.cursor/plugins/local` directory. Submit this public repository through
https://cursor.com/marketplace/publish using its root
`.cursor-plugin/marketplace.json` entry pointing to `grokbot`.

After approval, in Grok Bot open **Connect apps**, find **Kumiho Memory**, choose
**Add**, complete Kumiho OAuth, and attach the connector to a task with `@`.
Use `/` to select a memory skill. In Cursor, install the same marketplace plugin
and complete its MCP authentication flow. Until approval, the folder can be
reviewed as a portable plugin; a local schema check is not a Grok Bot install test.

- MCP: `https://mcp.kumiho.cloud/mcp` (Streamable HTTP)
- Sign-in/consent: `https://control.kumiho.cloud`
- OAuth scope: `memory` (read and write; no separate read-only OAuth scope)
- No bundled keys, headers, local server, native memory provider, or hooks.
- No schedules are created by installation. Requested routines use the host's
  supported scheduler with a specified Bot, scope, cadence and timezone.

## First use

Connect a trial or paid Kumiho Cloud workspace. The free 14-day trial starts at
https://kumiho.io/signup?intent=trial with Google sign-in, no card, one trial per
account, one seat, CognitiveMemory only and 2,000 nodes. After expiration, recall
and export remain available for 30 days while new saves stop. Subscribe to keep
using the workspace; deletion is announced by email beforehand.

1. Ask to verify the connected account with `kumiho_list_projects` (read only).
2. Ask: "Remember this decision: use weekly reports because daily ones are noisy."
3. In a fresh conversation ask: "What reporting cadence did I choose, and why?"
4. To correct or retire that memory, identify its returned reference. Retirement
   is reversible exclusion from ordinary recall, not permanent erasure.

Sample writes require user intent; install/connection verification does not save
sample memories automatically. Respect host approval prompts for every tool.

## Data and account boundaries

A narrow recall query leaves the host for Kumiho. Authorized captures, their
provenance and short response summaries are stored in the connected workspace.
Installation is not consent to upload all chats. User-selected exports may be
processed by the current host model; only reviewed captures go to Kumiho. The
optional Python 3.11+ backfill helper is local, standard-library only, and has no
network access. Its supported formats are Codex/Claude JSONL and selected ChatGPT
export conversations; it does not parse Grok Bot or Cursor history databases.

Grok Bot's computer, browser sessions and installed connectors are account-wide.
A Bot is not a separate credential boundary. Do not scan other Bots' files or
reuse conversation buffer IDs across Bots. No laptop access is implied.

The complete 18-tool surface is documented in [the hosted tool catalog](https://github.com/KumihoIO/kumiho-plugins/blob/main/muse/tools.json)
with exact SDK input schemas and MCP annotations. Buffer clear is irreversible;
consolidation both saves a summary and clears the current buffer. Memory review
is performed by the host model, not the native server Dream State engine.

Privacy: https://kumiho.io/en/legal#privacy · Support: support@kumiho.io

## Verification and distribution references

Package checks validate the official portable schemas, all six skills and MCP
references, then build a ZIP. They do not prove a logged-in OAuth/recall/write
flow in either client. Perform that smoke test before claiming client validation.

- [Grok Bot apps](https://docs.x.ai/grok-bot/computer-and-apps)
- [Grok Bot team template describing the shared marketplace route](https://x.ai/bot/marketplace/bots/tinkabot)
- [Cursor formats and marketplace schema](https://cursor.com/docs/reference/plugins)
- [Publish](https://cursor.com/marketplace/publish)
