"""Security boundaries of the optional live developer-test runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location("developer_test", Path(__file__).parents[1] / "scripts" / "developer_test.py")
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("query", [
    {},
    {"state": ["비정상"], "iss": [runner.ISSUER]},
    {"state": ["wrong"], "iss": [runner.ISSUER]},
    {"state": ["expected"], "iss": ["https://wrong.example"]},
    {"state": ["expected", "injected"], "iss": [runner.ISSUER]},
    {"state": ["expected"], "iss": [runner.ISSUER, "https://wrong.example"]},
])
def test_callback_rejects_mismatch_and_ambiguous_parameters(query):
    assert not runner.callback_valid(query, "expected")


def test_callback_requires_exact_state_and_issuer():
    assert runner.callback_valid({"state": ["expected"], "iss": [runner.ISSUER]}, "expected")


@pytest.mark.parametrize("url", [
    "http://control.kumiho.cloud/api/oauth/token",
    "https://control.kumiho.cloud.evil.example/api/oauth/token",
    "https://control.kumiho.cloud@evil.example/api/oauth/token",
    "https://evil.example/api/oauth/token",
    "https://control.kumiho.cloud/api/oauth/token#fragment",
])
def test_token_endpoints_cannot_redirect_credentials(url):
    with pytest.raises(RuntimeError):
        runner.endpoint({"token_endpoint": url}, "token_endpoint")


def test_public_probe_never_sends_credentials():
    seen = []

    def handle(request):
        seen.append(request)
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": runner.MCP_URL, "authorization_servers": [runner.ISSUER]})
        if request.url.path == "/mcp":
            return httpx.Response(401, headers={"www-authenticate": 'Bearer resource_metadata="https://mcp.kumiho.cloud/.well-known/oauth-protected-resource"'})
        return httpx.Response(200, json={"issuer": runner.ISSUER, "code_challenge_methods_supported": ["S256"]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runner.public_probes(client)
    assert len(seen) == 4

@pytest.fixture
def fake_live_session(monkeypatch):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    class FakeSession:
        empty_search = False
        fail_cleanup = False
        item = ""
        buffers = {}
        clear_calls = []
        ids_issued = 0

        async def initialize(self):
            return SimpleNamespace(server_info=SimpleNamespace(name="test"))

        async def list_tools(self):
            definitions = json.loads((Path(__file__).parents[1] / "chatgpt-app-submission.json").read_text(encoding="utf-8"))["tools"]
            tools = []
            for name, entry in definitions.items():
                tools.append(SimpleNamespace(name=name, input_schema={"properties": {}}, model_dump=lambda entry=entry, **_kw: entry))
            return SimpleNamespace(tools=tools)

        async def call_tool(self, name, arguments):
            error = False
            data = {"ok": True}
            if name == "kumiho_memory_store":
                self.item = "kref://CognitiveMemory/" + arguments["space_path"] + "/fixture.conversation"
                data = {"item_kref": self.item}
            elif name == "kumiho_get_item":
                data = {"kref": self.item, "deprecated": getattr(self, "deprecated", False)}
            elif name == "kumiho_memory_retrieve":
                data = {"item_krefs": [] if self.empty_search else [self.item]}
            elif name == "kumiho_deprecate_item":
                self.deprecated = arguments["deprecated"]
                data = {"updated": True, "deprecated": self.deprecated}
            elif name == "kumiho_chat_get":
                sid = arguments.get("session_id")
                if not sid:
                    self.ids_issued += 1
                    data = {"error": "session_required", "session_id": f"session-{self.ids_issued}"}
                    error = True
                else:
                    data = {"messages": self.buffers.get(sid, [])}
            elif name == "kumiho_memory_reflect":
                self.buffers[arguments["session_id"]] = [{"content": arguments["response"]}]
                data = {"buffered": True}
            elif name == "kumiho_chat_clear":
                sid = arguments["session_id"]
                self.clear_calls.append(sid)
                if self.fail_cleanup and sid == "session-1" and self.clear_calls.count(sid) == 2:
                    raise RuntimeError("simulated first cleanup failure")
                self.buffers[sid] = []
            return SimpleNamespace(is_error=error, content=[SimpleNamespace(text=json.dumps(data))])

    fake = FakeSession()
    fake.buffers = {}
    fake.clear_calls = []

    @asynccontextmanager
    async def context(*_args, **_kwargs):
        yield fake

    @asynccontextmanager
    async def transport(*_args, **_kwargs):
        yield None, None

    monkeypatch.setattr(runner.httpx2, "AsyncClient", context)
    monkeypatch.setattr(runner, "streamable_http_client", transport)
    monkeypatch.setattr(runner, "ClientSession", context)
    return fake


@pytest.mark.anyio
async def test_live_search_must_find_the_created_fixture(fake_live_session):
    fake_live_session.empty_search = True
    with pytest.raises(RuntimeError, match="search returns the created fixture"):
        await runner.authenticated_checks("test-only", writes=True)


@pytest.mark.anyio
async def test_live_cleanup_attempts_both_buffers_after_first_failure(fake_live_session):
    fake_live_session.fail_cleanup = True
    with pytest.raises(RuntimeError):
        await runner.authenticated_checks("test-only", writes=True)
    assert "session-2" in fake_live_session.clear_calls
    assert not fake_live_session.buffers["session-2"]

@pytest.mark.parametrize("failure", ["missing_successor", "empty_successor", "wrong_token_type", "unchanged_refresh", "wrong_rejection_reason", "success"])
def test_refresh_and_revocation_require_the_expected_token_contract(monkeypatch, failure):
    metadata = {"token_endpoint": runner.ISSUER + "/api/oauth/token", "revocation_endpoint": runner.ISSUER + "/api/oauth/revoke"}
    original = {"access_token": "test-access", "refresh_token": "test-refresh", "token_type": "Bearer"}
    token_calls = 0
    revocations = []

    def handle(request):
        nonlocal token_calls
        if request.url.path.endswith("/revoke"):
            from urllib.parse import parse_qs
            revocations.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        token_calls += 1
        if token_calls == 1:
            result = {"access_token": "test-new-access", "token_type": "Bearer"}
            if failure != "missing_successor":
                result["refresh_token"] = "test-new-refresh"
            if failure == "empty_successor":
                result["access_token"] = ""
            elif failure == "wrong_token_type":
                result["token_type"] = "MAC"
            elif failure == "unchanged_refresh":
                result["refresh_token"] = original["refresh_token"]
            return httpx.Response(200, json=result)
        return httpx.Response(400, json={"error": "invalid_request" if failure == "wrong_rejection_reason" else "invalid_grant"})

    real_client = httpx.Client
    monkeypatch.setattr(runner.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(runner, "public_probes", lambda _client: metadata)
    monkeypatch.setattr(runner, "login", lambda *_args: (original.copy(), "test-client"))
    monkeypatch.setattr(runner, "REPORT", {"checks": []})
    monkeypatch.setattr(runner.sys, "argv", ["developer_test.py", "--login"])

    async def no_tools(*_args):
        return None

    monkeypatch.setattr(runner, "authenticated_checks", no_tools)
    assert runner.main() == (0 if failure == "success" else 1)
    assert runner.REPORT["status"] == ("passed" if failure == "success" else "failed")
    assert revocations == ["test-new-refresh" if failure in {"success", "wrong_rejection_reason"} else "test-refresh"]


@pytest.mark.anyio
async def test_live_fixture_and_buffer_success(fake_live_session):
    await runner.authenticated_checks("test-only", writes=True)
    assert all(not messages for messages in fake_live_session.buffers.values())
