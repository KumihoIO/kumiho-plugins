---
name: kumiho-onboard
description: Connect or repair the hosted Kumiho Memory plugin and verify the accessible workspace. Use for first setup, expired login or missing Kumiho tools in ChatGPT or Codex.
---

# Connect Kumiho Memory

This directory plugin uses the hosted MCP endpoint through the host's OAuth
connection. It does not install a local server, Python SDK, hook or background job.

1. Inspect which Kumiho tools the host exposes. If the plugin is disconnected or
   reports an authentication error, use its Connect/Reconnect flow. Authentication
   belongs on the official Kumiho sign-in page; never ask for passwords, tokens or
   MFA codes in chat and never extract credentials from local caches.
2. Call `kumiho_list_projects` to verify account access. Do not switch accounts,
   regions or backends merely to get a passing result. If multiple destinations
   matter, ask the user to choose from the returned projects.
3. Explain that only authorized memories will be stored. Offer a small first-use
   workflow: save one decision and recall it in a fresh conversation, or backfill
   selected past conversations. Installation alone does not authorize history
   access, sample writes, profile creation or bulk import.
4. For an empty workspace, explain what is empty; do not fabricate prior memories.
   Use `kumiho_get_spaces` only when the user needs to choose a space. Create a
   space only as needed for their authorized save.
5. Verify the intended operation and state what was actually connected. A tool
   discovery success alone does not prove a memory was saved or recalled.

If the user explicitly wants self-hosted CE or the native local Codex integration,
explain that it is a separate backend setup and refer to
https://github.com/KumihoIO/kumiho-plugins/tree/main/codex . Do not silently convert
this OAuth connection into a local installation. User scope takes precedence.
