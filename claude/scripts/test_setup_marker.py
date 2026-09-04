#!/usr/bin/env python3
"""Tests for the onboarding wizard's end of the provisioning handshake.

The venv is only half of it. ``reflex_prefetch_worker._venv_ready`` requires the
interpreter AND ``<state-dir>/.installed-packages.txt``, so a wizard that
installs the packages and never writes that marker leaves auto-recall and the
reflect/consolidate nudges dead on every turn, with nothing but
``skip: venv not provisioned`` in reflex.log. The MCP server keeps starting
regardless -- it decides by comparing installed versions and consults the marker
only for extras identity -- which is exactly why this hid for a full working
session (kumiho-plugins#65).

Nothing here runs pip or builds a venv: ``bounded_proc.run`` is replaced and the
interpreter is an empty file.

Run: python -m pytest claude/scripts/test_setup_marker.py -q
"""

from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))  # both modules import bounded_proc by name
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("run_kumiho_mcp", "run_kumiho_mcp.py")


class _Runs:
    """Stands in for every subprocess the wizard shells out to.

    ``pip_returncode`` is the whole point: it is the difference between a
    successful install (marker) and a failed one (no marker, ever).
    """

    def __init__(self, pip_returncode: int = 0):
        self.pip_returncode = pip_returncode
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout=None, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        rc = self.pip_returncode if "pip" in argv else 0
        return subprocess.CompletedProcess(
            argv, rc, stdout="", stderr="pip exploded" if rc else "")

    def pip_argv(self) -> list[str]:
        for argv in self.calls:
            if "pip" in argv:
                return argv
        return []


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    """The wizard, pointed at a throwaway state dir with a venv already built.

    ``VENV_DIR`` is resolved at import time, so the environment has to be set
    before the module is loaded -- same reason the launcher suites reload it.
    """
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pdata"))
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)
    mod = _load("kumiho_setup", "setup.py")
    # Both layouts, so ``link_windows_bin`` sees its junction already in place
    # and no real ``mklink`` runs here.
    for sub in ("Scripts", "bin"):
        d = mod.VENV_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "python.exe").write_text("", encoding="utf-8")
        (d / "python").write_text("", encoding="utf-8")
    assert mod.VENV_PYTHON.exists()
    return mod


def _marker(tmp_path) -> Path:
    return tmp_path / "state" / L.MARKER_FILE


# ------------------------------------------------------- the missing marker

def test_a_successful_install_writes_the_provisioning_marker(wizard, tmp_path, monkeypatch):
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    assert wizard._setup_venv_locked("python3") == wizard.VENV_PYTHON
    assert _marker(tmp_path).exists(), (
        "onboarding finished without the marker: auto-recall and the "
        "reflect/consolidate nudges stay dead every turn")


def test_the_marker_holds_the_spec_the_launcher_would_have_written(wizard, tmp_path, monkeypatch):
    """Byte-for-byte what ``_provision`` writes, because ``_needs_install``
    parses it: a marker whose extras identity differs from the spec makes the
    launcher reinstall the venv the wizard just built."""
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == L.DEFAULT_PACKAGE_SPEC


def test_the_packages_installed_are_the_packages_the_marker_claims(wizard, tmp_path, monkeypatch):
    """No second hardcoded package list. The wizard used to carry its own copy
    of the spec, which is how a marker could truthfully be written and still
    describe something other than what pip was given."""
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    installed = runs.pip_argv()
    for token in shlex.split(_marker(tmp_path).read_text(encoding="utf-8")):
        assert token in installed


def test_a_failed_install_writes_no_marker(wizard, tmp_path, monkeypatch):
    """A marker over a broken install is worse than none: the launcher would
    read it as extras-identity agreement and the worker as a green light."""
    runs = _Runs(pip_returncode=1)
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    with pytest.raises(SystemExit):
        wizard._setup_venv_locked("python3")
    assert not _marker(tmp_path).exists()


def test_a_custom_spec_is_installed_and_recorded_verbatim(wizard, tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "kumiho[mcp]>=9.9.9")
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == "kumiho[mcp]>=9.9.9"
    assert "kumiho[mcp]>=9.9.9" in runs.pip_argv()


def test_an_unexpanded_placeholder_spec_falls_back_to_the_launcher_default(
        wizard, tmp_path, monkeypatch):
    """Hosts that do not expand ``${KUMIHO_CLAUDE_PACKAGE_SPEC:-...}`` pass the
    literal through; pip would be handed nonsense and the marker would record
    it."""
    monkeypatch.setenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "${KUMIHO_CLAUDE_PACKAGE_SPEC:-x}")
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == L.DEFAULT_PACKAGE_SPEC


# ------------------------------------------- the two consumers, end to end

def test_the_prefetch_worker_calls_the_venv_ready_after_onboarding(
        wizard, tmp_path, monkeypatch):
    """The assertion that actually matters: the worker's own probe, against the
    state onboarding leaves behind."""
    monkeypatch.setattr(wizard.bounded_proc, "run", _Runs())
    worker = _load("reflex_prefetch_worker", "reflex_prefetch_worker.py")

    assert worker._venv_ready(L) is None       # marker not written yet
    wizard._setup_venv_locked("python3")
    assert worker._venv_ready(L) == wizard.VENV_PYTHON


def test_the_launcher_does_not_reinstall_over_the_wizards_marker(wizard, tmp_path, monkeypatch):
    """``_needs_install`` compares the marker's name+extras identity to the
    spec's. A marker the wizard wrote must read as the same identity, or the
    first server start pays the cold install again."""
    monkeypatch.setattr(wizard.bounded_proc, "run", _Runs())
    wizard._setup_venv_locked("python3")

    previous, prev_ok = L._spec_floors(
        _marker(tmp_path).read_text(encoding="utf-8"))
    reqs, ok = L._spec_floors(L.DEFAULT_PACKAGE_SPEC)
    assert prev_ok and ok
    assert {(n, e) for n, e, _f, _c in previous} == {(n, e) for n, e, _f, _c in reqs}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
