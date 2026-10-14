# Kumiho Memory for Gemini CLI

Remember decisions, reasons, facts and preferences across conversations in your
private Kumiho workspace. Published by **Kumiho Inc.**

## Install

Requires Gemini CLI 0.64.0+ and its supported Node.js runtime, Git and a local
browser for OAuth. Install the company-published extension:

```sh
gemini extensions install https://github.com/KumihoIO/gemini-kumiho-memory
```

Restart Gemini, then run:

```text
/mcp auth kumiho-memory
/mcp list
/skills
/kumiho:connect
```

Sign in on Kumiho's own page and approve the intended workspace. Gemini manages
OAuth discovery, dynamic client registration, PKCE and token storage. No API
key, password in settings, Python SDK or local MCP server is required. OAuth
requires a browser and a reachable localhost callback.

A trial or paid Kumiho Cloud workspace is required. The 14-day trial uses Google
sign-in, needs no card, includes one seat and 2,000 CognitiveMemory nodes, and
can start once per account. After expiry, recall and export remain available
for 30 days while saves stop; deletion is announced beforehand.
[Start a trial](https://kumiho.io/signup?intent=trial).

## Use

| Command | Purpose |
| --- | --- |
| `/kumiho:remember <fact or decision>` | Save an explicit memory |
| `/kumiho:recall <topic>` | Read relevant saved context |
| `/kumiho:review <topic>` | Preview up to five memories without changes |
| `/kumiho:connect` | Verify or repair the OAuth connection |

Six bundled skills support recall, capture, connection, preferences, bounded
review and approved imports of selected history. The optional backfill helper
needs Python 3.11+ only for parsing user-selected Codex, Claude or ChatGPT files;
Gemini exports use supplied excerpts. Ordinary memory use does not need Python.

Example: ask "Remember that we chose Seoul to reduce pilot latency." Approve
the tool call, then open a fresh conversation and ask "Which region did we
choose for the pilot, and why?" Only the requested distilled memory is stored.

## Data and control

Endpoint: `https://mcp.kumiho.cloud/mcp`. The `memory` OAuth scope allows
both reads and writes; it is not a read-only permission. Gemini's confirmation
policy controls calls. This extension supplies no trust bypass or auto-approval.
Installation does not authorize saving every chat or reading local history.

Kumiho can save summaries and preferences, read memories and review graph
relationships. Retirement excludes a memory from ordinary recall and is
reversible; it is not permanent erasure. Clearing a session buffer is irreversible.
Consolidation saves a summary and clears that buffer; neither action runs merely
because the extension is installed. Never provide passwords or tokens in chat.

This hosted extension does not include the native git Decision Memory tools or
local hooks. Keep only one Kumiho memory integration enabled in the same host
to avoid duplicate calls. A self-hosted CE installation is a separate product path.

## Manage

```sh
gemini extensions update kumiho-memory
gemini extensions disable kumiho-memory
gemini extensions uninstall kumiho-memory
```

Disconnecting or uninstalling the extension does not erase stored Kumiho data.
Manage OAuth grants and data deletion through Kumiho account controls.

[Privacy](https://kumiho.io/en/legal#privacy) ·
[Terms](https://kumiho.io/en/legal#terms) ·
[Support](mailto:support@kumiho.io) · [Kumiho](https://kumiho.io)

## Distribution and verification

Maintained source: [KumihoIO/kumiho-plugins/gemini/cli](https://github.com/KumihoIO/kumiho-plugins/tree/main/gemini/cli).
The dedicated distribution repository places `gemini-extension.json` at its
root for Gemini CLI gallery indexing. The `gemini-cli-extension` GitHub topic
requests daily discovery; listing depends on Google's validation. Publication
by Kumiho does not imply Google endorsement.

Validated with Gemini CLI 0.64.0 using an isolated CLI home: install, extension
listing and six-skill discovery. Live user-authenticated OAuth and memory
save/recall are separate release checks; see the source release receipt for
their actual status.

MIT licensed.
