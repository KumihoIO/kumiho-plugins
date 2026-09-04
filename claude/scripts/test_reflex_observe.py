#!/usr/bin/env python3
"""Tests for the observation hook and the shared reflex state layer.

The load-bearing assertions here are the privacy one (the ledger must never
contain any substring of the assistant's response) and the Windows/encoding ones
(absolute-seek tail, cp949-safe round-trip), because those are the two classes of
bug that fail silently in production rather than in a test run.

Run: python -m pytest claude/scripts/test_reflex_observe.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
KOREAN = "이것은 절대로 원장에 저장되면 안 되는 매우 독특한 한국어 응답입니다"


def _run_hook(payload: dict, home: Path, env_extra: dict | None = None):
    # Deliberately does NOT set PYTHONIOENCODING, and feeds raw UTF-8 BYTES.
    # Production sets neither, and forcing them here made the suite structurally
    # blind: 78 tests passed green while the shipped hook wrote nothing for any
    # payload containing an em dash.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["KUMIHO_CLAUDE_HOME"] = str(home)
    env.update(env_extra or {})
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "reflex-observe.py")],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=env, timeout=30,
    )
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger(home: Path, sid: str) -> list:
    p = home / "reflex" / ("%s.turns.jsonl" % sid)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --------------------------------------------------------------- observe hook

def test_stop_records_a_turn_without_storing_the_text(tmp_path):
    r = _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                   "prompt_id": "p1", "last_assistant_message": KOREAN}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""  # prints nothing, ever

    rows = _ledger(tmp_path, "s1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "stop"
    assert rows[0]["resp_len"] == len(KOREAN)
    assert rows[0]["tool_only"] is False
    assert len(rows[0]["resp_sha12"]) == 12

    # THE privacy assertion: no substring of the response survives anywhere.
    raw = (tmp_path / "reflex" / "s1.turns.jsonl").read_text(encoding="utf-8")
    assert KOREAN not in raw
    for chunk in (KOREAN[:8], KOREAN[10:22], "한국어"):
        assert chunk not in raw


def test_tool_only_turn_is_flagged_not_counted_as_a_miss(tmp_path):
    r = _run_hook({"hook_event_name": "Stop", "session_id": "s2",
                   "prompt_id": "p1", "last_assistant_message": "   "}, tmp_path)
    assert r.returncode == 0
    rows = _ledger(tmp_path, "s2")
    assert rows[0]["tool_only"] is True
    assert rows[0]["resp_len"] == 0
    assert rows[0]["resp_sha12"] == ""


def test_stop_hook_active_writes_nothing(tmp_path):
    """A Stop hook re-firing inside its own continuation must not double-count."""
    r = _run_hook({"hook_event_name": "Stop", "session_id": "s3",
                   "stop_hook_active": True, "last_assistant_message": "hi"}, tmp_path)
    assert r.returncode == 0
    assert _ledger(tmp_path, "s3") == []


def test_post_tool_use_records_consolidate_with_a_success_flag(tmp_path):
    """The consolidate floor resets only on a consolidate that actually
    succeeded, so the row carries what the response said -- and nothing else
    from it."""
    base = {"hook_event_name": "PostToolUse", "prompt_id": "p9",
            "tool_name": "mcp__plugin_kumiho-memory_kumiho-memory__kumiho_memory_consolidate"}
    _run_hook({**base, "session_id": "s6", "tool_response": {"content": [
        {"type": "text", "text": '{"success": false, "error": "summarization failed"}'}]}},
        tmp_path)
    _run_hook({**base, "session_id": "s7", "tool_response": {"content": [
        {"type": "text", "text": '{"success": true, "summary": "..."}'}]}}, tmp_path)
    _run_hook({**base, "session_id": "s8"}, tmp_path)
    # An exception surfaces as {"error": ...} with no success key at all; it
    # stored nothing, so it must not reset the floor either.
    _run_hook({**base, "session_id": "s9", "tool_response": {"content": [
        {"type": "text", "text": '{"error": "Credential pattern detected"}'}]}}, tmp_path)
    failed, ok, unknown, raised = (_ledger(tmp_path, s)[0] for s in ("s6", "s7", "s8", "s9"))
    assert failed["tool"] == "consolidate" and failed["ok"] is False
    assert ok["ok"] is True
    assert unknown["ok"] is True, "no response at all must not read as a failure"
    assert raised["ok"] is False, "an error envelope stored nothing"
    assert "summarization failed" not in json.dumps(failed)


def test_post_tool_use_records_the_long_mcp_name(tmp_path):
    r = _run_hook({"hook_event_name": "PostToolUse", "session_id": "s4",
                   "prompt_id": "p9",
                   "tool_name": "mcp__plugin_kumiho-memory_kumiho-memory__kumiho_memory_engage"},
                  tmp_path)
    assert r.returncode == 0
    rows = _ledger(tmp_path, "s4")
    assert rows[0]["kind"] == "tool"
    assert rows[0]["tool"] == "engage"


def test_kill_switch_file_disables_everything(tmp_path):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "reflex.off").write_text("", encoding="utf-8")
    r = _run_hook({"hook_event_name": "Stop", "session_id": "s5",
                   "last_assistant_message": "hello"}, tmp_path)
    assert r.returncode == 0
    assert _ledger(tmp_path, "s5") == []


def test_env_gate_disables_everything(tmp_path):
    """C3 regression: this hook must gate on the SAME name as memory-reflex.py
    and .mcp.json. It used to read KUMIHO_MEMORY_REFLEX, declared nowhere, so
    KUMIHO_REFLEX=0 stopped injection while the observer and the detached
    prefetch worker both kept running."""
    r = _run_hook({"hook_event_name": "Stop", "session_id": "s6",
                   "last_assistant_message": "hello"}, tmp_path,
                  {"KUMIHO_REFLEX": "0"})
    assert r.returncode == 0
    assert _ledger(tmp_path, "s6") == []

    # the old, undeclared name must no longer be a live gate
    r2 = _run_hook({"hook_event_name": "Stop", "session_id": "s7",
                    "last_assistant_message": "hello"}, tmp_path,
                   {"KUMIHO_MEMORY_REFLEX": "0"})
    assert r2.returncode == 0
    assert len(_ledger(tmp_path, "s7")) == 1


def test_survives_garbage_and_empty_stdin(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "KUMIHO_CLAUDE_HOME": str(tmp_path)}
    for payload in ("", "not json {{{", "[]", "null"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "reflex-observe.py")],
                           input=payload, capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=30)
        assert r.returncode == 0, payload
        assert "Traceback" not in r.stderr


def test_path_traversing_session_id_is_rejected(tmp_path):
    r = _run_hook({"hook_event_name": "Stop", "session_id": "../../evil",
                   "last_assistant_message": "x"}, tmp_path)
    assert r.returncode == 0
    assert not list(tmp_path.rglob("*evil*"))


# ------------------------------------------------------------- state helpers

def test_tail_lines_handles_files_shorter_than_the_window(tmp_path, monkeypatch):
    """seek(-N, SEEK_END) would raise OSError 22 here; absolute seek must not."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    rs = _load("reflex_state.py")
    p = tmp_path / "tiny.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    assert rs.tail_lines(p, max_bytes=131072) == ["a", "b"]
    assert rs.tail_lines(tmp_path / "missing.jsonl") == []


def test_tail_lines_truncates_from_the_end(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    rs = _load("reflex_state.py")
    p = tmp_path / "big.jsonl"
    p.write_text("".join("line%d\n" % i for i in range(5000)), encoding="utf-8")
    out = rs.tail_lines(p, max_lines=10, max_bytes=2048)
    assert len(out) == 10
    assert out[-1] == "line4999"


def test_write_json_atomic_round_trips_korean(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    rs = _load("reflex_state.py")
    p = tmp_path / "x.json"
    assert rs.write_json_atomic(p, {"q": KOREAN}) is True
    assert rs.read_json(p)["q"] == KOREAN
    assert not (tmp_path / "x.json.tmp").exists()


def test_append_jsonl_refuses_past_the_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    rs = _load("reflex_state.py")
    p = tmp_path / "full.jsonl"
    p.write_text("x" * (rs._LEDGER_MAX_BYTES + 1), encoding="utf-8")
    assert rs.append_jsonl(p, {"a": 1}) is False
    assert "ledger full" in (tmp_path / "reflex.log").read_text(encoding="utf-8")


def test_non_ascii_response_still_records_a_turn(tmp_path):
    """THE regression test for the bug that shipped green.

    Claude Code writes the hook payload as raw UTF-8; Python on Windows decodes a
    pipe with the ambient codepage (cp949) under surrogateescape, which does not
    raise -- so json.loads returned mojibake carrying lone surrogates and the
    next .encode("utf-8") blew up. Both the ledger line AND the prefetch spawn
    were lost, silently, exit 0. An em dash was enough, and Claude writes those
    constantly. Fails on the pre-fix code for every case except "ascii".
    """
    for name, msg in (("ascii", "plain"), ("emdash", "we chose X — it worked"),
                      ("korean", "결제 오류"), ("emoji", "shipped 🚀"),
                      ("curly", "the “right” call")):
        home = tmp_path / name
        home.mkdir(parents=True, exist_ok=True)
        r = _run_hook({"hook_event_name": "Stop", "session_id": "s1",
                       "prompt_id": "p1", "last_assistant_message": msg}, home)
        assert r.returncode == 0, (name, r.stderr)
        rows = _ledger(home, "s1")
        assert len(rows) == 1, "%s produced no ledger row" % name
        assert rows[0]["resp_len"] == len(msg.strip()), name
        assert len(rows[0]["resp_sha12"]) == 12, name


def test_ledger_failure_cannot_cancel_the_prefetch_spawn(tmp_path, monkeypatch):
    """The spawn is load-bearing; the ledger is bookkeeping. They used to share
    one try/except, so one bad byte cost both."""
    mod = _load("reflex-observe.py")
    calls = []
    monkeypatch.setattr(mod, "_spawn_prefetch", lambda p, s: calls.append(s))
    monkeypatch.setattr(mod, "_on_stop", lambda p, s: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.setattr(mod.sys, "stdin", type("F", (), {
        "buffer": type("B", (), {"read": staticmethod(lambda: json.dumps(
            {"hook_event_name": "Stop", "session_id": "s1",
             "last_assistant_message": "x"}).encode("utf-8"))})()})())
    assert mod.main() == 0
    assert calls == ["s1"], "prefetch spawn was cancelled by a ledger failure"


def test_conf_reads_the_launcher_snapshot_and_env_wins(tmp_path, monkeypatch):
    """The .mcp.json env block never reaches a hook process, so every knob
    declared there used to be a control that silently did nothing. The launcher
    snapshots resolved values where hooks can read them; a real process env var
    still wins so ~/.claude/settings.json takes effect without a server restart."""
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.delenv("KUMIHO_REFLEX_TTL_S", raising=False)
    rs = _load("reflex_state.py")
    (tmp_path / "reflex.config.json").write_text(
        json.dumps({"KUMIHO_REFLEX_TTL_S": "120", "KUMIHO_REFLEX": "0"}),
        encoding="utf-8")
    assert rs.conf_int("KUMIHO_REFLEX_TTL_S", 900) == 120
    assert rs.gate("KUMIHO_REFLEX") is False          # snapshot disables
    assert rs.conf_int("KUMIHO_REFLEX_MISSING", 7) == 7  # falls back to default

    rs._CONF_CACHE.clear()
    monkeypatch.setenv("KUMIHO_REFLEX_TTL_S", "45")
    assert rs.conf_int("KUMIHO_REFLEX_TTL_S", 900) == 45  # env beats snapshot


def test_conf_survives_a_missing_or_corrupt_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.delenv("KUMIHO_REFLEX_TTL_S", raising=False)
    rs = _load("reflex_state.py")
    assert rs.conf_int("KUMIHO_REFLEX_TTL_S", 900) == 900   # no file at all
    rs._CONF_CACHE.clear()
    (tmp_path / "reflex.config.json").write_text("{not json", encoding="utf-8")
    assert rs.conf_int("KUMIHO_REFLEX_TTL_S", 900) == 900   # corrupt -> default


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
