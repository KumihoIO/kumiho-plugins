---
name: memory-capture
description: Explicitly save one user-provided fact, preference, or decision to Kumiho. Use for $memory-capture or a request to remember a specific piece of information; not for bulk history import or changing identity settings.
---

# Memory Capture (Codex)

1. Use the supplied text; ask for it only when absent. Never repeat or store
   credentials, even if asked. Honor off-record requests. For sensitive
   personal or third-party information, confirm the intended storage scope
   unless already explicitly authorized.
2. Reuse this turn's engage results, or make one targeted
   `kumiho_memory_engage(query=<text>, limit=3, recall_mode="summarized")`.
   Keep only relevant source krefs; do not retrieve a whole space.
3. Call `kumiho_memory_reflect` once with:
   - `response`: a brief description of the requested manual capture.
   - `captures`: one object containing `type` (`fact`, `preference`, or
     `decision`), a short dated `title`, faithful `content`,
     `tags: ["manual-capture"]`, and `space_hint`.
   - `source_krefs`: the relevant krefs from step 2.
   Copy a relevant space's exact casing; otherwise use `facts`, `preferences`,
   or `decisions`. Distinguish the capture date from the event date: supply
   `event_date` only when known. Omit `session_id`; Codex supplies its thread
   identity. Never borrow a Claude SessionStart id or invent one.
4. Check the result for errors and a stored kref, not merely a successful tool
   envelope. Confirm what was saved and include the returned kref. Report
   only edge counts the result actually provides. Do not reflect the same
   content again merely to record that this command ran.

If memory is unavailable, report that nothing was saved and use
`$kumiho-onboard`; do not switch the backend or handle tokens yourself.
