# Creative Memory — Cowork Output Tracking

Creative memory records **what was produced** and links it to the cognitive
decisions that shaped it. File content stays local — the graph stores only
the output path as an artifact pointer.

**Scope:** Cowork sessions only. Claude Code uses Git — skip unless user opts in.

---

## When to Capture

After writing a **deliverable file** to the outputs folder that the user will keep:
documents (.docx, .md, .html, .pdf), presentations (.pptx), spreadsheets (.xlsx, .csv),
code artifacts, designs (.svg, .mermaid), analyses, plans.

**Skip:** temp files, intermediate drafts, test outputs, helper scripts consumed immediately.

---

## Capture Flow

Run after delivering the file to the user. Steps compose existing tools — no special creative API.

1. **Find or create item** — `kumiho_search_items(context_filter="CognitiveMemory/creative/*", name_filter="<name>")`. If not found: `kumiho_create_item(space_path="CognitiveMemory/creative/<project>", item_name="<name>", kind="<kind>")`
2. **Create revision** — `kumiho_create_revision(item_kref=<item>, metadata={session_date, platform:"cowork", description})`
3. **Attach artifact** — `kumiho_create_artifact(revision_kref=<rev>, name="<filename>", location="<cowork output path>")`. The location is the full output path, e.g. `/sessions/.../mnt/outputs/report.docx`
4. **Link lineage** — if the output was shaped by a recalled cognitive memory: `kumiho_create_edge(source_kref=<rev>, target_kref=<cognitive kref>, edge_type="DERIVED_FROM")`
5. **Action record** — `kumiho_memory_store(user_text=<request>, assistant_text=<what was produced + path>, title="Created <name> on <date>", memory_type="summary", tags=["creative-output","<kind>"], source_revision_krefs=[<rev>])`
6. **Discover edges** — `kumiho_memory_discover_edges(revision_kref=<rev>, summary=<summary>)`

---

## Space & Naming

Creative outputs go under `CognitiveMemory/creative/<project-or-topic>`.
Examples: `creative/marketing-site`, `creative/quarterly-report`, `creative/kumiho-docs`.

**Item kinds:** `document`, `presentation`, `spreadsheet`, `code`, `design`, `analysis`, `plan`

---

## Revision Stacking

When updating an existing deliverable, create a **new revision** on the existing
item — don't create a new item. Link `DERIVED_FROM` the previous revision if it
evolved from it. The graph preserves full version history.

---

## Recalling Creative Outputs

| Need | Tool |
|------|------|
| By name/kind | `kumiho_search_items(context_filter="CognitiveMemory/creative/*", kind_filter=...)` |
| By topic | `kumiho_fulltext_search(query=..., kind=...)` |
| What was built on a decision | `kumiho_get_dependents(revision_kref=<decision>)` |
| Reverse lookup from file path | `kumiho_get_artifacts_by_location(location=<path>)` |

---

## Privacy

File contents stay local — only the path is stored in the cloud graph.
Summaries are PII-redacted. "Forget this output" → `kumiho_deprecate_item`.