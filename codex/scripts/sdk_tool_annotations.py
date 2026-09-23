"""Bridge core insight annotations into older SDK MCP server registries.

Some SDK releases auto-discover the tools but build MCP annotations exclusively
from TOOL_ANNOTATIONS. Fill missing entries from the installed core declaration;
never override an SDK entry or alter defaults for unrelated tools.
"""
from __future__ import annotations

import importlib
import os

_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def install_insight_annotations(server) -> int:
    table = getattr(server, "TOOL_ANNOTATIONS", None)
    if not isinstance(table, dict):
        return 0  # Older/different SDK: do not invent a new annotation API.
    try:
        core = importlib.import_module("kumiho_memory.insight_tools")
    except ModuleNotFoundError as exc:
        if exc.name in ("kumiho_memory", "kumiho_memory.insight_tools"):
            return 0
        raise  # A broken installed dependency is not an absent capability.
    count = 0
    for tool in getattr(core, "INSIGHT_TOOLS", ()):
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        annotations = tool.get("annotations")
        if (not isinstance(name, str) or not name.startswith("kumiho_memory_")
                or name in table or not isinstance(annotations, dict)
                or any(type(annotations.get(key)) is not bool for key in _HINTS)):
            continue  # No guesses about read-only/destructive behavior.
        title = tool.get("title")
        if not isinstance(title, str) or not title.strip():
            title = name.replace("_", " ").title()
        table.setdefault(name, {"title": title, **{key: annotations[key] for key in _HINTS}})
        count += 1
    return count


def run_mcp_server() -> None:
    # Patch the same module whose main serves tools/list. Running it again as
    # __main__ through runpy would create a fresh, unpatched registry.
    server = importlib.import_module("kumiho.mcp_server")
    install_insight_annotations(server)
    if os.getenv('KUMIHO_CLAUDE_HOST') == 'codex':
        from codex_lifecycle import install_codex_lifecycle
        install_codex_lifecycle(server)
    server.main()
