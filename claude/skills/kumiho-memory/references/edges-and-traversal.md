# Edges and Graph Traversal

## Graph-augmented recall

`kumiho_memory_recall(query=..., graph_augmented=true)` enables multi-query reformulation + edge traversal + semantic fallback.

**Use when**: indirect questions, decision chains, few/no standard recall results.
**Skip when**: direct lookups, obvious keyword overlap, just-discussed topics.

## Sibling revision selection

Recall results may include `sibling_revisions`. You are the reranker — scan sibling titles/summaries/structured metadata (entities, facts, implications, events), pick the best match for the current query, use its kref for follow-up stores. The primary result isn't always best.

## Graph traversal patterns

| Question pattern | Tool | Purpose |
|------------------|------|---------|
| "Why did we decide X?" | `kumiho_get_dependencies` | What X depends on |
| "What breaks if we change X?" | `kumiho_analyze_impact` | Downstream effects |
| "How are X and Y related?" | `kumiho_find_path` | Shortest reasoning chain |
| "What led to this?" | `kumiho_get_provenance_summary` | Full lineage |
| "What depends on this assumption?" | `kumiho_get_dependents` | Reverse dependencies |
| "What was X on date Y?" | `kumiho_get_revision_as_of` | Temporal query |

Translate traversal results into natural reasoning — don't dump raw output.