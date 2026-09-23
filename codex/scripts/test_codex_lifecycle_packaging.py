#!/usr/bin/env python3
"""Packaging and host-isolation contract for the Codex lifecycle MCP tool."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_mcp_transport_is_local_stdio():
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["kumiho-memory"]
    assert server["command"] == "node"
    assert server["args"] == ["scripts/run_kumiho_mcp.mjs"]
    assert server["cwd"] == "."
    assert server["env"]["KUMIHO_CLAUDE_HOST"] == "codex"
    assert not any(key in server for key in ("url", "endpoint", "transport"))


def test_bundled_hook_event_mapping_uses_local_lifecycle_tool():
    path = PLUGIN / "hooks" / "hooks.json"
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    for event_name, groups in hooks.items():
        assert len(groups) == 1
        if event_name == "PreToolUse":
            assert groups[0]["matcher"] == "^apply_patch$"
        if event_name == "PostToolUse":
            assert groups[0]["matcher"] == "kumiho_memory_(reflect|consolidate)$"
        assert len(groups[0]["hooks"]) == 1
        hook = groups[0]["hooks"][0]
        if event_name in {"PostToolUse", "Stop"}:
            assert hook["timeout"] >= 20
        assert hook["type"] == "mcp_tool"
        assert hook["server"] == "kumiho-memory"
        assert hook["tool"] == "kumiho_codex_lifecycle"
        event = hook["input"]["event"]
        assert event["hook_event_name"] == event_name
        assert "last_assistant_message" not in event
    assert "SessionEnd" not in hooks


def test_lifecycle_tool_is_codex_host_gated_and_registered_locally():
    lifecycle = _load(HERE / "codex_lifecycle.py", "codex_lifecycle_packaging_test")
    server = SimpleNamespace(TOOLS=[], TOOL_HANDLERS={}, TOOL_ANNOTATIONS={})
    original = os.environ.get("KUMIHO_CLAUDE_HOST")
    try:
        os.environ["KUMIHO_CLAUDE_HOST"] = "claude"
        assert lifecycle.install_codex_lifecycle(server) is False
        assert server.TOOLS == []
        assert server.TOOL_HANDLERS == {}

        os.environ["KUMIHO_CLAUDE_HOST"] = "codex"
        assert lifecycle.install_codex_lifecycle(server) is True
        tool = next(row for row in server.TOOLS if row["name"] == "kumiho_codex_lifecycle")
        assert tool["inputSchema"]["required"] == ["event"]
        assert server.TOOL_HANDLERS[tool["name"]] is lifecycle.tool_codex_lifecycle
        assert server.TOOL_ANNOTATIONS[tool["name"]]["openWorldHint"] is True
        before = (len(server.TOOLS), len(server.TOOL_HANDLERS))
        assert lifecycle.install_codex_lifecycle(server) is True
        assert (len(server.TOOLS), len(server.TOOL_HANDLERS)) == before
    finally:
        if original is None:
            os.environ.pop("KUMIHO_CLAUDE_HOST", None)
        else:
            os.environ["KUMIHO_CLAUDE_HOST"] = original


if __name__ == "__main__":
    for test in (
        test_codex_mcp_transport_is_local_stdio,
        test_bundled_hook_event_mapping_uses_local_lifecycle_tool,
        test_lifecycle_tool_is_codex_host_gated_and_registered_locally,
    ):
        test()
        print(f"PASS: {test.__name__}")
