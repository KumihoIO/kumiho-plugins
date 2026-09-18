---
name: memory-capture
description: Save one explicitly supplied fact, preference or decision to the connected Kumiho workspace. Use when the user asks to remember a particular item; use backfill for conversation-history imports.
---

# Memory Capture

1. Use the user's supplied content and scope. Ask only for missing content or an
   ambiguous storage destination. Do not capture credentials, inferred identity,
   off-record material or unrelated sensitive details.
2. If relevant saved context is needed, reuse it or call `kumiho_memory_engage`
   once with a short sanitized query. Existing context is sufficient for simple
   standalone saves; retrieval is not mandatory.
3. Call `kumiho_memory_reflect` with a brief `response` and exactly one capture:
   `type` (`fact`, `preference` or `decision`), `title`, faithful `content`,
   `tags: ["manual-capture"]`, and an exact existing `space_hint` or the matching
   `facts`, `preferences` or `decisions` space. Include `event_date` only if known
   and set `discover_edges: false`. Include only relevant returned `source_krefs`.
   If the item corrects or updates a memory already saved, first find that memory
   and set `revises` to its `kref`, keeping its type and language and restating the
   subject, so it becomes that memory's new revision. If you cannot tell which
   memory to revise, save it normally, and retire the old memory with
   `kumiho_deprecate_item` when the result is a separate item.
4. Omit `session_id` initially. On `session_required`, retry with the returned ID
   and retain it only in this conversation. Never borrow a historical session ID
   or pass `user_id`/`context` to change the identity of the session.
5. Verify the actual saved result and returned kref. Report a failure honestly;
   do not blindly retry an uncertain write. Do not save a second memory merely to
   record that the first capture succeeded.

Authentication happens through the plugin's OAuth connection. Never request a
password or token in chat. User instructions override this workflow.
