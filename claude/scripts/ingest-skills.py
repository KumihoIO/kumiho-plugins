#!/usr/bin/env python3
"""Ingest discoverable skills into CognitiveMemory/Skills.

Uses the generic skill ingest pipeline from kumiho-memory to parse this
plugin's SKILL.md and reference docs, then ingest non-inline sections into
the Kumiho graph.

All three agents (Claude, ZeroClaw, OpenClaw) share the same graph — skills
ingested here are discoverable by any agent via the Skill Discovery Protocol.

Usage:
    pip install "kumiho>=0.9.16" "kumiho-memory>=0.3.16"
    export KUMIHO_AUTH_TOKEN=kh_live_...
    python scripts/ingest-skills.py          # ingest all
    python scripts/ingest-skills.py --dry-run  # preview only
    python scripts/ingest-skills.py --list     # list sections
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent  # kumiho-plugins/claude/
SKILL_MD = PLUGIN_DIR / "skills" / "kumiho-memory" / "SKILL.md"
REFS_DIR = PLUGIN_DIR / "skills" / "kumiho-memory" / "references"


def main() -> int:
    try:
        from kumiho_memory.skill_ingest import ingest_batch, ingest_skill, parse_skill
    except ImportError:
        print(
            "ERROR: kumiho-memory package not installed.\n"
            "  pip install 'kumiho-memory>=0.3.16'",
            file=sys.stderr,
        )
        return 1

    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    list_only = "--list" in sys.argv

    if not SKILL_MD.exists():
        print(f"ERROR: SKILL.md not found at {SKILL_MD}", file=sys.stderr)
        return 1

    # List mode — show sections and exit
    if list_only:
        parsed = parse_skill(SKILL_MD)
        print(f"Skill: {parsed.name}")
        print(f"Sections ({len(parsed.sections)}):\n")
        for s in parsed.sections:
            marker = "[inline]" if s.inline else "[graph] "
            print(f"  {marker} {s.name}: {s.title} ({len(s.content)} chars)")
        graph_count = sum(1 for s in parsed.sections if not s.inline)
        print(f"\n{graph_count} sections would be ingested")

        if REFS_DIR.is_dir():
            refs = sorted(REFS_DIR.glob("*.md"))
            print(f"\nReference docs ({len(refs)}):\n")
            for f in refs:
                print(f"  [graph]  {f.stem}: {f.name}")
        return 0

    # Ingest SKILL.md sections
    print(f"Ingesting SKILL.md sections from {SKILL_MD}...")
    section_results = ingest_skill(SKILL_MD, dry_run=dry_run)
    for r in section_results:
        tag = "[NEW]" if r.created_new_item else "[REV]"
        print(f"  {tag} {r.item_name} -> {r.revision_kref}")

    # Ingest reference docs
    ref_results = []
    if REFS_DIR.is_dir():
        print(f"\nIngesting reference docs from {REFS_DIR}...")
        ref_results = ingest_batch(REFS_DIR, dry_run=dry_run)
        for r in ref_results:
            tag = "[NEW]" if r.created_new_item else "[REV]"
            print(f"  {tag} {r.item_name} -> {r.revision_kref}")

    total = len(section_results) + len(ref_results)
    action = "Would ingest" if dry_run else "Ingested"
    print(f"\n{action} {total} skills into CognitiveMemory/Skills.")
    if dry_run:
        print("(Dry run — no changes made)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
