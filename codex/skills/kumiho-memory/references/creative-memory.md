# Deliverable Tracking (Codex)

Use for retained documents, presentations, spreadsheets, designs, or analyses
when durable output tracking is useful or requested. Do not mirror ordinary
git edits here: use Decision Memory with code anchors. Skip temporary files,
test fixtures, and intermediate drafts.

1. Find the existing item with `kumiho_search_items` scoped to the relevant
   `CognitiveMemory/creative/<topic>` and name. If none exists, create an item
   in that space using `kumiho_create_item`; create a missing space only when
   this tracking is in scope. Kinds include `document`, `presentation`,
   `spreadsheet`, `design`, `analysis`, or `plan`.
2. Use `kumiho_create_revision` with concise string metadata such as
   `platform: "codex"`, `session_date`, and `description`.
3. Attach the delivered file with `kumiho_create_artifact`, passing the returned
   revision kref, filename, and real absolute `location`. No Cowork output-path
   assumptions. This stores a pointer; another machine may not have that file.
4. Link a genuinely supporting recalled decision using `kumiho_create_edge`
   with `edge_type="DERIVED_FROM"`. On updates, reuse the item and link the
   previous revision if appropriate; do not create duplicate items.
5. Fold the short output summary/path into the turn's normal reflect only if
   it adds useful context, with a matching `space_hint`. Do not capture it twice.

Recall by scoped name/kind, by `kumiho_fulltext_search(query=..., limit=3)`,
or reverse lookup using `kumiho_get_artifacts_by_location(location=<path>)`.
Follow [privacy-and-trust.md](privacy-and-trust.md): metadata and descriptions
are stored in the selected backend; artifact pointers do not upload file bytes.
