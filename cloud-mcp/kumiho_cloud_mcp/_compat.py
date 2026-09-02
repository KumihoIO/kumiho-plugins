"""Compatibility shims for the two sibling work packages that are still in
flight (plan §2.1 WP-A, §2.2 WP-A, §2.3 WP-B).

Each shim prefers the real implementation and degrades to a local one, so this
service builds and runs today against ``kumiho`` 0.9.x / ``kumiho-memory``
0.5.x and picks up the real behaviour the moment those releases land — no code
change here.

Three things are shimmed:

``RequestContext`` / ``request_context`` / ``current_request`` / ``hosted_mode``
    Imported from ``kumiho.request_context`` when it exists, otherwise defined
    here byte-for-byte from plan §2.1 so both copies interoperate through the
    same *semantics* (they are two different ContextVars, which is why
    :func:`request_context` sets *both* when the real module appears later).

``build_server()``
    Calls ``create_mcp_server(profile="connector", instructions=...)`` when the
    installed signature accepts it. Otherwise builds the full server and wraps
    its ``ListToolsRequest`` / ``CallToolRequest`` handlers so only the 18
    connector tools are visible or callable, attaching annotations from
    :mod:`kumiho_cloud_mcp.connector_profile`.

``redis_token_bridge()``
    Always sets ``kumiho_memory.redis_token_override_var`` to the caller's
    token for the duration of a request. Harmless once WP-B reads the token off
    ``current_request()`` itself.
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib
import inspect
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional

logger = logging.getLogger("kumiho.cloud_mcp.compat")

# ---------------------------------------------------------------------------
# §2.1 — request context
# ---------------------------------------------------------------------------
#
# There can be more than one *copy* of the §2.1 contextvar in a process while
# WP-A is in flight: ``kumiho.request_context`` is the canonical home, but
# ``kumiho_memory`` ships the same fallback under ``_request_context`` so it can
# release ahead of the SDK. Those are two distinct ContextVars — binding only
# ours would leave ``kumiho_memory.current_request()`` returning ``None`` on
# every request, which is exactly the bug that makes memory tools fall back to
# ambient credentials. So we discover every provider and set all of them.


def _discover_provider(module_name: str):
    try:
        # importlib, not ``import kumiho.request_context as m``: the SDK's
        # ``__init__`` re-exports the *function* ``request_context``, which
        # shadows the submodule attribute — that spelling hands back the
        # function, not the module.
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - not landed yet, or broken install
        return None
    setter = getattr(module, "request_context", None)
    getter = getattr(module, "current_request", None)
    if callable(setter) and callable(getter):
        return (module_name, module, setter, getter)
    return None


_PROVIDERS = [
    provider
    for provider in (
        _discover_provider("kumiho.request_context"),
        _discover_provider("kumiho_memory._request_context"),
    )
    if provider is not None
]

# Two entries backed by the same function object (WP-A landed and kumiho_memory
# re-exports it) only need binding once.
_seen_setters: set = set()
_UNIQUE_PROVIDERS = []
for _provider in _PROVIDERS:
    if id(_provider[2]) not in _seen_setters:
        _seen_setters.add(id(_provider[2]))
        _UNIQUE_PROVIDERS.append(_provider)

HAVE_UPSTREAM_REQUEST_CONTEXT = any(name == "kumiho.request_context" for name, *_ in _PROVIDERS)
PROVIDER_NAMES = [name for name, *_ in _UNIQUE_PROVIDERS]

_shared_class = None
for _name, _module, _setter, _getter in _UNIQUE_PROVIDERS:
    _candidate = getattr(_module, "RequestContext", None)
    if _candidate is not None:
        _shared_class = _candidate
        break

if _shared_class is not None:
    RequestContext = _shared_class  # type: ignore[assignment,misc]
else:

    @dataclass(frozen=True)
    class RequestContext:  # type: ignore[no-redef]
        """Vendored copy of ``kumiho.request_context.RequestContext`` (§2.1)."""

        tenant_id: str
        user_id: str
        auth_token: str
        context: str = "claude"
        session_id: Optional[str] = None
        client_id: Optional[str] = None
        scopes: List[str] = field(default_factory=list)
        tenant_slug: Optional[str] = None
        region_code: Optional[str] = None
        token_id: Optional[str] = None


# Our own mirror, so ``current_request()`` works even with no provider present.
_local_request_var: contextvars.ContextVar[Optional["RequestContext"]] = contextvars.ContextVar(
    "kumiho_cloud_mcp_request", default=None
)


def current_request() -> Optional["RequestContext"]:
    """The request context for the running task, or ``None``."""
    for _name, _module, _setter, getter in _UNIQUE_PROVIDERS:
        found = getter()
        if found is not None:
            return found
    return _local_request_var.get()


@contextlib.contextmanager
def request_context(ctx: "RequestContext") -> Iterator["RequestContext"]:
    """Bind ``ctx`` in every request-context implementation present."""
    token = _local_request_var.set(ctx)
    try:
        with contextlib.ExitStack() as stack:
            for _name, _module, setter, _getter in _UNIQUE_PROVIDERS:
                stack.enter_context(setter(ctx))
            yield ctx
    finally:
        _local_request_var.reset(token)


def hosted_mode() -> bool:
    """``KUMIHO_MCP_HOSTED`` is the switch that keeps stdio behaviour untouched."""
    for _name, module, _setter, _getter in _UNIQUE_PROVIDERS:
        upstream = getattr(module, "hosted_mode", None)
        if callable(upstream):
            return bool(upstream())
    return os.environ.get("KUMIHO_MCP_HOSTED", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# §2.3 — kumiho_memory redis token bridge
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def redis_token_bridge(token: Optional[str]) -> Iterator[None]:
    """Point ``kumiho_memory``'s Redis buffer at this request's credential.

    Once WP-B lands, ``RedisMemoryBuffer`` reads the token off
    ``current_request()`` itself and this becomes a redundant (but still
    correct) override.
    """
    var = None
    try:
        import kumiho_memory  # type: ignore

        var = getattr(kumiho_memory, "redis_token_override_var", None)
    except Exception:  # noqa: BLE001 - kumiho_memory is optional at import time
        var = None

    if var is None or not token:
        yield
        return

    reset = var.set(token)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            var.reset(reset)


# ---------------------------------------------------------------------------
# §2.2 — connector tool profile
# ---------------------------------------------------------------------------


def _connector_instructions() -> str:
    """Prefer the SDK's canonical text; fall back to our copy."""
    try:
        import kumiho.mcp_server as ms  # type: ignore

        text = getattr(ms, "CONNECTOR_INSTRUCTIONS", None)
        if isinstance(text, str) and text.strip():
            return text
    except Exception:  # noqa: BLE001
        pass
    from .connector_profile import CONNECTOR_INSTRUCTIONS

    return CONNECTOR_INSTRUCTIONS


def _annotations_for(name: str) -> Optional[dict]:
    """Annotation payload for ``name``: SDK table first, local table second."""
    try:
        import kumiho.mcp_server as ms  # type: ignore

        table = getattr(ms, "TOOL_ANNOTATIONS", None)
        if isinstance(table, dict) and name in table:
            value = table[name]
            if isinstance(value, dict):
                return dict(value)
    except Exception:  # noqa: BLE001
        pass
    from .connector_profile import CONNECTOR_TOOL_ANNOTATIONS

    found = CONNECTOR_TOOL_ANNOTATIONS.get(name)
    return dict(found) if found else None


def _apply_annotations(tool: Any) -> Any:
    """Attach ``annotations`` (and ``title``) to a ``types.Tool`` if missing."""
    import mcp.types as types

    payload = _annotations_for(tool.name)
    if not payload:
        return tool
    if getattr(tool, "annotations", None) is not None:
        return tool
    try:
        annotations = types.ToolAnnotations(**payload)
    except Exception:  # noqa: BLE001 - unknown keys from a newer table
        known = set(types.ToolAnnotations.model_fields)
        annotations = types.ToolAnnotations(**{k: v for k, v in payload.items() if k in known})
    update = {"annotations": annotations}
    if getattr(tool, "title", None) is None and payload.get("title"):
        update["title"] = payload["title"]
    return tool.model_copy(update=update)


def _restrict_to_connector_profile(server: Any, allowed: tuple) -> None:
    """Wrap the low-level handlers so only ``allowed`` tools exist.

    The MCP low-level ``Server`` stores one coroutine per request type in
    ``server.request_handlers``. Wrapping there (rather than re-registering
    through the decorators) keeps the SDK's own input validation and tool cache
    intact — the cache is still populated with every tool, so a call to an
    allowed tool validates exactly as before.
    """
    import mcp.types as types

    allowed_set = set(allowed)

    original_list = server.request_handlers.get(types.ListToolsRequest)
    warned: List[str] = []
    if original_list is not None:

        async def list_tools_handler(req: Any) -> Any:
            result = await original_list(req)
            inner = getattr(result, "root", result)
            available = {t.name for t in getattr(inner, "tools", [])}
            if not warned:
                warned.append("done")
                missing = sorted(allowed_set - available)
                if missing:
                    # The connector profile names 18 tools; anything the
                    # installed SDK does not define simply cannot be exposed.
                    logger.warning(
                        "connector profile tools missing from the installed SDK",
                        extra={"missing": missing, "exposed": len(allowed_set) - len(missing)},
                    )
            tools = [t for t in getattr(inner, "tools", []) if t.name in allowed_set]
            order = {name: i for i, name in enumerate(allowed)}
            tools.sort(key=lambda t: order.get(t.name, len(order)))
            tools = [_apply_annotations(t) for t in tools]
            return types.ServerResult(types.ListToolsResult(tools=tools))

        server.request_handlers[types.ListToolsRequest] = list_tools_handler

    _guard_call_tool(server, lambda: allowed_set)


def _not_available(name: str) -> Any:
    import mcp.types as types

    return types.ServerResult(
        types.CallToolResult(
            isError=True,
            content=[
                types.TextContent(
                    type="text",
                    text=(
                        f"Tool {name!r} is not available on the Kumiho Memory connector. "
                        "Call tools/list to see what is."
                    ),
                )
            ],
        )
    )


def _guard_call_tool(server: Any, resolve_allowed) -> None:
    """Refuse ``tools/call`` for anything the server does not list.

    This is applied on *both* paths on purpose. Filtering ``tools/list`` alone
    hides a tool from the model but leaves the JSON-RPC method reachable by
    anyone who knows the name — and the full surface includes
    ``kumiho_delete_project``. A resource server on the public internet must
    enforce its own profile rather than trust the list to be the boundary.
    """
    import mcp.types as types

    original = server.request_handlers.get(types.CallToolRequest)
    if original is None:  # pragma: no cover - a server with no tools
        return

    async def handler(req: Any) -> Any:
        name = req.params.name
        allowed = resolve_allowed()
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed and name not in allowed:
            logger.warning(
                "refused a tool call outside the connector profile", extra={"tool": name}
            )
            return _not_available(name)
        return await original(req)

    server.request_handlers[types.CallToolRequest] = handler


def _listed_tool_names(server: Any):
    """Lazily resolve (and cache) the names the server's own ``tools/list`` returns."""
    import mcp.types as types

    cache: List[set] = []

    async def resolve() -> set:
        if cache:
            return cache[0]
        handler = server.request_handlers.get(types.ListToolsRequest)
        if handler is None:  # pragma: no cover
            return set()
        result = await handler(types.ListToolsRequest(method="tools/list"))
        names = {tool.name for tool in getattr(result.root, "tools", [])}
        cache.append(names)
        return names

    return resolve


def _drop_unused_capabilities(server: Any) -> None:
    """Hide resources/prompts the connector profile does not advertise.

    The full server registers resource and prompt handlers that reach for an
    ambient client. In hosted mode there is no ambient client, and the Claude
    directory only reviews tools, so removing them shrinks the surface.
    """
    import mcp.types as types

    for request_type in (
        types.ListResourcesRequest,
        types.ReadResourceRequest,
        types.ListPromptsRequest,
        types.GetPromptRequest,
        types.ListResourceTemplatesRequest,
    ):
        server.request_handlers.pop(request_type, None)


def build_server(
    *,
    profile: str = "connector",
    instructions: Optional[str] = None,
    restrict_capabilities: bool = True,
    create: Optional[Callable[..., Any]] = None,
) -> Any:
    """Return a configured low-level MCP ``Server`` for the connector profile.

    Returns the server; the chosen strategy is recorded on
    ``server.__kumiho_profile_source__`` for the tests and the health endpoint.
    """
    if create is None:
        import kumiho.mcp_server as ms  # type: ignore

        create = ms.create_mcp_server

    text = instructions if instructions is not None else _connector_instructions()

    try:
        params = inspect.signature(create).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins / C funcs
        params = {}

    accepts_profile = "profile" in params
    accepts_instructions = "instructions" in params

    if accepts_profile:
        kwargs = {"profile": profile}
        if accepts_instructions:
            kwargs["instructions"] = text
        server = create(**kwargs)
        source = "native"
        # The SDK's profile filters tools/list but (as of kumiho 0.13.0) still
        # dispatches tools/call for unlisted names. Guard against whatever it
        # actually lists, so a future profile change is picked up for free.
        _guard_call_tool(server, _listed_tool_names(server))
        logger.info("mcp server built via native profile support", extra={"profile": profile})
    else:
        server = create()
        from .connector_profile import CONNECTOR_TOOLS

        _restrict_to_connector_profile(server, CONNECTOR_TOOLS)
        source = "shim"
        logger.warning(
            "kumiho.mcp_server.create_mcp_server has no profile= parameter; "
            "filtering tools locally (WP-A not landed yet)",
            extra={"profile": profile, "tool_count": len(CONNECTOR_TOOLS)},
        )

    if restrict_capabilities:
        _drop_unused_capabilities(server)

    if not getattr(server, "instructions", None):
        try:
            server.instructions = text
        except Exception:  # noqa: BLE001 - frozen server implementations
            logger.warning("could not set server instructions")

    try:
        server.__kumiho_profile_source__ = source
    except Exception:  # noqa: BLE001
        pass
    return server


__all__ = [
    "RequestContext",
    "request_context",
    "current_request",
    "hosted_mode",
    "redis_token_bridge",
    "build_server",
    "HAVE_UPSTREAM_REQUEST_CONTEXT",
    "PROVIDER_NAMES",
]
