# Kumiho Memory for Hermes Agent

The Kumiho-maintained memory plugin for [Hermes Agent](https://hermes-agent.nousresearch.com).
Recall decisions, facts and preferences across conversations in your own Kumiho
Cloud workspace. Six skills use the hosted MCP connector with browser sign-in;
no local Kumiho server, SDK, Node.js bridge or API key is needed.

This is the first Hermes distribution, version **0.1.0**, adapted from the hosted
Kumiho Memory **0.1.5** skills. Hermes catalog acceptance is a separate maintainer
review; publication here does not imply Nous Research endorsement.

## Install and connect

Use a current Hermes release with portable Agent Plugins v1 Streamable HTTP support
(the Hermes catalog uses `>=0.21.6` for this integration surface).

```bash
hermes plugins install KumihoIO/kumiho-plugins/hermes --no-enable
hermes plugins enable kumiho-memory
hermes mcp login kumiho-memory
```

Complete the Kumiho sign-in and consent page in your browser, then start a new
Hermes chat. If you run the messaging gateway, restart it after enabling the
plugin. OAuth discovery, PKCE, tokens and refresh are handled by Hermes.
Run the login command in an interactive terminal; unattended jobs cannot complete
a first sign-in. Never paste passwords, tokens or MFA codes into chat.

Connect a trial or paid Kumiho Cloud workspace. New users can start the
[14-day trial](https://kumiho.io/signup?intent=trial) with Google, no card required,
once per account: 1 seat, CognitiveMemory only and 2,000 nodes. After expiration,
recall and export remain available for 30 days while new saves stop. Subscribe
to continue with the same workspace. No workspace means reconnecting alone will
not fix access. The hosted connector does not reach self-hosted Community Edition.

Ask Hermes:

> Use the Kumiho onboarding skill to verify my workspace.

Then, when you want to test a real save:

> Remember this decision: our weekly planning meeting is on Monday because
> everyone can attend then.

In a fresh conversation:

> What did we decide about our weekly planning meeting, and why?

A successful tool listing is not proof of a successful save. The skills verify
returned results and memory references before reporting success.

## Six skills

| Skill | Purpose |
|---|---|
| kumiho-memory | Recall prior context and preserve authorized lasting information |
| memory-capture | Save one fact, preference or decision supplied by the user |
| kumiho-onboard | Connect and verify access without creating sample memories |
| kumiho-personalize | View or update explicitly requested preferences |
| dream-state | Preview bounded memory maintenance and apply authorized changes |
| kumiho-backfill | Preview and import distilled memories from selected history |

Hermes exposes portable skills under a generated namespace. Use `skills_list`
and `skill_view` to discover the actual names, or ask in natural language.
The plugin does not replace Hermes's built-in memory provider and does not
automatically upload every turn or all local history.

## Data, approvals and credentials

- Tool calls send the requested queries, summaries and approved captures to
  `https://mcp.kumiho.cloud/mcp`. Authentication uses Kumiho's authorization
  service at `https://control.kumiho.cloud`. Saved memories belong to the
  connected workspace and its access controls.
- The package marks its server `untrusted`. Hermes asks before calls that the
  server does not annotate read-only; unattended writes fail closed. Do not
  disable approval to work around a failed background save. Scheduled Dream
  State defaults to read-only review.
- An existing `mcp_servers.kumiho-memory` entry in the active profile replaces
  this package's server configuration, including trust. Review a duplicate
  configuration instead of silently replacing it.
- The plugin never reads or writes credentials. Hermes owns its OAuth storage.
  No self-updater, telemetry, background process or lifecycle upload hook is
  shipped.
- Backfill reads only files or excerpts the user selected. An optional bundled
  Python 3.11+ stdlib helper prepares Codex/Claude Code/ChatGPT exports locally;
  it has no network or authentication code. It writes review packets in a new
  directory the user selects. It does **not** parse Hermes session databases.
  Hermes excerpts use the manual preview path.
- Selected history is processed by the current host model. Only approved
  distilled captures are sent to Kumiho; installation alone is not permission
  to import history. Secrets and off-record content are excluded.
- Retiring a memory excludes it from normal recall and is reversible; it is not
  permanent erasure. Corrections preserve provenance as revisions.

## Troubleshooting

Check `hermes plugins list` and `hermes mcp list`. For an expired sign-in,
run `hermes mcp login kumiho-memory` interactively and start a new chat.
A manually configured server with the same name takes precedence over the
package. Keep only one Kumiho integration active to avoid duplicate recalls
and saves. Preserve your active profile and workspace when troubleshooting.

## Development and release

This directory is self-contained: `plugin.json`, `mcp.json`, six skills and the
optional backfill helper. It deliberately has no native `plugin.yaml`, because
a native manifest takes precedence over the portable loader.

```bash
hermes plugins validate ./hermes --install-deps
```

The repository CI runs the official Hermes validator at an immutable Hermes
commit and checks real portable loading, skill discovery and transport
translation. CI does not sign in to a live Kumiho account or write real memories.
A user-authenticated save/recall smoke test remains separate from those checks.

Source protocol: hosted skills at KumihoIO/kumiho-plugins commit
`0b9fce7ef786312f849466b05a3287ac4b986595`. OAuth onboarding and skill discovery
are adapted for Hermes; the backfill helper is unchanged.

[Source](https://github.com/KumihoIO/kumiho-plugins/tree/main/hermes) ·
[Kumiho](https://kumiho.io) · [Documentation](https://docs.kumiho.io)

MIT licensed.
