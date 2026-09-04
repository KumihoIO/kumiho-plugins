from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier


HERE = Path(__file__).resolve().parent
SHIM = HERE / "codex_thread_context.py"
PRIVATE = "__kumiho_codex_thread_id"
REQUIRED_HANDLERS = {
    "kumiho_chat_add",
    "kumiho_memory_consolidate",
    "kumiho_memory_recall",
    "kumiho_memory_engage",
    "kumiho_memory_reflect",
}


def _load_shim(monkeypatch, handler):
    package = types.ModuleType("kumiho_memory")
    package.__path__ = []
    tools = types.ModuleType("kumiho_memory.mcp_tools")
    manager = types.ModuleType("kumiho_memory.memory_manager")
    request = types.ModuleType("kumiho_memory._request_context")

    manager._host_session_env = lambda: "ambient-fallback"
    tools._recall_scope = lambda _args: ""
    tools.MEMORY_TOOL_HANDLERS = {
        name: handler for name in REQUIRED_HANDLERS
    }
    request.current_request = lambda: None
    package.mcp_tools = tools
    package.memory_manager = manager

    monkeypatch.setitem(sys.modules, "kumiho_memory", package)
    monkeypatch.setitem(sys.modules, "kumiho_memory.mcp_tools", tools)
    monkeypatch.setitem(sys.modules, "kumiho_memory.memory_manager", manager)
    monkeypatch.setitem(sys.modules, "kumiho_memory._request_context", request)

    spec = importlib.util.spec_from_file_location(
        "codex_thread_context_test_subject", SHIM
    )
    assert spec is not None and spec.loader is not None
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    return shim, tools, manager


def test_private_carrier_becomes_host_context_and_is_removed(monkeypatch):
    observed = {}

    def handler(args):
        observed["args"] = args
        observed["host"] = manager._host_session_env()
        observed["scope"] = tools._recall_scope(args)
        return {"session_id_source": "host-env", "session_id": observed["host"]}

    shim, tools, manager = _load_shim(monkeypatch, handler)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    assert shim.install_codex_thread_context()

    result = tools.MEMORY_TOOL_HANDLERS["kumiho_memory_reflect"](
        {PRIVATE: "thread-1", "response": "done", "session_id": "historical"}
    )
    assert PRIVATE not in observed["args"]
    assert observed["args"]["session_id"] == "historical"
    assert observed["host"] == "thread-1"
    assert observed["scope"] == "codex\x1ethread-1"
    assert result["session_id_source"] == "codex-thread-meta"
    assert manager._host_session_env() == "ambient-fallback"


def test_context_is_isolated_between_concurrent_tool_calls(monkeypatch):
    barrier = Barrier(2)

    def handler(_args):
        barrier.wait(timeout=5)
        return {"host": manager._host_session_env()}

    shim, tools, manager = _load_shim(monkeypatch, handler)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    shim.install_codex_thread_context()
    wrapped = tools.MEMORY_TOOL_HANDLERS["kumiho_memory_reflect"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda value: wrapped({PRIVATE: value}),
            ("thread-a", "thread-b"),
        ))
    assert {result["host"] for result in results} == {"thread-a", "thread-b"}
    assert manager._host_session_env() == "ambient-fallback"


def test_context_survives_an_async_tool_handler(monkeypatch):
    async def handler(args):
        await asyncio.sleep(0)
        return {
            "host": manager._host_session_env(),
            "args": args,
            "session_id_source": "host-env",
        }

    shim, tools, manager = _load_shim(monkeypatch, handler)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    shim.install_codex_thread_context()

    result = asyncio.run(tools.MEMORY_TOOL_HANDLERS["kumiho_memory_reflect"](
        {PRIVATE: "thread-async", "response": "done"}
    ))
    assert result["host"] == "thread-async"
    assert PRIVATE not in result["args"]
    assert result["session_id_source"] == "codex-thread-meta"
    assert manager._host_session_env() == "ambient-fallback"


def test_claude_host_is_a_noop(monkeypatch):
    handler = lambda args: args
    shim, tools, manager = _load_shim(monkeypatch, handler)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    original_handler = tools.MEMORY_TOOL_HANDLERS["kumiho_memory_reflect"]
    original_host = manager._host_session_env

    assert shim.install_codex_thread_context() is False
    assert tools.MEMORY_TOOL_HANDLERS["kumiho_memory_reflect"] is original_handler
    assert manager._host_session_env is original_host
