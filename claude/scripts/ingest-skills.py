#!/usr/bin/env python3
"""Ingest discoverable skills into CognitiveMemory/Skills.

Uses the generic skill ingest pipeline from kumiho-memory to parse this
plugin's SKILL.md and reference docs, then ingest non-inline sections into
the Kumiho graph.

All agents (Claude, OpenClaw) share the same graph — skills ingested here
are discoverable by any agent via the Skill Discovery Protocol.

Usage:
    pip install "kumiho[mcp]>=0.12.2" "kumiho-memory[all]>=1.4.0"
    export KUMIHO_AUTH_TOKEN=kh_live_...
    python scripts/ingest-skills.py          # ingest all
    python scripts/ingest-skills.py --dry-run  # preview only
    python scripts/ingest-skills.py --list     # list sections
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Skill titles and the summary line carry non-ASCII (em-dashes, Korean), which a
# legacy Windows code page cannot encode once stdout is redirected or captured.
# Module scope, matching claude/scripts/setup.py and backfill_inventory.py --
# setup.py runs this script as a subprocess, i.e. always with a pipe.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent  # kumiho-plugins/claude/
SKILL_MD = PLUGIN_DIR / "skills" / "kumiho-memory" / "SKILL.md"
REFS_DIR = PLUGIN_DIR / "skills" / "kumiho-memory" / "references"


def _configure_explicit_ce() -> None:
    """Bind CE directly so cached Cloud auth can never redirect ingestion."""
    mode = (os.getenv("KUMIHO_CLAUDE_MODE", "") or "").strip().lower()
    endpoint = (os.getenv("KUMIHO_SERVER_ENDPOINT", "") or "").strip()
    if mode not in {"ce", "community", "self-hosted", "self_hosted", "local"}:
        return
    if not endpoint:
        raise RuntimeError("CE ingestion requires KUMIHO_SERVER_ENDPOINT")
    for key in (
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_DISCOVERY_CACHE_FILE",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    ):
        os.environ.pop(key, None)
    scheme = endpoint.partition("://")[0].lower() if "://" in endpoint else ""
    if scheme in {"grpcs", "https"}:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
        os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    else:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    import kumiho

    client = kumiho.connect(
        endpoint=endpoint,
        token="",
        enable_auto_login=False,
        use_discovery=False,
    )
    kumiho.configure_default_client(client)


def main() -> int:
    try:
        _configure_explicit_ce()
        from kumiho_memory.skill_ingest import ingest_batch, ingest_skill, parse_skill
    except ImportError:
        print(
            "ERROR: kumiho-memory package not installed.\n"
            "  pip install 'kumiho-memory[all]>=1.4.0'",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
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
