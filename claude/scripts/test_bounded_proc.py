#!/usr/bin/env python3
"""Tests that ``bounded_proc.run``'s timeout is a real bound (kumiho-plugins#36).

The regression these pin is subtle: ``subprocess.run(..., timeout=N)`` *looks*
bounded and behaves bounded in the easy case. It only degrades to an unbounded
wait when the killed child leaves its pipes held open by a descendant -- so a
test that just sleeps proves nothing about the bug. ``test_timeout_is_bounded_
when_a_grandchild_holds_the_pipes`` is the one that fails on ``subprocess.run``.

pytest-native (plain ``assert``), matching test_code_capture_pending_cap.py.

Run: python -m pytest claude/scripts/test_bounded_proc.py -q
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

import bounded_proc

#: Long enough that a regression (unbounded wait) blows past every assertion
#: below, short enough that a stray grandchild is gone before anyone notices.
_HOLD_S = 20


def test_returns_decoded_streams_on_success():
    r = bounded_proc.run(
        [sys.executable, "-c", "print('hi'); import sys; sys.stderr.write('err')"],
        timeout=30,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "hi"
    assert r.stderr.strip() == "err"


def test_non_utf8_output_never_raises():
    """The cp949 drop mechanism documented in ``code_capture_pending._git``:
    decoding must degrade to replacement chars, never explode in a reader
    thread and hand back ``None``."""
    r = bounded_proc.run(
        [sys.executable, "-c",
         "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok')"],
        timeout=30,
    )
    assert r.returncode == 0
    assert "ok" in r.stdout


def test_timeout_raises_on_a_plain_sleeper():
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_proc.run(
            [sys.executable, "-c", f"import time; time.sleep({_HOLD_S})"],
            timeout=1, grace=2,
        )
    assert time.monotonic() - start < _HOLD_S / 2


def test_timeout_is_bounded_when_a_grandchild_holds_the_pipes():
    """The actual #36 scenario.

    The child spawns a grandchild that inherits the pipe handles and then dies
    on ``kill()``. The grandchild keeps the pipes open, so the post-kill
    ``communicate()`` has nothing to read and no EOF to wait for.
    ``subprocess.run`` blocks there for the grandchild's whole lifetime; this
    must come back inside timeout + grace.
    """
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({_HOLD_S})']); "
        f"time.sleep({_HOLD_S})"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_proc.run([sys.executable, "-c", child], timeout=1, grace=2)
    elapsed = time.monotonic() - start
    # 1s timeout + 2s grace + startup slack, and decisively under the 20s a
    # regression would take.
    assert elapsed < 10, f"post-kill wait was not bounded: {elapsed:.1f}s"


def test_missing_executable_raises_oserror_not_timeout():
    """Call sites catch OSError separately to log 'failed to start' rather than
    'timed out'; Popen must keep surfacing it."""
    with pytest.raises(OSError):
        bounded_proc.run(["kumiho-no-such-binary-xyz"], timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows concept")
def test_windows_children_start_with_a_hidden_console(monkeypatch):
    """A detached worker has no console, so a console-subsystem child would pop
    a visible one for as long as it runs. The child must be started with an
    SW_HIDE STARTUPINFO -- a STARTUPINFO, not CREATE_NO_WINDOW, so that the
    child's own children (git under kumiho_memory) inherit the hidden console
    instead of allocating a visible one of their own."""
    seen = {}

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

        def kill(self):
            pass

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(bounded_proc.subprocess, "Popen", fake_popen)
    bounded_proc.run(["x"], timeout=1)
    info = seen["startupinfo"]
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert info.wShowWindow == subprocess.SW_HIDE
    assert "creationflags" not in seen
