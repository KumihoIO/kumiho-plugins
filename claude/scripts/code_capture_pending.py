#!/usr/bin/env python3
"""Keyless pending-commit queue for Decision Memory (kumiho-plugins).

The detached commit-ingest worker (``code_ingest_worker.py``) needs an LLM to
mine a commit into decisions -- but a git hook fires with NO agent in the loop,
so when no real model is configured it cannot extract anything. Instead of
DROPPING the commit (the old ``skip: no LLM`` behaviour), it ENQUEUES it here.

The in-loop agent (Claude -- the model the user already chose) then DRAINS the
queue on its next session, keyless: it reads each commit's diff and calls
``kumiho_code_capture`` (the agent extracts the decision; the tool only writes).
No external API key, end to end -- exactly the plugin's hard constraint.

Subcommands (the agent uses ``list`` / ``done``; the worker uses ``enqueue``):
  enqueue <repo> [commit]   append a commit (default HEAD) to the queue, deduped
  list                       print pending entries as a JSON array (for the agent)
  done <commit>              drop an entry once the agent has captured it;
                             exits 1 and says so when nothing matched
  count                      queue depth + the absolute drain command
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import bounded_proc

#: git here only reads refs and one commit subject; anything slower is a stuck
#: repo, not a slow one.
_GIT_TIMEOUT_S = 30

# Bound the backlog so a long keyless stretch can't grow forever. Raised from 50
# after the queue was measured pinned AT the old cap, silently dropping the
# oldest commit on every enqueue: at the observed ~6 commits/day, 200 is ~33 days
# of headroom while staying small enough for an agent to actually drain. Evicted
# entries now spill to a sibling file and every eviction is logged -- the old
# ``entries[-_MAX_QUEUE:]`` discarded them with no record at all.
_MAX_QUEUE = 200


def _state_dir() -> Path:
    """Mirror ``run_kumiho_mcp._state_dir`` (kept in sync deliberately; this
    helper must run standalone for the agent without importing the launcher)."""
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def _queue_path() -> Path:
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "pending-code-captures.jsonl"


def _overflow_path() -> Path:
    return _queue_path().with_suffix(".overflow.jsonl")


def _log(msg: str) -> None:
    """Append to the worker's EXISTING log; a queue event is a capture-path event,
    so it belongs in the same file an operator already reads."""
    try:
        with open(_state_dir() / "code-ingest.log", "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass  # logging must never break the capture path


def _read() -> list:
    p = _queue_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _write(entries: list) -> None:
    _queue_path().write_text(
        "".join(json.dumps(e, ensure_ascii=True) + "\n" for e in entries),
        encoding="utf-8",
    )


def _apply_cap(entries: list, cap: int = _MAX_QUEUE) -> tuple:
    """Return ``(keep, spilled)``. Oldest entries spill. Pure, so the cap
    behaviour is testable without a git repo or a state dir."""
    if len(entries) <= cap:
        return entries, []
    return entries[-cap:], entries[:-cap]


def _append_overflow(spilled: list) -> None:
    """Durable landing place for evicted entries. Append-only: the queue is
    bounded for drainability, but nothing should be destroyed to keep it so."""
    try:
        with open(_overflow_path(), "a", encoding="utf-8") as fh:
            for e in spilled:
                fh.write(json.dumps(e, ensure_ascii=True) + "\n")
    except OSError as exc:
        _log("queue overflow: could not spill (%s)" % exc)


def _git(repo: str, *args: str) -> str:
    """Read git output as UTF-8, under a real time bound.

    Decoding is explicit: with ``text=True`` and no encoding the ambient
    codepage decodes it; on cp949 a non-ASCII commit subject raises inside
    subprocess's reader thread, so ``stdout`` comes back None and ``.strip()``
    raises AttributeError -- which is neither OSError nor SubprocessError, so it
    escapes enqueue() entirely and the commit is dropped. The live log shows 64
    such drops, and the queue holds zero non-ASCII subjects: every one was lost.

    The wait is bounded via ``bounded_proc`` because this is the enqueue path a
    git hook drives with no agent in the loop: a stuck ``git`` here used to hang
    the worker forever (kumiho-plugins#36 -- and ``subprocess.run(timeout=...)``
    would not have been a bound either).
    """
    try:
        r = bounded_proc.run(["git", "-C", repo, *args], timeout=_GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _log("git timed out after %ss: %s" % (_GIT_TIMEOUT_S, " ".join(args)))
        return ""
    except OSError as exc:
        _log("git failed to start (%s): %s" % (exc, " ".join(args)))
        return ""
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def enqueue(repo: str, commit: str = "") -> None:
    """Queue a commit for keyless agent capture. Dedup by hash; capture is
    idempotent anyway (get-or-create + SDK marker), so a re-enqueue is safe."""
    repo = os.path.abspath(repo)
    commit = commit or _git(repo, "rev-parse", "HEAD")
    if not commit:
        return
    entries = _read()
    if any(e.get("commit") == commit for e in entries):
        return
    entries.append({
        "repo": repo,
        "commit": commit,
        "subject": _git(repo, "log", "-1", "--format=%s", commit),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    keep, spilled = _apply_cap(entries)
    if spilled:
        _append_overflow(spilled)
        for e in spilled:
            _log("queue overflow: evicted %s %s" % (
                (e.get("commit") or "?")[:12], (e.get("subject") or "")[:80]))
    _write(keep)


def done(commit: str) -> dict:
    """Drop the queue entry for ``commit`` once the agent has captured it.

    The argument is stripped first: the agent usually pipes the hash out of a
    shell, and on Windows that pipe carries a trailing carriage return. On
    2026-09-02 four ``done <sha>\r`` calls each exited 0 and removed nothing,
    so the queue looked drained while every entry was still there. A hash that
    matches nothing, or a prefix that matches more than one entry, is reported
    in the result and the command exits non-zero: a silent no-op here is the
    invisible failure the queue exists to prevent. A prefix counts only from
    seven characters, git's own short-hash floor.
    """
    wanted = (commit or "").strip()
    entries = _read()
    matched = [e for e in entries if e.get("commit") == wanted]
    if not matched and len(wanted) >= 7:
        matched = [e for e in entries
                   if str(e.get("commit") or "").startswith(wanted)]
    if not wanted or not matched:
        return {"removed": 0, "commit": wanted, "error": "not found"}
    if len(matched) > 1:
        return {"removed": 0, "commit": wanted, "error": "ambiguous prefix",
                "candidates": [e.get("commit") for e in matched]}
    target = matched[0].get("commit")
    _write([e for e in entries if e.get("commit") != target])
    return {"removed": 1, "commit": target}


def count() -> dict:
    """Queue depth plus a drain command that actually runs.

    ``drain_cmd`` is built from ``sys.executable`` + this file's absolute path on
    purpose: ``CLAUDE_PLUGIN_ROOT`` is empty in the agent's shell environment, so
    the ``$CLAUDE_PLUGIN_ROOT``-based drain command documented in SKILL.md has
    never expanded to a runnable path."""
    overflow = _overflow_path()
    n_over = 0
    if overflow.exists():
        n_over = sum(1 for ln in overflow.read_text(encoding="utf-8").splitlines() if ln.strip())
    return {
        "pending": len(_read()),
        "overflow": n_over,
        "queue_path": str(_queue_path()),
        "drain_cmd": '%s "%s" list' % (sys.executable, os.path.abspath(__file__)),
    }


def main(argv: list) -> int:
    if not argv:
        print("usage: code_capture_pending.py enqueue <repo> [commit] | list | done <commit> | count")
        return 2
    cmd = argv[0]
    if cmd == "enqueue" and len(argv) >= 2:
        enqueue(argv[1], argv[2] if len(argv) > 2 else "")
        return 0
    if cmd == "list":
        print(json.dumps(_read(), ensure_ascii=True))
        return 0
    if cmd == "count":
        print(json.dumps(count(), ensure_ascii=True))
        return 0
    if cmd == "done" and len(argv) >= 2:
        result = done(argv[1])
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["removed"] else 1
    print("bad args")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
