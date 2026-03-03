# Artifacts, Executions & Session Close

## Persist significant outputs

Every significant agent output (code, docs, analyses, plans) → write to disk + associate with graph revision + `kumiho_memory_discover_edges`.

**Significant**: documents, code, analyses, plans, creative outputs.
**Not significant**: short answers, conversational responses, one-liners.

### Artifact flow

1. Write file to `{artifact_dir}/{category}/{descriptive-name}.{ext}`
2. Search for existing item (stack revisions, don't create duplicates)
3. Store:
   - **New item**: `kumiho_memory_store(artifact_location=<path>, memory_item_kind=<kind>, ...)`
   - **Existing item**: `kumiho_create_revision` + `kumiho_tag_revision("published")` + `kumiho_create_edge(CREATED_FROM)`
4. `kumiho_memory_discover_edges` on result

## Procedural memory — tool executions

Store significant commands via `kumiho_memory_store_execution(task, status, stdout, exit_code, duration_ms, tools, topics)`.
Store: builds, deploys, tests, migrations, complex tool chains. Skip: trivial commands (`ls`, `git status`).

## Context compaction

After `/compact` or auto-compression, immediately:
```
kumiho_memory_store(user_text=<context>, assistant_text=<compact summary>, title="Session compact: <topic>", memory_type="summary", tags=["compact","session-context"])
```
Then `kumiho_memory_discover_edges` on result.

## Conversation artifacts

For sessions with 2+ meaningful exchanges, at task boundaries or session end:

1. Write markdown to `{artifact_dir}/{YYYY-MM-DD}/{session_id}.md` with YAML frontmatter (session_id, user_id, agent_name, date, topics, summary) + exchanges
2. `kumiho_memory_consolidate(session_id=<id>)`
3. `kumiho_memory_discover_edges` on consolidation result
4. Close with continuity — reference what's open for next session