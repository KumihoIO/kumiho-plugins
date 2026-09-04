---
name: kumiho-onboard
description: Set up, re-authenticate, repair, or switch the Kumiho Memory backend for Codex. Use when the user invokes $kumiho-onboard, installs Kumiho Memory, has missing memory tools or authentication errors, or wants Kumiho Cloud versus self-hosted CE configuration.
---

# Kumiho Memory Onboarding (Codex)

Configure the native Codex plugin end to end: shared `~/.kumiho/venv` runtime, backend,
authentication, skill ingestion, and verification. This is the Codex equivalent
of Claude's `/kumiho-onboard`, implemented without changing Claude settings.

## Absolute secret boundary

Never ask the user to paste a password, API token, refresh token, private key,
or other credential into chat. Never place one in a command argument,
environment assignment, tool input, memory capture, or response. If a secret
appears in chat, do not repeat or reflect it; tell the user to rotate it.

Cloud login is permitted only in a terminal controlled directly by the user.
The bundled login masks the password and writes Kumiho's private credential
cache. This skill and its setup helper accept no token argument.

## Resolve the installed entrypoint

Derive `PLUGIN_ROOT` from this skill's own path: it is two directories above
the containing `kumiho-onboard` directory (`SKILL_MD.parent.parent.parent`). Use the absolute path to
`PLUGIN_ROOT/scripts/run_kumiho_mcp.mjs`; never assume the repository checkout
or a versioned cache path.

Run the helper only through Node so Python discovery is identical to MCP
startup:

```text
node <absolute-plugin-root>/scripts/run_kumiho_mcp.mjs --onboard ...
```

## Flow

1. Determine the backend from the user's request.
   - `cloud`, `managed`, or `Kumiho Cloud` means Cloud.
   - `ce`, `community`, `self-hosted`, or `local` means CE.
   - If absent, do not ask. Run `--onboard auto --non-interactive`; the helper
     reuses an existing Codex choice, then checks cached Cloud auth, then local
     CE, and otherwise selects Cloud.
2. For Cloud, run:

   ```text
   node <entrypoint> --onboard cloud --non-interactive
   ```

   This completes automatically when the Codex-only credential cache at
   `~/.kumiho/codex-cloud/` is already valid. Claude credentials are deliberately
   not reused because they may belong to a custom control plane. If it reports that
   secure interactive login is required, state the exact command without
   `--non-interactive` as the required next action, without framing it as a
   question. Continue any part of the user's original request that does not
   require authentication. Do not offer to receive credentials in chat.
3. For CE, run non-interactively. The endpoint defaults to
   `127.0.0.1:9190`; pass an explicitly requested endpoint with
   `--ce-endpoint`. Pass `--ce-redis-url` and `--ce-llm-base-url` only when the
   user supplied non-secret URLs. Never pass a URL containing embedded
   credentials. Plaintext `redis://` is loopback-only; remote Redis must use
   `rediss://`.

   ```text
   node <entrypoint> --onboard ce --non-interactive
   ```

4. Relay concise failures without raw environment values, credential-file
   contents, HTTP bodies, or stack traces. The wizard is idempotent; it is safe
   to rerun after fixing Python, network, authentication, or a CE server.
5. On success, tell the user to start a new Codex session. An existing session
   does not refresh its MCP tool catalog after provisioning.

## What the helper may change

- The shared Kumiho Python runtime at `~/.kumiho/venv`.
- The Codex-only Cloud credential directory at `~/.kumiho/codex-cloud/`, but
  only through secure terminal login.
- `~/.kumiho/codex.json`, containing only Codex backend selection and
  non-secret CE endpoints.
- Kumiho graph skill documents during ingestion.

It must not edit Claude Desktop config, Claude settings, Claude hooks, the
Claude plugin directory, Codex's global MCP registration, or project files.
