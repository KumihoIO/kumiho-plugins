"""The connector tool profile: what ``tools/list`` shows and what it annotates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc

from kumiho_cloud_mcp._compat import build_server
from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.connector_profile import (
    CONNECTOR_INSTRUCTIONS,
    CONNECTOR_TOOL_ANNOTATIONS,
    CONNECTOR_TOOL_COUNT,
    CONNECTOR_TOOL_DESCRIPTIONS,
    CONNECTOR_TOOLS,
    INSTRUCTIONS_LEAD_CHARS,
    MAX_INSTRUCTIONS_BYTES,
    MAX_TOOL_DESCRIPTION_CHARS,
)
from kumiho_cloud_mcp.sessions import SESSION_DESCRIPTION, SESSION_TOOLS

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def real_server():
    """Built once — constructing the real Kumiho MCP server is not cheap."""
    return build_server()


@pytest.fixture
def app(settings, fake_clients, real_server):
    return create_app(settings, server_factory=lambda: real_server)


async def _tools(http, token):
    response = await http.post(
        "/mcp",
        json=rpc("tools/list"),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]["tools"]


async def _instructions(http, token):
    response = await http.post(
        "/mcp",
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]["instructions"]


def _schema_descriptions(node):
    """Every ``description`` string anywhere in an input schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield value
            else:
                yield from _schema_descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _schema_descriptions(value)


def test_profile_names_exactly_the_expected_tools():
    assert CONNECTOR_TOOL_COUNT == 18
    assert len(CONNECTOR_TOOLS) == CONNECTOR_TOOL_COUNT
    assert len(set(CONNECTOR_TOOLS)) == CONNECTOR_TOOL_COUNT
    assert set(CONNECTOR_TOOLS) == set(CONNECTOR_TOOL_ANNOTATIONS)


def test_dream_state_is_not_in_v1():
    """Dropped for v1: hosted tenants are keyless, and it is LLM-hungry."""
    assert "kumiho_memory_dream_state" not in CONNECTOR_TOOLS


def test_every_tool_has_the_annotations_the_directory_requires():
    """Claude's submission review needs title + readOnlyHint or destructiveHint."""
    for name, hints in CONNECTOR_TOOL_ANNOTATIONS.items():
        assert hints["title"], name
        assert "readOnlyHint" in hints, name
        assert "destructiveHint" in hints, name
        assert hints["openWorldHint"] is False, name
        if hints["readOnlyHint"]:
            assert hints["destructiveHint"] is False, name


def test_read_only_and_destructive_are_mutually_exclusive():
    for name, ann in CONNECTOR_TOOL_ANNOTATIONS.items():
        assert not (ann["readOnlyHint"] and ann["destructiveHint"]), name


def test_destructive_tools_are_marked():
    destructive = {
        name
        for name, ann in CONNECTOR_TOOL_ANNOTATIONS.items()
        if ann["destructiveHint"]
    }
    assert destructive == {
        "kumiho_deprecate_item", "kumiho_chat_clear", "kumiho_memory_consolidate",
        "kumiho_memory_store", "kumiho_memory_reflect", "kumiho_memory_decompose",
    }
    assert CONNECTOR_TOOL_ANNOTATIONS["kumiho_deprecate_item"]["title"] == "Forget a memory"


async def test_tools_list_matches_the_profile(app, control_plane, keypair):
    """Nothing outside the profile is ever exposed.

    With an SDK that implements the profile natively the answer is all 18; with
    an older one the shim can only expose the names that exist, so the weaker
    subset assertion is what holds in both worlds. The startup smoke check is
    what turns "fewer than 18" into a loud error at deploy time.
    """
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = await _tools(http, token)

    names = [t["name"] for t in tools]
    assert names, "no tools exposed at all"
    assert set(names) <= set(CONNECTOR_TOOLS)
    assert names == sorted(names, key=CONNECTOR_TOOLS.index)
    # Whatever else drifts, the memory verbs the connector exists for are here.
    assert {"kumiho_memory_engage", "kumiho_memory_reflect", "kumiho_memory_recall"} <= set(names)

    if app.state.profile_source == "native":
        assert names == list(CONNECTOR_TOOLS), (
            "the SDK profile and the local mirror have drifted apart"
        )
        assert len(names) == CONNECTOR_TOOL_COUNT


async def test_no_project_destroying_tool_is_reachable(app, control_plane, keypair):
    """Hiding a tool from tools/list is not the same as making it unreachable."""
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        names = {t["name"] for t in await _tools(http, token)}
        response = await http.post(
            "/mcp",
            json=rpc("tools/call", {"name": "kumiho_delete_project", "arguments": {}}),
            headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
        )
    assert "kumiho_delete_project" not in names
    assert "kumiho_delete_space" not in names
    result = response.json()["result"]
    assert result["isError"] is True
    assert "not available" in result["content"][0]["text"]


async def test_startup_smoke_check_records_what_is_exposed(app, control_plane, keypair):
    async with client_for(app, control_plane) as http:
        health = (await http.get("/healthz")).json()
    assert health["expected_tools"] == CONNECTOR_TOOL_COUNT
    assert health["tools"] == len(app.state.exposed_tools)


async def test_exposed_tools_carry_annotations(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = await _tools(http, token)

    for tool in tools:
        expected = CONNECTOR_TOOL_ANNOTATIONS[tool["name"]]
        annotations = tool.get("annotations")
        assert annotations is not None, tool["name"]
        assert annotations["title"] == expected["title"]
        assert annotations["readOnlyHint"] == expected["readOnlyHint"]
        assert annotations["destructiveHint"] == expected["destructiveHint"]
        assert annotations["openWorldHint"] is False


async def test_initialize_advertises_the_connector_instructions(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        instructions = await _instructions(http, token)
    # build_server always serves the hosted text, on the native path as well:
    # it deliberately differs from the SDK's stdio-oriented default.
    assert instructions == CONNECTOR_INSTRUCTIONS
    assert "kumiho_memory_engage" in instructions
    assert "kumiho_memory_reflect" in instructions
    assert instructions.startswith("Kumiho Memory")


async def test_served_instructions_fit_the_budget_with_a_standalone_lead(app, control_plane, keypair):
    """Claude Code cuts instructions at 2KB; ChatGPT relies on the first 512 chars."""
    async with client_for(app, control_plane) as http:
        instructions = await _instructions(http, keypair.sign(base_claims()))
    assert len(instructions.encode("utf-8")) <= MAX_INSTRUCTIONS_BYTES

    lead = instructions[:INSTRUCTIONS_LEAD_CHARS]
    # The whole opening paragraph fits in the lead, so nothing it says is cut mid-rule.
    assert "\n\n" in lead
    lead = lead.split("\n\n", 1)[0]
    lowered = lead.lower()
    for term in ("password", "token", "mfa", "recovery code", "weather"):
        assert term in lowered, term
    assert "without calling any Kumiho tool" in lead
    # ...and the core recall-then-capture rhythm, not just the refusal.
    assert "kumiho_memory_engage" in lead
    assert "kumiho_memory_reflect" in lead


async def test_every_served_description_fits_claude_code(app, control_plane, keypair):
    """Measured on the tools/list response, including the session suffix."""
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}
    assert set(tools) == set(CONNECTOR_TOOLS)
    for name, tool in tools.items():
        description = tool.get("description") or ""
        assert description.strip(), name
        assert len(description) <= MAX_TOOL_DESCRIPTION_CHARS, (name, len(description))


async def test_no_served_description_names_another_connector_tool(app, control_plane, keypair):
    """Directory attestation: descriptions carry no instructions about other tools."""
    async with client_for(app, control_plane) as http:
        tools = await _tools(http, keypair.sign(base_claims()))
    assert {tool["name"] for tool in tools} == set(CONNECTOR_TOOLS)
    for tool in tools:
        texts = [tool.get("description") or "", *_schema_descriptions(tool.get("inputSchema"))]
        for text in texts:
            for other in CONNECTOR_TOOLS:
                if other != tool["name"]:
                    assert other not in text, (tool["name"], other)
            assert "other memory tool" not in text.lower(), tool["name"]


async def test_memory_tools_carry_their_own_scoped_exclusions(app, control_plane, keypair):
    """The credential/authorization/live-info boundary lives in each tool, not in one."""
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}

    credential_tools = {
        "kumiho_memory_engage", "kumiho_memory_recall", "kumiho_memory_retrieve",
        "kumiho_memory_store", "kumiho_memory_reflect", "kumiho_memory_consolidate",
    }
    for name in credential_tools:
        text = tools[name]["description"].lower()
        for term in ("password", "access token", "mfa", "recovery code"):
            assert term in text, (name, term)
    for name in credential_tools - {"kumiho_memory_consolidate"}:
        assert "do not call this tool" in tools[name]["description"], name
    for name in ("kumiho_memory_engage", "kumiho_memory_recall", "kumiho_memory_retrieve"):
        text = tools[name]["description"]
        assert "live information" in text, name
        assert "authorized" in text, name


async def test_session_tools_keep_the_session_required_wording(app, control_plane, keypair):
    assert SESSION_TOOLS <= set(CONNECTOR_TOOL_DESCRIPTIONS)
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}
    for name in SESSION_TOOLS:
        description = tools[name]["description"]
        assert description.startswith(CONNECTOR_TOOL_DESCRIPTIONS[name]), name
        assert description.endswith(SESSION_DESCRIPTION), name
        assert "session_required" in description, name
        assert tools[name]["inputSchema"]["properties"]["session_id"]["description"] == SESSION_DESCRIPTION


async def test_resource_and_prompt_capabilities_are_not_advertised(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        response = await http.post(
            "/mcp",
            json=rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            ),
            headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
        )
    capabilities = response.json()["result"]["capabilities"]
    assert "tools" in capabilities
    assert capabilities.get("resources") is None
    assert capabilities.get("prompts") is None


async def test_every_tool_declares_oauth_for_chatgpt(app, control_plane, keypair):
    async with client_for(app, control_plane) as http:
        tools = await _tools(http, keypair.sign(base_claims()))
    for tool in tools:
        expected = [{"type": "oauth2", "scopes": ["memory"]}]
        assert tool["securitySchemes"] == expected
        assert tool["_meta"]["securitySchemes"] == expected


async def test_hosted_search_never_solicits_or_accepts_credentials(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        catalog = {tool["name"]: tool for tool in await _tools(http, token)}
        assert "auth_token" not in catalog["kumiho_search_items"]["inputSchema"]["properties"]
        response = await http.post(
            "/mcp", json=rpc("tools/call", {"name": "kumiho_search_items", "arguments": {"auth_token": "test-secret-must-not-be-echoed"}}),
            headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
        )
    assert response.json()["result"]["isError"] is True
    assert "test-secret-must-not-be-echoed" not in response.text
    assert "OAuth" in response.text


async def test_consolidation_advertises_buffer_deletion(app, control_plane, keypair):
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, token)}
    tool = tools["kumiho_memory_consolidate"]
    assert tool["annotations"]["destructiveHint"] is True
    assert tool["annotations"]["readOnlyHint"] is False
    assert "clear" in tool["description"]


async def test_submission_hints_match_the_discovered_hosted_catalog(app, control_plane, keypair):
    """Prevent the reviewer import file from drifting from the real MCP response."""
    submission = json.loads((Path(__file__).parents[1] / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}
    assert set(submission["tools"]) == set(tools)
    for name, declared in submission["tools"].items():
        for hint in ("readOnlyHint", "destructiveHint", "openWorldHint"):
            expected = declared["annotations"][hint]
            assert isinstance(expected, bool)
            assert tools[name]["annotations"][hint] is expected, (name, hint)
