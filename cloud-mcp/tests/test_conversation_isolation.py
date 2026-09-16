"""Exercise the hosted wrapper and real SDK buffer handlers together."""
import json
from dataclasses import replace
from types import SimpleNamespace

import anyio
import mcp.types as types
import pytest

from kumiho_cloud_mcp._compat import RequestContext, build_server, request_context

pytestmark = pytest.mark.anyio


@pytest.fixture
def buffered_server(monkeypatch):
    import kumiho_memory.mcp_tools as memory
    messages = {}
    class Buffer:
        async def get_messages(self, *, project, session_id, limit):
            return {"messages": messages.get(session_id, [])[:limit]}
        async def clear_session(self, project, session_id):
            messages.pop(session_id, None)
            return {"success": True}
    class Manager:
        project = "shared-workspace"
        redis_buffer = Buffer()
        async def resolve_session_id(self, **kwargs):
            # The old SDK fallback converges both conversations on this pointer.
            return "shared-active-session", "active_session"
        async def add_assistant_response(self, *, session_id, response):
            messages.setdefault(session_id, []).append(response)
            return {"success": True}
    monkeypatch.setattr(memory, "_get_manager", lambda: Manager())
    return SimpleNamespace(server=build_server(), messages=messages)


def identity(**over):
    return RequestContext(tenant_id="tenant", user_id="user", auth_token="token", client_id="app", context="chatgpt", **over)


async def call(fixture, ctx, name, args):
    with request_context(ctx):
        result = await fixture.server.get_request_handler("tools/call").handler(
            None, types.CallToolRequestParams(name=name, arguments=args)
        )
    return result, json.loads(result.content[0].text)


async def start(fixture, ctx):
    result, payload = await call(fixture, ctx, "kumiho_memory_reflect", {"response": "do not buffer yet"})
    assert result.is_error is True
    assert payload["error"] == "session_required"
    assert fixture.messages == {}
    return payload["session_id"]


async def test_missing_conversation_never_reads_or_writes_shared_pointer(buffered_server):
    a = await start(buffered_server, identity())
    b = await start(buffered_server, identity())
    assert a != b
    for tool in ("kumiho_chat_get", "kumiho_chat_clear", "kumiho_memory_consolidate"):
        result, payload = await call(buffered_server, identity(), tool, {})
        assert result.is_error is True
        assert payload["error"] == "session_required"
    assert buffered_server.messages == {}


async def test_two_chats_read_and_clear_only_their_buffer(buffered_server):
    ctx = identity()
    a, b = await start(buffered_server, ctx), await start(buffered_server, ctx)
    async def reflect(sid, message):
        result, payload = await call(buffered_server, ctx, "kumiho_memory_reflect", {"session_id": sid, "response": message})
        assert not result.is_error
        assert payload["session_id"] == sid
    async with anyio.create_task_group() as group:
        group.start_soon(reflect, a, "chat a")
        group.start_soon(reflect, b, "chat b")
    _, payload = await call(buffered_server, ctx, "kumiho_chat_get", {"session_id": a})
    assert payload["messages"] == ["chat a"]
    await call(buffered_server, ctx, "kumiho_chat_clear", {"session_id": a})
    _, payload = await call(buffered_server, ctx, "kumiho_chat_get", {"session_id": b})
    assert payload["messages"] == ["chat b"]


@pytest.mark.parametrize("changed", [{"user_id": "other"}, {"tenant_id": "other"}, {"client_id": "other"}, {"context": "codex"}])
async def test_session_from_another_identity_is_rejected(buffered_server, changed):
    ctx = identity()
    sid = await start(buffered_server, ctx)
    result, payload = await call(buffered_server, replace(ctx, **changed), "kumiho_chat_get", {"session_id": sid})
    assert result.is_error is True
    assert payload["error"] == "invalid_session"


async def test_host_id_is_scoped_and_cannot_be_overridden(buffered_server):
    ctx = identity(session_id="host-conversation")
    _, first = await call(buffered_server, ctx, "kumiho_memory_reflect", {"response": "first"})
    _, second = await call(buffered_server, replace(ctx, user_id="other"), "kumiho_memory_reflect", {"response": "second"})
    assert first["session_id"] != second["session_id"]
    _, echoed = await call(buffered_server, replace(ctx, auth_token="rotated"), "kumiho_chat_get", {"session_id": first["session_id"]})
    assert echoed["messages"] == ["first"]
    result, payload = await call(buffered_server, ctx, "kumiho_chat_clear", {"session_id": "different-conversation"})
    assert result.is_error is True
    assert payload["error"] == "invalid_session"


@pytest.mark.parametrize("session", ["", "  ", 1, [], "x" * 513])
async def test_invalid_session_never_touches_the_buffer(buffered_server, session):
    result, _ = await call(buffered_server, identity(), "kumiho_memory_reflect", {"session_id": session, "response": "no"})
    assert result.is_error is True
    assert buffered_server.messages == {}
