#!/usr/bin/env python3
"""Tests for the UserPromptSubmit injection hook.

These pin the parts that fail SILENTLY in production:
  - the envelope shape (a drifted key is stripped with a warning, so the feature
    would look wired and inject nothing);
  - cross-turn dedup (additionalContext persists in message history, so a
    re-emitted block accrues duplicate tokens every turn);
  - the floor's off-by-one (a floor of 1 fires on literally every turn);
  - the no-poll decision (a missing cache must return fast, not wait).

Run: python -m pytest claude/scripts/test_memory_reflex.py -q
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
KOREAN_MEM = "지난주에 nano_banana_2 가격 정책을 이미지 크기별로 바꾸기로 결정했습니다"


def _run(payload: dict, home: Path, args=(), env_extra: dict | None = None):
    # No PYTHONIOENCODING, raw UTF-8 bytes -- production conditions. See the
    # note in test_reflex_observe._run_hook.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["KUMIHO_CLAUDE_HOME"] = str(home)
    env.update(env_extra or {})
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "memory-reflex.py"), *args],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=env, timeout=30,
    )
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


def _seed_cache(home: Path, sid: str, block: str, sha: str = "deadbeef1234",
                age_s: int = 0) -> None:
    d = home / "reflex"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.recall.json" % sid)).write_text(json.dumps({
        "generated_at": int(time.time()) - age_s, "query": "q", "block": block,
        "content_sha12": sha, "count": 1, "krefs": ["kref://x"],
    }, ensure_ascii=True), encoding="utf-8")


def _ledger(home: Path, sid: str, rows: list) -> None:
    d = home / "reflex"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.turns.jsonl" % sid)).write_text(
        "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in rows), encoding="utf-8")


def _ups(sid: str, **kw) -> dict:
    base = {"hook_event_name": "UserPromptSubmit", "session_id": sid,
            "prompt": "why did we pick nano_banana_2?", "prompt_id": "p1"}
    base.update(kw)
    return base


def test_envelope_is_exact(tmp_path):
    """The single most important test: a drifted envelope is silently ignored."""
    _seed_cache(tmp_path, "aaaa", "<kumiho_memory>MARK-7</kumiho_memory>")
    r = _run(_ups("aaaa", cwd=str(tmp_path)), tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert set(d.keys()) == {"hookSpecificOutput"}
    assert set(d["hookSpecificOutput"].keys()) == {"hookEventName", "additionalContext"}
    assert d["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "MARK-7" in d["hookSpecificOutput"]["additionalContext"]


def test_second_identical_turn_emits_no_memory_block(tmp_path):
    """Dedup regression test. additionalContext persists in history."""
    _seed_cache(tmp_path, "bbbb", "<kumiho_memory>MARK-7</kumiho_memory>")
    r1 = _run(_ups("bbbb"), tmp_path)
    assert "MARK-7" in r1.stdout
    r2 = _run(_ups("bbbb"), tmp_path)
    assert "MARK-7" not in r2.stdout


def test_a_new_sha_is_injected_again(tmp_path):
    _seed_cache(tmp_path, "cccc", "<kumiho_memory>OLD</kumiho_memory>", sha="1111")
    assert "OLD" in _run(_ups("cccc"), tmp_path).stdout
    _seed_cache(tmp_path, "cccc", "<kumiho_memory>NEW</kumiho_memory>", sha="2222")
    assert "NEW" in _run(_ups("cccc"), tmp_path).stdout


def test_stale_cache_is_not_served(tmp_path):
    _seed_cache(tmp_path, "dddd", "<kumiho_memory>STALE</kumiho_memory>", age_s=100000)
    r = _run(_ups("dddd"), tmp_path)
    assert "STALE" not in r.stdout


def test_missing_cache_is_fast_and_silent(tmp_path):
    """Pins the no-poll decision: no waiting on a file that may never appear."""
    t0 = time.time()
    r = _run(_ups("eeee"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""
    assert time.time() - t0 < 5.0


def test_floor_does_not_fire_below_threshold(tmp_path):
    """Off-by-one regression: FLOOR=3 must not fire at 2 unreflected turns."""
    _ledger(tmp_path, "ffff", [{"kind": "stop", "tool_only": False}] * 2)
    r = _run(_ups("ffff"), tmp_path)
    assert "Turns since your last" not in r.stdout


def test_floor_fires_at_threshold(tmp_path):
    _ledger(tmp_path, "gggg", [{"kind": "stop", "tool_only": False}] * 3)
    r = _run(_ups("gggg"), tmp_path)
    assert "Turns since your last kumiho_memory_reflect: 3" in r.stdout
    assert "gggg" in r.stdout  # carries the session_id the model cannot otherwise know


def test_reflect_in_ledger_resets_the_count(tmp_path):
    _ledger(tmp_path, "hhhh", [
        {"kind": "stop", "tool_only": False},
        {"kind": "stop", "tool_only": False},
        {"kind": "tool", "tool": "reflect"},
        {"kind": "stop", "tool_only": False},
    ])
    r = _run(_ups("hhhh"), tmp_path)
    assert "Turns since your last" not in r.stdout


def test_tool_only_turns_do_not_count_toward_the_floor(tmp_path):
    _ledger(tmp_path, "iiii", [{"kind": "stop", "tool_only": True}] * 6)
    r = _run(_ups("iiii"), tmp_path)
    assert "Turns since your last" not in r.stdout


def test_session_budget_stops_injection(tmp_path):
    _seed_cache(tmp_path, "jjjj", "<kumiho_memory>" + ("x" * 500) + "</kumiho_memory>")
    r = _run(_ups("jjjj"), tmp_path, env_extra={"KUMIHO_REFLEX_SESSION_BUDGET_CHARS": "10"})
    assert "kumiho_memory" not in r.stdout


def test_kill_switch_produces_empty_stdout(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "reflex.off").write_text("", encoding="utf-8")
    _seed_cache(tmp_path, "kkkk", "<kumiho_memory>MARK</kumiho_memory>")
    r = _run(_ups("kkkk"), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_env_gate_produces_empty_stdout(tmp_path):
    _seed_cache(tmp_path, "llll", "<kumiho_memory>MARK</kumiho_memory>")
    r = _run(_ups("llll"), tmp_path, env_extra={"KUMIHO_REFLEX": "0"})
    assert r.stdout == ""


def test_korean_memory_survives_byte_identically(tmp_path):
    _seed_cache(tmp_path, "mmmm", "<kumiho_memory>%s</kumiho_memory>" % KOREAN_MEM)
    r = _run(_ups("mmmm"), tmp_path)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert KOREAN_MEM in ctx


def test_subagent_card_has_its_own_event_and_no_parent_memories(tmp_path):
    _seed_cache(tmp_path, "nnnn", "<kumiho_memory>PARENT-ONLY</kumiho_memory>")
    r = _run({"hook_event_name": "SubagentStart", "session_id": "nnnn"},
             tmp_path, args=("--subagent",))
    d = json.loads(r.stdout)
    assert d["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "PARENT-ONLY" not in d["hookSpecificOutput"]["additionalContext"]
    assert "kumiho_memory_reflect" in d["hookSpecificOutput"]["additionalContext"]


def test_empty_and_malformed_stdin_are_silent(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "KUMIHO_CLAUDE_HOME": str(tmp_path)}
    for payload in ("", "not json {{{", "[]", "null"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "memory-reflex.py")],
                           input=payload, capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=30)
        assert r.returncode == 0, payload
        assert r.stdout == ""
        assert "Traceback" not in r.stderr


def test_other_events_are_ignored(tmp_path):
    _seed_cache(tmp_path, "oooo", "<kumiho_memory>MARK</kumiho_memory>")
    r = _run({"hook_event_name": "Stop", "session_id": "oooo"}, tmp_path)
    assert r.stdout == ""


def test_pending_queue_line_uses_a_runnable_absolute_drain_cmd(tmp_path):
    """Regression: this line once read a count.json that nothing ever wrote."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pending-code-captures.jsonl").write_text(
        "".join(json.dumps({"commit": "c%d" % i}) + "\n" for i in range(12)),
        encoding="utf-8")
    r = _run(_ups("qqqq"), tmp_path)
    assert "12 commits are queued" in r.stdout
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    command = ctx.split("To drain: ", 1)[1].split(" list.", 1)[0] + " list"
    argv = shlex.split(command)
    assert Path(argv[0]).is_absolute()
    assert argv[1] == "-I"
    assert Path(argv[2]).is_absolute() and Path(argv[2]).exists()
    assert argv[3:] == ["--claude-host", "list"]


def test_pending_queue_line_silent_below_threshold(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pending-code-captures.jsonl").write_text(
        json.dumps({"commit": "c1"}) + "\n", encoding="utf-8")
    r = _run(_ups("rrrr"), tmp_path)
    assert "queued for keyless" not in r.stdout


def test_stored_prompt_is_capped(tmp_path):
    r = _run(_ups("pcap", prompt="x" * 9000), tmp_path)
    assert r.returncode == 0
    stored = json.loads((tmp_path / "reflex" / "pcap.turn.json")
                        .read_text(encoding="utf-8"))["prompt"]
    assert len(stored) == 2000


def test_prompt_storage_can_be_turned_off(tmp_path):
    """Everywhere else this design stores a hash and a length, not text; the
    prompt is the one exception and it must be refusable."""
    secret = "my api key is sk-abcdefghijklmnop"
    r = _run(_ups("poff", prompt=secret), tmp_path,
             env_extra={"KUMIHO_REFLEX_STORE_PROMPT": "0"})
    assert r.returncode == 0
    raw = (tmp_path / "reflex" / "poff.turn.json").read_text(encoding="utf-8")
    assert secret not in raw
    assert json.loads(raw)["prompt"] == ""


def test_consolidate_floor_is_silent_below_threshold(tmp_path):
    _ledger(tmp_path, "cf01", [{"kind": "stop", "tool_only": False}] * 19)
    r = _run(_ups("cf01"), tmp_path)
    assert "last consolidated" not in r.stdout


def test_consolidate_floor_fires_at_threshold_with_the_keyless_call_shape(tmp_path):
    """Consolidation was an adjective ("after 20+ exchanges") with no counter
    behind it. Now it is a counted fact, and the line carries the one thing the
    model must not get wrong: the summary is ITS job, not an external LLM's."""
    _ledger(tmp_path, "cf02", [{"kind": "stop", "tool_only": False}] * 20)
    r = _run(_ups("cf02"), tmp_path)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "last consolidated: 20 (floor 20)" in ctx
    assert "kumiho_memory_consolidate(session_id=cf02, summary=" in ctx
    assert "subagent" in ctx
    assert "Never call it without summary" in ctx


def test_a_successful_consolidate_in_the_ledger_resets_the_count(tmp_path):
    rows = [{"kind": "stop", "tool_only": False}] * 20
    rows += [{"kind": "tool", "tool": "consolidate", "ok": True}]
    rows += [{"kind": "stop", "tool_only": False}] * 3
    _ledger(tmp_path, "cf03", rows)
    r = _run(_ups("cf03"), tmp_path)
    assert "last consolidated" not in r.stdout


def test_a_failed_consolidate_does_not_reset_the_count(tmp_path):
    """A consolidate that came back success:false left the buffer full; going
    quiet for another 20 turns would be exactly the silent failure the ledger
    exists to surface."""
    rows = [{"kind": "stop", "tool_only": False}] * 20
    rows += [{"kind": "tool", "tool": "consolidate", "ok": False}]
    rows += [{"kind": "stop", "tool_only": False}]
    _ledger(tmp_path, "cf04", rows)
    r = _run(_ups("cf04"), tmp_path)
    assert "last consolidated: 21" in r.stdout


def test_consolidate_floor_honours_a_cooldown_and_can_be_disabled(tmp_path):
    _ledger(tmp_path, "cf05", [{"kind": "stop", "tool_only": False}] * 25)
    assert "last consolidated" in _run(_ups("cf05"), tmp_path).stdout
    assert "last consolidated" not in _run(_ups("cf05"), tmp_path).stdout  # cooldown
    _ledger(tmp_path, "cf06", [{"kind": "stop", "tool_only": False}] * 25)
    r = _run(_ups("cf06"), tmp_path, env_extra={"KUMIHO_REFLEX_CONSOLIDATE_FLOOR": "0"})
    assert "last consolidated" not in r.stdout


def test_consolidate_line_names_the_configured_working_memory_ttl(tmp_path):
    """The idle warning used to hard-code "an hour". The launcher now resolves
    KUMIHO_WORKING_MEMORY_TTL into the reflex snapshot (86400 in CE mode), and
    the line must say what is actually configured."""
    _ledger(tmp_path, "cf07", [{"kind": "stop", "tool_only": False}] * 20)
    assert "expires after an hour idle" in _run(_ups("cf07"), tmp_path).stdout
    _ledger(tmp_path, "cf08", [{"kind": "stop", "tool_only": False}] * 20)
    r = _run(_ups("cf08"), tmp_path, env_extra={"KUMIHO_WORKING_MEMORY_TTL": "86400"})
    assert "expires after 24 hours idle" in r.stdout
    _ledger(tmp_path, "cf09", [{"kind": "stop", "tool_only": False}] * 20)
    r = _run(_ups("cf09"), tmp_path, env_extra={"KUMIHO_WORKING_MEMORY_TTL": "5400"})
    assert "expires after 90 minutes idle" in r.stdout


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
