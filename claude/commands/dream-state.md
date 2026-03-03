---
description: Run Dream State memory consolidation — review, enrich, and clean up stored memories
argument-hint: "[--dry-run]"
---

# Dream State

Trigger the Dream State consolidation pipeline. This mirrors biological
sleep-phase memory processing: it replays recent memories, extracts patterns,
enriches metadata, and deprecates low-value or redundant entries — all under
conservative safety guards.

## When to suggest this to the user

- After a heavy burst of activity (many sessions in a short period)
- When memory recall starts returning noisy or redundant results
- Periodically (e.g., end of week) as memory hygiene
- When the user asks to "clean up" or "organize" their memories

## Steps

1. Explain what Dream State does in plain language:

   > "Dream State reviews your stored memories — scoring relevance, flagging
   > duplicates, adding missing tags, and identifying connections between
   > memories. It won't delete anything important: published items are
   > protected, and there's a hard cap on how much can be deprecated in one
   > run."

2. Check if the user passed `--dry-run` as an argument. If so, mention that
   this will be assessment-only — no changes will be applied.

3. Call the consolidation tool:
   ```
   kumiho_memory_dream_state(
     dry_run = <true if --dry-run, false otherwise>
   )
   ```

4. When the run completes, summarize the report for the user:
   - How many memories were assessed
   - How many were deprecated (and why)
   - How many got metadata enrichments or new tags
   - How many new relationships were discovered
   - Duration

   Example:
   > "Dream State reviewed 42 memories. Deprecated 3 duplicates, enriched
   > tags on 7 items, and found 5 new connections between related memories.
   > Everything else was kept as-is."

5. If the circuit breaker triggered (>50% deprecation capped), mention it:
   > "The safety circuit breaker kicked in — the consolidation wanted to
   > deprecate more than 50% of assessed memories, which usually means
   > something's off. I capped it and kept the rest. You may want to review
   > manually."

## Safety reminders

- **Published protection**: Items tagged `published` (like your agent
  instruction) are never touched by Dream State.
- **Circuit breaker**: Max 50% deprecation per run.
- **Audit trail**: Every run produces a full Markdown report stored as a
  revision artifact on the `_dream_state` internal item.
- **Dry run**: Use `--dry-run` to preview what would happen without making
  any changes.
