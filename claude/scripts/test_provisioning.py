#!/usr/bin/env python3
"""Tests for first-run provisioning: the async handoff and its lock.

Measured 2026-07-31: a cold provision takes 205-320 s while the host's MCP
startup budget is 30 s (``MCP_TIMEOUT``, default 30000 in the shipped binary).
Building inline was not slow-but-working, it was guaranteed failure -- the host
gave up, killed the process, and took pip with it, so the FIRST session of every
new install could never connect and the next one started over.

The launcher now hands a cold build to a detached child and exits immediately
(measured 0.8 s). That introduced the one hazard these tests pin: provisioning
must never run twice at once, because two ``pip install`` runs against a single
venv interleave their writes. Observed live while testing -- a concurrent
``--self-test`` started a second pip against the same tree.

Run: python -m pytest claude/scripts/test_provisioning.py -q
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _launcher():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_kumiho_mcp", SCRIPTS / "run_kumiho_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _launcher()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.delenv(L._SYNC_PROVISION_ENV, raising=False)
    return tmp_path


def test_cold_start_hands_off_and_exits_instead_of_blocking(state, monkeypatch):
    """The whole point: a cold start must not sit on the host's startup clock."""
    spawned = []
    monkeypatch.setattr(L, "_spawn_detached_provisioning", lambda: spawned.append(1))
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert spawned == [1], "it must actually start the background build"
    assert "not built yet" in str(exc.value)
    assert "exiting now ON PURPOSE" in str(exc.value)


def test_a_second_cold_start_does_not_spawn_a_second_provisioner(state, monkeypatch):
    """Hosts retry. Each retry used to be another pip against the same venv."""
    spawned = []
    monkeypatch.setattr(L, "_spawn_detached_provisioning", lambda: spawned.append(1))
    with pytest.raises(SystemExit):
        L._ensure_runtime()
    L._provision_lock_path().write_text("999999", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert spawned == [1], "the second attempt must not spawn again"
    assert "already running" in str(exc.value)


def test_sync_provisioning_refuses_to_race_a_live_holder(state, monkeypatch):
    """--provision / --self-test are not on a clock, but they still must not
    interleave pip writes with another provisioner."""
    monkeypatch.setenv(L._SYNC_PROVISION_ENV, "1")
    L._provision_lock_path().write_text("999999", encoding="utf-8")
    called = []
    monkeypatch.setattr(L, "_provision", lambda *a: called.append(1))
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert not called
    assert "Another process is provisioning" in str(exc.value)


def test_a_stale_lock_is_broken_rather_than_wedging_the_install(state, monkeypatch):
    """A provisioner killed mid-install must not lock the venv out forever."""
    lock = L._provision_lock_path()
    lock.write_text("999999", encoding="utf-8")
    old = time.time() - (L.PROVISION_LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    assert not L._provision_in_progress()
    assert not lock.exists(), "the stale lock should have been cleared"


def test_the_warm_path_never_touches_the_lock(state, monkeypatch):
    """A satisfied venv must return straight through: no lock, no provisioning,
    no risk of one session's startup blocking another's."""
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "_needs_install", lambda *a: False)
    monkeypatch.setattr(L, "_provision", lambda *a: pytest.fail("must not provision"))

    assert L._ensure_runtime() == venv_python
    assert not L._provision_lock_path().exists()


def test_the_lock_is_released_even_when_provisioning_raises(state, monkeypatch):
    """pip failing is the common case; it must not leave the venv locked."""
    monkeypatch.setenv(L._SYNC_PROVISION_ENV, "1")

    def boom(*_a):
        raise subprocess.CalledProcessError(1, "pip")

    monkeypatch.setattr(L, "_provision", boom)
    with pytest.raises(subprocess.CalledProcessError):
        L._ensure_runtime()
    assert not L._provision_lock_path().exists()


def test_provision_flag_is_accepted_by_the_cli():
    """The detached child re-invokes this file with --provision; if argparse
    rejected it the background build would die instantly and silently."""
    r = subprocess.run([sys.executable, str(SCRIPTS / "run_kumiho_mcp.py"), "--help"],
                       capture_output=True, text=True, timeout=60)
    assert "--provision" in r.stdout


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
