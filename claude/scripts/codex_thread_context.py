"""Bridge Codex MCP per-call thread metadata into Kumiho request context.

Codex sends its stable thread id in per-call ``params._meta`` (including the
``openai/threadId``, ``codexThreadId``, and ``threadId`` spellings used across
Codex builds). The Node stdio launcher carries it in a private tool argument
because the installed MCP
server currently discards request metadata before handler dispatch. This
module removes that carrier and exposes the id through a ContextVar-backed
host-session resolver. Process-global environment mutation would race when a
long-lived MCP server handles calls from multiple Codex threads.

The module ships beside both host adapters but is inert unless the adapter
explicitly installs it for ``KUMIHO_CLAUDE_HOST=codex``. Claude behavior is
therefore unchanged.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import os
import sys
from typing import Any, Callable, Dict, Optional


THREAD_CONTEXT_ARGUMENT = "__kumiho_codex_thread_id"
_MAX_THREAD_ID_CHARS = 256
_thread_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "kumiho_codex_thread_id", default=None
)


def _clean_thread_id(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_THREAD_ID_CHARS:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return None
    return candidate


def current_codex_thread_id() -> Optional[str]:
    """Return the id bound to the in-flight tool handler, if any."""
    return _thread_id.get()


def _wrap_handler(handler: Callable[[Dict[str, Any]], Any]):
    def mark_source(result: Any, thread_id: Optional[str]) -> Any:
        if (
            thread_id
            and isinstance(result, dict)
            and result.get("session_id_source") == "host-env"
        ):
            result = dict(result)
            result["session_id_source"] = "codex-thread-meta"
        return result

    @functools.wraps(handler)
    def wrapped(arguments: Dict[str, Any], *args, **kwargs):
        if not isinstance(arguments, dict):
            return handler(arguments, *args, **kwargs)
        clean_arguments = dict(arguments)
        thread_id = _clean_thread_id(
            clean_arguments.pop(THREAD_CONTEXT_ARGUMENT, None)
        )
        token = _thread_id.set(thread_id) if thread_id else None
        try:
            result = handler(clean_arguments, *args, **kwargs)
            if inspect.isawaitable(result) and thread_id:
                async def await_with_context():
                    async_token = _thread_id.set(thread_id)
                    try:
                        return mark_source(await result, thread_id)
                    finally:
                        _thread_id.reset(async_token)

                return await_with_context()
            return mark_source(result, thread_id)
        finally:
            if token is not None:
                _thread_id.reset(token)

    return wrapped


def install_codex_thread_context() -> bool:
    """Install the request-scoped compatibility layer once.

    Fails loudly when a required Kumiho seam disappears. Quiet fallback would
    return the exact broken behavior this bridge exists to prevent: missing or
    cross-thread session identity.
    """
    if os.getenv("KUMIHO_CLAUDE_HOST") != "codex":
        return False

    from kumiho_memory import mcp_tools
    from kumiho_memory import memory_manager
    from kumiho_memory._request_context import current_request

    if getattr(mcp_tools, "_kumiho_codex_thread_context_installed", False):
        return True

    original_host_session = getattr(memory_manager, "_host_session_env", None)
    original_recall_scope = getattr(mcp_tools, "_recall_scope", None)
    handlers = getattr(mcp_tools, "MEMORY_TOOL_HANDLERS", None)
    required_handlers = {
        "kumiho_chat_add",
        "kumiho_memory_consolidate",
        "kumiho_memory_recall",
        "kumiho_memory_engage",
        "kumiho_memory_reflect",
    }
    if (
        not callable(original_host_session)
        or not callable(original_recall_scope)
        or not isinstance(handlers, dict)
        or not required_handlers.issubset(handlers)
    ):
        raise RuntimeError(
            "installed kumiho-memory lacks the Codex thread-context seams"
        )

    def host_session_for_call() -> Optional[str]:
        # A verified hosted request remains authoritative. This compatibility
        # layer is for local Codex stdio and must never override tenant context.
        if current_request() is None:
            thread_id = current_codex_thread_id()
            if thread_id:
                return thread_id
        return original_host_session()

    def recall_scope_for_call(arguments: Dict[str, Any]) -> str:
        original_scope = original_recall_scope(arguments)
        thread_id = current_codex_thread_id()
        if not thread_id:
            return original_scope
        codex_scope = f"codex\x1e{thread_id}"
        return (
            f"{codex_scope}\x1e{original_scope}"
            if original_scope
            else codex_scope
        )

    wrapped_handlers = {
        name: _wrap_handler(handler) for name, handler in handlers.items()
    }
    memory_manager._host_session_env = host_session_for_call
    mcp_tools._recall_scope = recall_scope_for_call
    handlers.update(wrapped_handlers)

    # Usually the MCP server is imported after this installer. Keep the update
    # correct if an SDK release starts importing it eagerly through kumiho.
    server_module = sys.modules.get("kumiho.mcp_server")
    server_handlers = getattr(server_module, "TOOL_HANDLERS", None)
    if isinstance(server_handlers, dict):
        server_handlers.update(wrapped_handlers)

    mcp_tools._kumiho_codex_thread_context_installed = True
    return True


__all__ = [
    "THREAD_CONTEXT_ARGUMENT",
    "current_codex_thread_id",
    "install_codex_thread_context",
]
