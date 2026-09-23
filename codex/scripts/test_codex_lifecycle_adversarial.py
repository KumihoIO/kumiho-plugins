"""Adversarial local fixtures for the Codex host-owned memory lifecycle.

No test reads a real transcript, contacts a backend, or writes a real memory.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


MODULE = Path(__file__).with_name("codex_lifecycle.py")
spec = importlib.util.spec_from_file_location("codex_lifecycle_adversarial_subject", MODULE)
assert spec and spec.loader
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class Backend:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, dict(args)))
        response = self.responses.get(name, {})
        if isinstance(response, Exception):
            raise response
        return response


def event(name, *, session="thread-a", transcript="thread-a.jsonl", turn="turn-1", **extra):
    return {"hook_event_name": name, "session_id": session,
            "transcript_path": transcript, "turn_id": turn, **extra}


def prompt(backend, state, *, session="thread-a", transcript="thread-a.jsonl", turn="turn-1", text="Please help with the code"):
    return lifecycle.dispatch(event("UserPromptSubmit", session=session,
                                    transcript=transcript, turn=turn, prompt=text), backend, state)


def reflect_result(*, refs=None, stored=None, buffered=True, rows=None):
    refs = [] if refs is None else refs
    result = {"buffered": buffered, "captures_stored": len(refs) if stored is None else stored,
              "stored_krefs": refs, "created_bucket": False, "session_id": "thread-a", "session_id_source": "codex-thread-meta"}
    if rows is not None:
        result["capture_results"] = rows
    return result


def reflect_event(*, turn="turn-1", response=None, captures=None, **identity):
    return event("PostToolUse", turn=turn,
                 tool_name="mcp__kumiho__kumiho_memory_reflect",
                 tool_input={"captures_present": True, "captures_count": len([] if captures is None else captures)},
                 tool_response={} if response is None else response, **identity)


def test_sensitive_prompt_is_not_automatically_sent_or_persisted(tmp_path):
    backend = Backend()
    secrets = (
        'password="EXAMPLE_PASSWORD_VALUE_123456"',
        'api_key="sk-FAKE_TEST_VALUE_123456789012345"',
        'https://alice:FAKE_PASSWORD_VALUE@example.invalid/path',
        '```env\nTOKEN="EXAMPLE_TOKEN_VALUE_123456"\n```',
    )
    for index, secret in enumerate(secrets):
        prompt(backend, tmp_path, turn=f"secret-{index}", text=f"Please investigate {secret}")
    # Fenced content may be removed while harmless surrounding text is queried.
    assert len(backend.calls) <= 1
    wire = json.dumps(backend.calls)
    for marker in ("EXAMPLE_PASSWORD_VALUE", "FAKE_TEST_VALUE", "EXAMPLE_TOKEN_VALUE"):
        assert marker not in wire
    saved = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert all(secret not in saved for secret in secrets)


def test_off_and_private_turns_have_no_backend_calls_or_stop_continuation(tmp_path, monkeypatch):
    backend = Backend()
    monkeypatch.setenv("KUMIHO_MEMORY_OFF", "1")
    assert "recall=skipped; result=private" in prompt(backend, tmp_path)["hookSpecificOutput"]["additionalContext"]
    assert lifecycle.dispatch(event("Stop"), backend, tmp_path) == {}
    monkeypatch.delenv("KUMIHO_MEMORY_OFF")
    assert "recall=skipped; result=private" in prompt(backend, tmp_path, turn="private", text="Off record: tell me a joke")["hookSpecificOutput"]["additionalContext"]
    assert lifecycle.dispatch(event("Stop", turn="private"), backend, tmp_path) == {}
    assert backend.calls == []


def test_missing_isolated_identity_never_calls_backend(tmp_path):
    backend = Backend()
    for missing in ("session_id", "turn_id", "transcript_path"):
        row = event("UserPromptSubmit", prompt="remember this")
        row.pop(missing)
        lifecycle.dispatch(row, backend, tmp_path)
    assert backend.calls == []
    assert list(tmp_path.glob("*.json")) == []


def test_recall_empty_is_distinct_from_error(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    empty = prompt(backend, tmp_path, turn="empty")
    assert "no matching memories" in empty["hookSpecificOutput"]["additionalContext"].lower()
    backend.responses["kumiho_memory_engage"] = {"error": "offline"}
    failed = prompt(backend, tmp_path, turn="failed")
    assert "KUMIHO_LIFECYCLE_RECEIPT: recall=failed; result=error" in failed["hookSpecificOutput"]["additionalContext"]
    assert "no matching memories" not in json.dumps(failed).lower()




def test_malformed_engage_response_is_not_reported_as_valid_empty(tmp_path):
    backend = Backend({"kumiho_memory_engage": {}})
    result = prompt(backend, tmp_path)
    assert "KUMIHO_LIFECYCLE_RECEIPT: recall=failed; result=error" in result["hookSpecificOutput"]["additionalContext"]


def test_repeated_same_prompt_event_does_not_duplicate_engage(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    prompt(backend, tmp_path)
    assert [name for name, _ in backend.calls].count("kumiho_memory_engage") == 1


def test_wrong_session_reflect_result_does_not_count_as_saved(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    lifecycle.dispatch(reflect_event(response={**reflect_result(),
                  "session_id": "another-session"}), backend, tmp_path)
    stopped = lifecycle.dispatch(event("Stop"), backend, tmp_path)
    assert stopped.get("decision") == "block"


def test_arbitrary_generation_suffix_does_not_satisfy_turn(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    lifecycle.dispatch(reflect_event(response={**reflect_result(),
                  "session_id": "thread-a:cx"}), backend, tmp_path)
    assert lifecycle.dispatch(event("Stop"), backend, tmp_path).get("decision") == "block"


def test_foreign_turn_tool_result_cannot_satisfy_current_turn(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path, turn="current")
    lifecycle.dispatch(reflect_event(turn="foreign", response=reflect_result()),
                       backend, tmp_path)
    stopped = lifecycle.dispatch(event("Stop", turn="current"), backend, tmp_path)
    assert stopped.get("decision") == "block"


def test_success_claim_with_failed_store_result_does_not_reset_consolidation(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    for number in range(1, 21):
        turn = f"turn-{number}"
        prompt(backend, tmp_path, turn=turn)
        lifecycle.dispatch(reflect_event(turn=turn, response=reflect_result()), backend, tmp_path)
        if number == 20:
            lifecycle.dispatch(event("PostToolUse", turn=turn,
                tool_name="mcp__kumiho__kumiho_memory_consolidate",
                tool_input={"summary_present": True},
                tool_response={"success": True, "store_result": {"error": "write failed"}}),
                backend, tmp_path)
        lifecycle.dispatch(event("Stop", turn=turn), backend, tmp_path)
    state_files = list(tmp_path.glob("*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["count"] == 20
    assert state["watermark"] < 20


def test_multi_file_patch_cannot_bypass_first_edit_guard(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name in ("a.py", "b.py"):
        (repo / name).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "initial"], cwd=repo, check=True)
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0},
                       "kumiho_code_why": {"decisions": [], "context": ""}})
    state = tmp_path / "state"
    prompt(backend, state)
    patch = ("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n"
             "*** Update File: b.py\n@@\n-x = 1\n+x = 2\n*** End Patch")
    verdict = lifecycle.dispatch(event("PreToolUse", cwd=str(repo), tool_name="apply_patch",
                                       tool_input={"command": patch}), backend, state)
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 2

def test_reflect_false_positive_and_partial_result_remain_pending(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    captures = [{"type": "fact", "title": "fixture", "content": "value"}]
    for index, response in enumerate((
        {"success": True, "message": "saved"},
        reflect_result(refs=[], stored=0),
        reflect_result(refs=["kref://project/space/item?r=1"], stored=1,
                       rows=[{"error": "partial failure"}]),
        {"isError": True, "content": [{"type": "text", "text": '{"captures_stored":1}'}]},
    )):
        turn = f"failed-reflect-{index}"
        prompt(backend, tmp_path, turn=turn)
        lifecycle.dispatch(reflect_event(turn=turn, captures=captures, response=response), backend, tmp_path)
        stopped = lifecycle.dispatch(event("Stop", turn=turn, last_assistant_message="done"), backend, tmp_path)
        assert stopped.get("decision") == "block", (index, stopped)


def test_reflect_without_explicit_captures_cannot_satisfy_turn(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    lifecycle.dispatch(event("PostToolUse",
        tool_name="mcp__kumiho__kumiho_memory_reflect",
        tool_input={"response": "done"},
        tool_response=reflect_result()), backend, tmp_path)
    stopped = lifecycle.dispatch(event("Stop"), backend, tmp_path)
    assert stopped.get("decision") == "block"


def test_new_host_bucket_is_valid_when_session_and_receipts_match(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    first_bucket = {**reflect_result(), "created_bucket": True}
    lifecycle.dispatch(reflect_event(response=first_bucket), backend, tmp_path)
    assert lifecycle.dispatch(event("Stop"), backend, tmp_path) == {}


def test_verified_empty_capture_reflect_and_duplicate_stop_count_once(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    lifecycle.dispatch(reflect_event(response=reflect_result()), backend, tmp_path)
    assert lifecycle.dispatch(event("Stop", last_assistant_message="done"), backend, tmp_path) == {}
    assert lifecycle.dispatch(event("Stop", last_assistant_message="done"), backend, tmp_path) == {}
    for number in range(2, 20):
        turn = f"turn-{number}"
        prompt(backend, tmp_path, turn=turn)
        lifecycle.dispatch(reflect_event(turn=turn, response=reflect_result()), backend, tmp_path)
        assert lifecycle.dispatch(event("Stop", turn=turn), backend, tmp_path) == {}
    prompt(backend, tmp_path, turn="turn-20")
    lifecycle.dispatch(reflect_event(turn="turn-20", response=reflect_result()), backend, tmp_path)
    twentieth = lifecycle.dispatch(event("Stop", turn="turn-20"), backend, tmp_path)
    assert twentieth.get("decision") == "block"
    assert "consolidate" in twentieth.get("reason", "").lower()


def test_own_stop_continuation_is_bounded_and_does_not_start_new_turn(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    first = lifecycle.dispatch(event("Stop", last_assistant_message="done"), backend, tmp_path)
    assert first.get("decision") == "block"
    second = lifecycle.dispatch(event("Stop", stop_hook_active=True,
                                      last_assistant_message="done"), backend, tmp_path)
    assert second.get("decision") != "block"
    third = lifecycle.dispatch(event("Stop", stop_hook_active=True,
                                     last_assistant_message="done"), backend, tmp_path)
    assert third.get("decision") != "block"


def test_thread_and_transcript_identity_do_not_share_receipts(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    for session, transcript in (("thread-a", "parent.jsonl"),
                                ("thread-b", "parent.jsonl"),
                                ("thread-a", "subagent.jsonl")):
        prompt(backend, tmp_path, session=session, transcript=transcript)
        stopped = lifecycle.dispatch(event("Stop", session=session,
                                          transcript=transcript), backend, tmp_path)
        assert stopped.get("decision") == "block"


def test_successful_keyless_consolidation_resets_only_its_completed_floor(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    for number in range(1, 21):
        turn = f"turn-{number}"
        prompt(backend, tmp_path, turn=turn)
        lifecycle.dispatch(reflect_event(turn=turn, response=reflect_result()), backend, tmp_path)
        if number == 20:
            lifecycle.dispatch(event("PostToolUse", turn=turn,
                tool_name="mcp__kumiho__kumiho_memory_consolidate",
                tool_input={"summary_present": True},
                tool_response={"success": True, "session_id": "thread-a",
                               "session_id_source": "codex-thread-meta",
                               "store_result": {"revision_kref": "kref://project/space/item?r=20"}}),
                backend, tmp_path)
        assert lifecycle.dispatch(event("Stop", turn=turn), backend, tmp_path) == {}
    state_path, = tmp_path.glob("*.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["count"] == state["watermark"] == 20
    prompt(backend, tmp_path, turn="turn-21")
    lifecycle.dispatch(reflect_event(turn="turn-21", response={**reflect_result(), "session_id": "thread-a:c1", "created_bucket": True}), backend, tmp_path)
    assert lifecycle.dispatch(event("Stop", turn="turn-21"), backend, tmp_path) == {}


def test_continuation_alias_does_not_count_as_new_user_turn(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path, turn="original")
    first = lifecycle.dispatch(event("Stop", turn="original"), backend, tmp_path)
    assert first.get("decision") == "block"
    assert lifecycle.dispatch(event("UserPromptSubmit", turn="continuation",
                                    prompt=first["reason"]), backend, tmp_path) == {}
    lifecycle.dispatch(reflect_event(turn="continuation",
                                    response=reflect_result()), backend, tmp_path)
    assert lifecycle.dispatch(event("Stop", turn="continuation",
                                    stop_hook_active=True), backend, tmp_path) == {}
    state_path, = tmp_path.glob("*.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["count"] == 1
    assert len(state["turns"]) == 1
    assert [name for name, _ in backend.calls].count("kumiho_memory_engage") == 1


def test_corrupt_receipt_file_never_resets_counters_or_replays_calls(tmp_path):
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0}})
    prompt(backend, tmp_path)
    state_path, = tmp_path.glob("*.json")
    state_path.write_text("{corrupt", encoding="utf-8")
    calls_before = len(backend.calls)
    verdict = lifecycle.dispatch(event("Stop"), backend, tmp_path)
    assert "unreadable" in verdict.get("systemMessage", "").lower()
    assert state_path.read_text(encoding="utf-8") == "{corrupt"
    assert len(backend.calls) == calls_before


def test_two_mcp_processes_preserve_distinct_completed_turns(tmp_path):
    worker = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("subject", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class Backend:
    def call(self, name, args):
        return {"context": "", "results": [], "count": 0}
turn = sys.argv[3]
base = {"session_id": "same-thread", "transcript_path": "same-transcript.jsonl", "turn_id": turn}
root = Path(sys.argv[2])
mod.dispatch({**base, "hook_event_name": "UserPromptSubmit", "prompt": "Work on this fixture"}, Backend(), root)
mod.dispatch({**base, "hook_event_name": "PostToolUse",
    "tool_name": "mcp__kumiho__kumiho_memory_reflect",
    "tool_input": {"captures_present": True, "captures_count": 0},
    "tool_response": {"buffered": True, "created_bucket": False,
        "captures_stored": 0, "stored_krefs": [], "session_id": "same-thread",
        "session_id_source": "codex-thread-meta"}}, Backend(), root)
mod.dispatch({**base, "hook_event_name": "Stop"}, Backend(), root)
"""
    processes = [subprocess.Popen([sys.executable, "-c", worker, str(MODULE),
                                   str(tmp_path), f"turn-{number}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for number in (1, 2)]
    for process in processes:
        _out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err.decode("utf-8", "replace")
    state_files = list(tmp_path.glob("*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["count"] == 2
    assert state["turns"]["turn-1"]["counted"] is True
    assert state["turns"]["turn-2"]["counted"] is True


def test_code_why_receipt_is_file_head_and_content_specific(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    file = repo / "demo.py"
    file.write_text("print('one')\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "initial"], cwd=repo, check=True)
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0},
                       "kumiho_code_why": {"decisions": [], "context": ""}})
    state = tmp_path / "state"
    prompt(backend, state)
    edit = event("PreToolUse", cwd=str(repo), tool_name="apply_patch",
                 tool_input={"command": "*** Begin Patch\n*** Update File: demo.py\n@@\n-print('one')\n+print('two')\n*** End Patch"})
    first = lifecycle.dispatch(edit, backend, state)
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 1
    assert lifecycle.dispatch(edit, backend, state) == {}
    file.write_text("print('changed elsewhere')\n", encoding="utf-8")
    changed = lifecycle.dispatch(edit, backend, state)
    assert changed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 2, changed
    assert lifecycle.dispatch(edit, backend, state) == {}
    subprocess.run(["git", "add", "demo.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "new head"], cwd=repo, check=True)
    head_changed = lifecycle.dispatch(edit, backend, state)
    assert head_changed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 3


def test_code_why_transport_error_does_not_issue_receipt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "initial"], cwd=repo, check=True)
    monkeypatch.setattr(lifecycle, "_repo_snapshot", lambda _event: (repo.resolve(), "f" * 40))
    backend = Backend({"kumiho_memory_engage": {"context": "", "results": [], "count": 0},
                       "kumiho_code_why": RuntimeError("offline")})
    state = tmp_path / "state"
    prompt(backend, state)
    edit = event("PreToolUse", cwd=str(repo), tool_name="apply_patch",
                 tool_input={"command": "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"})
    for _ in range(2):
        verdict = lifecycle.dispatch(edit, backend, state)
        assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 2


def test_boolean_or_negative_engage_count_is_failure_receipt(tmp_path):
    for turn, count in (("bool", True), ("negative", -1)):
        backend = Backend({"kumiho_memory_engage": {"context": "incorrect", "results": [], "count": count}})
        output = prompt(backend, tmp_path, turn=turn)
        assert output["hookSpecificOutput"]["additionalContext"].startswith(
            "KUMIHO_LIFECYCLE_RECEIPT: recall=failed; result=error")
