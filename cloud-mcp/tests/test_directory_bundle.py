"""The hosted directory plugin bundle shared by the Claude and Codex manifests."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOLS

BUNDLE = Path(__file__).resolve().parents[1] / "directory" / "kumiho-memory"
SKILL_DIRS = sorted(path.parent for path in BUNDLE.glob("skills/*/SKILL.md"))
BACKFILL = BUNDLE / "skills" / "kumiho-backfill" / "scripts" / "prepare_backfill.py"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _frontmatter(path):
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path} has no frontmatter"
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def _backfill():
    spec = importlib.util.spec_from_file_location("prepare_backfill", BACKFILL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claude_and_codex_manifests_describe_the_same_plugin():
    claude = _json(BUNDLE / ".claude-plugin" / "plugin.json")
    codex = _json(BUNDLE / ".codex-plugin" / "plugin.json")
    assert claude["name"] == codex["name"] == "kumiho-memory"
    assert claude["version"] == codex["version"]
    assert claude["license"] == codex["license"]


def test_mcp_json_declares_only_the_hosted_connector():
    assert _json(BUNDLE / ".mcp.json") == {
        "kumiho-memory": {"type": "http", "url": "https://mcp.kumiho.cloud/mcp"}
    }


def test_every_skill_has_a_matching_name_and_a_description():
    assert [path.name for path in SKILL_DIRS] == [
        "dream-state",
        "kumiho-backfill",
        "kumiho-memory",
        "kumiho-onboard",
        "kumiho-personalize",
        "memory-capture",
    ]
    for skill in SKILL_DIRS:
        meta = _frontmatter(skill / "SKILL.md")
        assert meta.get("name") == skill.name
        assert meta.get("description"), skill.name


def test_skills_reference_only_tools_the_connector_exposes():
    referenced = set()
    for skill in SKILL_DIRS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        referenced |= set(re.findall(r"\bkumiho_[a-z_]+\b", text))
    assert referenced
    assert referenced <= set(CONNECTOR_TOOLS), sorted(referenced - set(CONNECTOR_TOOLS))


def test_claude_code_backfill_keeps_the_conversation_and_drops_harness_records():
    def user(content, **flags):
        return {"type": "user", "timestamp": "t", "message": {"role": "user", "content": content}, **flags}

    records = [
        user("<system-reminder>queued notice</system-reminder>\nWe chose Postgres."),
        user("<task-notification><task-id>a</task-id><summary>done</summary></task-notification>"),
        user("<command-name>/clear</command-name>\n<command-message>clear</command-message>\n<command-args></command-args>"),
        user([{"type": "text", "text": "Base directory for this skill"}], isMeta=True),
        user("This session is being continued from a previous conversation.", isCompactSummary=True),
        user([{"type": "tool_result", "tool_use_id": "x", "content": "ls output"}], toolUseResult={}),
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": "sub-agent"}},
        {"type": "assistant", "timestamp": "t", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "text", "text": "Noted."},
            {"type": "tool_use", "id": "x", "name": "Bash", "input": {}},
        ]}},
    ]
    raw = "\n".join(json.dumps(record) for record in records)
    turns = _backfill().local_turns(raw, "claude")
    assert [(role, text.strip()) for _, role, text in turns] == [
        ("user", "We chose Postgres."),
        ("assistant", "Noted."),
    ]


def test_codex_backfill_is_not_affected_by_claude_harness_stripping():
    record = {"type": "response_item", "timestamp": "t", "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "<system-reminder>kept</system-reminder> as typed"}],
    }}
    turns = _backfill().local_turns(json.dumps(record), "codex")
    assert [text for _, _, text in turns] == ["<system-reminder>kept</system-reminder> as typed"]


def test_inventory_output_survives_a_narrow_stdout_encoding(tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps([{"id": "c1", "title": "Launch \U0001F680 plan"}]), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(BACKFILL), "inventory", "--source", "chatgpt", "--input", str(export)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.decode("utf-8")) == {"id": "c1", "title": "Launch \U0001F680 plan"}
