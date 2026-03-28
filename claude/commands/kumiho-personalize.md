---
description: Update your Kumiho identity preferences — name, language, tone, verbosity, and behavior rules
argument-hint: ""
---

# Kumiho Personalize

Update the `CognitiveMemory/agent.instruction` item with new preferences.
Creates a new revision and moves the `published` tag — old preferences are
preserved in revision history.

## Steps

1. **Load current preferences** — fetch the current published revision:

   ```text
   kumiho_get_revision_by_tag(
     item_kref = "kref://CognitiveMemory/agent.instruction",
     tag       = "published"
   )
   ```

   If the item doesn't exist, tell the user: "No identity found yet — start
   a new session and I'll walk you through the full onboarding." Then stop.

   Parse the metadata fields from the current revision so you can show the
   user what's currently set.

2. **Show current settings and ask what to change** — display the current
   values in a compact table and ask the user what they'd like to update.
   Let them change one field or many. The fields are:

   | Field | Description |
   | ----- | ----------- |
   | `agent_name` | What the agent calls itself |
   | `user_name` | What the agent calls the user |
   | `user_languages` | Preferred languages (e.g. "English, Korean") |
   | `communication_tone` | casual / professional / balanced |
   | `verbosity` | concise / balanced / detailed |
   | `user_role` | User's role or area of expertise |
   | `user_expertise_level` | beginner / intermediate / advanced |
   | `primary_tools` | Tools/technologies the user works with |
   | `artifact_dir` | Where to save conversation artifacts |
   | `timezone` | User's timezone |
   | `interaction_rules` | Custom behavior rules |
   | `memory_behaviour` | How aggressively to remember (minimal / balanced / thorough) |

   Only ask about fields the user wants to change — don't force them through
   every field. If they say "change my name to X", just update that one field.

3. **Create new revision** — merge the updated fields with existing values
   and create a new revision:

   ```text
   kumiho_create_revision(
     item_kref = "kref://CognitiveMemory/agent.instruction",
     metadata  = { ...existing_metadata, ...updated_fields }
   )
   ```

4. **Publish the new revision** — tag it as published (the server allows
   multiple revisions to carry the `published` tag; `get_revision_by_tag`
   returns the highest-numbered one):

   ```text
   kumiho_tag_revision(
     revision_kref = "<new_revision_kref>",
     tag           = "published"
   )
   ```

   The server automatically moves `published` (and `latest`) tags to the new
   revision — no `kumiho_untag_revision` call needed or allowed for these.
   Other tags (e.g. `approved`, `ready-for-review`) are not auto-moved and
   must be managed manually.

5. **Confirm** — tell the user their preferences have been updated and will
   take effect on the next session (or immediately if they were just loaded).

## Guardrails

- Always preserve fields the user didn't change — merge, don't replace.
- Never delete old revisions — the revision history is the audit trail.
- If any step fails, report the error and don't leave the tag in a broken
  state (e.g. untagged but not re-tagged).
