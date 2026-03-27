#!/usr/bin/env python3
"""One-time skill ingestion — populate CognitiveMemory/Skills from Claude plugin source.

Reads the canonical SKILL.md and reference docs from the Claude plugin,
then creates skill items in the Kumiho graph so any agent (Claude, ZeroClaw,
OpenClaw) can discover them via the Skill Discovery Protocol.

Usage:
    pip install "kumiho>=0.9.16" "kumiho-memory>=0.3.16"
    export KUMIHO_AUTH_TOKEN=kh_live_...
    python scripts/ingest-skills.py

Idempotent: checks for existing items before creating. Re-running creates
new revisions on existing items rather than duplicating.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve source paths (Claude plugin is the source of truth)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent.parent  # kumiho-plugins/
CLAUDE_SKILL_DIR = PLUGIN_ROOT / "claude" / "skills" / "kumiho-memory"
CLAUDE_REFS_DIR = CLAUDE_SKILL_DIR / "references"

PROJECT = "CognitiveMemory"
SKILLS_SPACE = f"{PROJECT}/Skills"

# ---------------------------------------------------------------------------
# Skill definitions — each becomes an item in CognitiveMemory/Skills
# ---------------------------------------------------------------------------

SKILLS: list[dict] = [
    {
        "name": "memory-discipline",
        "title": "Memory Discipline — Stack, Don't Scatter",
        "source_file": CLAUDE_SKILL_DIR / "SKILL.md",
        "section": "Memory Discipline",
        "tags": ["skill", "memory", "core"],
        "content": """\
## Memory Discipline

- **Stack, don't scatter** — search before creating items. Stack revisions on existing items. Never name items with `-v2`, `-final`.
- **Auto-store**: user decisions, preferences, facts, corrections, tool patterns. Your own: architecture decisions, bug resolutions, complex explanations, config outcomes, long-form drafts (posts, emails, documents), creative outputs, and any substantive content the user would want to recall later.
- **Don't store**: trivial one-liners, uncommitted brainstorming, credentials/secrets.
- **Use absolute dates when storing** — summaries and titles must use absolute dates ("on Feb 24", "2026-02-24") instead of relative ones ("today", "yesterday", "30 minutes ago"). Relative time becomes meaningless when recalled in a future session. The `created_at` timestamp handles recency at recall time.
- **Contradictions**: acknowledge evolution, ingest the new fact. SUPERSEDES edges are automatic.
""",
    },
    {
        "name": "session-end",
        "title": "Session End — Artifacts, Consolidation, Continuity",
        "source_file": CLAUDE_SKILL_DIR / "SKILL.md",
        "section": "Session End + Artifacts",
        "tags": ["skill", "session", "core"],
        "content": """\
## Session End

1. Generate conversation artifact at `{artifact_dir}/{YYYY-MM-DD}/{session_id}.md`
   - YAML frontmatter: session_id, user_id, agent_name, date, topics, summary
   - Include meaningful exchanges (skip trivial acknowledgements)
2. Call consolidation tool with the session ID, then discover_edges on result
3. Close with continuity — reference what's open for next session

## Conversation Artifacts

For sessions with 2+ meaningful exchanges, at task boundaries or session end:
- Write markdown with YAML frontmatter + exchanges
- Only generate for meaningful sessions (4+ messages)

## Context Compaction

After auto-compression or explicit compact, immediately store the compact summary
with memory_type='summary' and tags ['compact', 'session-context'], then discover_edges.

## Procedural Memory — Tool Executions

Store significant commands (builds, deploys, tests, migrations, complex tool chains)
via the store_execution tool. Skip trivial commands (ls, git status).
""",
    },
    {
        "name": "creative-memory",
        "title": "Creative Memory — Cowork Output Tracking",
        "source_file": CLAUDE_REFS_DIR / "creative-memory.md",
        "section": "Full document",
        "tags": ["skill", "creative", "cowork"],
        "content": (CLAUDE_REFS_DIR / "creative-memory.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "creative-memory.md").exists()
        else "# Creative Memory\n\nSource file not found.",
    },
    {
        "name": "edges-and-traversal",
        "title": "Edges & Graph Traversal Patterns",
        "source_file": CLAUDE_REFS_DIR / "edges-and-traversal.md",
        "section": "Full document",
        "tags": ["skill", "graph", "traversal"],
        "content": (CLAUDE_REFS_DIR / "edges-and-traversal.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "edges-and-traversal.md").exists()
        else "# Edges and Traversal\n\nSource file not found.",
    },
    {
        "name": "privacy-and-trust",
        "title": "Privacy & Trust — Data Handling Rules",
        "source_file": CLAUDE_REFS_DIR / "privacy-and-trust.md",
        "section": "Full document",
        "tags": ["skill", "privacy", "trust"],
        "content": (CLAUDE_REFS_DIR / "privacy-and-trust.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "privacy-and-trust.md").exists()
        else "# Privacy & Trust\n\nSource file not found.",
    },
    {
        "name": "artifacts",
        "title": "Artifacts, Executions & Session Close",
        "source_file": CLAUDE_REFS_DIR / "artifacts.md",
        "section": "Full document",
        "tags": ["skill", "artifacts", "execution"],
        "content": (CLAUDE_REFS_DIR / "artifacts.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "artifacts.md").exists()
        else "# Artifacts\n\nSource file not found.",
    },
    {
        "name": "bootstrap-details",
        "title": "Session Bootstrap — Identity Load Details",
        "source_file": CLAUDE_REFS_DIR / "bootstrap.md",
        "section": "Full document",
        "tags": ["skill", "bootstrap", "identity"],
        "content": (CLAUDE_REFS_DIR / "bootstrap.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "bootstrap.md").exists()
        else "# Bootstrap\n\nSource file not found.",
    },
    {
        "name": "onboarding",
        "title": "Onboarding Flow — First Meeting with New User",
        "source_file": CLAUDE_REFS_DIR / "onboarding.md",
        "section": "Full document",
        "tags": ["skill", "onboarding", "identity"],
        "content": (CLAUDE_REFS_DIR / "onboarding.md").read_text(encoding="utf-8")
        if (CLAUDE_REFS_DIR / "onboarding.md").exists()
        else "# Onboarding\n\nSource file not found.",
    },
    {
        "name": "tools-reference",
        "title": "Tools Quick Reference & Edge Types",
        "source_file": CLAUDE_SKILL_DIR / "SKILL.md",
        "section": "Tools Quick Reference",
        "tags": ["skill", "tools", "reference"],
        "content": """\
## Tools Quick Reference

**Working memory**: chat_add, chat_get, chat_clear

**Memory lifecycle**: memory_ingest, memory_add_response, memory_consolidate, memory_recall (semantic search), memory_retrieve (structured filters: space, bundle, mode), memory_store, memory_discover_edges (mandatory after store/consolidate), memory_store_execution (build/deploy/test outcomes), memory_dream_state

**Graph**: create_edge, get_edges, get_dependencies, get_dependents, find_path, analyze_impact, get_provenance_summary

**Creative output tracking**: Composes search_items, create_item, create_revision, create_artifact, create_edge, memory_store, memory_discover_edges

**Edge types**: DERIVED_FROM (default), DEPENDS_ON (assumptions), REFERENCED (auto from discover_edges), CREATED_FROM (artifacts), SUPERSEDES (belief revision), CONTAINS (bundles)

Note: Tool names are agent-specific. Claude uses kumiho_memory_<tool>, ZeroClaw uses kumiho_memory__<tool> (double underscore), OpenClaw uses wrapped names like memory_search.
""",
    },
]

# Inter-skill edges to create after ingestion
EDGES: list[dict] = [
    {
        "source": "session-end",
        "target": "artifacts",
        "edge_type": "CONTAINS",
    },
    {
        "source": "session-end",
        "target": "memory-discipline",
        "edge_type": "DEPENDS_ON",
    },
    {
        "source": "creative-memory",
        "target": "privacy-and-trust",
        "edge_type": "DEPENDS_ON",
    },
    {
        "source": "onboarding",
        "target": "bootstrap-details",
        "edge_type": "DEPENDS_ON",
    },
]


# ---------------------------------------------------------------------------
# Ingestion logic
# ---------------------------------------------------------------------------


def ingest(dry_run: bool = False) -> None:
    """Ingest all skill definitions into the Kumiho graph."""
    try:
        from kumiho import KumihoClient
    except ImportError:
        print(
            "ERROR: kumiho package not installed.\n"
            "  pip install 'kumiho>=0.9.16' 'kumiho-memory>=0.3.16'",
            file=sys.stderr,
        )
        sys.exit(1)

    token = os.getenv("KUMIHO_AUTH_TOKEN", "").strip()
    if not token:
        print(
            "ERROR: KUMIHO_AUTH_TOKEN not set.\n"
            "  export KUMIHO_AUTH_TOKEN=kh_live_...",
            file=sys.stderr,
        )
        sys.exit(1)

    client = KumihoClient(api_token=token)

    # Ensure Skills space exists
    print(f"Ensuring space: {SKILLS_SPACE}")
    if not dry_run:
        try:
            client.get_space(space_path=SKILLS_SPACE)
            print(f"  Space already exists.")
        except Exception:
            client.create_space(project=PROJECT, space_path=SKILLS_SPACE)
            print(f"  Created space.")

    # Track created krefs for edge creation
    item_krefs: dict[str, str] = {}
    revision_krefs: dict[str, str] = {}

    for skill in SKILLS:
        name = skill["name"]
        item_kref = f"kref://{SKILLS_SPACE}/{name}.skill"
        item_krefs[name] = item_kref
        print(f"\nIngesting: {name}")
        print(f"  Title: {skill['title']}")
        print(f"  Source: {skill['source_file']}")
        print(f"  Content length: {len(skill['content'])} chars")

        if dry_run:
            print(f"  [DRY RUN] Would create item + revision")
            continue

        # Check if item exists
        try:
            existing = client.get_item(item_kref=item_kref)
            print(f"  Item exists — creating new revision")
            revisions = client.get_item_revisions(item_kref=item_kref)
            next_rev = len(revisions) + 1
        except Exception:
            # Create new item
            print(f"  Creating new item")
            client.create_item(
                space_path=SKILLS_SPACE,
                item_name=name,
                kind="skill",
            )
            next_rev = 1

        # Create revision with full content
        rev_kref = f"{item_kref}?r={next_rev}"
        client.create_revision(
            item_kref=item_kref,
            metadata={
                "title": skill["title"],
                "content": skill["content"],
                "source": str(skill["source_file"]),
                "section": skill["section"],
                "version": "0.1.0",
                "agent_compat": ["claude", "zeroclaw", "openclaw"],
                "tags": skill["tags"],
            },
        )
        revision_krefs[name] = rev_kref
        print(f"  Created revision: {rev_kref}")

        # Tag as published
        try:
            client.tag_revision(revision_kref=rev_kref, tag="published")
            print(f"  Tagged as published")
        except Exception as e:
            print(f"  Warning: could not tag as published: {e}")

    # Create inter-skill edges
    if not dry_run and revision_krefs:
        print(f"\nCreating inter-skill edges...")
        for edge in EDGES:
            src = edge["source"]
            tgt = edge["target"]
            if src in revision_krefs and tgt in revision_krefs:
                try:
                    client.create_edge(
                        source_kref=revision_krefs[src],
                        target_kref=revision_krefs[tgt],
                        edge_type=edge["edge_type"],
                    )
                    print(f"  {src} --[{edge['edge_type']}]--> {tgt}")
                except Exception as e:
                    print(f"  Warning: edge {src}->{tgt} failed: {e}")

    print(f"\nDone. Ingested {len(SKILLS)} skills into {SKILLS_SPACE}.")
    if dry_run:
        print("(Dry run — no changes made)")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    ingest(dry_run=dry)
