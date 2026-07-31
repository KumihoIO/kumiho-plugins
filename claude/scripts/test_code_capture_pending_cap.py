#!/usr/bin/env python3
"""Tests for the pending-queue cap, spill, eviction logging, and drain command.

The queue was measured pinned at its old cap of 50, silently discarding the
oldest commit on every enqueue. These tests pin the three properties that make
that impossible to regress: the cap spills instead of dropping, every eviction is
logged, and ``count`` reports a drain command that is actually runnable.

pytest-native (plain ``assert``, fixtures) rather than the older return-bool
house style in this directory -- pytest never enforced those, so they only ran
under ``python <file>.py``.

Run: python -m pytest claude/scripts/test_code_capture_pending_cap.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _load(name: str):
    """Load a sibling script by path.

    Underscore-named here, but loaded by path anyway so this matches the pattern
    the hyphen-named hook entrypoints REQUIRE (they are not importable by name).
    """
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").removesuffix(".py"), SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def test_apply_cap_spills_oldest_instead_of_dropping():
    ccp = _load("code_capture_pending.py")
    entries = [{"commit": "c%04d" % i} for i in range(205)]
    keep, spilled = ccp._apply_cap(entries, cap=200)
    assert len(keep) == 200
    assert len(spilled) == 5
    # oldest spill out, newest are kept
    assert spilled[0]["commit"] == "c0000"
    assert keep[0]["commit"] == "c0005"
    assert keep[-1]["commit"] == "c0204"


def test_apply_cap_is_a_noop_under_the_cap():
    ccp = _load("code_capture_pending.py")
    entries = [{"commit": "c%d" % i} for i in range(10)]
    keep, spilled = ccp._apply_cap(entries, cap=200)
    assert keep == entries
    assert spilled == []


def test_enqueue_spills_and_logs_every_eviction(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")

    # enqueue() early-returns when `git rev-parse HEAD` is empty, so the
    # integration path needs a real repo with at least one commit.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")

    ccp._write([{"commit": "c%04d" % i, "subject": "old"} for i in range(200)])
    ccp.enqueue(str(repo))

    state = ccp._state_dir()
    assert (state / "pending-code-captures.overflow.jsonl").exists()
    log = (state / "code-ingest.log").read_text(encoding="utf-8")
    assert "queue overflow: evicted" in log
    # the queue stays exactly at the cap, and the new commit made it in
    assert len(ccp._read()) == 200


def test_count_reports_an_absolute_runnable_drain_cmd(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "code_capture_pending.py"), "count"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "KUMIHO_CLAUDE_HOME": str(tmp_path),
             "PYTHONIOENCODING": "utf-8"},
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["pending"] == 0
    assert d["overflow"] == 0
    # the interpreter and the script path must both be absolute, because
    # $CLAUDE_PLUGIN_ROOT is empty in the agent's shell
    interpreter, script = d["drain_cmd"].split('"')[0].strip(), d["drain_cmd"].split('"')[1]
    assert Path(interpreter).is_absolute()
    assert Path(script).is_absolute()
    assert Path(script).exists()


def test_queue_survives_non_ascii_subjects(tmp_path, monkeypatch):
    """Piped children report cp949 on this machine; ensure_ascii=True keeps
    Korean subjects byte-safe through the JSONL round-trip."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    ccp._write([{"commit": "c1", "subject": "한국어 커밋 제목"}])
    assert ccp._read()[0]["subject"] == "한국어 커밋 제목"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
