---
description: Run the Kumiho Memory onboarding wizard — venv, auth, MCP config, skill ingestion
argument-hint: ""
---

# Kumiho Onboarding Wizard

Run the interactive onboarding wizard that configures the kumiho-memory plugin
end-to-end: Python venv, authentication, MCP server config, and skill
ingestion into the graph.

## Steps

1. Run the onboarding wizard:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py"
   ```

   If `CLAUDE_PLUGIN_ROOT` is not set, fall back to the plugin directory
   relative to this file (e.g. the `claude/` directory containing `scripts/`).

   The wizard is **interactive** — it prompts for user input at each step.
   Run it with the Bash tool and let the user interact directly.

2. The wizard handles five steps automatically:
   - **Python venv** — creates or reuses `~/.kumiho/venv` with `kumiho[mcp]`
     and `kumiho-memory[all]`
   - **Authentication** — offers token paste, CLI login, or skip
   - **MCP config** — patches `.mcp.json` with the auth token so the MCP
     server restarts with credentials
   - **Skill ingestion** — populates `CognitiveMemory/Skills` in the graph
     from SKILL.md and reference docs
   - **Verification** — tests the MCP server connection

3. After the wizard completes, report the outcome concisely:
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
  the wizard detects existing auth and offers to re-authenticate without
  repeating other steps.
