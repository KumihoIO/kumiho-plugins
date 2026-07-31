#!/usr/bin/env python3
"""Shared state layer for the memory reflex (hooks + detached workers).

Stdlib only, and deliberately does NOT import ``run_kumiho_mcp.py``: this module
runs on the per-turn critical path, where the launcher's env hydration, auth
validation and runtime probing would cost far more than the work itself. It
duplicates ``_state_dir`` for the same reason ``code_capture_pending.py`` already
does.

Single-writer discipline -- every file has exactly one writer, so ``os.replace``
only ever contends with a reader:

    <sid>.session.json   written by session-bootstrap.py     read by workers
    <sid>.turn.json      written by memory-reflex.py         read by workers
    <sid>.recall.json    written by reflex_prefetch_worker   read by memory-reflex
    <sid>.turns.jsonl    appended by reflex-observe.py       tailed by memory-reflex

Run: imported; not an entrypoint.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_LEDGER_MAX_BYTES = 2 * 1024 * 1024
_SAFE_ID_REJECT = '\\/:*?"<>|'


def state_dir() -> Path:
    """Mirror ``run_kumiho_mcp._state_dir`` (kept in sync deliberately)."""
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def reflex_dir() -> Path:
    d = state_dir() / "reflex"
    d.mkdir(parents=True, exist_ok=True)
    return d


def off() -> bool:
    """Universal kill switch. A FILE, not an env var: hooks never see the
    ``.mcp.json`` env block, so an env-only switch would be unreachable from the
    one place a user needs it -- turning the reflex off without an app restart."""
    try:
        return (state_dir() / "reflex.off").exists()
    except OSError:
        return False


_CONF_CACHE = {}


def conf(name: str, default: str = "") -> str:
    """Resolve a knob for a HOOK process.

    Hooks inherit the CLI's environment, not the MCP server's, so a name declared
    only in ``.mcp.json`` is invisible to them -- which made every value declared
    there a control that silently did nothing. The launcher, which DOES see that
    block (and resolves ``${VAR:-default}`` on Desktop, where it arrives
    literally), snapshots the resolved values to ``<state>/reflex.config.json``;
    this reads that snapshot.

    Precedence: real process env > launcher snapshot > caller default. The env
    wins so ``~/.claude/settings.json`` and an ad-hoc shell override still take
    effect immediately, without waiting for a server restart to refresh the file.
    """
    raw = (os.getenv(name, "") or "").strip()
    if raw:
        return raw
    if not _CONF_CACHE:
        _CONF_CACHE.update(read_json(state_dir() / "reflex.config.json", {}) or {"_": ""})
    val = _CONF_CACHE.get(name)
    return str(val).strip() if val not in (None, "") else default


def conf_int(name: str, default: int) -> int:
    try:
        return int(conf(name, "") or default)
    except (ValueError, TypeError):
        return default


def gate(name: str, default_true: bool = True) -> bool:
    """The falsy-tuple idiom from ``code-capture-hook.py``, over ``conf``."""
    raw = conf(name, "").lower()
    if not raw:
        return default_true
    return raw not in ("0", "false", "no", "off")


def safe_id(value: str) -> str:
    """Session/prompt ids become filenames; never trust them as path components."""
    value = str(value or "").strip()
    if not value or any(c in value for c in _SAFE_ID_REJECT):
        return ""
    return value


def log(msg: str) -> None:
    try:
        with open(state_dir() / "reflex.log", "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def tail_lines(path, max_lines: int = 200, max_bytes: int = 131072) -> list:
    """Absolute-seek tail.

    Never ``seek(-n, SEEK_END)``: in text mode that raises
    ``io.UnsupportedOperation``, and in binary mode it raises ``OSError 22`` when
    the file is shorter than the window. Seeking to an absolute offset is always
    legal, so this works on a 12-byte file and a 200 MB one alike.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)  # absolute -> always legal
                f.readline()              # discard the partial first line
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-max_lines:]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_atomic(path, obj, attempts: int = 4) -> bool:
    """Atomic-ish replace with a Windows retry loop.

    ``os.replace`` raises ``PermissionError`` (WinError 32 sharing violation, or 5
    access denied) whenever either file is open in another process -- which is the
    normal case here, since a reader hook may be mid-read. Retry with backoff,
    then give up and LOG rather than raising into a hook.
    """
    path = str(path)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=True)
    except OSError as exc:
        log("write failed: %s (%s)" % (path, exc))
        return False
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.05 * (i + 1))
        except OSError as exc:
            log("replace error: %s (%s)" % (path, exc))
            break
    log("replace failed after %d attempts: %s" % (attempts, path))
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return False


def append_jsonl(path, obj) -> bool:
    """Append one JSON line.

    Capped by BYTES rather than rotated: rotation-by-rename hits the same Windows
    sharing violation as ``os.replace``, and rotating would discard the newest
    ledger entries, which are the ones any diagnosis needs.
    """
    path = str(path)
    try:
        if os.path.exists(path) and os.path.getsize(path) >= _LEDGER_MAX_BYTES:
            log("ledger full, refusing to append: %s" % path)
            return False
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=True) + "\n")
        return True
    except OSError as exc:
        log("append failed: %s (%s)" % (path, exc))
        return False


def prune(max_sessions: int = 40, max_age_days: int = 7) -> int:
    """Drop old per-session file sets. Called only from detached workers -- never
    from a hook, because it stats the whole directory. Logs every deletion."""
    removed = 0
    try:
        d = reflex_dir()
        cutoff = time.time() - (max_age_days * 86400)
        sessions = {}
        for p in d.iterdir():
            if not p.is_file():
                continue
            sid = p.name.split(".")[0]
            sessions.setdefault(sid, []).append(p)
        ordered = sorted(
            sessions.items(),
            key=lambda kv: max((f.stat().st_mtime for f in kv[1]), default=0.0),
            reverse=True,
        )
        for idx, (sid, files) in enumerate(ordered):
            newest = max((f.stat().st_mtime for f in files), default=0.0)
            if idx < max_sessions and newest >= cutoff:
                continue
            for f in files:
                try:
                    f.unlink()
                    removed += 1
                    log("prune: removed %s" % f.name)
                except OSError:
                    pass
    except OSError as exc:
        log("prune error: %s" % exc)
    return removed
