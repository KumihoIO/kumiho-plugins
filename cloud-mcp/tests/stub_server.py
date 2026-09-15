"""A one-tool MCP server used by the tests that are not about the tool profile.

``whoami`` reports what the *ambient* request state looks like from inside a
tool handler: the client that ``kumiho.get_client()`` resolves to, the tenant
on the ``RequestContext``, and the token ``kumiho_memory``'s Redis buffer would
use. That is exactly the state a real Kumiho tool depends on, so if two tenants
ever bled into each other this is where it would show.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import mcp.types as types
from mcp.server.lowlevel import Server


def _snapshot() -> dict:
    import kumiho

    from kumiho_cloud_mcp._compat import current_request

    client = kumiho.get_client()
    ctx = current_request()
    redis_token = None
    try:
        import kumiho_memory

        var = getattr(kumiho_memory, "redis_token_override_var", None)
        redis_token = var.get() if var is not None else None
    except Exception:  # noqa: BLE001
        redis_token = None

    return {
        "client_target": getattr(client, "target", None),
        "client_tenant": getattr(client, "tenant_id", None),
        "client_token": getattr(client, "token", None),
        "ctx_tenant": getattr(ctx, "tenant_id", None),
        "ctx_user": getattr(ctx, "user_id", None),
        "ctx_token_id": getattr(ctx, "token_id", None),
        "redis_token_set": bool(redis_token),
        "redis_token_matches_ctx": bool(ctx) and redis_token == getattr(ctx, "auth_token", None),
    }


def build_stub_server(delay: float = 0.0) -> Server:
    server: Server = Server("kumiho-stub")

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return [
            types.Tool(
                name="whoami",
                description="Report the ambient tenant state seen inside a tool handler.",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> Any:
        if name != "whoami":
            raise ValueError(f"unknown tool {name}")

        def work() -> dict:
            # Real tool handlers are blocking gRPC calls dispatched with
            # asyncio.to_thread; do the same so contextvar propagation is
            # exercised the way production exercises it.
            return _snapshot()

        if delay:
            await asyncio.sleep(delay)
        snapshot = await asyncio.to_thread(work)
        return [types.TextContent(type="text", text=json.dumps(snapshot))]

    server.__kumiho_profile_source__ = "stub"  # type: ignore[attr-defined]
    return server
