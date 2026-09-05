---
name: kumiho-personalize
description: Show or update Kumiho identity preferences such as names, language, tone, verbosity, timezone, and memory behaviour. Use for $kumiho-personalize or an explicit request to change remembered agent preferences; not for backend authentication or ordinary facts.
---

# Kumiho Personalize (Codex)

Update the existing identity by merging requested fields into a new revision.
This identity is shared by Kumiho clients, including Claude; it is not a
Codex-only settings file. Mention that scope before a persistent update. If
the user explicitly wants a Codex-only preference, do not change this shared
item: honor it in this conversation and explain the shared scope.

1. Fetch `kumiho_get_revision_by_tag` with
   `item_kref="kref://CognitiveMemory/agent.instruction", tag="published"`.
   Only on not-found, retry
   `kref://CognitiveMemory/personal/agent.instruction`. Keep the item kref that
   resolved for every subsequent write. Auth/connection errors are not
   not-found; report them without creating another identity.
2. Read the revision's metadata. If both items are not-found, follow the
   existing [first-identity flow](../kumiho-memory/references/onboarding.md),
   incorporating any explicitly requested preference, rather than asking the
   user to restart. If only displaying preferences, report absence without
   creating anything.
3. If a change was supplied, use it without another questionnaire. Otherwise
   show current values briefly and ask only what to change. Supported fields:
   `agent_name`, `user_name`, `user_languages`, `communication_tone`,
   `verbosity`, `user_role`, `user_expertise_level`, `primary_tools`,
   `artifact_dir`, `timezone`, `interaction_rules`, `memory_behaviour`.
   Preserve all other metadata, including fields not listed here. Never
   persist credentials or infer unrequested preferences from old transcripts.
4. If values actually changed, call `kumiho_create_revision` with the resolved
   `item_kref` and merged `metadata` (string values, matching the tool schema).
   Use the **returned** `revision.kref` as `revision_kref` in
   `kumiho_tag_revision(tag="published")`.
   Do not guess revision numbers, untag the previous revision, or delete history.
   If publication fails, report the unpublished revision; do not claim success
   or blindly create another revision on retry.
5. Read back the published revision and confirm only the changed fields.
   Apply the preferences now; later sessions load the published identity.
   A show-only request or no-op change performs no write.
