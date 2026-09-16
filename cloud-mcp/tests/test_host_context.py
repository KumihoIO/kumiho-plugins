"""The deployment chooses host context; caller identity stays token-derived."""
import pytest
from starlette.requests import Request

from kumiho_cloud_mcp.app import _context_for
from kumiho_cloud_mcp.settings import load_settings


def test_host_context_is_explicit_and_validated():
    assert load_settings({}).host_context == "claude"
    assert load_settings({"KUMIHO_MCP_HOST_CONTEXT": "chatgpt"}).host_context == "chatgpt"
    assert load_settings({"KUMIHO_MCP_HOST_CONTEXT": "codex"}).host_context == "codex"
    with pytest.raises(ValueError, match="HOST_CONTEXT"):
        load_settings({"KUMIHO_MCP_HOST_CONTEXT": "unknown"})


def test_context_does_not_invent_conversation_identity():
    # The principal constructor is tested through transport/auth tests; this
    # exercises the mapping independently of a particular token format.
    from types import SimpleNamespace
    principal = SimpleNamespace(tenant_id="t1", user_id="u1", token="test",
        client_id="oauth-client", scopes=("memory",), tenant_slug="acme",
        region_code="ap-northeast-2", token_id="jti")
    request = Request({"type": "http", "headers": [(b"x-kumiho-host", b"claude")]})
    ctx = _context_for(principal, request, "chatgpt")
    assert ctx.context == "chatgpt"
    assert ctx.tenant_id == "t1"
    assert ctx.session_id is None
