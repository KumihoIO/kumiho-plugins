"""A thin wrapper over the real ``mcp`` streamable-HTTP client.

Deliberately the published client rather than raw JSON-RPC: the point of these
tests is that a client Claude itself would use can complete the protocol, so
initialize negotiation, the SSE framing of a POST response and the session
header all have to be exercised by the library, not re-implemented here.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, AsyncIterator, Dict, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def result_payload(result: Any) -> Any:
    """The tool's JSON body, or its raw text when it is not JSON."""
    raw = "\n".join(getattr(block, "text", "") or "" for block in result.content)
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class Connector:
    """One MCP session against the live server."""

    def __init__(self, session: ClientSession, init: Any) -> None:
        self.session = session
        self.init = init

    async def list_tools(self):
        return (await self.session.list_tools()).tools

    async def call(self, name: str, arguments: Optional[Dict[str, Any]] = None):
        """``(is_error, payload)`` — errors are values here, not exceptions."""
        result = await self.session.call_tool(name, arguments or {})
        return bool(result.isError), result_payload(result)

    async def ok(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        is_error, payload = await self.call(name, arguments)
        assert not is_error, f"{name} failed: {str(payload)[:1500]}"
        return payload


@contextlib.asynccontextmanager
async def connect(
    url: str, headers: Optional[Dict[str, str]] = None
) -> AsyncIterator[Connector]:
    async with streamablehttp_client(url, headers=headers or {}) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            yield Connector(session, init)
