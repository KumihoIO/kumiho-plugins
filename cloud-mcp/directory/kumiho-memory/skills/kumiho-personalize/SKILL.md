---
name: kumiho-personalize
description: View or save explicitly requested communication and memory preferences in the connected Kumiho workspace. Use for preferred language, name, tone, verbosity or remembered behavior.
---

# Kumiho Personalize

In the hosted plugin, personalization is stored as preference memories, available
to clients that recall this workspace. This workflow does not rewrite the native
`agent.instruction` identity document or the host's account settings. State that
scope before a persistent change. Honor conversation-only preferences without a
write. Explicit user instructions override remembered preferences.

1. For show-only requests, use a narrow `kumiho_memory_retrieve` query for the
   requested preference. Inspect a relevant returned item with
   `kumiho_get_revision_by_tag` if the summary is insufficient. Do not create a
   profile when no preference is found.
2. For a change, reuse values explicitly supplied by the user. Do not infer a name,
   profession, language or identity from imported history. Show any conflicting
   existing preference and preserve unrelated settings.
3. Save a concise `preference` capture using `kumiho_memory_reflect`, with a known
   exact preference-space name (otherwise `preferences`), tags
   `["personalization"]`, and `discover_edges: false`. To change a saved
   preference, first find it and set `revises` to its `kref`, keep its language and
   restate the subject, so the change becomes a new revision of that memory.
   Include the relevant old kref in `source_krefs` and say which preference this
   corrects. If you cannot tell which preference to revise, save it normally, and
   retire the old preference with `kumiho_deprecate_item` when the result is a
   separate item. Do not claim the previous item was deleted or rewritten. Do not
   re-save an identical preference.
4. Initially omit `session_id`; retry only on `session_required` using its returned
   ID for this conversation. Never pass another person's identity or session ID.
5. Verify the write by the returned capture and its kref, not by a
   `kumiho_get_revision_by_tag` lookup for `published`: a tagged item may have no
   published revision. Apply the requested preference now. Report only changes
   that saved.

Never store credentials or a preference that authorizes disclosure of someone
else's private data. An instruction to ignore future user corrections is not a
valid enduring preference. Native shared-identity editing requires the separate
local integration; do not claim this workflow changes it.
