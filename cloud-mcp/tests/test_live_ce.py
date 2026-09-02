"""End-to-end round trip against a local Kumiho CE server (dev mode).

Skipped unless a CE server answers on ``KUMIHO_LOCAL_SERVER_ENDPOINT``
(default ``127.0.0.1:9190``). Unlike every other test here this one runs the
app under a real uvicorn on a real socket and drives it with the *real* MCP
Python client over streamable HTTP — so it exercises the wire format, the SSE
framing and the session headers, not just our handlers.

    python -m pytest tests/test_live_ce.py -v -s
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Optional

import pytest

pytestmark = pytest.mark.anyio

CE_ENDPOINT = os.environ.get("KUMIHO_LOCAL_SERVER_ENDPOINT", "127.0.0.1:9190")


def _reachable(endpoint: str, timeout: float = 1.0) -> bool:
    host, _, port = endpoint.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class UvicornThread:
    """Run the app on a real port so a real MCP client can connect."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.server: Optional[object] = None
        self.thread: Optional[threading.Thread] = None

    def __enter__(self):
        import uvicorn

        from kumiho_cloud_mcp.app import create_app
        from kumiho_cloud_mcp.settings import load_settings

        os.environ["KUMIHO_MCP_DEV_MODE"] = "ce"
        os.environ.setdefault("KUMIHO_LOCAL_SERVER_ENDPOINT", CE_ENDPOINT)
        os.environ["PORT"] = str(self.port)
        os.environ["KUMIHO_MCP_LOG_LEVEL"] = "WARNING"
        app = create_app(load_settings())

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if getattr(self.server, "started", False):
                break
            time.sleep(0.1)
        else:  # pragma: no cover
            raise RuntimeError("uvicorn did not start")
        return self

    def __exit__(self, *exc) -> None:
        if self.server is not None:
            self.server.should_exit = True  # type: ignore[attr-defined]
        if self.thread is not None:
            self.thread.join(timeout=15)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.mark.skipif(
    not _reachable(CE_ENDPOINT),
    reason=f"no Kumiho CE server on {CE_ENDPOINT}",
)
async def test_live_round_trip_against_ce(capsys):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    with UvicornThread(_free_port()) as server:
        async with streamablehttp_client(server.url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print("\n--- initialize ---")
                print("server:", init.serverInfo.name, init.serverInfo.version)
                print("protocol:", init.protocolVersion)
                print("instructions (first 120):", (init.instructions or "")[:120])

                listing = await session.list_tools()
                names = [tool.name for tool in listing.tools]
                print("\n--- tools/list ---")
                print(f"{len(names)} tools: {names}")
                for tool in listing.tools[:3]:
                    print(
                        f"  {tool.name}: title={tool.annotations.title!r} "
                        f"readOnly={tool.annotations.readOnlyHint} "
                        f"destructive={tool.annotations.destructiveHint}"
                    )

                print("\n--- tools/call kumiho_memory_engage ---")
                result = await session.call_tool(
                    "kumiho_memory_engage",
                    {"query": "What did we decide about the hosted Claude connector?"},
                )
                text = result.content[0].text if result.content else ""
                print("isError:", result.isError)
                print("payload:", text[:1500])

    from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOLS

    assert set(names) <= set(CONNECTOR_TOOLS)
    assert "kumiho_memory_engage" in names
    assert init.instructions and "kumiho_memory_engage" in init.instructions
    # The call must reach the tool. Whether the tool then finds memories
    # depends on what this CE instance holds, so only the transport is asserted.
    assert text, "engage returned no content"
    json.loads(text)
