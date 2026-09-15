"""The connector tool profile: what ``tools/list`` shows and what it annotates."""

from __future__ import annotations

import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc

from kumiho_cloud_mcp._compat import build_server
from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.connector_profile import (
    CONNECTOR_INSTRUCTIONS,
    CONNECTOR_TOOL_ANNOTATIONS,
    CONNECTOR_TOOL_COUNT,
    CONNECTOR_TOOLS,
)

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
    assert destructive == {"kumiho_deprecate_item", "kumiho_chat_clear"}
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
    instructions = response.json()["result"]["instructions"]
    # The SDK's CONNECTOR_INSTRUCTIONS wins when present; ours is the fallback.
    # Either way the engage/reflect protocol has to be in there, because there
    # is no skill or hook on a remote connector to carry it.
    assert instructions
    assert "kumiho_memory_engage" in instructions
    assert "kumiho_memory_reflect" in instructions
    assert instructions.startswith("Kumiho Memory")
    if app.state.profile_source != "native":
        assert instructions == CONNECTOR_INSTRUCTIONS


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
