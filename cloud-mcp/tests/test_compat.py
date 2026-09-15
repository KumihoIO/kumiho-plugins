"""The WP-A / WP-B compatibility shims.

These tests pin the *contract* rather than the current SDK: each one builds a
fake ``create_mcp_server`` with a particular signature and asserts the shim
takes the right branch. When WP-A lands, ``test_native_profile_is_preferred``
is what proves the hand-off happened.
"""

from __future__ import annotations

import json
from typing import Any, List

import mcp.types as types
import pytest
from mcp.server.lowlevel import Server

from kumiho_cloud_mcp import _compat
from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOLS

pytestmark = pytest.mark.anyio


def _server_with(names: List[str]) -> Server:
    server: Server = Server("fake")

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return [
            types.Tool(name=n, description=n, inputSchema={"type": "object", "properties": {}})
            for n in names
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> Any:
        return [types.TextContent(type="text", text=json.dumps({"called": name}))]

    return server


async def _list(server: Server) -> List[types.Tool]:
    handler = server.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    return result.root.tools


async def _call(server: Server, name: str):
    handler = server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name=name, arguments={})
    )
    return (await handler(request)).root


# ---------------------------------------------------------------------------
# build_server strategy selection
# ---------------------------------------------------------------------------


async def test_native_profile_is_preferred_when_the_signature_accepts_it():
    seen = {}

    def create(profile=None, instructions=None):
        seen["profile"] = profile
        seen["instructions"] = instructions
        return _server_with(["kumiho_memory_engage", "kumiho_delete_project"])

    server = _compat.build_server(create=create)
    assert seen["profile"] == "connector"
    assert "kumiho_memory_engage" in seen["instructions"]
    assert server.__kumiho_profile_source__ == "native"
    # Native means we trust the SDK: no local filtering is applied.
    assert {t.name for t in await _list(server)} == {
        "kumiho_memory_engage",
        "kumiho_delete_project",
    }


async def test_native_path_still_blocks_calls_to_unlisted_tools():
    """The SDK filters tools/list but dispatches any tools/call by name.

    That is a hole on a public endpoint, so the guard is applied on the native
    path too, keyed to whatever the server itself lists.
    """

    def create(profile=None, instructions=None):
        server = _server_with(["kumiho_memory_engage", "kumiho_delete_project"])
        original = server.request_handlers[types.ListToolsRequest]

        async def only_engage(req):
            result = await original(req)
            keep = [t for t in result.root.tools if t.name == "kumiho_memory_engage"]
            return types.ServerResult(types.ListToolsResult(tools=keep))

        server.request_handlers[types.ListToolsRequest] = only_engage
        return server

    server = _compat.build_server(create=create)
    assert server.__kumiho_profile_source__ == "native"
    assert [t.name for t in await _list(server)] == ["kumiho_memory_engage"]

    blocked = await _call(server, "kumiho_delete_project")
    assert blocked.isError is True
    assert "not available" in blocked.content[0].text
    assert (await _call(server, "kumiho_memory_engage")).isError is not True


async def test_profile_only_signature_still_gets_instructions_set():
    def create(profile=None):
        return _server_with(["kumiho_memory_engage"])

    server = _compat.build_server(create=create)
    assert server.__kumiho_profile_source__ == "native"
    assert "kumiho_memory_engage" in server.instructions


async def test_shim_filters_when_the_signature_is_the_old_one():
    def create():
        return _server_with(list(CONNECTOR_TOOLS) + ["kumiho_delete_project", "kumiho_delete_space"])

    server = _compat.build_server(create=create)
    assert server.__kumiho_profile_source__ == "shim"
    names = [t.name for t in await _list(server)]
    assert names == list(CONNECTOR_TOOLS)


async def test_shim_intersects_with_what_the_sdk_actually_defines():
    subset = list(CONNECTOR_TOOLS[:5])

    def create():
        return _server_with(subset + ["kumiho_delete_project"])

    server = _compat.build_server(create=lambda: create())
    assert [t.name for t in await _list(server)] == subset


async def test_shim_attaches_annotations_and_titles():
    def create():
        return _server_with(list(CONNECTOR_TOOLS))

    server = _compat.build_server(create=create)
    tools = {t.name: t for t in await _list(server)}
    engage = tools["kumiho_memory_engage"]
    assert engage.title == "Engage memory before responding"
    assert engage.annotations.readOnlyHint is True
    assert engage.annotations.openWorldHint is False
    forget = tools["kumiho_deprecate_item"]
    assert forget.annotations.destructiveHint is True
    assert forget.annotations.title == "Forget a memory"


async def test_shim_blocks_calls_to_tools_outside_the_profile():
    def create():
        return _server_with(list(CONNECTOR_TOOLS) + ["kumiho_delete_project"])

    server = _compat.build_server(create=create)
    blocked = await _call(server, "kumiho_delete_project")
    assert blocked.isError is True
    assert "not available" in blocked.content[0].text

    allowed = await _call(server, "kumiho_memory_engage")
    assert allowed.isError is not True


async def test_capabilities_that_need_an_ambient_client_are_dropped():
    def create():
        server = _server_with(["kumiho_memory_engage"])

        @server.list_resources()
        async def list_resources() -> List[types.Resource]:  # pragma: no cover
            return []

        return server

    server = _compat.build_server(create=create)
    assert types.ListResourcesRequest not in server.request_handlers


async def test_explicit_instructions_win():
    def create():
        return _server_with(["kumiho_memory_engage"])

    server = _compat.build_server(create=create, instructions="custom text")
    assert server.instructions == "custom text"


# ---------------------------------------------------------------------------
# request context
# ---------------------------------------------------------------------------


def test_request_context_roundtrip():
    assert _compat.current_request() is None
    ctx = _compat.RequestContext(tenant_id="t1", user_id="u1", auth_token="tok")
    with _compat.request_context(ctx) as bound:
        assert bound is ctx
        assert _compat.current_request() is ctx
        assert _compat.current_request().context == "claude"
    assert _compat.current_request() is None


def test_request_context_nests():
    outer = _compat.RequestContext(tenant_id="t1", user_id="u1", auth_token="a")
    inner = _compat.RequestContext(tenant_id="t2", user_id="u2", auth_token="b")
    with _compat.request_context(outer):
        with _compat.request_context(inner):
            assert _compat.current_request().tenant_id == "t2"
        assert _compat.current_request().tenant_id == "t1"


def test_request_context_has_the_fields_the_plan_specifies():
    ctx = _compat.RequestContext(
        tenant_id="t",
        user_id="u",
        auth_token="tok",
        session_id="s",
        client_id="c",
        scopes=["memory"],
        tenant_slug="slug",
        region_code="us-east-1",
        token_id="jti",
    )
    for field in (
        "tenant_id",
        "user_id",
        "auth_token",
        "context",
        "session_id",
        "client_id",
        "scopes",
        "tenant_slug",
        "region_code",
        "token_id",
    ):
        assert hasattr(ctx, field), field


def test_binding_is_visible_to_every_discovered_provider():
    """Each package may vendor its own copy of the §2.1 contextvar.

    They are *different* ContextVars until WP-A lands and everyone imports the
    canonical one, so binding one and not the others would silently leave
    kumiho_memory reading ambient credentials.
    """
    providers = []
    for name in ("kumiho.request_context", "kumiho_memory._request_context"):
        try:
            module = __import__(name, fromlist=["*"])
        except Exception:  # noqa: BLE001
            continue
        if callable(getattr(module, "current_request", None)):
            providers.append((name, module))

    ctx = _compat.RequestContext(tenant_id="t-9", user_id="u-9", auth_token="tok-9")
    with _compat.request_context(ctx):
        for name, module in providers:
            found = module.current_request()
            assert found is not None, f"{name} did not see the request"
            assert found.tenant_id == "t-9", name
    for name, module in providers:
        assert module.current_request() is None, name


def test_provider_names_are_reported():
    assert isinstance(_compat.PROVIDER_NAMES, list)
    for name in _compat.PROVIDER_NAMES:
        assert name in ("kumiho.request_context", "kumiho_memory._request_context")


def test_hosted_mode_reads_the_env(monkeypatch):
    if _compat.HAVE_UPSTREAM_REQUEST_CONTEXT:  # pragma: no cover - once WP-A lands
        pytest.skip("upstream hosted_mode() owns this")
    monkeypatch.setenv("KUMIHO_MCP_HOSTED", "1")
    assert _compat.hosted_mode() is True
    monkeypatch.setenv("KUMIHO_MCP_HOSTED", "0")
    assert _compat.hosted_mode() is False


def test_redis_token_bridge_sets_and_restores():
    kumiho_memory = pytest.importorskip("kumiho_memory")
    var = getattr(kumiho_memory, "redis_token_override_var", None)
    if var is None:  # pragma: no cover - very old kumiho-memory
        pytest.skip("no redis_token_override_var")
    assert var.get() is None
    with _compat.redis_token_bridge("secret-token"):
        assert var.get() == "secret-token"
    assert var.get() is None


def test_redis_token_bridge_is_a_noop_without_a_token():
    with _compat.redis_token_bridge(None):
        pass


def test_logging_never_serialises_a_token():
    import logging

    from kumiho_cloud_mcp.logging_setup import JsonFormatter, token_fingerprint

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    record.auth_token = "super-secret"  # type: ignore[attr-defined]
    record.tenant_id = "t1"  # type: ignore[attr-defined]
    record.token_id = "jti"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["auth_token"] == "[redacted]"
    assert payload["tenant_id"] == "t1"
    assert payload["token_id"] == "jti"

    fingerprint = token_fingerprint("super-secret")
    assert fingerprint and len(fingerprint) == 12
    assert "super-secret" not in fingerprint
