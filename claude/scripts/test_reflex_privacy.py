#!/usr/bin/env python3
"""Tests for per-prompt privacy classification and its session-mining gate.

Run: python -m pytest claude/scripts/test_reflex_privacy.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from reflex_privacy import classify  # noqa: E402

SHARED_CASES = json.loads((SCRIPTS.parents[1] / "tests/fixtures/lifecycle-privacy-cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SHARED_CASES, ids=lambda case: case["id"])
def test_shared_codex_claude_privacy_cases(case):
    assert classify(case["prompt"]) == case["expected"]


@pytest.mark.parametrize("prompt", [
    "Off-record: recall the project decision",
    "off the record, what do you think?",
    "Do not recall; do not store",
    "이건 오프 더 레코드인데",
    "이건 기억하지 말고 그냥 답해",
    "이건 비공개야.",
    "비공개로 해줘",
    "Recall password=fixture-secret-value",
    "배포 봐줘 비밀번호: hunter2",
    "deploy with token=ghp_abcdefghijklmnopqrstu",
    pytest.param("x" * 300000, id="oversized"),
])
def test_private(prompt):
    assert classify(prompt) == "private"


@pytest.mark.parametrize("prompt", [
    "Do not save or change any memory; only recall the project decision.",
    "Don't store this; recall the decision",
    "Only recall, no saving",
    "Only recall the decision; don't write to memory",
    "이건 저장하지 말고 알려줘",
    "메모리에 쓰지 마",
    "기억에 저장하지 마",
])
def test_no_write(prompt):
    assert classify(prompt) == "no_write"


@pytest.mark.parametrize("prompt", [
    "why did we pick nano_banana_2?",
    "비공개 저장소 하나 만들어줘",
    "이거 비공개 레포로 만들어",
    "파일 저장하지 마",
    "the record label changed",
    "turn off recording in OBS",
    "",
])
def test_ordinary(prompt):
    assert classify(prompt) == ""


# ------------------------------------------------------ session-mining gate

def _transcript(tmp_path: Path, *user_texts: str) -> Path:
    path = tmp_path / "t.jsonl"
    rows = []
    for text in user_texts:
        rows.append({"message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        rows.append({"message": {"role": "assistant", "content": "ok"}})
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _run_mine(tmp_path, monkeypatch, transcript: Path) -> tuple:
    import session_mine_worker as smw

    runtime = []

    class Launcher:
        def _hydrate_env_from_local_config(self): pass
        def _sanitize_placeholder_env_vars(self): pass
        def _state_dir(self): return tmp_path / "state"
        def _ce_mode_enabled(self): return False
        def _kumiho_home(self): return tmp_path
        def _configure_llm_fallback(self): pass

        def _ensure_runtime(self):
            runtime.append(True)
            raise RuntimeError("stop before any real mining")

    monkeypatch.setattr(smw, "_load_launcher", lambda: Launcher())
    monkeypatch.setattr(smw, "_automine_enabled", lambda: True)
    monkeypatch.setattr(smw.bounded_proc, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="true\n", stderr=""))
    monkeypatch.delenv("KUMIHO_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["w", str(tmp_path), "sess1", str(transcript)])
    assert smw.main() == 0
    log = (tmp_path / "state" / "session-mine.log").read_text(encoding="utf-8")
    return runtime, log


def test_off_record_session_is_never_mined(tmp_path, monkeypatch):
    t = _transcript(tmp_path, "fix the cache bug", "off the record: 연봉 협상 얘기인데", "thanks")
    runtime, log = _run_mine(tmp_path, monkeypatch, t)
    assert runtime == []
    assert "off-record or credential turn" in log


def test_ordinary_session_still_reaches_mining(tmp_path, monkeypatch):
    t = _transcript(tmp_path, "fix the cache bug", "비공개 저장소로 옮겨줘")
    runtime, log = _run_mine(tmp_path, monkeypatch, t)
    assert runtime == [True]
    assert "off-record" not in log


# ------------------------------------------------------------------ backfill

def test_backfill_pattern_is_pinned_to_the_live_classifier():
    """backfill_inventory inlines the pattern (it runs pre-install and ships
    vendored into Codex); the two copies must never drift apart."""
    import backfill_inventory as inv
    from reflex_privacy import PRIVATE_RE
    assert inv._OFF_RECORD_RE.pattern == PRIVATE_RE.pattern
    assert inv._OFF_RECORD_RE.flags == PRIVATE_RE.flags


def test_backfill_drops_off_record_turns_and_their_replies(tmp_path):
    import backfill_inventory as inv

    def rec(role, ts, text):
        return {"type": role, "timestamp": ts, "sessionId": "s-priv",
                "cwd": "/home/u/proj", "userType": "external", "entrypoint": "cli",
                "message": {"role": role, "content": text}}

    path = tmp_path / "s-priv.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (
        rec("user", "2026-09-01T10:00:00Z", "Which cache should we use?"),
        rec("assistant", "2026-09-01T10:01:00Z", "We picked Redis for the queue."),
        rec("user", "2026-09-01T10:02:00Z", "off the record: 연봉 협상은 7천으로 하려고"),
        rec("assistant", "2026-09-01T10:03:00Z", "7천이면 적정 범위입니다."),
        rec("assistant", "2026-09-01T10:03:30Z", "협상 팁도 드릴게요."),
        rec("user", "2026-09-01T10:04:00Z", "ok back to the cache"),
        rec("assistant", "2026-09-01T10:05:00Z", "Redis it is."),
    )), encoding="utf-8")
    meta = inv.parse_claude_session(path)
    text = " ".join(t for _, _, t in meta["messages"])
    assert "연봉" not in text and "7천" not in text and "협상" not in text
    assert "Redis for the queue" in text and "Redis it is" in text
    assert meta["human_msgs"] == 2
    assert "7천" not in inv.build_packet(meta)


def test_backfill_session_that_is_entirely_off_record_is_skipped(tmp_path):
    import backfill_inventory as inv
    msgs = [("t1", "user", "이건 오프 더 레코드인데"), ("t2", "assistant", "네")]
    assert inv._drop_off_record_turns(msgs) == []


# ------------------------------------------------------- SessionEnd artifact

def _load_artifact_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "save_session_artifact", SCRIPTS / "save-session-artifact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_session_artifact_omits_off_record_exchanges(tmp_path, monkeypatch):
    mod = _load_artifact_module()
    transcript = tmp_path / "t.jsonl"
    rows = [
        ("user", "Which cache should we use?"),
        ("assistant", "We picked Redis for the queue."),
        ("user", "이건 기억하지 말고, 연봉 7천 괜찮을까?"),
        ("assistant", "7천이면 적정 범위입니다."),
        ("user", "deploy with password=hunter2-secret"),
        ("assistant", "Configured with hunter2-secret."),
        ("user", "ok back to the cache"),
        ("assistant", "Redis it is."),
    ]
    transcript.write_text("".join(json.dumps({"message": {"role": r, "content": t}},
                                              ensure_ascii=False) + "\n" for r, t in rows),
                          encoding="utf-8")
    out = tmp_path / "artifacts"
    monkeypatch.setattr(mod, "_artifact_dir", lambda: out)
    monkeypatch.setattr(mod, "_read_hook_input", lambda: {
        "session_id": "sess-art", "transcript_path": str(transcript)})
    assert mod.main() == 0
    md = next(out.rglob("sess-art.md")).read_text(encoding="utf-8")
    for leaked in ("연봉", "7천", "hunter2"):
        assert leaked not in md
    assert md.count("turn omitted") == 2
    assert "Redis for the queue" in md and "Redis it is" in md
