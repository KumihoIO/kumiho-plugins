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
