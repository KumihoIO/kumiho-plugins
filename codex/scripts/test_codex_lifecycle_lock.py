"""Bounded state lock contention without wall-clock sleeping."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("codex_lifecycle_lock_subject", HERE / "codex_lifecycle.py")
assert spec and spec.loader
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class FakeLock:
    def __init__(self, outcome):
        self.outcome = outcome
        self.timeout = None
        self.released = False

    def acquire(self, *, timeout):
        self.timeout = timeout
        return self.outcome

    def release(self):
        self.released = True

    def locked(self):
        return False


def test_local_thread_contention_has_same_twelve_second_budget(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    local = FakeLock(False)
    monkeypatch.setitem(lifecycle._SESSION_LOCKS, str(target), local)
    ticks = iter((100.0, 100.0))
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="thread lock"):
        with lifecycle._state_lock(target):
            pytest.fail("blocked lock entered")
    assert local.timeout == 12.0
    assert not local.released
    assert not (tmp_path / "state.json.lock").exists()


def test_file_lock_uses_time_left_after_local_wait(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    local = FakeLock(True)
    monkeypatch.setitem(lifecycle._SESSION_LOCKS, str(target), local)
    ticks = iter((100.0, 111.5, 112.1))
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="file lock"):
        with lifecycle._state_lock(target):
            pytest.fail("expired file lock entered")
    assert local.timeout == 0.5
    assert local.released


def test_public_tool_reports_busy_state_without_claiming_receipt(monkeypatch):
    def busy(_event):
        raise TimeoutError("busy")
    monkeypatch.setattr(lifecycle, "dispatch", busy)
    result = lifecycle.tool_codex_lifecycle({"event": {
        "_kumiho_filtered": True,
        "hook_event_name": "Stop",
        "tool_input": {},
    }})
    assert "busy" in result["systemMessage"]
    assert "receipt" in result["systemMessage"]


def test_busy_state_denies_covered_edit(monkeypatch):
    def busy(_event):
        raise TimeoutError("busy")
    monkeypatch.setattr(lifecycle, "dispatch", busy)
    result = lifecycle.tool_codex_lifecycle({"event": {
        "_kumiho_filtered": True,
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "session_id": "thread-a",
        "turn_id": "turn-a",
        "transcript_path": "C:/codex/transcript.jsonl",
        "cwd": "C:/repo",
        "edit_paths": ["src/example.py"],
        "tool_input": {},
    }})
    assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "without a receipt" in result["hookSpecificOutput"]["permissionDecisionReason"]
