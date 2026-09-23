"""Local Node-filter -> Python-dispatch contract; no network memory writes."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("codex_lifecycle_bridge_subject", HERE / "codex_lifecycle.py")
assert SPEC and SPEC.loader
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js unavailable")


class Backend:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "kumiho_memory_engage":
            return {"context": "Project decision context", "results": [], "count": 1}
        if name == "kumiho_code_why":
            return {"decisions": [], "context": ""}
        raise AssertionError(name)


def filtered(event):
    script = (
        "import {sanitizeCodexLifecycleEvent} from './codex/scripts/lifecycle_event_filter.mjs';"
        "let data='';process.stdin.on('data',c=>data+=c);"
        "process.stdin.on('end',()=>process.stdout.write(JSON.stringify(sanitizeCodexLifecycleEvent(JSON.parse(data)))));"
    )
    result = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=HERE.parents[1],
                            input=json.dumps(event), text=True, capture_output=True, check=True, timeout=10)
    clean = json.loads(result.stdout)
    assert clean["_kumiho_filtered"] is True
    assert "prompt" not in clean and "tool_response" not in clean
    return clean


def base(kind, *, turn="turn-1", **extra):
    return {"hook_event_name": kind, "session_id": "thread-a", "turn_id": turn,
            "transcript_path": "C:/codex/transcript/parent.jsonl", "cwd": str(HERE.parents[1]), **extra}


def test_safe_prompt_empty_reflect_and_stop(tmp_path):
    backend = Backend()
    first = filtered(base("UserPromptSubmit", prompt="What was the project decision?"))
    assert first["safe_query"] == "What was the project decision?"
    context = lifecycle.dispatch(first, backend, tmp_path)
    assert "Project decision context" in context["hookSpecificOutput"]["additionalContext"]
    assert [name for name, _ in backend.calls] == ["kumiho_memory_engage"]

    raw = base("PostToolUse", tool_name="mcp__kumiho_memory__kumiho_memory_reflect",
               tool_input={"response": "Private final answer", "captures": []},
               tool_response={"buffered": True, "created_bucket": False, "captures_stored": 0,
                              "stored_krefs": [], "session_id": "thread-a",
                              "session_id_source": "codex-thread-meta"})
    clean = filtered(raw)
    assert clean["tool_input"] == {"captures_present": True, "captures_count": 0, "summary_present": False}
    assert "Private final answer" not in json.dumps(clean)
    assert lifecycle.dispatch(clean, backend, tmp_path) == {}
    assert lifecycle.dispatch(filtered(base("Stop", stop_hook_active=False)), backend, tmp_path) == {}


def test_first_edit_defers_then_valid_receipt_allows(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "demo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "initial"], cwd=repo, check=True)
    backend = Backend()
    lifecycle.dispatch(filtered(base("UserPromptSubmit", prompt="Explain code choices")), backend, tmp_path / "state")
    patch = "*** Begin Patch\n*** Update File: demo.py\n@@\n-x = 1\n+x = 2\n*** End Patch"
    event = base("PreToolUse", cwd=str(repo), tool_name="apply_patch", tool_input={"command": patch})
    clean = filtered(event)
    assert clean["edit_paths"] == ["demo.py"]
    assert patch not in json.dumps(clean)
    first = lifecycle.dispatch(clean, backend, tmp_path / "state")
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert lifecycle.dispatch(clean, backend, tmp_path / "state") == {}
    assert [name for name, _ in backend.calls].count("kumiho_code_why") == 1


def test_continuation_new_turn_skips_recall_and_resolves_original(tmp_path):
    backend = Backend()
    state = tmp_path / "state"
    lifecycle.dispatch(filtered(base("UserPromptSubmit", prompt="Recall project plan")), backend, state)
    first_stop = lifecycle.dispatch(filtered(base("Stop", stop_hook_active=False)), backend, state)
    assert first_stop["decision"] == "block"
    continuation = filtered(base("UserPromptSubmit", turn="turn-2", prompt=first_stop["reason"]))
    assert lifecycle.dispatch(continuation, backend, state) == {}
    assert [name for name, _ in backend.calls].count("kumiho_memory_engage") == 1
    receipt = filtered(base("PostToolUse", turn="turn-2",
        tool_name="mcp__kumiho_memory__kumiho_memory_reflect",
        tool_input={"response": "Done", "captures": []},
        tool_response={"buffered": True, "created_bucket": False, "captures_stored": 0,
                       "stored_krefs": [], "session_id": "thread-a",
                       "session_id_source": "codex-thread-meta"}))
    lifecycle.dispatch(receipt, backend, state)
    assert lifecycle.dispatch(filtered(base("Stop", turn="turn-2", stop_hook_active=True)),
                              backend, state) == {}
    saved = list(state.glob("*.json"))
    assert len(saved) == 1
    body = json.loads(saved[0].read_text(encoding="utf-8"))
    assert body["count"] == 1


def test_malformed_and_nine_file_patches_deny_at_production_entry(monkeypatch):
    def unexpected_dispatch(_event):
        pytest.fail("degraded edit reached lifecycle backend")
    monkeypatch.setattr(lifecycle, "dispatch", unexpected_dispatch)
    malformed = filtered(base("PreToolUse", tool_name="apply_patch",
                              tool_input={"command": "*** Begin Patch\nnot a file header\n*** End Patch"}))
    assert malformed["event_degraded"] is True
    paths = "".join(f"*** Update File: file-{number}.py\n@@\n-x\n+y\n"
                    for number in range(9))
    over_limit = filtered(base("PreToolUse", tool_name="apply_patch",
                               tool_input={"command": "*** Begin Patch\n" + paths + "*** End Patch"}))
    assert over_limit["event_degraded"] is True
    for clean in (malformed, over_limit):
        result = lifecycle.tool_codex_lifecycle({"event": clean})
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
