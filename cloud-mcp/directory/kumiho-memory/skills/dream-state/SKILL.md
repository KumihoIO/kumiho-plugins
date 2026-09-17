---
name: dream-state
description: Review Kumiho memories with the current app model, apply authorized corrections, and schedule bounded maintenance through the app's own scheduler when requested. Use for Dream State or memory-cleanup requests.
---

# Dream State — hosted memory review

Use the current app model for assessment, MCP tools for data operations and the
app's own scheduler for requested recurring runs. No external LLM API key, separate
model service or operating-system cron is needed. Runs consume the host app's
normal usage. This workflow does not call the native server-side Dream State
engine, retag identity documents or merge graph nodes. Report the operations
actually performed instead of claiming the native engine ran.

## Preview

1. Establish the project/topic/space the user wants reviewed. An ambiguous
   cleanup request means preview. Do not fan out across all projects.
2. Use `kumiho_memory_retrieve` for at most five relevant items initially, with the
   specified project and space filters. Inspect returned published revisions with
   `kumiho_get_revision_by_tag` before judging duplicates or contradictions.
3. Report candidate pairs, their krefs, evidence and uncertainty. Age alone is not
   a reason to retire a memory; distinguish proposals, accepted decisions and
   later corrections. Keep useful differences and provenance visible.
4. Preview performs reads only. Do not call space profiling merely to preview: that
   tool can persist results. No consolidation, decomposition, tagging or retirement
   is authorized by a preview request.

## Apply approved changes

Show the exact changes and obtain authorization for each affected item unless the
user already supplied those exact changes. Native published-item safeguards are
not automatically supplied by this manual path, so do not use a broad "clean up"
request to authorize bulk retirement. Never retire all copies of information.

- Use `kumiho_memory_reflect` for a user-approved correction with relevant
  `source_krefs`, the corrected memory's `space` as `space_hint`, its memory type
  and language, the subject restated, no `tags`, and `discover_edges: false`, so it
  becomes a new revision of that memory. If the returned kref ends in `?r=1` or
  names a different item, the old memory is still recalled until its approved
  retirement. Omit session ID initially and follow a returned `session_required`
  ID for this conversation only.
- Use `kumiho_deprecate_item` only for the specifically approved item kref and
  report the result. Retirement excludes it from normal recall; it is not permanent
  erasure. Do not clear chat buffers or run unexposed tools as a substitute.

Stop after the bounded reviewed set. Report reviewed/proposed/applied/failed counts
separately and only from observed results. User instructions take precedence;
installation or testing does not authorize maintenance of real memories.

## Recurring maintenance in the app

When the user asks for recurring maintenance, use the host's supported task or
automation tool after checking its current schema. Reuse an existing matching task
when possible. Do not create an OS scheduled task, server cron, subprocess daemon
or external model subscription. A request to add this skill is not a request to
create an actual schedule.

Put a self-contained instruction in the scheduled task: the connected Kumiho
plugin, exact project/space/topic, requested timezone/cadence, at most five items
per run unless otherwise authorized, review steps above, and allowed mutations.
Default to read-only review and proposed changes. If the user authorizes automatic
changes, encode the precise policy and bounds; do not re-ask on each run for the
same authorized operations, but stop for ambiguous targets or operations outside
that policy. An automatic retirement policy must identify the evidence required
and which retained item preserves the information. "Clean up everything" alone
does not establish that policy.

Keep credentials and transcript content out of the schedule prompt. Connect through
the app's existing OAuth integration. Suppress routine notifications when nothing
actionable changed; report actual changes, failures or required user decisions,
unless the user requests regular reports. Verify the scheduler returned an active
task before saying it is scheduled. If this host or plan lacks scheduling or cannot
call the plugin from scheduled runs, say so; do not claim a task will run elsewhere.
