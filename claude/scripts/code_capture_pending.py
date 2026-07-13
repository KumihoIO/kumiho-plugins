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
  done <commit>              drop an entry once the agent has captured it
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_MAX_QUEUE = 50  # bound the backlog so a long keyless stretch can't grow forever


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
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
        encoding="utf-8",
    )


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


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
    _write(entries[-_MAX_QUEUE:])


def main(argv: list) -> int:
    if not argv:
        print("usage: code_capture_pending.py enqueue <repo> [commit] | list | done <commit>")
        return 2
    cmd = argv[0]
    if cmd == "enqueue" and len(argv) >= 2:
        enqueue(argv[1], argv[2] if len(argv) > 2 else "")
        return 0
    if cmd == "list":
        print(json.dumps(_read(), ensure_ascii=False))
        return 0
    if cmd == "done" and len(argv) >= 2:
        _write([e for e in _read() if e.get("commit") != argv[1]])
        return 0
    print("bad args")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
