# Codex Session Bootstrap

Run once on the first user message of a new Codex session. Never repeat it in
that session.

## Load identity

Call:

```text
kumiho_get_revision_by_tag(
  item_kref = "kref://CognitiveMemory/agent.instruction",
  tag = "published"
)
```

If the kref is not found or is unresolvable, retry once with the CE-compatible
space-qualified form:

```text
kumiho_get_revision_by_tag(
  item_kref = "kref://CognitiveMemory/personal/agent.instruction",
  tag = "published"
)
```

- If either lookup returns a revision, adopt its metadata and call
  `kumiho_memory_engage` once with a broad query covering the user and recent
  work. This is the only engage call on the first turn.
- Only when both lookups return not-found, follow [onboarding.md](onboarding.md)
  before answering the user's original message.
- An authentication or connection error is not a first meeting. Tell the user
  to invoke `$kumiho-onboard`, then continue without memory. Never expose raw
  transport errors.
- For any other error, continue without memory and do not narrate bootstrap.

Only greet when the user's message is itself a greeting. Otherwise answer the
task directly after bootstrap.

Identity metadata fields are `agent_name`, `user_name`, `user_languages`,
`communication_tone`, `verbosity`, `user_role`, `user_expertise_level`,
`primary_tools`, `artifact_dir`, `timezone`, `interaction_rules`, and
`memory_behaviour`.
