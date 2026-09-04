# Automatic First Identity Onboarding

Use this only after both identity krefs in [bootstrap.md](bootstrap.md) returned
not-found. Authentication and connection failures never trigger onboarding.

Explain briefly that Kumiho stores only selected summaries and explicit
captures from this protocol, never credentials, and that revision history lets
the user inspect, update, or deprecate memories.

Do not ask onboarding questions, stop, or wait for an answer. Never ask for or
persist secrets. Build the initial identity metadata automatically:

- Infer the response language from the user's current message and conversation.
- Use `Kumiho` as the agent name.
- Use `balanced` for both tone and verbosity.
- Use `~/.kumiho/artifacts/` as the artifact directory.
- Auto-detect timezone and infer primary tools over time.
- Include a preferred user name, role, expertise, or behavior rule only when it
  is already explicit in the conversation or other trusted context; otherwise
  leave that optional field unset.

These are initial defaults, not permanent guesses. A later explicit user
preference creates a new revision and moves the `published` tag.

## Persist without interrupting the request

All steps must succeed:

1. Call `kumiho_list_projects`; create `CognitiveMemory` if missing.
2. Call `kumiho_get_spaces(project_name="CognitiveMemory")`; create `personal`
   if missing.
3. Call `kumiho_create_item(space_path="CognitiveMemory/personal",
   item_name="agent", kind="instruction")`. An already-exists response is safe
   after a partial onboarding; continue.
4. Call `kumiho_create_revision` for
   `kref://CognitiveMemory/personal/agent.instruction` with the collected
   metadata. Capture the returned revision kref; never assume `r=1`.
5. Call `kumiho_tag_revision` on that returned revision with tag `published`.

After persistence, continue the user's original request in the same turn. Do
not require a separate welcome or acknowledgement step. If persistence fails,
report only an actionable auth or connection issue when relevant and continue
the original request without memory. Preference updates create a new revision
and move the `published` tag; never delete the old revision.
