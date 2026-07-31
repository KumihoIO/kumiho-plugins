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

The bound is restored by never issuing an unbounded wait:

1. ``Popen`` + ``communicate(timeout=timeout)``
2. on ``TimeoutExpired``: ``kill()``, then a **bounded** grace
   ``communicate(timeout=grace)``, then abandon and re-raise
3. on any other ``BaseException`` (KeyboardInterrupt included): ``kill()`` and
   re-raise, again without waiting -- ``subprocess.run`` waits here too

Abandoning deliberately leaves the pipes open. CPython's reader threads are
daemons, so they cannot hold the interpreter open; closing the pipes underneath
a thread blocked in ``read()`` only buys an "Exception ignored in thread" on
stderr. These are short-lived hooks and workers -- the handles go away when the
process does.

Output is decoded here rather than via ``text=True``: without an explicit
encoding the ambient codepage decodes it, and on cp949 a non-ASCII git subject
raises inside subprocess's reader thread (the drop mechanism documented in
``code_capture_pending._git``).
"""
from __future__ import annotations

import subprocess
from typing import Optional, Sequence

#: Grace given to a killed child to release its pipes before we stop waiting.
#: Small on purpose -- past it, waiting longer is the bug this module exists to
#: prevent, not diligence.
DEFAULT_GRACE_S = 5.0


def _decode(raw: Optional[bytes]) -> str:
    return (raw or b"").decode("utf-8", "replace")


def run(
    cmd: Sequence[str],
    *,
    timeout: float,
    grace: float = DEFAULT_GRACE_S,
    env=None,
    cwd=None,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` capturing stdout/stderr, bounded by ``timeout`` for real.

    Returns a ``CompletedProcess`` with ``str`` streams. Raises
    ``subprocess.TimeoutExpired`` if the child outlives ``timeout`` -- by then it
    has been killed and waited on for at most ``grace`` more seconds.
    """
    argv = [str(c) for c in cmd]
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            pass  # abandoned on purpose -- see module docstring
        raise
    except BaseException:
        proc.kill()
        raise
    return subprocess.CompletedProcess(
        argv, proc.returncode, _decode(out), _decode(err),
    )
