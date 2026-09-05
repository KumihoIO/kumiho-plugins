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

import os
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

    The child spawns a grandchild that inherits the pipe handles. Killing only
    the child leaves that grandchild alive and the pipes open, so the post-kill
    ``communicate()`` has neither data nor EOF. The bounded runner must tear
    down the whole tree and return inside timeout + grace.
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


def test_timeout_always_terminates_the_process_tree(monkeypatch):
    class FakeProc:
        returncode = None

        def __init__(self):
            self.communicates = 0

        def communicate(self, timeout=None):
            self.communicates += 1
            if self.communicates == 1:
                raise subprocess.TimeoutExpired(["x"], timeout)
            return b"", b""

    proc = FakeProc()
    terminated = []
    monkeypatch.setattr(
        bounded_proc, "_spawn_process", lambda *a, **kw: (proc, None))
    monkeypatch.setattr(
        bounded_proc, "_terminate_process_tree",
        lambda child, job=None: terminated.append((child, job)),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        bounded_proc.run(["x"], timeout=1, grace=1)
    assert terminated == [(proc, None)]
    assert proc.communicates == 2


def test_non_timeout_exception_always_terminates_the_process_tree(monkeypatch):
    class FakeProc:
        returncode = None

        def communicate(self, timeout=None):
            raise KeyboardInterrupt

    proc = FakeProc()
    terminated = []
    monkeypatch.setattr(
        bounded_proc, "_spawn_process", lambda *a, **kw: (proc, None))
    monkeypatch.setattr(
        bounded_proc, "_terminate_process_tree",
        lambda child, job=None: terminated.append((child, job)),
    )

    with pytest.raises(KeyboardInterrupt):
        bounded_proc.run(["x"], timeout=1)
    assert terminated == [(proc, None)]


def test_abort_scope_terminates_the_process_tree_while_communicating(monkeypatch):
    """A lost provisioning lock must cancel pip before its timeout expires."""
    class AbortAfterOnePoll:
        def __init__(self):
            self.polls = 0

        def is_set(self):
            self.polls += 1
            # pre-spawn check, first communicate slice, then abort
            return self.polls >= 3

    class FakeProc:
        returncode = None

        def __init__(self):
            self.communicates = 0

        def communicate(self, timeout=None):
            self.communicates += 1
            if self.communicates == 1:
                raise subprocess.TimeoutExpired(["pip"], timeout)
            return b"", b""

    proc = FakeProc()
    terminated = []
    monkeypatch.setattr(
        bounded_proc, "_spawn_process", lambda *a, **kw: (proc, None))
    monkeypatch.setattr(
        bounded_proc, "_terminate_process_tree",
        lambda child, job=None: terminated.append((child, job)),
    )

    with bounded_proc.abort_scope(AbortAfterOnePoll()):
        with pytest.raises(bounded_proc.ProcessAborted):
            bounded_proc.run(["pip"], timeout=30, grace=1)

    assert terminated == [(proc, None)]
    assert proc.communicates == 2, "cleanup wait must remain bounded"


def test_custom_stdout_and_stderr_are_forwarded(monkeypatch):
    seen = {}

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return None, None

    def fake_spawn(argv, **kwargs):
        seen.update(kwargs)
        return FakeProc(), None

    monkeypatch.setattr(bounded_proc, "_spawn_process", fake_spawn)
    result = bounded_proc.run(
        ["x"], timeout=1,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    assert seen["stdout"] == subprocess.DEVNULL
    assert seen["stderr"] == subprocess.STDOUT
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups use a new session")
def test_posix_children_start_in_a_new_session(monkeypatch):
    seen = {}

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(bounded_proc.subprocess, "Popen", fake_popen)
    bounded_proc.run(["x"], timeout=1)
    assert seen["start_new_session"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows concept")
def test_windows_tree_termination_uses_hidden_taskkill(monkeypatch):
    seen = {}

    class Killer:
        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            return b"", b""

        def kill(self):
            seen["killer_killed"] = True

    class Target:
        pid = 4242

        def poll(self):
            return None

        def kill(self):
            seen["target_killed"] = True

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Killer()

    monkeypatch.setattr(bounded_proc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        bounded_proc, "_windows_system_directory",
        lambda: r"C:\TrustedWindows\System32",
    )
    bounded_proc._terminate_process_tree(Target())

    assert seen["argv"][0] == r"C:\TrustedWindows\System32\taskkill.exe"
    assert seen["argv"][1:] == ["/PID", "4242", "/T", "/F"]
    assert seen["kwargs"]["env"] == {"SystemRoot": r"C:\TrustedWindows"}
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL
    assert seen["kwargs"]["close_fds"] is True
    assert (
        seen["kwargs"]["creationflags"]
        == bounded_proc._WINDOWS_CREATE_NO_WINDOW
    )
    info = seen["kwargs"]["startupinfo"]
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert info.wShowWindow == subprocess.SW_HIDE
    assert seen["target_killed"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows concept")
def test_windows_children_start_with_a_hidden_console(monkeypatch):
    """A detached worker has no console, so a console-subsystem child would pop
    a visible one for as long as it runs. The child must be started with an
    SW_HIDE STARTUPINFO -- a STARTUPINFO, not CREATE_NO_WINDOW, so that the
    child's own children (git under kumiho_memory) inherit the hidden console
    instead of allocating a visible one of their own."""
    seen = {}
    events = []

    class FakeProc:
        returncode = 0
        _handle = 101

        def communicate(self, timeout=None):
            events.append("communicate")
            return b"", b""

        def kill(self):
            pass

    class FakeJob:
        def assign(self, proc):
            events.append("assign")

        def resume(self, proc):
            events.append("resume")

        def close(self):
            events.append("close")

    def fake_popen(argv, **kwargs):
        events.append("spawn")
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(
        bounded_proc, "_create_windows_kill_job", lambda: FakeJob())
    monkeypatch.setattr(bounded_proc.subprocess, "Popen", fake_popen)
    bounded_proc.run(
        ["x"], timeout=1,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    info = seen["startupinfo"]
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert info.wShowWindow == subprocess.SW_HIDE
    assert seen["creationflags"] & bounded_proc._WINDOWS_CREATE_SUSPENDED
    assert not seen["creationflags"] & bounded_proc._WINDOWS_CREATE_NO_WINDOW
    assert seen["stdout"] == subprocess.DEVNULL
    assert seen["stderr"] == subprocess.STDOUT
    assert events == ["spawn", "assign", "resume", "communicate", "close"]


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
def test_windows_job_assignment_failure_kills_suspended_child(monkeypatch):
    events = []

    class FakeProc:
        returncode = None
        pid = 5151
        _handle = 202

        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            events.append(("wait", timeout))
            return 1

    class RejectingJob:
        def assign(self, proc):
            events.append("assign")
            raise OSError("cannot assign job")

        def resume(self, proc):
            events.append("RESUMED")

        def terminate_and_close(self):
            events.append("terminate-job")
            return True

        def close(self):
            events.append("close")

    monkeypatch.setattr(
        bounded_proc, "_create_windows_kill_job", lambda: RejectingJob())
    monkeypatch.setattr(
        bounded_proc.subprocess, "Popen", lambda *a, **kw: FakeProc())

    with pytest.raises(OSError, match="cannot assign job"):
        bounded_proc.run(["x"], timeout=1)

    assert "RESUMED" not in events
    assert events == [
        "assign", "terminate-job", "kill", ("wait", bounded_proc.DEFAULT_GRACE_S),
    ]
