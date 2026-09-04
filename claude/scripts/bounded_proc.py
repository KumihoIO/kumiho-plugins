#!/usr/bin/env python3
"""Subprocess execution whose timeout is actually a bound (kumiho-plugins).

``subprocess.run(..., timeout=N)`` is NOT bounded on Windows. Its
``TimeoutExpired`` handler calls ``process.kill()`` and then
``process.communicate()`` with **no timeout at all**. ``TerminateProcess`` is
asynchronous, so a child stuck in uninterruptible kernel I/O -- or any
descendant that inherited the pipe handles -- keeps the pipes open and that
second ``communicate()`` blocks forever. ``timeout=N`` silently becomes an
unbounded wait. See KumihoIO/kumiho-SDKs#79 for the full forensics and #80 for
the original fix; this module ports that pattern to this repo's workers
(KumihoIO/kumiho-plugins#36).

The failure is not theoretical here: a ``kumiho_code_capture`` MCP call hung for
30 minutes on 2026-07-16, and a ``kumiho_code_why`` call reproduced the same
30-minute silence on 2026-07-31.

The bound is restored by never issuing an unbounded wait and by containing the
child in a process tree that can be torn down as one unit:

1. POSIX children start in a new session. Windows children start suspended,
   enter a ``KILL_ON_JOB_CLOSE`` Job Object, and only then resume. Closing that
   job still kills descendants if the root process has already exited.
2. ``Popen`` + ``communicate(timeout=timeout)``.
3. on ``TimeoutExpired``: kill the entire tree, then a **bounded** grace
   ``communicate(timeout=grace)``, then re-raise.
4. on any other ``BaseException`` (KeyboardInterrupt included): kill the entire
   tree and re-raise without an unbounded wait.

Output is decoded here rather than via ``text=True``: without an explicit
encoding the ambient codepage decodes it, and on cp949 a non-ASCII git subject
raises inside subprocess's reader thread (the drop mechanism documented in
``code_capture_pending._git``).
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import time
from typing import Optional, Sequence


_WINDOWS_CREATE_SUSPENDED = 0x00000004
_WINDOWS_CREATE_NO_WINDOW = 0x08000000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

def _hidden_console_kwargs() -> dict:
    """Keep a console-subsystem child off the screen when WE have no console.

    Detached workers (and the launcher when Desktop spawns it) run without a
    console, so every console child -- pip, git, a ``python -m`` run -- would
    allocate a NEW, visible one: the console window that flashed on Windows
    for the duration of each background job.  SW_HIDE on the STARTUPINFO hides
    that window and, unlike CREATE_NO_WINDOW, the hidden console is inherited
    by the child's own children, so git spawned by kumiho_memory stays hidden
    too.  No-op when a console is inherited, and on POSIX.
    """
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": info}


#: Grace given to a killed child to release its pipes before we stop waiting.
#: Small on purpose -- past it, waiting longer is the bug this module exists to
#: prevent, not diligence.
DEFAULT_GRACE_S = 5.0

# A provisioning lock heartbeat can fail while ``communicate`` is waiting on a
# long-running pip/build-backend child.  A ContextVar lets the lock owner bind
# one cancellation event around the complete critical section without plumbing
# it through every helper that eventually calls this runner.
_ABORT_EVENT = contextvars.ContextVar("kumiho_bounded_proc_abort_event", default=None)
_ABORT_POLL_S = 0.1


class ProcessAborted(subprocess.SubprocessError):
    """Raised after an externally-cancelled subprocess tree has been killed."""

    def __init__(self, cmd: Sequence[str]) -> None:
        self.cmd = [str(value) for value in cmd]
        super().__init__(f"subprocess aborted: {self.cmd!r}")


@contextlib.contextmanager
def abort_scope(event):
    """Abort every :func:`run` in this context when ``event`` becomes set."""
    token = _ABORT_EVENT.set(event)
    try:
        yield
    finally:
        _ABORT_EVENT.reset(token)


def _decode(raw: Optional[bytes]) -> str:
    return (raw or b"").decode("utf-8", "replace")


class _WindowsKillJob:
    """A non-inheritable Windows Job Object that owns the spawned tree."""

    def __init__(self, handle, kernel32) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    @classmethod
    def create(cls) -> "_WindowsKillJob":
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        return cls(handle, kernel32)

    def assign(self, proc: subprocess.Popen) -> None:
        import ctypes

        if not self._kernel32.AssignProcessToJobObject(
            self._handle, int(proc._handle),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, proc: subprocess.Popen) -> None:
        """Resume a CREATE_SUSPENDED process without a racy thread handle."""
        import ctypes
        from ctypes import wintypes

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = int(ntdll.NtResumeProcess(int(proc._handle)))
        if status != 0:
            raise OSError(
                "NtResumeProcess failed with NTSTATUS "
                f"0x{status & 0xffffffff:08x}"
            )

    def terminate_and_close(self) -> bool:
        """Terminate every job member, close the job, and report API success."""
        if not self._handle:
            return False
        handle, self._handle = self._handle, None
        try:
            return bool(self._kernel32.TerminateJobObject(handle, 1))
        finally:
            self._kernel32.CloseHandle(handle)

    def close(self) -> None:
        """Close the kill-on-close job; any surviving descendants die here."""
        if self._handle:
            handle, self._handle = self._handle, None
            self._kernel32.CloseHandle(handle)


def _create_windows_kill_job() -> _WindowsKillJob:
    return _WindowsKillJob.create()


def _windows_system_directory() -> str:
    """Resolve System32 through Win32, never environment variables or PATH."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetSystemDirectoryW(buffer, size)
        if length == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            return buffer.value
        size = int(length) + 1
    raise OSError("Windows system directory exceeds the supported path length")


def _windows_taskkill_tree(proc: subprocess.Popen) -> None:
    """Fallback tree kill using only a trusted absolute executable path."""
    try:
        system_dir = _windows_system_directory()
        taskkill = os.path.join(system_dir, "taskkill.exe")
        killer = subprocess.Popen(
            [taskkill, "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # taskkill needs no caller state. SystemRoot is derived from the
            # trusted API result rather than inherited from a mutable env var.
            env={"SystemRoot": os.path.dirname(system_dir)},
            close_fds=True,
            creationflags=_WINDOWS_CREATE_NO_WINDOW,
            **_hidden_console_kwargs(),
        )
        try:
            killer.communicate(timeout=DEFAULT_GRACE_S)
        except BaseException:
            try:
                killer.kill()
            except OSError:
                pass
    except (OSError, ValueError):
        pass


def _terminate_process_tree(
    proc: subprocess.Popen,
    job: Optional[_WindowsKillJob] = None,
) -> None:
    """Best-effort hard termination of ``proc`` and every descendant.

    POSIX children are placed in their own session by :func:`run`, making the
    process group safe to signal. Windows normally terminates the Job Object,
    which remains authoritative after the root exits; trusted-System32
    ``taskkill /T`` is only a fallback. Every cleanup wait remains bounded.
    """
    if os.name == "nt":
        job_terminated = job.terminate_and_close() if job is not None else False
        if not job_terminated:
            _windows_taskkill_tree(proc)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass

    # Tree termination can fail (missing taskkill, permissions, a concurrent
    # exit). Still make the direct child termination best-effort and bounded.
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def _spawn_process(argv, *, stdout, stderr, env, cwd):
    """Spawn a contained child and return ``(process, Windows job or None)``."""
    if os.name != "nt":
        proc = subprocess.Popen(
            argv,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            start_new_session=True,
            **_hidden_console_kwargs(),
        )
        return proc, None

    # Creating the process suspended removes the spawn→assign race: none of its
    # code can create an escaping descendant before Job Object containment.
    job = _create_windows_kill_job()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            creationflags=_WINDOWS_CREATE_SUSPENDED,
            **_hidden_console_kwargs(),
        )
    except BaseException:
        job.close()
        raise
    try:
        job.assign(proc)
        job.resume(proc)
    except BaseException:
        # The child has not intentionally been allowed to run unless resume
        # itself failed after taking effect. In either case, contain and reap it
        # before surfacing the setup failure: never continue uncontained.
        _terminate_process_tree(proc, job)
        try:
            proc.wait(timeout=DEFAULT_GRACE_S)
        except BaseException:
            pass
        raise
    return proc, job


def _communicate_until_done(proc, argv, *, timeout, abort_event):
    """Communicate while polling an external abort without losing the bound."""
    if abort_event is None:
        return proc.communicate(timeout=timeout)

    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if abort_event.is_set():
            raise ProcessAborted(argv)
        if deadline is None:
            wait_s = _ABORT_POLL_S
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            wait_s = min(_ABORT_POLL_S, remaining)
        try:
            return proc.communicate(timeout=wait_s)
        except subprocess.TimeoutExpired:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, timeout)


def run(
    cmd: Sequence[str],
    *,
    timeout: float | None,
    grace: float = DEFAULT_GRACE_S,
    env=None,
    cwd=None,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    abort_event=None,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with a real timeout; capture output by default.

    ``stdout`` and ``stderr`` accept the same targets as ``Popen`` while their
    defaults preserve the original captured-output behavior. Returns a
    ``CompletedProcess`` with decoded ``str`` streams when captured. Raises
    ``subprocess.TimeoutExpired`` if the child outlives ``timeout`` -- by then it
    has been killed and waited on for at most ``grace`` more seconds.
    """
    argv = [str(c) for c in cmd]
    active_abort = abort_event if abort_event is not None else _ABORT_EVENT.get()
    if active_abort is not None and active_abort.is_set():
        raise ProcessAborted(argv)
    proc, job = _spawn_process(
        argv, stdout=stdout, stderr=stderr, env=env, cwd=cwd,
    )
    try:
        out, err = _communicate_until_done(
            proc,
            argv,
            timeout=timeout,
            abort_event=active_abort,
        )
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc, job)
        try:
            proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        raise
    except ProcessAborted:
        _terminate_process_tree(proc, job)
        try:
            proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        raise
    except BaseException:
        _terminate_process_tree(proc, job)
        raise
    finally:
        if job is not None:
            job.close()
    return subprocess.CompletedProcess(argv, proc.returncode, _decode(out), _decode(err))
