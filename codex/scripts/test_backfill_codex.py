"""Offline backfill integration tests: only synthetic histories and fake writes.

Run with the shared runtime: python -I codex/scripts/test_backfill_codex.py
"""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backfill_codex as host
import run_kumiho_mcp as codex
from backfill import ingest_runner as runner


class BackfillTests(unittest.TestCase):
    def test_vendored_engine_parity(self):
        canonical = HERE.parent.parent / "claude" / "scripts"
        if not canonical.exists():
            self.skipTest("installed snapshot has no Claude source")
        for source, target in (("backfill_inventory.py", "inventory.py"),
                               ("backfill/ingest_runner.py", "ingest_runner.py")):
            self.assertEqual((canonical / source).read_text(encoding="utf-8"),
                             (HERE / "backfill" / target).read_text(encoding="utf-8"))

    def test_isolated_snapshot_extract_stage_preview_and_refusal(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for installed-entrypoint smoke")
        with tempfile.TemporaryDirectory(prefix="kumiho-backfill-test-") as tmp:
            root = Path(tmp)
            snapshot = root / "plugin with spaces"
            shutil.copytree(HERE.parent, snapshot,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            self.assertTrue((snapshot / "skills/kumiho-backfill/SKILL.md").is_file())
            sessions = root / "codex-home" / "sessions"
            sessions.mkdir(parents=True)
            for i in range(7):
                records = [
                    {"type": "session_meta", "timestamp": "2026-09-01T00:00:00Z",
                     "payload": {"id": f"fixture-{i}", "cwd": "/synthetic/repo"}},
                    {"type": "response_item", "timestamp": "2026-09-01T00:00:01Z",
                     "payload": {"type": "message", "role": "user", "content": [
                         {"type": "input_text", "text": f"We decided fixture {i} uses SQLite because it is local."}]}},
                    {"type": "response_item", "timestamp": "2026-09-01T00:00:02Z",
                     "payload": {"type": "message", "role": "assistant", "content": [
                         {"type": "output_text", "text": "Recorded the decision."}]}},
                ]
                (sessions / f"rollout-{i}.jsonl").write_text(
                    "\n".join(json.dumps(r) for r in records), encoding="utf-8")
            state = root / "batch"
            env = dict(os.environ, CODEX_HOME=str(sessions.parent),
                       KUMIHO_PYTHON=sys.executable, KUMIHO_AUTO_CONFIGURE="1")

            def run(*args, code=0):
                proc = subprocess.run(
                    [node, str(snapshot / "scripts/run_kumiho_mcp.mjs"),
                     "--backfill", "--state-dir", str(state), *args],
                    cwd=root, env=env, capture_output=True, text=True,
                    encoding="utf-8", timeout=40,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
                return proc.stdout + proc.stderr

            scan = run("inventory", "scan")
            self.assertIn("7 session files", scan)
            self.assertNotIn("Source: Claude Code", scan)
            self.assertFalse(state.exists())
            run("inventory", "manifest")
            staging_file = state / "staging.json"
            staging = json.loads(staging_file.read_text(encoding="utf-8"))
            self.assertEqual(len(staging["sessions"]), 7)
            self.assertEqual({s["source"] for s in staging["sessions"]}, {"codex"})
            run("inventory", "packetize")
            self.assertEqual(len(list((state / "packets").glob("*.md"))), 5)
            packet = next((state / "packets").glob("*.md"))
            self.assertIn("UNTRUSTED DATA", packet.read_text(encoding="utf-8"))
            payload = root / "captures.json"
            payload.write_text(json.dumps({"captures": [{
                "type": "summary", "title": "Fixture decision on 2026-09-01",
                "content": "A synthetic session selected SQLite for local storage.",
                "event_date": "2026-09-01", "space_hint": "test", "tags": [],
                "evidence": [{"role": "user", "ts": "2026-09-01T00:00:01Z",
                              "quote": "LOCAL_ONLY_EVIDENCE"}],
            }], "decompose": {}}), encoding="utf-8")
            for _ in range(2):
                run("inventory", "stage", "--session", packet.stem,
                    "--captures-file", str(payload))
            staged = json.loads(staging_file.read_text(encoding="utf-8"))
            selected = next(s for s in staged["sessions"] if s["source_session_id"] == packet.stem)
            self.assertEqual(len(selected["captures"]), 1)
            before = staging_file.read_bytes()
            preview = run("ingest", "--dry-run", "--limit", "5")
            self.assertIn("A synthetic session selected SQLite", preview)
            self.assertNotIn("LOCAL_ONLY_EVIDENCE", preview)
            self.assertIn("nothing uploaded", preview)
            run("ingest", "--limit", "5", code=1)
            self.assertEqual(before, staging_file.read_bytes())

    def test_backend_routes_use_codex_config_and_pin_keyless(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "codex.json"
            for backend in ("ce", "cloud"):
                config_path.write_text(json.dumps({"schema_version": 1, "backend": backend,
                    "endpoint": "127.0.0.1:9190", "llm_base_url": "http://127.0.0.1:1234/v1"}),
                    encoding="utf-8")
                observed = []

                def execute(path, **kwargs):
                    observed.append((Path(path).name, list(sys.argv), dict(os.environ)))

                with patch.dict(os.environ, {"KUMIHO_CLAUDE_MODE": "cloud",
                        "KUMIHO_AUTH_TOKEN": "synthetic-token", "ANTHROPIC_API_KEY": "synthetic"}, clear=True), \
                     patch.object(codex, "_codex_config_path", return_value=config_path), \
                     patch.object(host.runpy, "run_path", side_effect=execute), \
                     patch.object(sys, "argv", ["test"]):
                    self.assertEqual(host.ingest(["--yes", "--limit", "5"]), 0)
                name, argv, env = observed[0]
                self.assertEqual(name, f"run_kumiho_{backend}.py")
                self.assertEqual(argv[1], "--script")
                self.assertEqual(env["KUMIHO_CLAUDE_HOST"], "codex")
                self.assertEqual(env["KUMIHO_LLM_BASE_URL"], "http://127.0.0.1:9/v1")
                self.assertEqual(env["KUMIHO_AUTO_ASSESS"], "0")
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                if backend == "ce":
                    self.assertEqual(env["KUMIHO_SERVER_ENDPOINT"], "127.0.0.1:9190")
                    self.assertEqual(env["KUMIHO_AUTH_TOKEN"], "")
                else:
                    self.assertEqual(env["KUMIHO_AUTH_TOKEN"], "synthetic-token")
                    self.assertNotIn("KUMIHO_SERVER_ENDPOINT", env)

    def test_dry_run_never_binds_backend_even_with_yes(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(codex, "_apply_codex_config", side_effect=AssertionError("backend accessed")), \
             patch.object(host.runpy, "run_path") as execute, \
             patch.object(sys, "argv", ["test"]):
            host.ingest(["--dry-run", "--yes"])
            self.assertEqual(Path(execute.call_args.args[0]).name, "ingest_runner.py")

    def test_replay_resume_keeps_evidence_local(self):
        class Redactor:
            def reject_credentials(self, text):
                pass

            def anonymize_summary(self, text):
                return text

        calls = []

        def reflect(args):
            calls.append(args)
            return {"capture_results": [{"revision_kref": "kref://test/fixture.summary?r=1"}]}

        session = {"source_session_id": "fixture", "status": "extracted", "captures": [{
            "type": "summary", "title": "Fixture", "content": "Synthetic only",
            "event_date": "2026-09-01", "content_sha256": "fixture-hash",
            "evidence": [{"quote": "LOCAL_ONLY_EVIDENCE"}]}]}
        staging = {"sessions": [session]}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            path = Path(tmp) / "staging.json"
            first = runner.ingest_session(session, staging, path, reflect, lambda _: {},
                                          Redactor(), ValueError, use_batch=True)
            second = runner.ingest_session(session, staging, path, reflect, lambda _: {},
                                           Redactor(), ValueError, use_batch=True)
            self.assertEqual(first["stored"], 1)
            self.assertEqual(second["already"], 1)
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0]["discover_edges"])
            self.assertEqual(calls[0]["captures"][0]["event_date"], "2026-09-01")
            self.assertNotIn("LOCAL_ONLY_EVIDENCE", json.dumps(calls))
            self.assertEqual(json.loads(path.read_text())["sessions"][0]["status"], "ingested")


if __name__ == "__main__":
    unittest.main(verbosity=2)
