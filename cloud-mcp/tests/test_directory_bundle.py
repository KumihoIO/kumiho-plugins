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

from kumiho_cloud_mcp.connector_profile import (
    CONNECTOR_INSTRUCTIONS,
    CONNECTOR_TOOL_DESCRIPTIONS,
    CONNECTOR_TOOLS,
    MAX_INSTRUCTIONS_BYTES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    # One shared skills/ directory ships in both bundles, so a skill change is a
    # change to both; the test keeps the two version numbers from drifting apart.
    assert claude["version"] == codex["version"] == "0.1.4"
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
    # kumiho_get_revision_by_tag has no default tag, so the skill has to name one.
    assert "pass `latest`" in latest
    assert "`kumiho_memory_reflect`" in step_with("asks you to remember something")

    # A capture carrying `revises` becomes that memory's next revision whatever its
    # similarity or space. Placed by `space_hint` alone, a correction that did not
    # look similar enough became a second item and recall returned both values.
    correction = step_with("corrects or updates something already saved")
    assert "`kumiho_memory_reflect`, setting the capture's `revises`" in correction
    assert "no `space_hint` is needed" in correction
    assert "Keep its memory type" in correction and "not `correction`" in correction
    assert "`kumiho_deprecate_item` when the result is a separate item" in correction
    for name in ("memory-capture", "kumiho-personalize", "dream-state"):
        text = " ".join((BUNDLE / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split())
        assert "type: correction" not in text, name
        assert "`revises`" in text and "a separate item" in text, name
        assert "?r=1" not in text and "as `space_hint`" not in text, name

    # claude.ai has no session-start hook and drops MCP server instructions, so the
    # description is what makes Claude load the uploaded skill. Uploads cap it at 200.
    meta = _frontmatter(BUNDLE / "skills" / "kumiho-memory" / "SKILL.md")
    assert meta["name"] == "kumiho-memory"
    assert len(meta["description"]) <= 200, len(meta["description"])
    for cue in ("what you know about them", "decision", "preference", "earlier chats",
                "remember", "save", "forget", "correct", "recent memories"):
        assert cue in meta["description"], cue
    # A subject and verbs, not a bare list of cues. The literal "what's my..." cue was
    # dropped to make room; "asks what you know about them" covers it.
    assert meta["description"].startswith("Use when the user ")
    assert "asks what you know about them" in meta["description"]
    # A new user who has not signed in has no Kumiho tools: the skill has to load
    # for that situation too, or nothing ever tells them to connect.
    assert "Kumiho tools are missing" in meta["description"]


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


# Observed 2026-10-12: a protocol that said "reflect after you respond" made the
# assistant write its full answer, call kumiho_memory_reflect, and then write a
# second, rephrased copy of the whole answer. Reflect now comes first.
STALE_REFLECT_TIMING = re.compile(
    r"after (?:responding|you respond|a substantive response|the response)", re.I)


#: A follow-up that needs reflect's result (a retire after a correction that did not
#: revise, decompose, a requested consolidate) runs silently, so the answer is still
#: written once. Every place that states the ordering rule carries this exception, or
#: the rule reads as "nothing may follow reflect" and contradicts those instructions.
SILENT_FOLLOW_UP = (
    "follow-up that needs reflect's result",
    "silently right after it, with no user-visible text",
)


#: The ordering rule, as every place that states it words it.
REFLECT_RULE = ("the last memory call before the final answer, apart from silent "
                "follow-ups that need its result")

# One definition of "an instruction that puts a call after the answer", shared with
# the claude/scripts tests. It lives in the repository-root tests/ directory because
# neither test tree can import the other, so each loads it by path.
_reflect_order = _load("reflect_order_check", REPO_ROOT / "tests" / "reflect_order_check.py")
_requires_a_call_after_the_answer = _reflect_order.calls_after_the_answer


def test_reflect_comes_before_the_final_answer_so_the_user_reads_it_once():
    for skill in SKILL_DIRS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert STALE_REFLECT_TIMING.search(" ".join(text.split())) is None, skill.name
        assert "After a substantive response, call" not in text, skill.name

    flat = " ".join((BUNDLE / "skills" / "kumiho-memory" / "SKILL.md")
                    .read_text(encoding="utf-8").split())
    for needle in (
        f"`kumiho_memory_reflect` is {REFLECT_RULE}",
        "show only short progress notes",
        "`response` a concise summary of the answer you are about to give",
        "never a verbatim second copy",
        "then write the answer once",
        "end the turn right after reflect and any such follow-up, with no restatement",
        "at most one short line that adds new information",
    ):
        assert needle in flat, needle
    # "last tool call" and "last memory write" are the earlier wordings; the first
    # contradicted the follow-ups, the second hid that a retire is a write too.
    assert "last tool call" not in flat.lower() and "last memory write" not in flat.lower()
    # Capture happens when something settles, not saved for the end.
    assert "Capture it as soon as it is settled, not saved for the end of the conversation" in flat
    assert "not only at the end" not in flat

    # The served tool descriptions and server instructions must not reintroduce
    # "after responding" either.
    for name, description in CONNECTOR_TOOL_DESCRIPTIONS.items():
        assert STALE_REFLECT_TIMING.search(" ".join(description.split())) is None, name
    assert STALE_REFLECT_TIMING.search(" ".join(CONNECTOR_INSTRUCTIONS.split())) is None


def test_the_silent_follow_up_exception_is_stated_wherever_the_rule_is():
    flat = " ".join((BUNDLE / "skills" / "kumiho-memory" / "SKILL.md")
                    .read_text(encoding="utf-8").split())
    for marker in SILENT_FOLLOW_UP:
        assert marker in flat, marker
    # kumiho_deprecate_item still follows a correction that stored a separate item.
    assert "`kumiho_deprecate_item` after a correction that did not revise the old memory" in flat
    assert "when the result is a separate item" in flat
    assert "a `kumiho_memory_consolidate` the user asked for" in flat


def test_no_hosted_instruction_requires_a_tool_call_after_the_final_answer():
    for skill in SKILL_DIRS:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert _requires_a_call_after_the_answer(text) == [], skill.name
    flat_instructions = " ".join(CONNECTOR_INSTRUCTIONS.split())
    assert _requires_a_call_after_the_answer(flat_instructions) == []
    for name, description in CONNECTOR_TOOL_DESCRIPTIONS.items():
        assert _requires_a_call_after_the_answer(description) == [], name


def test_the_after_the_answer_check_flags_every_phrasing_that_puts_a_call_after_the_answer():
    for positive in _reflect_order.POSITIVES:
        assert _requires_a_call_after_the_answer(positive), positive
    for negative in _reflect_order.NEGATIVES:
        assert _requires_a_call_after_the_answer(negative) == [], negative
    # Phrasings the first version of the check missed.
    for missed in ("Write the full answer, then call kumiho_memory_reflect.",
                   "Once you have answered, call kumiho_memory_reflect.",
                   "After writing your answer, call kumiho_memory_reflect.",
                   "Reflect after you reply.",
                   "REFLECT — after you respond.",
                   "Capture what matters after responding."):
        assert _requires_a_call_after_the_answer(missed), missed


def test_server_instructions_say_to_capture_when_settled_and_fit_the_budget():
    flat = " ".join(CONNECTOR_INSTRUCTIONS.split())
    # "not at the end" could be read as "never reflect once the answer is written".
    assert "not at the end" not in flat and "not only at conversation end" not in flat
    assert "as soon as something is settled, not saved for the end; never on routine turns" in flat
    # Claude Code truncates the instructions at 2KB, so the wording must still fit.
    assert len(CONNECTOR_INSTRUCTIONS.encode("utf-8")) <= MAX_INSTRUCTIONS_BYTES <= 2000


# Installed users were not becoming active. In Claude Code the bundled HTTP server
# needs a sign-in only the user can start, and until then no Kumiho tool exists, so
# nothing in the bundle ever ran. The skill is the one place that can tell them.
def test_the_first_instruction_tells_an_unconnected_user_how_to_connect():
    text = (BUNDLE / "skills" / "kumiho-memory" / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1]
    assert body.lstrip().startswith("# Kumiho Memory")
    # Everything between the title and the first section heading is the first instruction.
    first = " ".join(body.split("\n## ", 1)[0].split())
    assert first.startswith("# Kumiho Memory First, check that the Kumiho tools (`kumiho_*`) are available.")
    for needle in (
        "tell them once, in their language",
        "continue the task without memory",
        "Never pretend to remember",
        # One bullet per host, in the order Claude Code, Cowork, claude.ai and Desktop
        # chat, then the OpenAI hosts that ship the same skills/ directory.
        "- Claude Code: run `/mcp`, select `plugin:kumiho-memory-cloud:kumiho-memory`, choose Authenticate",
        "- Cowork: use the plugin's Connect prompt, or Customize > Connectors > Kumiho Memory.",
        "- claude.ai and Claude Desktop chat: Customize > Connectors > Kumiho Memory.",
        "- ChatGPT and Codex: use the app's Connect flow.",
        "Connecting needs a paid Kumiho Cloud workspace",
        "Never ask for a password, token or MFA code in chat",
        "If sign-in was refused because the account has no workspace, reconnecting will not help",
        "create a workspace at kumiho.io first",
        "Say all this once per conversation",
    ):
        assert needle in first, needle
    order = [first.index(host) for host in (
        "- Claude Code:", "- Cowork:", "- claude.ai and Claude Desktop chat:", "- ChatGPT and Codex:")]
    assert order == sorted(order)
    # No trial or price claim before one exists, and "account" alone was not enough:
    # a sign-in without a workspace is the case reconnecting cannot fix.
    assert "trial" not in first.lower() and "free" not in first.lower()
    assert "Kumiho Cloud account" not in first
    # One definition of the rule: the older "connector settings" bullet is gone.
    assert text.count("authentication error") == 1
    assert "in the app's connector settings" not in text


def test_the_claude_manifest_says_what_it_does_then_how_to_connect():
    description = _json(BUNDLE / ".claude-plugin" / "plugin.json")["description"]
    assert description.startswith("Persistent memory for Claude across conversations.")
    connect = "Connect via /mcp (Cowork: Connect) after installing."
    assert connect in description
    assert description.index(connect) < description.index("Bundles the hosted")
    # The account requirement stays, and no trial or price is claimed before one exists.
    assert description.endswith("Requires a paid Kumiho Cloud account.")
    assert "trial" not in description.lower() and "free" not in description.lower()
    assert "TODO" not in description


def test_the_readme_leads_with_a_short_getting_started_and_marks_the_trial_todo():
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Getting started") < readme.index("## Requirements")
    started = readme.split("## Getting started", 1)[1].split("\n## ", 1)[0]
    assert started.index("**Connect.**") < started.index("**Check.**") < started.index("**Use.**")
    assert "`/mcp`" in started and "`plugin:kumiho-memory-cloud:kumiho-memory`" in started
    # The requirement is a workspace, and a refused sign-in without one is explained.
    flat_started = " ".join(started.split())
    assert "paid Kumiho Cloud workspace" in flat_started
    assert "reconnecting will not help" in flat_started
    assert "create a workspace at kumiho.io first" in flat_started
    # The marker lives in the README only, as a comment, and nothing outside the
    # comment claims a trial.
    assert "<!-- TODO(free-trial)" in started
    claims = started.split("<!-- TODO(free-trial)", 1)[0].lower()
    assert "trial" not in claims and "free" not in claims
    for path in (BUNDLE / ".claude-plugin" / "plugin.json", BUNDLE / ".codex-plugin" / "plugin.json",
                 *(skill / "SKILL.md" for skill in SKILL_DIRS)):
        assert "TODO" not in path.read_text(encoding="utf-8"), path


def test_submission_docs_keep_internal_metrics_out_and_flag_the_shared_skills_change():
    submission = (DIRECTORY / "CLAUDE-PLUGIN-SUBMISSION.md").read_text(encoding="utf-8")
    # Directory analytics (install, load and session counts, active accounts) are
    # internal: matched by shape, so no figure has to be written down to be kept out.
    metric = re.compile(r"\b\d[\d,]*\s+(?:installs|loads|sessions)\b|active accounts", re.I)
    for count, noun in (("7", "installs"), ("1,234", "loads"), ("99", "sessions")):
        assert metric.search(f"{count} {noun}"), (count, noun)
    assert metric.search("no active accounts")
    for path in (DIRECTORY / "CLAUDE-PLUGIN-SUBMISSION.md", DIRECTORY.parent / "CHATGPT-SUBMISSION.md",
                 BUNDLE / "README.md", BUNDLE / ".claude-plugin" / "plugin.json",
                 *(skill / "SKILL.md" for skill in SKILL_DIRS)):
        assert metric.search(path.read_text(encoding="utf-8")) is None, path.name
    # The skills/ directory is shared with the OpenAI bundle, so a skill change there
    # is flagged for the owner, who decides whether a resubmission is needed.
    chatgpt = (DIRECTORY.parent / "CHATGPT-SUBMISSION.md").read_text(encoding="utf-8")
    assert "0.1.4" in chatgpt and "owner decision" in chatgpt
    assert "OpenAI resubmission" in chatgpt


def test_the_bundle_still_runs_nothing_locally_beyond_the_optional_backfill_helper():
    # The directory review depends on this: no hooks, agents, commands or MCP stdio server.
    assert not (BUNDLE / "hooks").exists()
    assert not (BUNDLE / "commands").exists()
    assert not (BUNDLE / "agents").exists()
    assert "hooks" not in _json(BUNDLE / ".claude-plugin" / "plugin.json")
    scripts = sorted(path.relative_to(BUNDLE).as_posix() for path in BUNDLE.rglob("*.py"))
    assert scripts == ["skills/kumiho-backfill/scripts/prepare_backfill.py"]


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
