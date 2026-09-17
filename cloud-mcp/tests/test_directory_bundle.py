"""The hosted directory plugin bundle shared by the Claude and Codex manifests."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOLS

DIRECTORY = Path(__file__).resolve().parents[1] / "directory"
BUNDLE = DIRECTORY / "kumiho-memory"
SKILL_DIRS = sorted(path.parent for path in BUNDLE.glob("skills/*/SKILL.md"))
BACKFILL = BUNDLE / "skills" / "kumiho-backfill" / "scripts" / "prepare_backfill.py"
PACKAGER = DIRECTORY / "scripts" / "package_web_skill.py"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _frontmatter(path):
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path} has no frontmatter"
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backfill():
    return _load("prepare_backfill", BACKFILL)


def _packager():
    return _load("package_web_skill", PACKAGER)


def test_claude_and_codex_manifests_describe_the_same_plugin():
    claude = _json(BUNDLE / ".claude-plugin" / "plugin.json")
    codex = _json(BUNDLE / ".codex-plugin" / "plugin.json")
    assert claude["name"] == "kumiho-memory-cloud"
    assert claude["displayName"] == "Kumiho Memory Cloud"
    # Already submitted to OpenAI under this name; unaffected by the Claude rename.
    assert codex["name"] == "kumiho-memory"
    assert claude["version"] == codex["version"]
    assert claude["license"] == codex["license"]


def test_hosted_claude_plugin_name_differs_from_the_local_claude_plugin():
    # Claude Code namespaces MCP tools (mcp__plugin_<name>_<server>__*), the /mcp
    # server id (plugin:<name>:<server>) and skills (<name>:<skill>) by plugin name
    # alone, so two enabled plugins sharing a name collide on all three.
    hosted = _json(BUNDLE / ".claude-plugin" / "plugin.json")
    local = _json(DIRECTORY.parents[1] / "claude" / ".claude-plugin" / "plugin.json")
    assert hosted["name"] != local["name"], hosted["name"]


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


# General recall belongs to engage and "remember this" to reflect. Recall and store
# are lower-level tools: a skill may still use them deliberately (for example store
# inside an approval flow), but never as the answer to those two intents.
GENERAL_RECALL_CUES = ("like we discussed", "as we discussed", "the usual setup", "not in your context")
REMEMBER_CUES = ("remember this", "remember something", "asks you to remember", "asks to remember")


def _steps(text):
    """Paragraphs and top-level list items, with line wrapping collapsed."""
    blocks = re.split(r"\n\s*\n|\n(?=(?:\d+\.|-) )", text)
    return [" ".join(block.split()) for block in blocks if block.strip()]


def _misrouted(text):
    found = []
    for step in _steps(text):
        lowered = step.lower()
        if "kumiho_memory_recall" in lowered and any(cue in lowered for cue in GENERAL_RECALL_CUES):
            found.append(("kumiho_memory_recall", step))
        if "kumiho_memory_store" in lowered and any(cue in lowered for cue in REMEMBER_CUES):
            found.append(("kumiho_memory_store", step))
    return found


def test_misrouting_check_catches_the_routing_the_skill_once_shipped():
    shipped = (
        "2. When the user refers to something not in your context (\"like we discussed\",\n"
        "   \"the usual setup\", a project name you have not seen), call\n"
        "   `kumiho_memory_recall` with a natural-language description of what you need\n"
    )
    assert [tool for tool, _ in _misrouted(shipped)] == ["kumiho_memory_recall"]
    store = "- When the user asks you to remember something, call `kumiho_memory_store`.\n"
    assert [tool for tool, _ in _misrouted(store)] == ["kumiho_memory_store"]


def test_skills_route_general_recall_to_engage_and_remember_this_to_reflect():
    for skill in SKILL_DIRS:
        assert _misrouted((skill / "SKILL.md").read_text(encoding="utf-8")) == [], skill.name

    steps = _steps((BUNDLE / "skills" / "kumiho-memory" / "SKILL.md").read_text(encoding="utf-8"))

    def step_with(cue):
        matches = [step for step in steps if cue in step]
        assert len(matches) == 1, (cue, matches)
        return matches[0]

    assert "`kumiho_memory_engage`" in step_with("like we discussed")
    latest = step_with("most recent")
    assert "`kumiho_memory_retrieve` with mode \"latest\"" in latest
    assert "`space_paths`" in latest and "`memory_types`" in latest
    assert "`kumiho_memory_reflect`" in step_with("asks you to remember something")

    # Reflect stacks a capture onto a similar memory only inside the space it is
    # given, and a stacked revision takes the published tag only when the capture
    # has no tags. A correction without that memory's space was filed at the
    # project root as a second item, and recall returned both values.
    correction = step_with("corrects or updates something already saved")
    assert "`kumiho_memory_reflect` in the same space as that memory" in correction
    assert "unchanged as `space_hint`" in correction
    assert "Keep its memory type" in correction and "not `correction`" in correction
    assert "leave out `tags`" in correction
    assert "`?r=1`" in correction and "`kumiho_deprecate_item`" in correction
    for name in ("memory-capture", "kumiho-personalize", "dream-state"):
        text = " ".join((BUNDLE / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split())
        assert "type: correction" not in text, name
        assert "as `space_hint`" in text and "`?r=1`" in text, name
        assert "`tags`" in text, name

    # claude.ai has no session-start hook and drops MCP server instructions, so the
    # description is what makes Claude load the uploaded skill. Uploads cap it at 200.
    meta = _frontmatter(BUNDLE / "skills" / "kumiho-memory" / "SKILL.md")
    assert meta["name"] == "kumiho-memory"
    assert len(meta["description"]) <= 200, len(meta["description"])
    for cue in ("what you know about them", "preferences", "what's my", "earlier chats",
                "remember", "save", "forget", "correct", "recent memories"):
        assert cue in meta["description"], cue


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


def test_core_skill_carries_the_whole_protocol_on_its_own():
    # claude.ai web and Desktop chat drop the MCP server `instructions` field
    # (anthropics/claude-ai-mcp#93), so an uploaded skill is the only protocol there.
    text = (BUNDLE / "skills" / "kumiho-memory" / "SKILL.md").read_text(encoding="utf-8")
    for needle in (
        "kumiho_memory_engage",
        "kumiho_memory_retrieve",
        "kumiho_memory_reflect",
        "kumiho_memory_consolidate",
        "kumiho_deprecate_item",
        "session_required",
        "does not authorize recording every conversation",
    ):
        assert needle in text, needle


def test_web_skill_zip_is_one_skill_folder_and_reproducible(tmp_path):
    packager = _packager()
    first = packager.build("kumiho-memory", tmp_path / "a")
    second = packager.build("kumiho-memory", tmp_path / "b")
    assert first.name == "kumiho-memory-skill.zip"
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["kumiho-memory/", "kumiho-memory/SKILL.md"]
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert b"\r\n" not in archive.read("kumiho-memory/SKILL.md")


def test_web_skill_zip_does_not_depend_on_checkout_line_endings(tmp_path, monkeypatch):
    packager = _packager()
    reference = packager.build("kumiho-memory", tmp_path / "reference")

    skill = tmp_path / "skills" / "kumiho-memory"
    (skill / "agents").mkdir(parents=True)
    (skill / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    source = (BUNDLE / "skills" / "kumiho-memory" / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
    (skill / "SKILL.md").write_bytes(source.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(packager, "SKILLS", tmp_path / "skills")

    assert packager.build("kumiho-memory", tmp_path / "crlf").read_bytes() == reference.read_bytes()


def test_web_skill_validation_applies_the_upload_limits():
    validate = _packager().validate
    ok = "---\nname: demo\ndescription: Short and specific.\n---\n# Demo\n"
    assert validate("demo", ok) == []
    assert validate("other", ok)
    assert validate("demo", ok.replace("name: demo", "name: Demo"))
    assert validate("demo", ok.replace("Short and specific.", "x" * 201))
    assert validate("demo", ok.replace("\n---\n# Demo", "\nlicense: MIT\n---\n# Demo"))
    assert validate("demo", ok + "line\n" * 500)
