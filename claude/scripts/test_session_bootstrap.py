#!/usr/bin/env python3
"""Tests for the SessionStart bootstrap hook.

This is the first hook test in the repository, so it establishes the two patterns
every later hook test should use:

  PATTERN A -- run the real entrypoint via subprocess, feeding the hook payload on
  stdin. Use this for any assertion about exit code or exact stdout: those are the
  things that break silently in production, and only the real process exercises
  the __main__ guard, the stdout encoding, and the JSON envelope together.

  PATTERN B -- importlib + spec_from_file_location. Use this for pure functions.
  Hyphen-named hook entrypoints are not importable by module name, so loading by
  path is mandatory; it only became safe once the script grew a __main__ guard
  (before that, exec_module printed and raised SystemExit at import time).

Run: python -m pytest claude/scripts/test_session_bootstrap.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _run_hook(script: str, payload: dict, env_extra: dict | None = None):
    """PATTERN A -- real entrypoint, real stdin, real exit code."""
    # No PYTHONIOENCODING, raw UTF-8 bytes -- production conditions.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env.update(env_extra or {})
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=env, timeout=30,
    )
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


def _load(script: str):
    """PATTERN B -- load a hyphen-named script by path."""
    spec = importlib.util.spec_from_file_location(
        script.replace("-", "_").removesuffix(".py"), SCRIPTS / script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # safe only because of the __main__ guard
    return mod


def test_emits_the_exact_injection_envelope(tmp_path):
    r = _run_hook("session-bootstrap.py", {"session_id": "s1", "source": "startup"},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    # exactly one top-level key: the CLI ignores unrecognized top-level keys with
    # a warning, so an almost-right envelope silently injects nothing
    assert list(d.keys()) == ["hookSpecificOutput"]
    assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert d["hookSpecificOutput"]["additionalContext"].strip()


def test_survives_empty_stdin(tmp_path):
    """SessionStart must never fail a session."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "KUMIHO_CLAUDE_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPTS / "session-bootstrap.py")],
                       input="", capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=30)
    assert r.returncode == 0
    assert "Traceback" not in r.stderr
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_survives_garbage_stdin(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "KUMIHO_CLAUDE_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPTS / "session-bootstrap.py")],
                       input="not json at all {{{", capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=30)
    assert r.returncode == 0
    assert "Traceback" not in r.stderr


def test_persists_the_host_session_facts(tmp_path):
    sid = "11111111-2222-3333-4444-555555555555"
    r = _run_hook("session-bootstrap.py",
                  {"session_id": sid, "source": "startup", "cwd": str(tmp_path),
                   "transcript_path": str(tmp_path / "t.jsonl")},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    d = json.loads((tmp_path / "reflex" / ("%s.session.json" % sid))
                   .read_text(encoding="utf-8"))
    assert d["session_id"] == sid
    assert d["source"] == "startup"
    assert d["transcript_path"].endswith("t.jsonl")


def test_no_session_id_persists_nothing(tmp_path):
    r = _run_hook("session-bootstrap.py", {"source": "startup"},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert not (tmp_path / "reflex").exists() or \
        not list((tmp_path / "reflex").glob("*.session.json"))


def test_rejects_a_path_traversing_session_id(tmp_path):
    """session_id becomes a filename; never trust it as a path component."""
    r = _run_hook("session-bootstrap.py",
                  {"session_id": "../../evil", "source": "startup"},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    assert r.returncode == 0
    assert not list(tmp_path.rglob("*evil*"))


def test_no_longer_bans_consulting_the_skill(tmp_path):
    """The old text forbade the only natural repair for a displaced protocol,
    making the diagnosed failure unrecoverable by construction."""
    r = _run_hook("session-bootstrap.py", {"session_id": "s", "source": "startup"},
                  {"KUMIHO_CLAUDE_HOME": str(tmp_path)})
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Do NOT invoke the kumiho-memory skill" not in ctx
    assert "MAY consult the kumiho-memory skill" in ctx


def test_importable_without_side_effects():
    """The __main__ guard must hold: exec_module previously printed the whole
    envelope and raised SystemExit, which would trap every future hook test."""
    mod = _load("session-bootstrap.py")
    assert callable(mod.main)
    assert isinstance(mod.CONTEXT, str)


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
