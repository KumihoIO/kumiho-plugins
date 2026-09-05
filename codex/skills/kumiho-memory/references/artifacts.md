# Artifacts, Execution Results, and Session Close (Codex)

Preserve useful results, not a duplicate log of every tool call. Existing code
and documents stay at their workspace paths; do not copy a repository into
the memory artifact directory. Use identity `artifact_dir` only for new
standalone memory artifacts. Never overwrite a user file to create a receipt.

For a significant retained output, reuse an existing graph item when updating
it. `kumiho_create_artifact(revision_kref=..., name=..., location=<absolute-path>)`
attaches a pointer, not file bytes. Store a short sanitized description and
link relevant recalled decisions with `DERIVED_FROM`. See
[creative-memory.md](creative-memory.md) for deliverable tracking.

For a meaningful test/build/deployment result, `kumiho_memory_store_execution`
accepts `task`, `status`, `exit_code`, `duration_ms`, `tools`, `topics`, and
`space_hint`. Include only brief sanitized `stdout`/`stderr` excerpts when
they add evidence. Never upload raw logs, environments, or credentials; skip
routine listings and avoid duplicating evidence already in a code decision.

At session end or a substantial handoff, write a concise summary of decisions,
rationale, verification, and open work. Call
`kumiho_memory_consolidate(summary=<your summary>)` once; this is the keyless
path and clears the session's working memory. Omit `session_id` so the Codex
bridge supplies the real thread. If saving a local session artifact, use the
id reported by a memory tool, never one fabricated from the repository/date.
Do not re-read full transcripts after compaction just to reconstruct logs.

Honor off-record requests: no artifacts, reflect, or consolidation for the
excluded content. See [privacy-and-trust.md](privacy-and-trust.md).
