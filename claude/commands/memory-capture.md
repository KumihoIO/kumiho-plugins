---
description: Capture a user fact or preference into Kumiho memory
argument-hint: "<fact or preference>"
---

# Memory Capture

Store a single user fact or preference into Kumiho memory so it can be recalled
in later sessions.

## Steps

1. If no argument was provided, ask the user for the memory text to store.
2. Call `kumiho_memory_recall` with the memory text as query to find
   related existing memories. Collect any returned `kref` values with
   score > 0.5.
3. Call `kumiho_memory_store` with:
   - `user_text`: the provided memory text
   - `assistant_text`: `"Manual memory capture via /memory-capture"`
   - `memory_type`: infer from content — `"fact"` for facts, `"decision"`
     for decisions, `"summary"` for preferences
   - `source_revision_krefs`: krefs from step 2 (if any were relevant)
   - `space_hint`: `"manual-capture"`
4. Call `kumiho_memory_discover_edges` with:
   - `revision_kref`: the `revision_kref` from step 3's result
   - `summary`: the provided memory text
5. Confirm what was stored, how many edges were created, and mention it
   can be recalled with `kumiho_memory_recall`.

## Guardrails

- Do not store secrets (passwords, private keys, API secrets).
- If the user asks to store sensitive data, ask for confirmation first.
