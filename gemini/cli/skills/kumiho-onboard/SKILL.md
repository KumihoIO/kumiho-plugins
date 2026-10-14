---
name: kumiho-onboard
description: Connect or repair the hosted Kumiho Memory plugin and verify the accessible workspace. Use for first setup, expired login or missing Kumiho tools in Gemini CLI.
---

# Connect Kumiho Memory

This directory plugin uses the hosted MCP endpoint `https://mcp.kumiho.cloud/mcp`
through the host's OAuth connection. It does not install a local server, Python
SDK, hook or background job.

It connects a trial or paid Kumiho Cloud workspace. Start a 14-day free trial at
https://kumiho.io/signup?intent=trial with Google sign-in and no card. Each account
can start once; the trial includes 1 seat, CognitiveMemory only and 2,000 nodes.
After expiration, recall and export remain available for 30 days while new saves
stop. Subscribe to continue using the same workspace. Deletion is announced by
email beforehand. The free self-hosted Community Edition (CE) is not reachable
from this connector; CE uses the separate local plugin described below.

1. Inspect which Kumiho tools the host exposes. If none are available or a tool
   reports an authentication error, connect or reconnect through the host:
   - Gemini CLI: install or enable `kumiho-memory`, start an interactive
     session and run `/mcp auth kumiho-memory`. Inspect `gemini extensions list`
     in the terminal and `/mcp list` inside Gemini. Restart after installation.
     Do not start browser sign-in from an unattended session.

   The host opens the Kumiho sign-in and consent page at `control.kumiho.cloud`,
   where the user continues with Google or email and approves access.
   Authentication belongs on that page; never ask for passwords, tokens or MFA
   codes in chat and never extract credentials from local caches or config files.
2. Call `kumiho_list_projects` to verify account access. Do not switch accounts,
   regions or backends merely to get a passing result. If multiple destinations
   matter, ask the user to choose from the returned projects. A sign-in without a
   Kumiho Cloud workspace can start the trial on the sign-in/consent page or at
   https://kumiho.io/signup?intent=trial. Let the user sign in and accept terms;
   never create the account or accept an agreement silently.
3. Explain that only authorized memories will be stored. Offer a small first-use
   workflow: save one decision and recall it in a fresh conversation, or backfill
   selected past conversations. Installation alone does not authorize history
   access, sample writes, profile creation or bulk import.
4. For an empty workspace, explain what is empty; do not fabricate prior memories.
   Use `kumiho_get_spaces` only when the user needs to choose or confirm a space.
   Create a space only as needed for their authorized save.
5. Verify the intended operation and state what was actually connected. A tool
   discovery success alone does not prove a memory was saved or recalled.

If the user explicitly wants self-hosted CE or a native local integration, explain
that it is a separate backend setup and point them to its instructions: for Claude
Code, https://github.com/KumihoIO/kumiho-plugins/tree/main/claude ; for Codex,
https://github.com/KumihoIO/kumiho-plugins/tree/main/codex . The local plugin and
this one each run their own memory protocol, so keep only one of the two enabled;
with both enabled, every recall and capture would happen twice. Do not silently
convert this OAuth connection into a local installation. User scope takes
precedence.

## Gemini CLI integration

Use the tool names and schemas actually exposed by Gemini CLI; server-qualified
names may differ. Activate the matching bundled skill with `activate_skill`.
Inspect `/skills` and `/mcp list` if discovery fails. Respect Gemini's tool
confirmation and cancellation. Do not add trust, auto-approve rules or a blanket
allow policy. In unattended runs, report operations requiring approval.

This hosted extension does not expose native git Decision Memory tools, install
hooks, scan conversation history or replace Gemini's built-in memory. For a
recurring review, use a scheduler only if this host actually exposes one and the
user requested it; otherwise provide a manual review without promising a job.
