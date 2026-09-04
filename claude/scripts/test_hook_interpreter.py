#!/usr/bin/env python3
"""The hook interpreter is ``<venv>/bin/pythonw`` on every platform.

On Windows the ``bin`` junction exposes the venv's real ``pythonw.exe``; on
POSIX no such binary exists, so the launcher links ``bin/pythonw`` to
``bin/python``. These pin the POSIX half on any OS (the link function is
platform-neutral: symlink first, copy when the filesystem refuses) and the
launcher's promise that the link is idempotent and never raises.

Run: python -m pytest claude/scripts/test_hook_interpreter.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _launcher():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "kumiho_claude_launcher_hook_test", SCRIPTS / "run_kumiho_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_posix_venv(root: Path) -> Path:
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_bytes(b"#!/bin/sh\necho fake python\n")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    return venv


def test_posix_link_makes_bin_pythonw_resolve_to_python(tmp_path):
    launcher = _launcher()
    venv = _fake_posix_venv(tmp_path)
    launcher._link_posix_pythonw(venv)
    link = venv / "bin" / "pythonw"
    assert link.exists()
    # A symlink to the sibling, or a byte-identical copy where symlinks are
    # refused (Windows without developer mode) -- either way it runs python.
    assert link.is_symlink() or link.read_bytes() == (venv / "bin" / "python").read_bytes()


def test_posix_link_is_idempotent_and_never_replaces_an_existing_file(tmp_path):
    launcher = _launcher()
    venv = _fake_posix_venv(tmp_path)
    link = venv / "bin" / "pythonw"
    link.write_bytes(b"already here")
    launcher._link_posix_pythonw(venv)
    launcher._link_posix_pythonw(venv)
    assert link.read_bytes() == b"already here"


def test_posix_link_does_nothing_without_a_python_to_point_at(tmp_path):
    launcher = _launcher()
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    launcher._link_posix_pythonw(venv)  # must not raise
    assert not (venv / "bin" / "pythonw").exists()


def test_hooks_name_pythonw_everywhere():
    """One literal for every platform: the junction serves it on Windows, the
    link above on POSIX. A hook naming bin/python would flash a console per
    hook under Desktop, where claude.exe has no console to inherit."""
    import json
    hooks = json.loads((SCRIPTS.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = {
        h["command"]
        for event in hooks["hooks"].values()
        for group in event
        for h in group["hooks"]
    }
    assert commands == {"${CLAUDE_PLUGIN_DATA}/venv/bin/pythonw"}


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
