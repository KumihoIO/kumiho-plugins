"""Offline checks for core-to-SDK annotation forwarding and serving-module identity."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool(name="record_experience", *, read_only=False):
    return {"name": "kumiho_memory_" + name, "annotations": {
        "readOnlyHint": read_only, "destructiveHint": False,
        "idempotentHint": read_only, "openWorldHint": name != "validate_insight_response",
    }}


def core_module(monkeypatch, declarations):
    core = ModuleType("kumiho_memory.insight_tools")
    core.INSIGHT_TOOLS = declarations
    monkeypatch.setitem(sys.modules, core.__name__, core)
    return core


def helper():
    return load(ROOT / "claude/scripts/sdk_tool_annotations.py", "annotation_helper_test")


def test_forwards_declared_hints_and_keeps_existing_sdk_entries(monkeypatch):
    core = core_module(monkeypatch, [tool(), tool("validate_insight_response", read_only=True)])
    server = ModuleType("fake_server")
    existing = {"title": "SDK owns this", "readOnlyHint": False}
    server.TOOL_ANNOTATIONS = {"unrelated": existing}
    assert helper().install_insight_annotations(server) == 2
    assert server.TOOL_ANNOTATIONS["unrelated"] is existing
    for declaration in core.INSIGHT_TOOLS:
        forwarded = server.TOOL_ANNOTATIONS[declaration["name"]]
        assert forwarded["title"]
        assert all(forwarded[k] == v for k, v in declaration["annotations"].items())
    future = {"title": "Future SDK entry", "readOnlyHint": True}
    server.TOOL_ANNOTATIONS["kumiho_memory_record_experience"] = future
    assert helper().install_insight_annotations(server) == 0
    assert server.TOOL_ANNOTATIONS["kumiho_memory_record_experience"] is future


def test_absent_registry_and_malformed_declarations_keep_defaults(monkeypatch):
    malformed = tool()
    malformed["annotations"]["readOnlyHint"] = "true"
    core_module(monkeypatch, [malformed, {"name": "unrelated"}, None])
    server = ModuleType("fake_server")
    assert helper().install_insight_annotations(server) == 0
    server.TOOL_ANNOTATIONS = {}
    assert helper().install_insight_annotations(server) == 0
    assert server.TOOL_ANNOTATIONS == {}


def test_absent_core_is_optional_but_broken_dependency_is_not_hidden(monkeypatch):
    bridge = helper()
    server = ModuleType("fake_server")
    server.TOOL_ANNOTATIONS = {}
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(bridge.importlib, "import_module", missing)
    assert bridge.install_insight_annotations(server) == 0
    def broken(name):
        raise ModuleNotFoundError(name="broken_dependency")
    monkeypatch.setattr(bridge.importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        bridge.install_insight_annotations(server)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("backend", ["cloud", "ce"])
@pytest.mark.parametrize("args", [[], ["--module", "kumiho.mcp_server"]])
def test_adapters_forward_before_same_module_serves(monkeypatch, host, backend, args):
    monkeypatch.setattr(sys, "path", sys.path[:])
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("KUMIHO_SERVER_ENDPOINT", "127.0.0.1:9190")
    monkeypatch.setenv("UPSTASH_REDIS_URL", "redis://127.0.0.1:6379")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", host)
    monkeypatch.setattr(sys, "argv", ["adapter", *args])
    sdk = ModuleType("kumiho")
    sdk.__path__ = []
    sdk.connect = lambda **kwargs: object()
    sdk.configure_default_client = lambda client: None
    monkeypatch.setitem(sys.modules, "kumiho", sdk)
    server = ModuleType("kumiho.mcp_server")
    server.TOOL_ANNOTATIONS = {}
    served = []
    server.main = lambda: served.append(server.TOOL_ANNOTATIONS.copy())
    monkeypatch.setitem(sys.modules, server.__name__, server)
    context = ModuleType("codex_thread_context")
    context.install_codex_thread_context = lambda: True
    monkeypatch.setitem(sys.modules, context.__name__, context)
    core_module(monkeypatch, [tool()])
    adapter = load(ROOT / host / "scripts" / ("run_kumiho_" + backend + ".py"), "adapter_test")
    if backend == "cloud":
        monkeypatch.setattr(adapter, "_prepare_environment", lambda: None)
        monkeypatch.setattr(adapter, "_configure_cloud", lambda **kwargs: True)
    monkeypatch.setattr(adapter.runpy, "run_module", lambda *a, **k: pytest.fail("second unpatched serving module"))
    adapter.main()
    assert len(served) == 1
    assert served[0]["kumiho_memory_record_experience"]["readOnlyHint"] is False


def test_claude_codex_helpers_are_identical():
    assert (ROOT / "claude/scripts/sdk_tool_annotations.py").read_bytes() == (
        ROOT / "codex/scripts/sdk_tool_annotations.py").read_bytes()
