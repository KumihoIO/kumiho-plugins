# Kumiho Memory directory plugin

This bundle packages the hosted Kumiho Memory MCP connector
(`https://mcp.kumiho.cloud/mcp`) with six focused skills. One `skills/` directory
is shared by two manifests:

| Manifest | Hosts |
|---|---|
| `.claude-plugin/plugin.json` + `.mcp.json` | Claude Code and Cowork |
| `.codex-plugin/plugin.json` + `skills/*/agents/openai.yaml` | ChatGPT and Codex |

## Requirements

- **A paid Kumiho Cloud workspace.** The connector serves Kumiho Cloud workspaces
  only. Signing in happens on Kumiho's own OAuth page at `control.kumiho.cloud`
  (continue with Google or email); the plugin never asks for a password or token.
- **Self-hosted Community Edition (CE) is not reachable from this plugin.** CE is
  served by the separate local plugins in this repository:
  [`claude/`](https://github.com/KumihoIO/kumiho-plugins/tree/main/claude) for
  Claude Code (`kumiho-memory@kumiho-plugins`) and
  [`codex/`](https://github.com/KumihoIO/kumiho-plugins/tree/main/codex) for Codex.
  The local Claude Code plugin has the same name, `kumiho-memory`; keep only one
  of the two enabled.
- No local server, Python SDK, hook, background job or extra model API key.

## Skills

- `kumiho-memory` recalls and preserves approved facts, decisions and context.
- `memory-capture` saves one explicitly approved capture.
- `kumiho-onboard` connects or reconnects the OAuth workspace and verifies access.
- `kumiho-personalize` stores approved preference memories.
- `dream-state` previews bounded cleanup and can use the host app's scheduler for a
  user-requested recurring review.
- `kumiho-backfill` stages selected Claude Code, Codex or ChatGPT history locally,
  previews distilled captures, and stores only approved results.

## Install in Claude

### Claude Code

1. Run `/plugin`, find **Kumiho Memory** in Anthropic's official marketplace
   (`claude-plugins-official`) and install it, or run
   `claude plugin install kumiho-memory@claude-plugins-official`.
2. Run `/mcp`, select `plugin:kumiho-memory:kumiho-memory` and authenticate. Your
   browser opens the Kumiho sign-in and consent page. Use the same menu to
   re-authenticate after a login expires.
3. Run `/kumiho-memory:kumiho-onboard` to verify the workspace, or ask Claude to
   list your Kumiho projects.

To load this directory from a local checkout for one session instead:

```bash
claude --plugin-dir cloud-mcp/directory/kumiho-memory
```

### Cowork

Install **Kumiho Memory** from the Claude plugin directory, then connect the
Kumiho Memory connector when prompted or from Customize > Connectors. The
connector requires the same Kumiho Cloud sign-in.

## Install in ChatGPT and Codex

Add the Kumiho Memory plugin in the host and use its Connect flow. The
`agents/openai.yaml` file in each skill declares the same remote MCP dependency
for these hosts.

## How it works

The host application's model performs distillation and Dream State assessment;
the host's own automation feature performs requested recurring runs. The bundle
does not require an external LLM API key, server cron, SDK installation or a
second model service.

The backfill helper is intentionally local and stdlib-only. It prepares packets
and reviewed payloads but never authenticates, calls the network or ingests data.
Selected history is read by the current host's model; MCP writes happen only after
the user approves the exact staged captures, and only those distilled captures are
sent to the Kumiho workspace.

Memories are stored in your Kumiho Cloud workspace on Kumiho's servers, not in
the host application. Asking to forget a memory retires it from recall; it is not
permanent erasure.

## Privacy Policy

Kumiho's privacy policy, which covers data stored through this connector, is at
https://kumiho.io/en/legal#privacy.

## Support

- Documentation: https://kumiho.io/docs/connect/claude
- Contact: support@kumiho.io
- Issues: https://github.com/KumihoIO/kumiho-plugins/issues
