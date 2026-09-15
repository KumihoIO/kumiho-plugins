"""Real MCP 2.x client over HTTP, with disposable in-process backend fixtures."""
import json

import httpx2
import pytest
from conftest import base_claims, client_for
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import create_app

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_real_v2_http_client_preserves_auth_and_tenant_context(
    mode, settings, fake_clients, control_plane, keypair
):
    app = create_app(settings, server_factory=build_stub_server)
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {token}"}) as http:
            transport = streamable_http_client("https://mcp.test/mcp", http_client=http)
            async with Client(transport, mode=mode, cache=None) as client:
                listing = await client.list_tools()
                assert [tool.name for tool in listing.tools] == ["whoami"]
                result = await client.call_tool("whoami", {})
                assert not result.is_error
                data = json.loads(result.content[0].text)
                assert data["ctx_tenant"] == "tenant-aaaa"
                assert data["client_token"] == token


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_real_v2_client_validates_the_kumiho_profile(mode, settings, fake_clients, control_plane, keypair):
    app = create_app(settings)
    async with client_for(app, control_plane):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {keypair.sign(base_claims())}"}) as http:
            async with Client(streamable_http_client("https://mcp.test/mcp", http_client=http),
                              mode=mode, cache=None) as client:
                listing = await client.list_tools()
                assert len(listing.tools) == 18
                assert all(t.meta["securitySchemes"] == [{"type": "oauth2", "scopes": ["memory"]}]
                           for t in listing.tools)
                bad_input = await client.call_tool("kumiho_memory_engage", {"query": ["wrong-type"]})
                assert bad_input.is_error
                hidden = await client.call_tool("kumiho_delete_project", {"name": "unused"})
                assert hidden.is_error
                assert "not available" in hidden.content[0].text
