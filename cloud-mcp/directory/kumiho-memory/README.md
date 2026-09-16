# Kumiho Memory directory skill bundle

This bundle supplies six focused skills for the hosted Kumiho Memory MCP app:

- `kumiho-memory` recalls and preserves approved facts, decisions and context.
- `memory-capture` saves one explicitly approved capture.
- `kumiho-onboard` connects and checks the OAuth workspace.
- `kumiho-personalize` stores approved preference memories.
- `dream-state` previews bounded cleanup and can use the host app scheduler for a
  user-requested recurring review.
- `kumiho-backfill` stages selected Codex, Claude or ChatGPT history locally,
  previews distilled captures, and stores only approved results.

The skills use the same remote MCP dependency at `https://mcp.kumiho.cloud/mcp`.
The host application's model performs distillation and Dream State assessment;
the app's own automation feature performs requested recurring runs. The bundle
does not require an external LLM API key, server cron, SDK installation or a
second model service.

The backfill helper is intentionally local and stdlib-only. It prepares packets
and reviewed payloads but never authenticates, calls the network or ingests data.
MCP writes happen only after the user approves the exact staged captures.
