# Gemini integrations

- [Gemini web/mobile connector and current registration limits](CONSUMER-APP.md)
- [Gemini CLI extension](cli/README.md)

Both use Kumiho's existing hosted OAuth MCP. A custom connection, a published
CLI extension and a public consumer catalog listing are separate outcomes.

## Release status on 2026-10-14

Gemini CLI 0.1.0 is [published by Kumiho Inc.](https://github.com/KumihoIO/gemini-kumiho-memory/releases/tag/v0.1.0).
The public repository has the root manifest and `gemini-cli-extension` topic
required for Google's daily gallery discovery. A visible gallery listing is
not yet confirmed. The source [PR #137](https://github.com/KumihoIO/kumiho-plugins/pull/137)
is separate and requires reviewer approval; the standalone distribution is
already published from the tested upstream CLI subtree.

The exact public install URL passed install, all-six-skill discovery and
uninstall on Windows with Node 22.23.3 and Gemini CLI 0.64.0. Linux Node 22
official-loader CI also passed. Windows Node 24 emitted a native shutdown
assertion after reporting successful uninstall; use Node 22 LTS for Windows
until that upstream runtime issue is resolved. Authenticated memory save/recall
remains unverified.

Consumer web/mobile catalog registration remains **not submitted**.
The company Workspace account has no custom-app add control; the company Gmail
account inspected also showed no custom-app add field. See the eligibility
and intake limitations in [the consumer dossier](CONSUMER-APP.md).
