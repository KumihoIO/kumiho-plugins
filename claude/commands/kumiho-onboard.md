---
description: Run the Kumiho Memory onboarding wizard — venv, auth, MCP config, skill ingestion
argument-hint: "<token>"
---

# Kumiho Onboarding Wizard

Run the onboarding wizard that configures the kumiho-memory plugin end-to-end:
Python venv, authentication, MCP server config, and skill ingestion into the
graph.

## Steps

1. **Collect the token** — The wizard needs an API token to authenticate.

   - If the user supplied a token as the command argument (e.g. `/kumiho-onboard eyJ...`),
     use that token directly. **Never echo the token back to the user.**
   - If no argument was provided, ask the user:
     > Paste your Kumiho API token (from kumiho.io > Dashboard > API Keys).
     Wait for their reply before proceeding.

2. **Run the wizard non-interactively** with `--token` and `--yes`:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --token "<TOKEN>" --yes
   ```

   If `CLAUDE_PLUGIN_ROOT` is not set, fall back to the plugin directory
   relative to this file (e.g. the `claude/` directory containing `scripts/`).

   The `--token` flag skips interactive auth prompts. The `--yes` flag
   auto-confirms all yes/no prompts (venv creation, skill ingestion, etc.).

3. The wizard handles five steps automatically:
   - **Python venv** — creates or reuses `~/.kumiho/venv` with `kumiho[mcp]`
     and `kumiho-memory[all]`
   - **Authentication** — validates and caches the token
   - **MCP config** — patches `.mcp.json` with auth token so the MCP
     server restarts with credentials
   - **Skill ingestion** — populates `CognitiveMemory/Skills` in the graph
     from SKILL.md and reference docs
   - **Verification** — tests the MCP server connection

4. After the wizard completes, report the outcome concisely:
   - If setup succeeded: "Onboarding complete. Start a new session — memory
     connects on first message."
   - If auth was skipped: "Onboarding complete but unauthenticated. Re-run
     `/kumiho-onboard` when you have a token."
   - If the script failed: relay the error and suggest running it manually
     from a terminal: `python scripts/setup.py`

## Guardrails

- **Never** echo auth tokens in user-visible output.
- The wizard is designed to be re-runnable (idempotent) — re-running it
  upgrades packages, re-authenticates if requested, and re-ingests skills
  (stacking revisions, not duplicating).
- If the user just needs to re-authenticate, `/kumiho-onboard` handles it —
  the wizard validates and caches the new token without repeating other steps.
