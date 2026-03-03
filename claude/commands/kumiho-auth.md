---
description: Set up Kumiho authentication for memory tools
argument-hint: "[token]"
---

# Kumiho Auth Setup

Authenticate the kumiho-memory plugin so that memory tools can connect to
the Kumiho Cloud backend.

## Prerequisites

A **Kumiho Cloud account** is required. If the user says they don't have
one, direct them to sign up at **[kumiho.io](https://kumiho.io)** (free tier available).
Once signed up, they can mint an API token from the dashboard under
**API Keys**, then come back and run `/kumiho-auth` again.

## Steps

### 1. Choose authentication method

If the user already provided a token as the command argument (`$ARGUMENTS`),
skip straight to **Method A -- step 2** (cache the token).

Otherwise, use the `AskUserQuestion` tool to ask the user which method they
prefer. Present exactly these two options:

- **A -- Dashboard API token**: Paste a long-lived JWT from the Kumiho Cloud
  dashboard. No interactive login needed.
- **B -- CLI login**: Sign in with email and password via `kumiho-cli login`.

### 2. Execute the chosen method

#### Method A -- Dashboard API token

1. If no token was provided as an argument, ask the user to paste their
   **Kumiho API token** using the `AskUserQuestion` tool. Remind them:
   - The token looks like `eyJ...` (three dot-separated base64url parts).
   - They can find it in the Kumiho Cloud dashboard under **API Tokens**.
   - Both raw JWT and `Bearer <jwt>` formats are accepted.

2. Extract the token string. Strip any surrounding quotes, whitespace,
   or a leading `Bearer` prefix before passing it to the cache script.

3. Store the token. Run each of the following steps **exactly once** --
   never re-run a step, even if it fails. Replace `<CLEANED_TOKEN>` with
   the extracted JWT -- **do not** echo it back in visible output.

   **Step 3a** -- Write the credential cache (long-term storage):

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/cache_auth_token.py" --token "<CLEANED_TOKEN>"
   ```

   If `CLAUDE_PLUGIN_ROOT` is not set, fall back to a path relative to
   the plugin directory.

   **Step 3b** -- Write `.env.local` at the plugin root (picked up by the
   MCP server on next restart):

   ```bash
   printf 'KUMIHO_AUTH_TOKEN=%s\n' '<CLEANED_TOKEN>' > "${CLAUDE_PLUGIN_ROOT}/.env.local"
   ```

   If `CLAUDE_PLUGIN_ROOT` is not set, skip this step.
   If the filesystem is read-only, note the failure silently and move on.

   **Step 3c** -- Patch `.mcp.json` to trigger a Claude Desktop server
   restart. The MCP server process persists across chat sessions; writing
   the token into `.mcp.json` causes the Claude app to detect the config
   change and restart the server automatically.

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/patch_mcp_json_token.py" --token "<CLEANED_TOKEN>"
   ```

   If `CLAUDE_PLUGIN_ROOT` is not set, fall back to a path relative to
   the plugin directory. If the filesystem is read-only, note the failure
   silently and move on.

   **IMPORTANT**: Each step runs once. Do NOT retry any step. Do NOT
   re-run a step "separately" if a different step failed.

4. **Report results concisely** -- one short summary, not a step-by-step
   narration. Examples:

   - All succeeded (Claude Code): "Token stored. Start a new session
     and memory will connect automatically."
   - Cache succeeded but file writes failed (Claude Desktop / Cowork):
     "Token stored. Quit and relaunch the app for memory to connect."
   - Cache failed: relay the error and ask the user to double-check their
     token.

   Do NOT narrate individual step outcomes ("The .env.local write failed
   due to..."). Just give the user the actionable summary.

#### Method B -- CLI login

1. Tell the user to run the following in their own terminal (outside Claude):

   ```bash
   kumiho-cli login
   ```

   This will prompt for email and password and create
   `~/.kumiho/kumiho_authentication.json` with `id_token` and
   `control_plane_token`.

2. Let them know:
   - "Once login completes, restart this session and memory tools will
     authenticate automatically."
   - "Session tokens expire -- you may need to re-run `kumiho-cli login`
     periodically."

## Guardrails

- **Never** log or echo the full token in user-visible output.
- If the user provides the token as a command argument, still prefer
  passing it via `--token` rather than piping through stdin, to avoid
  shell-quoting issues on Windows.
- Never overwrite session tokens (`id_token`, `control_plane_token`)
  when caching a dashboard API token -- the script handles this
  automatically by storing under a separate `api_token` key.
