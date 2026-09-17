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
import copy
import importlib
import inspect
import json
import logging
import os
from dataclasses import dataclass, field, replace
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
    """Use the hosted conversation contract, independent of stdio defaults."""
    from .connector_profile import CONNECTOR_INSTRUCTIONS

    return CONNECTOR_INSTRUCTIONS


def _annotations_for(name: str) -> Optional[dict]:
    """Use reviewed hosted annotations before any upstream defaults."""
    from .connector_profile import CONNECTOR_TOOL_ANNOTATIONS

    found = CONNECTOR_TOOL_ANNOTATIONS.get(name)
    if found:
        return dict(found)
    return None


def _apply_annotations(tool: Any) -> Any:
    """Apply the reviewed policy, including overrides to native annotations."""
    import mcp.types as types

    payload = _annotations_for(tool.name)
    if not payload:
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


async def listed_tools(server: Any) -> list:
    """Read the static tool catalog using MCP 2.x's public handler API."""
    entry = server.get_request_handler("tools/list")
    if entry is None:
        raise RuntimeError("MCP server has no tools/list handler")
    return (await entry.handler(None, None)).tools


async def _openai_auth_metadata(ctx: Any, call_next: Any) -> Any:
    """Mirror auth policy after core MCP result validation/serialization.

    MCP 2.x removes non-protocol fields from typed Tool results. OpenAI also
    reads securitySchemes at the top level, so use public response middleware
    to mirror the policy stored in the standard _meta extension point.
    """
    result = await call_next(ctx)
    if ctx.method == "tools/list" and isinstance(result, dict):
        result = {**result, "tools": [
            {**tool, "securitySchemes": tool["_meta"]["securitySchemes"]}
            for tool in result.get("tools", [])
        ]}
    return result


def build_server(
    *,
    profile: str = "connector",
    instructions: Optional[str] = None,
    restrict_capabilities: bool = True,
    create: Optional[Callable[..., Any]] = None,
) -> Any:
    """Expose the Kumiho tool handlers through a native MCP 2.x server.

    Kumiho 0.13.0 already registers v2 constructor handlers and validates tool
    inputs. Keep those implementations, project only the hosted capabilities,
    and enforce the published tool list at dispatch as well as discovery.
    Handler registration uses the native API. Credential-bound SDK handle
    caches are isolated per tool call until the SDK provides that lifetime.
    """
    import mcp.types as types
    from mcp.server import Server

    if create is None:
        import kumiho.mcp_server as ms

        from .sdk_caches import install_sdk_cache_isolation

        install_sdk_cache_isolation()
        create = ms.create_mcp_server
    text = instructions if instructions is not None else _connector_instructions()
    try:
        params = inspect.signature(create).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {}
    source = "native" if "profile" in params else "shim"
    if "profile" in params:
        kwargs["profile"] = profile
    if "instructions" in params:
        kwargs["instructions"] = text
    upstream = create(**kwargs)
    if not callable(getattr(upstream, "get_request_handler", None)):
        raise RuntimeError("The hosted MCP server requires MCP SDK 2.2 or newer")
    original_list = upstream.get_request_handler("tools/list")
    original_call = upstream.get_request_handler("tools/call")
    if original_list is None or original_call is None:
        raise RuntimeError("Kumiho SDK did not register the required tool handlers")

    from .connector_profile import CONNECTOR_TOOL_DESCRIPTIONS, CONNECTOR_TOOLS
    from .sdk_caches import sdk_cache_scope
    from .sessions import (
        SESSION_DESCRIPTION,
        SESSION_TOOLS,
        SessionError,
        resolve_buffer_session,
    )
    allowed = set(CONNECTOR_TOOLS)
    order = {name: i for i, name in enumerate(CONNECTOR_TOOLS)}

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        result = await original_list.handler(ctx, params)
        # An SDK upgrade must not silently expand the reviewed hosted surface.
        tools = [tool for tool in result.tools if tool.name in allowed]
        tools.sort(key=lambda tool: order[tool.name])
        annotated = []
        for tool in tools:
            tool = _apply_annotations(tool)
            if tool.name in CONNECTOR_TOOL_DESCRIPTIONS:
                tool = tool.model_copy(update={"description": CONNECTOR_TOOL_DESCRIPTIONS[tool.name]})
            if tool.name == "kumiho_search_items":
                schema = copy.deepcopy(tool.input_schema)
                schema.get("properties", {}).pop("auth_token", None)
                if "required" in schema:
                    schema["required"] = [key for key in schema["required"] if key != "auth_token"]
                tool = tool.model_copy(update={"input_schema": schema})
            if tool.name in SESSION_TOOLS:
                schema = copy.deepcopy(tool.input_schema)
                schema.setdefault("properties", {})["session_id"] = {
                    "type": "string", "minLength": 1, "maxLength": 512,
                    "description": SESSION_DESCRIPTION,
                }
                # Every session tool has a hosted override (a KeyError here is
                # deliberate): the SDK's own text describes stdio session rules.
                tool = tool.model_copy(update={
                    "input_schema": schema,
                    "description": f"{CONNECTOR_TOOL_DESCRIPTIONS[tool.name]}\n\n{SESSION_DESCRIPTION}",
                })
            annotated.append(tool.model_copy(update={"meta": {
                **(tool.meta or {}),
                "securitySchemes": [{"type": "oauth2", "scopes": ["memory"]}],
            }}))
        return result.model_copy(update={"tools": annotated})

    async def on_call_tool(ctx: Any, params: Any) -> types.CallToolResult:
        catalog = await on_list_tools(ctx, None)
        if params.name not in {tool.name for tool in catalog.tools}:
            return types.CallToolResult(is_error=True, content=[types.TextContent(
                type="text", text=f"Tool {params.name!r} is not available on the Kumiho Memory connector. Call tools/list to see what is.",
            )])
        if params.name == "kumiho_search_items" and "auth_token" in (params.arguments or {}):
            # Do not accept secrets from a conversation, including stale clients
            # that cached the SDK's stdio-only credential override field.
            return types.CallToolResult(is_error=True, content=[types.TextContent(
                type="text", text="Do not provide credentials in tool arguments. Connect your Kumiho account through OAuth, then retry without auth_token.",
            )])
        request = current_request()
        if params.name in SESSION_TOOLS and request is not None:
            arguments = dict(params.arguments or {})
            try:
                session_id = resolve_buffer_session(request, arguments)
            except SessionError as exc:
                return types.CallToolResult(is_error=True, content=[types.TextContent(
                    type="text", text=json.dumps(exc.payload),
                )], structured_content=exc.payload)
            arguments["session_id"] = session_id
            params = params.model_copy(update={"arguments": arguments})
            with request_context(replace(request, session_id=session_id)), sdk_cache_scope():
                return await original_call.handler(ctx, params)
        # Upstream's v2 path validates inputSchema before dispatching to the
        # same tenant-scoped, blocking tool implementations as its v1 path.
        with sdk_cache_scope():
            return await original_call.handler(ctx, params)

    if restrict_capabilities:
        @contextlib.asynccontextmanager
        async def lifespan(_server):
            async with upstream.lifespan(upstream) as state:
                yield state

        server = Server(
            upstream.name, version=upstream.version, title=upstream.title,
            description=upstream.description, instructions=text,
            website_url=upstream.website_url, icons=upstream.icons,
            lifespan=lifespan, on_list_tools=on_list_tools, on_call_tool=on_call_tool,
        )
    else:
        server = upstream
        server.instructions = text
        server.add_request_handler("tools/list", original_list.params_type, on_list_tools)
        server.add_request_handler("tools/call", original_call.params_type, on_call_tool)
    server.middleware.append(_openai_auth_metadata)
    server.__kumiho_profile_source__ = source
    return server


__all__ = [
    "RequestContext",
    "request_context",
    "current_request",
    "hosted_mode",
    "redis_token_bridge",
    "build_server",
    "listed_tools",
    "HAVE_UPSTREAM_REQUEST_CONTEXT",
    "PROVIDER_NAMES",
]
