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
import shlex
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


def _seed(ccp, *commits):
    ccp._write([{"repo": "r", "commit": c, "subject": "s"} for c in commits])


def test_done_removes_the_exact_commit_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    _seed(ccp, "a" * 40, "b" * 40)
    assert ccp.done("a" * 40) == {"removed": 1, "commit": "a" * 40}
    assert [e["commit"] for e in ccp._read()] == ["b" * 40]


def test_done_strips_the_carriage_return_a_windows_pipe_adds(tmp_path, monkeypatch):
    """Four `done <sha>\\r` calls on 2026-09-02 each exited 0 and removed
    nothing; the queue looked drained while every entry was still there."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    _seed(ccp, "a" * 40)
    assert ccp.done("a" * 40 + "\r\n")["removed"] == 1
    assert ccp._read() == []


def test_done_with_an_unknown_commit_is_loud_and_leaves_the_queue_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    _seed(ccp, "a" * 40)
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "code_capture_pending.py"), "done", "f" * 40],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "KUMIHO_CLAUDE_HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8"},
    )
    assert out.returncode == 1, out.stderr
    assert json.loads(out.stdout) == {"removed": 0, "commit": "f" * 40, "error": "not found"}
    assert len(ccp._read()) == 1


def test_done_accepts_a_unique_prefix_but_refuses_an_ambiguous_one(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    _seed(ccp, "abc1234" + "0" * 33, "abc1234" + "1" * 33, "def5678" + "0" * 33)
    ambiguous = ccp.done("abc1234")
    assert ambiguous["removed"] == 0 and ambiguous["error"] == "ambiguous prefix"
    assert len(ccp._read()) == 3
    assert ccp.done("def5678")["removed"] == 1
    assert len(ccp._read()) == 2
    # shorter than git's own short hash is never treated as a prefix
    assert ccp.done("abc")["error"] == "not found"
    assert len(ccp._read()) == 2


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
    drain = shlex.split(d["drain_cmd"])
    done = shlex.split(d["done_cmd"])
    interpreter, isolated, script = drain[:3]
    assert isolated == "-I"
    assert Path(interpreter).is_absolute()
    assert Path(script).is_absolute()
    assert Path(script).exists()
    assert drain[3:] == ["--claude-host", "list"]
    assert done[3:] == ["--claude-host", "done"]


def test_count_shell_quotes_spaced_and_substitution_like_paths(tmp_path, monkeypatch):
    ccp = _load("code_capture_pending.py")
    fake_python = str(tmp_path / "User $(never-run)" / "python")
    fake_script = str(tmp_path / "Plugin `never-run`" / "pending.py")
    monkeypatch.setattr(ccp.sys, "executable", fake_python)
    monkeypatch.setattr(ccp, "__file__", fake_script)

    commands = ccp.count()

    assert shlex.split(commands["drain_cmd"]) == [
        fake_python, "-I", fake_script, "--claude-host", "list"
    ]
    assert shlex.split(commands["done_cmd"]) == [
        fake_python, "-I", fake_script, "--claude-host", "done"
    ]


def test_queue_survives_non_ascii_subjects(tmp_path, monkeypatch):
    """Piped children report cp949 on this machine; ensure_ascii=True keeps
    Korean subjects byte-safe through the JSONL round-trip."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    ccp = _load("code_capture_pending.py")
    ccp._write([{"commit": "c1", "subject": "한국어 커밋 제목"}])
    assert ccp._read()[0]["subject"] == "한국어 커밋 제목"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
