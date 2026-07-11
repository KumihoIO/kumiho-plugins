---
description: Run the Kumiho Memory onboarding wizard — venv, auth, MCP config, skill ingestion
argument-hint: "<token> | ce"
---

# Kumiho Onboarding Wizard

Run the onboarding wizard that configures the kumiho-memory plugin end-to-end:
Python venv, backend selection, MCP server config, and skill ingestion into
the graph.

## Steps

1. **Pick the backend.** The plugin can use either Kumiho Cloud (managed,
   API-token) or a self-hosted `kumiho-server` Community Edition (CE, no token).

   - If the argument looks like a JWT (`eyJ...`, three dot-separated parts),
     treat it as a **Cloud** token — skip the question.
   - If the argument is `ce` (or `self-hosted` / `community`), go straight to
     the **CE** path.
   - Otherwise ask:
     > Which backend? **1) Kumiho Cloud** (API token) or **2) Self-hosted CE**
     > (local kumiho-server, no token)?

     Wait for their reply before proceeding.

2. **Cloud path** — collect the token, then run the wizard non-interactively:

   - If a token was supplied as the argument, use it directly.
     **Never echo the token back to the user.**
   - Otherwise ask: *"Paste your Kumiho API token (kumiho.io > Dashboard >
     API Keys)."* and wait.

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --token "<TOKEN>" --yes
   ```

3. **CE path** — no token is needed. Run:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --ce --yes
   ```

   If the user runs CE on a non-default endpoint, pass it through:
   `--ce-endpoint HOST:PORT` (default `127.0.0.1:9190`). Optional:
   `--ce-redis-url URL`, `--ce-llm-base-url URL` for a local LLM.

   The CE server must be running first — see
   [kumiho-server-community](https://github.com/KumihoIO/kumiho-server-community).

   In both paths, if `CLAUDE_PLUGIN_ROOT` is not set, fall back to the plugin
   directory relative to this file (the `claude/` directory containing
   `scripts/`). `--yes` auto-confirms all yes/no prompts.

4. The wizard handles five steps automatically:
   - **Python venv** — creates or reuses `~/.kumiho/venv` with `kumiho[mcp]`
     and `kumiho-memory[all]`
   - **Backend** — Cloud: validates and caches the token. CE: writes
     `KUMIHO_CLAUDE_MODE=ce` (+ endpoint) and probes the local server.
   - **MCP config** — writes credentials/CE config to OS env, Claude Desktop
     config, and `.env.local` so the MCP server restarts configured
   - **Skill ingestion** — populates `CognitiveMemory/Skills` in the graph
     from SKILL.md and reference docs
   - **Verification** — Cloud: control-plane discovery. CE: `/api/_live` probe.

5. After the wizard completes, report the outcome concisely:
   - If setup succeeded (Cloud): "Onboarding complete. Start a new session —
     memory connects on first message."
   - If setup succeeded (CE): "CE onboarding complete. Ensure your
     kumiho-server CE is running, then start a new session."
   - If auth was skipped: "Onboarding complete but unauthenticated. Re-run
     `/kumiho-onboard` when you have a token."
   - If the script failed: relay the error and suggest running it manually
     from a terminal: `python scripts/setup.py`

## Guardrails

- **Never** echo auth tokens in user-visible output.
- The wizard is designed to be re-runnable (idempotent) — re-running it
  upgrades packages, re-authenticates / re-writes CE config, and re-ingests
  skills (stacking revisions, not duplicating).
- If the user just needs to re-authenticate, `/kumiho-onboard` handles it —
  the wizard validates and caches the new token without repeating other steps.
