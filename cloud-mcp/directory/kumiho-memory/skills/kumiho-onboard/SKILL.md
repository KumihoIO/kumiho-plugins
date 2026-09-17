---
name: kumiho-onboard
description: Connect or repair the hosted Kumiho Memory plugin and verify the accessible workspace. Use for first setup, expired login or missing Kumiho tools in Claude Code, Cowork, claude.ai, ChatGPT or Codex.
---

# Connect Kumiho Memory

This directory plugin uses the hosted MCP endpoint `https://mcp.kumiho.cloud/mcp`
through the host's OAuth connection. It does not install a local server, Python
SDK, hook or background job.

It requires a paid Kumiho Cloud workspace. The free self-hosted Community Edition
(CE) is not reachable from this connector; CE is served by the separate local
Kumiho plugin described at the end of this workflow.

1. Inspect which Kumiho tools the host exposes. If none are available or a tool
   reports an authentication error, connect or reconnect through the host:
   - Claude Code: run `/mcp`, select the Kumiho Memory server
     (`plugin:kumiho-memory-cloud:kumiho-memory`) and authenticate, or
     re-authenticate an expired login. If it is not listed, check with `/plugin`
     that the plugin is installed and enabled.
   - Cowork and claude.ai: open Customize > Connectors (Settings > Connectors in
     some versions), select Kumiho Memory and connect it, or reconnect an expired
     login.
   - ChatGPT and Codex: use the plugin's Connect/Reconnect flow.

   The host opens the Kumiho sign-in and consent page at `control.kumiho.cloud`,
   where the user continues with Google or email and approves access.
   Authentication belongs on that page; never ask for passwords, tokens or MFA
   codes in chat and never extract credentials from local caches or config files.
2. Call `kumiho_list_projects` to verify account access. Do not switch accounts,
   regions or backends merely to get a passing result. If multiple destinations
   matter, ask the user to choose from the returned projects. A sign-in without a
   Kumiho Cloud workspace cannot be fixed from chat; say so plainly.
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
