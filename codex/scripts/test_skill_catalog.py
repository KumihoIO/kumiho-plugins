"""Skill parity, portable links, and complete offline SDK ingestion coverage."""

import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent


def load_ingest(plugin):
    spec = importlib.util.spec_from_file_location("codex_catalog_ingest", plugin / "scripts/ingest_skills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundled_documents(plugin):
    return set((plugin / "skills").glob("*/SKILL.md")) | set(
        (plugin / "skills").glob("*/references/*.md"))


class SkillCatalogTests(unittest.TestCase):
    def test_every_claude_command_has_a_codex_skill(self):
        commands = PLUGIN.parent / "claude/commands"
        if not commands.exists():
            self.skipTest("installed snapshot has no canonical Claude commands")
        missing = [p.stem for p in commands.glob("*.md")
                   if not (PLUGIN / "skills" / p.stem / "SKILL.md").is_file()]
        self.assertEqual(missing, [])

    def test_reference_topics_are_available(self):
        canonical = PLUGIN.parent / "claude/skills/kumiho-memory/references"
        if not canonical.exists():
            self.skipTest("installed snapshot has no canonical references")
        missing = [p.name for p in canonical.glob("*.md")
                   if not (PLUGIN / "skills/kumiho-memory/references" / p.name).is_file()]
        self.assertEqual(missing, [])

    def test_local_document_links_are_portable(self):
        for document in bundled_documents(PLUGIN):
            for target in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                if "://" in target or target.startswith("#"):
                    continue
                resolved = (document.parent / target.split("#", 1)[0]).resolve()
                self.assertTrue(resolved.is_relative_to(PLUGIN.resolve()), str(resolved))
                self.assertTrue(resolved.is_file(), f"{document}: {target}")

    def test_every_document_is_ingested_under_stable_codex_names(self):
        module = load_ingest(PLUGIN)
        writes = []

        def ingest_file(path, **kwargs):
            writes.append((path, kwargs))
            return types.SimpleNamespace(item_name=kwargs["item_name"])

        fake = types.SimpleNamespace(ingest_file=ingest_file, DEFAULT_AGENT_COMPAT=["claude"])
        module._enable_codex_agent_compat(fake)
        results = module._ingest_documents(fake, dry_run=True)
        self.assertEqual(fake.DEFAULT_AGENT_COMPAT, ["codex"])
        self.assertEqual({path for path, _ in writes}, bundled_documents(PLUGIN))
        self.assertEqual(len(results), len(writes))
        names = [kw["item_name"] for _, kw in writes]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("codex-kumiho-memory", names)
        self.assertIn("codex-kumiho-memory-ref-bootstrap", names)
        self.assertIn("codex-kumiho-memory-ref-onboarding", names)
        for _, kwargs in writes:
            self.assertTrue(kwargs["item_name"].startswith("codex-"))
            self.assertEqual(kwargs["project"], "CognitiveMemory")
            self.assertEqual(kwargs["space_name"], "Skills")
            self.assertTrue(kwargs["dry_run"])

    @unittest.skipUnless(os.getenv("KUMIHO_TEST_REAL_SDK") == "1",
                         "real-sdk CI job runs this with installed dependencies")
    def test_isolated_snapshot_real_sdk_dry_run(self):
        # No backend is configured and no real identity/capture/dream action
        # runs: SDK ingest_file(dry_run=True) parses/screens local docs only.
        with tempfile.TemporaryDirectory(prefix="kumiho-catalog-test-") as tmp:
            snapshot = Path(tmp) / "plugin snapshot"
            shutil.copytree(PLUGIN, snapshot, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            module = load_ingest(snapshot)
            with patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp,
                                        "KUMIHO_AUTO_CONFIGURE": "0"}, clear=True):
                from kumiho_memory import skill_ingest
                with patch.object(skill_ingest, "DEFAULT_AGENT_COMPAT", ["codex"]):
                    results = module._ingest_documents(skill_ingest, dry_run=True)
            self.assertEqual(len(results), len(bundled_documents(snapshot)))
            self.assertTrue(all(r.item_kref.startswith("kref://CognitiveMemory/Skills/codex-") for r in results))
            self.assertFalse(any(r.created_new_item for r in results))
            self.assertEqual([(r.item_name, r.quarantine_reasons) for r in results if r.quarantined], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
