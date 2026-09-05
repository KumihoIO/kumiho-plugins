---
name: dream-state
description: Run or preview Kumiho Dream State to assess stored memories, enrich metadata, identify relationships, and deprecate redundant entries. Use for $dream-state or an explicit memory-cleanup request; not for ordinary recall or keyless session summarization.
---

# Dream State (Codex)

Use the existing `kumiho_memory_dream_state` MCP tool on Codex's configured
backend. Do not introduce a Python command, change CE/Cloud, or implement
another maintenance engine.

1. Explain briefly that a full cycle can enrich/tag memories and deprecate
   redundant entries. Assessment can use the backend's configured LLM even
   in dry-run mode; this is **not** the keyless
   `kumiho_memory_consolidate(summary=...)` session-summary operation.
   Local CE routing does not by itself imply a local assessment model.
2. For `--dry-run`, a preview request, or an ambiguous cleanup request, call:

   ```json
   {"dry_run": true, "allow_published_deprecation": false, "max_deprecation_ratio": 0.5}
   ```

   Pass `project` only when the user supplied a project scope. Never fan out
   across every project. If the user explicitly requested an applied run,
   use `dry_run: false` without an extra confirmation loop. Adding or testing
   this skill is not permission to run maintenance on real memories.
3. Keep published-item protection enabled and the deprecation cap at 0.5
   or a stricter requested value. Do not turn on `maintenance_llm`, change
   providers, pass an API key, or raise the cap to get past an error. Reuse
   existing SDK-managed configuration; no credentials in chat or arguments.
4. Summarize the returned assessed/deprecated/enriched/linked counts, duration,
   warnings, circuit-breaker status, and report kref **when present**. Do not
   invent counts, equate deprecation with permanent deletion, or promise that
   nothing important can ever change. Distinguish preview from applied work.
5. If the configured assessment model or memory backend is unavailable,
   report the actual limitation and stop that run. Do not substitute another
   backend or silently repeat a potentially costly cycle. A user who only
   wants a session summary can instead request keyless consolidation.

Do not schedule this automatically. If the user explicitly requests recurring
maintenance, use Codex's supported automation workflow with their requested
scope; do not create OS cron/Task Scheduler jobs or embed credentials.
