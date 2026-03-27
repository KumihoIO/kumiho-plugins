---
description: Capture a user fact or preference into Kumiho memory
argument-hint: "<fact or preference>"
---

# Memory Capture

Store a single user fact or preference into Kumiho memory so it can be recalled
in later sessions.

## Steps

1. If no argument was provided, ask the user for the memory text to store.
2. Call `kumiho_memory_engage` with the memory text as query to find
   related existing memories. Hold the returned `source_krefs`.
3. Call `kumiho_memory_reflect` with:
   - `session_id`: current session ID
   - `response`: `"Manual memory capture via /memory-capture"`
   - `captures`: one capture with:
     - `type`: infer from content — `"fact"` for facts, `"decision"`
       for decisions, `"preference"` for preferences
     - `title`: short descriptive title with absolute date
     - `content`: the provided memory text
     - `tags`: `["manual-capture"]`
   - `source_krefs`: krefs from step 2 (if any were relevant)
4. Confirm what was stored and how many edges were created.

## Guardrails

- Do not store secrets (passwords, private keys, API secrets).
- If the user asks to store sensitive data, ask for confirmation first.
