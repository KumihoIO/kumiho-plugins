# Graph Traversal (Codex)

Use this for impact, lineage, temporal state, or relationships that a direct
recall summary cannot answer. Reuse krefs already returned this turn; do not
retrieve whole spaces or turn a small question into a graph crawl.

| Question | Tool |
| --- | --- |
| Why does this code exist? | `kumiho_code_why` for the affected file |
| What does this revision depend on? | `kumiho_get_dependencies` |
| What depends on it? | `kumiho_get_dependents` |
| What would this change affect? | `kumiho_analyze_impact` |
| How are two known revisions connected? | `kumiho_find_path` |
| What is its lineage? | `kumiho_get_provenance_summary` |
| What was published at a past date? | `kumiho_get_revision_as_of` |

Inspect the available tool schema for exact arguments. Start from the relevant
revision and the smallest useful depth/result limit. If recall supplies sibling
revisions, compare their dates and summaries before choosing one. The first
result is not necessarily the revision that answers the question.

For an indirect question, the turn's one engage call may use
`graph_augmented=true`, `limit=3`, `recall_mode="summarized"`. Do not issue a
second engage merely to turn graph augmentation on after a normal recall.
Explain the short evidence chain, not the raw response. Follow the core skill's
`decompose(supersedes=..., contradicts=...)` protocol for belief changes;
traversal is read-only and a fabricated edge type is not a replacement.
