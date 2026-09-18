"""The connector tool profile: what ``tools/list`` shows and what it annotates."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc

from kumiho_cloud_mcp._compat import build_server, listed_tools
from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.connector_profile import (
    CONNECTOR_INSTRUCTIONS,
    CONNECTOR_TOOL_ANNOTATIONS,
    CONNECTOR_TOOL_COUNT,
    CONNECTOR_TOOL_DESCRIPTIONS,
    CONNECTOR_TOOLS,
    HOSTED_RECALL_MODE,
    INSTRUCTIONS_LEAD_CHARS,
    MAX_INSTRUCTIONS_BYTES,
    MAX_TOOL_DESCRIPTION_CHARS,
    RECALL_MODE_TOOLS,
)
from kumiho_cloud_mcp.sessions import (
    SESSION_DESCRIPTION,
    SESSION_TOOLS,
    USER_ID_DESCRIPTION,
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


def _schema_keys(node):
    """Every property name anywhere in an input schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                yield from value
            yield from _schema_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _schema_keys(value)


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
        # The 2KB cut is in bytes; the margin must hold for non-ASCII text too.
        assert len(description.encode("utf-8")) <= MAX_TOOL_DESCRIPTION_CHARS, name


#: Any Kumiho tool name in served text, hosted or not.
_TOOL_NAME = re.compile("kumiho_[a-z0-9_]+")


async def test_no_served_description_names_another_connector_tool(app, control_plane, keypair):
    """Directory attestation: descriptions carry no instructions about other tools.

    Including tools the connector does not host. kumiho-memory 1.5.0 describes
    user_id on the session tools by pointing at its kumiho_memory_ingest
    workflow; ingest is outside the 18, so that text sends a connector client
    after a tool it can never call. Property descriptions count as served text.
    """
    async with client_for(app, control_plane) as http:
        tools = await _tools(http, keypair.sign(base_claims()))
    assert {tool["name"] for tool in tools} == set(CONNECTOR_TOOLS)
    for tool in tools:
        texts = [tool.get("description") or "", *_schema_descriptions(tool.get("inputSchema"))]
        for text in texts:
            for other in CONNECTOR_TOOLS:
                if other != tool["name"]:
                    assert other not in text, (tool["name"], other)
            for named in _TOOL_NAME.findall(text):
                assert named in CONNECTOR_TOOLS, (tool["name"], named)
            assert "other memory tool" not in text.lower(), tool["name"]


async def test_session_tools_serve_a_rewritten_user_id_hint(app, control_plane, keypair):
    """The four identity-bearing schemas keep a usable hint, minus the tool name."""
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}
    for name in SESSION_TOOLS:
        served = tools[name]["inputSchema"]["properties"]["user_id"]["description"]
        assert served == USER_ID_DESCRIPTION, name
        assert "ingest" not in served.lower(), name


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


#: Phrases a user says to have something kept. Only reflect may claim them.
CAPTURE_TRIGGERS = (
    "remember this", "save this", "note that", "asks you to remember", "asks to remember",
    "asks to keep", "asks to save", "remember, save or note",
)
#: General recall (referring back, what was decided). Only engage may claim it.
GENERAL_RECALL_INTENTS = (
    "refers back", "referring back", "not visible in this conversation",
    "not in the current context", "as we decided", "as we discussed",
    "what do you remember", "decided or noted before", "what was decided", "noted earlier",
)
#: Newest and oldest lookups. Only retrieve may claim them.
RECENCY_INTENTS = (
    "recent", "latest", "newest", "what did i save", "what was saved",
)


def _without(text, phrases):
    return [phrase for phrase in phrases if phrase in text]


async def test_memory_tool_descriptions_each_claim_their_own_intents(app, control_plane, keypair):
    """General recall -> engage, newest/oldest -> retrieve, capture -> reflect.

    Regressions: a store description that claimed "remember this" competed with
    reflect, and an engage description that claimed "what did I save recently?"
    sent recency questions to a relevance-ranked search.
    """
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool["description"].lower()
                 for tool in await _tools(http, keypair.sign(base_claims()))}

    engage = tools["kumiho_memory_engage"]
    for phrase in ("start of a conversation", "refers back", "what do you remember about me?",
                   "decided or noted before"):
        assert phrase in engage, phrase
    assert _without(engage, RECENCY_INTENTS) == []
    assert _without(engage, CAPTURE_TRIGGERS) == []

    retrieve = tools["kumiho_memory_retrieve"]
    useful_when = retrieve.split("\n\n", 1)[0]
    for phrase in ("most recent", "what did i save recently?", "newest"):
        assert phrase in useful_when, phrase
    assert 'mode to "latest"' in retrieve
    assert "not the memory text" in retrieve and "exact" in retrieve
    assert _without(retrieve, GENERAL_RECALL_INTENTS) == []
    assert _without(retrieve, CAPTURE_TRIGGERS) == []

    reflect = tools["kumiho_memory_reflect"]
    for phrase in ("remember this", "save this", "note that", "asks you to remember",
                   "decision", "preference", "durable fact", "correction"):
        assert phrase in reflect, phrase
    assert _without(reflect, RECENCY_INTENTS + GENERAL_RECALL_INTENTS) == []

    for name in ("kumiho_memory_store", "kumiho_memory_recall"):
        assert _without(tools[name], CAPTURE_TRIGGERS + RECENCY_INTENTS + GENERAL_RECALL_INTENTS) == [], name
    assert "memory_types" in tools["kumiho_memory_recall"]
    assert "space_paths" in tools["kumiho_memory_recall"]


async def test_engage_promises_no_count_and_calls_an_empty_result_an_answer(
    app, control_plane, keypair,
):
    """Judged delivery makes the delivered count dynamic and zero a real answer.

    On a tenant whose tier judges the candidates, engage delivers what passed,
    not the caller's `limit` — and for a query nothing saved is relevant to,
    that is nothing. Without this sentence the description's only empty-result
    wording is the deduplication one, which reads as a caller mistake to retry.
    """
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, keypair.sign(base_claims()))}

    engage = tools["kumiho_memory_engage"]["description"].lower()
    assert "how many come back varies" in engage
    assert "an empty result is a valid answer" in engage
    assert "not a failure" in engage


async def test_corrections_stack_onto_the_corrected_memory(app, control_plane, keypair):
    """A correction names the memory it revises, then keeps its type and language.

    Regression (claude.ai, 2026-09-18): "fix it, my favorite color is black" was
    captured with type correction and no space, so reflect never tried to stack
    it and recall showed blue and black side by side. kumiho-memory 1.5.1 takes
    "revises" per capture and passes it to the SDK store's item_kref, so the
    description names the memory instead of relying on the similarity gate. The
    fallback retire is described by what it does, never by naming the forget
    tool: descriptions carry no instructions about other tools.
    """
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool["description"] for tool in await _tools(http, token)}
        instructions = await _instructions(http, token)

    reflect = tools["kumiho_memory_reflect"]
    lowered = reflect.lower()
    # Name the memory being revised, then keep its type and language anyway:
    # without "revises" the store still falls back to the lexical gate.
    assert "to correct a saved memory" in lowered
    assert "set that capture's revises to the memory's reference" in lowered
    assert "search results return it" in lowered
    assert "the capture becomes that memory's new revision" in lowered
    assert "keep its memory type and language, and restate the subject" in lowered
    # The verified space_hint form: a result's space, project prefix included.
    assert 'copied exactly as results show it ("/CognitiveMemory/preferences")' in reflect
    # "correction" is a reason to reflect, not the type to file it under.
    types = reflect.split("Each capture needs type (", 1)[1].split(")", 1)[0]
    assert "correction" not in types, types
    # The retire fallback, for when no memory can be named.
    assert "if you cannot tell which memory to revise, save normally" in lowered
    assert "retire the old memory by its reference only when stored_krefs names a different item" in lowered
    assert "kumiho_deprecate_item" not in reflect

    deprecate = tools["kumiho_deprecate_item"].lower()
    assert "retire the memory a user's correction replaced" in deprecate
    assert "did not stack as a new revision" in deprecate
    # The ambiguity rule still holds for that retire.
    assert "confirm with the user when more than one memory could match" in deprecate

    served_reflect = f"{CONNECTOR_TOOL_DESCRIPTIONS['kumiho_memory_reflect']}\n\n{SESSION_DESCRIPTION}"
    assert reflect == served_reflect
    for text in (reflect, tools["kumiho_deprecate_item"]):
        assert len(text) <= MAX_TOOL_DESCRIPTION_CHARS < 2000
        assert len(text.encode("utf-8")) <= MAX_TOOL_DESCRIPTION_CHARS
    assert len(instructions.encode("utf-8")) <= MAX_INSTRUCTIONS_BYTES <= 2000


async def test_reflect_serves_the_revises_capture_field(app, control_plane, keypair):
    """The field the correction wording depends on has to be on the wire.

    ``revises`` arrived in kumiho-memory 1.5.1; a 1.5.0 image serves the same
    reflect tool without it, and the description would then tell the model to
    set an argument the schema rejects. Fail here rather than on a user's
    correction. It is the SDK's own property, so its text is checked for a
    leaked tool name too: the no-other-tools attestation covers served
    property descriptions, not just tool descriptions.
    """
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, token)}

    reflect = tools["kumiho_memory_reflect"]
    captures = reflect["inputSchema"]["properties"]["captures"]
    properties = captures["items"]["properties"]
    assert "revises" in properties, sorted(properties)
    revises = properties["revises"]
    assert revises["type"] == "string", revises
    # The description tells the model the field exists; the schema has to agree.
    assert "revises" in reflect["description"]

    served = revises.get("description") or ""
    assert served, revises
    for other in CONNECTOR_TOOLS:
        assert other not in served, other
    for named in _TOOL_NAME.findall(served):
        assert named in CONNECTOR_TOOLS, named


async def test_instructions_route_the_same_intents(app, control_plane, keypair):
    async with client_for(app, control_plane) as http:
        instructions = await _instructions(http, keypair.sign(base_claims()))
    lead = instructions.split("\n\n", 1)[0]
    assert "kumiho_memory_engage" in lead and "kumiho_memory_reflect" in lead
    for name in ("kumiho_memory_store", "kumiho_memory_recall", "kumiho_memory_retrieve"):
        assert name not in lead, name
    lowered = instructions.lower()
    assert "remember, save or note" in lowered

    sentences = [part for chunk in lowered.split("\n\n") for part in chunk.replace("; ", ". ").split(". ")]
    retrieve = [s for s in sentences if "kumiho_memory_retrieve" in s]
    assert any('mode "latest"' in s and "recent" in s for s in retrieve), retrieve
    engage_recall = [s for s in sentences if "engage also covers" in s]
    assert len(engage_recall) == 1
    assert "referring back" in engage_recall[0] and "decided or noted before" in engage_recall[0]
    for sentence in sentences:
        if "engage" in sentence:
            assert _without(sentence, RECENCY_INTENTS) == [], sentence
        if "kumiho_memory_retrieve" in sentence:
            assert _without(sentence, CAPTURE_TRIGGERS + GENERAL_RECALL_INTENTS) == [], sentence
        if "kumiho_memory_store" in sentence or "kumiho_memory_recall" in sentence:
            assert _without(sentence, CAPTURE_TRIGGERS + RECENCY_INTENTS + GENERAL_RECALL_INTENTS) == [], sentence
            assert "explicitly asks" not in sentence, sentence


#: The kumiho 0.13.0 workaround: recency only held with an empty query.
EMPTY_QUERY_WORKAROUND = (
    "leave query", "leave the query", "query empty", "queries empty", "no query",
    "keywords and topics empty", "without a query", "relevance instead", "whatever the query",
)


async def test_retrieve_recency_wording_matches_the_sdk(app, control_plane, keypair):
    """kumiho 0.13.2 orders mode "latest" by date with or without a query.

    Regression: under 0.13.0 a query switched results back to relevance order,
    so the description told the model to leave the query empty. The served
    schema's own mode text is the SDK's, so a downgrade fails here too: 0.13.2
    rewrote it to order by the returned revision's date and to name the alias
    spellings it now folds.
    """
    token = keypair.sign(base_claims())
    async with client_for(app, control_plane) as http:
        tools = {tool["name"]: tool for tool in await _tools(http, token)}
        instructions = await _instructions(http, token)

    tool = tools["kumiho_memory_retrieve"]
    retrieve = tool["description"]
    lowered = retrieve.lower()
    assert _without(lowered, EMPTY_QUERY_WORKAROUND) == []
    # Recency still routes to mode "latest", and a query keeps date order.
    assert 'set mode to "latest"' in retrieve
    for phrase in ("newest first by last update", "narrow them to relevant matches",
                   "still newest first", "created_at", "my latest note on the launch plan"):
        assert phrase in lowered, phrase
    assert 'mode "first"' in retrieve and "oldest relevant match" in lowered
    assert len(retrieve.encode("utf-8")) <= MAX_TOOL_DESCRIPTION_CHARS < 2000

    mode = tool["inputSchema"]["properties"]["mode"]["description"]
    assert "newest first by the returned revision's date" in mode, mode
    assert "the top max(limit*4, 20) relevance hits ordered by date" in mode, mode

    assert len(instructions.encode("utf-8")) <= MAX_INSTRUCTIONS_BYTES <= 2000
    sentences = [part for chunk in instructions.lower().split("\n\n")
                 for part in chunk.replace("; ", ". ").split(". ")]
    retrieve_sentences = [s for s in sentences if "kumiho_memory_retrieve" in s]
    assert retrieve_sentences
    for sentence in retrieve_sentences:
        assert _without(sentence, EMPTY_QUERY_WORKAROUND) == [], sentence
    assert 'mode "latest" or "first" and, for a topic, a query' in instructions


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


async def test_no_served_schema_offers_a_recall_mode(app, control_plane, keypair):
    """Hosted recall is pinned; the SDK's mode choice is not on offer anywhere."""
    async with client_for(app, control_plane) as http:
        token = keypair.sign(base_claims())
        tools = await _tools(http, token)
        instructions = await _instructions(http, token)
    assert {tool["name"] for tool in tools} == set(CONNECTOR_TOOLS)
    for tool in tools:
        schema = tool["inputSchema"]
        assert "recall_mode" not in set(_schema_keys(schema)), tool["name"]
        assert "recall_mode" not in schema.get("required", []), tool["name"]
        assert "recall_mode" not in json.dumps(tool), tool["name"]
        for text in (tool.get("description") or "", *_schema_descriptions(schema)):
            lowered = text.lower()
            assert "artifact content" not in lowered, tool["name"]
            assert "full mode" not in lowered and "'full'" not in lowered, tool["name"]
    lowered = instructions.lower()
    for term in ("recall_mode", "artifact", "full mode"):
        assert term not in lowered, term
    # The query argument itself survives the schema edit.
    served = {tool["name"]: tool for tool in tools}
    for name in RECALL_MODE_TOOLS:
        assert "query" in served[name]["inputSchema"]["properties"], name
        assert served[name]["inputSchema"]["required"] == ["query"], name


async def test_recall_mode_tools_match_the_sdk(real_server):
    """If the SDK adds recall_mode to another connector tool, the pin must follow."""
    import kumiho.mcp_server as ms

    upstream = await listed_tools(ms.create_mcp_server())
    declared = {
        tool.name for tool in upstream
        if tool.name in CONNECTOR_TOOLS and "recall_mode" in (tool.input_schema or {}).get("properties", {})
    }
    assert declared == set(RECALL_MODE_TOOLS)


class _RecallManager:
    """Tenant memory manager stand-in whose recall hits carry sibling revisions."""

    # An SDK default of "full" must not reach hosted results either.
    recall_mode = "full"
    _last_backend_error = None

    def __init__(self):
        self.context_modes = []

    async def recall_memories(self, query, **kwargs):
        return [{
            "kref": "kref://CognitiveMemory/decisions/region.decision?r=3",
            "title": "Chose the Seoul region on 2026-09-16",
            "summary": "Seoul, for latency to the team.",
            "score": 0.91,
            "sibling_revisions": [
                {"kref": "kref://CognitiveMemory/decisions/region.decision?r=2",
                 "summary": "SIBLING-PROSE-R2 Tokyo, before the latency test."},
                {"kref": "kref://CognitiveMemory/decisions/region.decision?r=1",
                 "summary": "SIBLING-PROSE-R1 Undecided between regions."},
            ],
        }]

    def build_recalled_context(self, results, query="", recall_mode=None):
        self.context_modes.append(recall_mode)
        return "Chose the Seoul region on 2026-09-16."


@pytest.fixture
def recall_manager(monkeypatch):
    import kumiho_memory.mcp_tools as memory

    manager = _RecallManager()
    monkeypatch.setattr(memory, "_get_manager", lambda: manager)
    return manager


@pytest.fixture
def compat_debug_log():
    import logging

    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("kumiho.cloud_mcp.compat")
    handler, level = _Keep(level=logging.DEBUG), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.mark.parametrize("name", sorted(RECALL_MODE_TOOLS))
@pytest.mark.parametrize("requested", ["full", None])
async def test_hosted_recall_is_always_summarized(
    app, control_plane, keypair, recall_manager, compat_debug_log, name, requested,
):
    """Stale cached schemas and direct callers cannot select the other mode."""
    marker = uuid.uuid4().hex  # unique per call, so the dedup guard never answers
    arguments = {"query": f"which region did we choose {marker}"}
    if requested is not None:
        arguments["recall_mode"] = requested
    async with client_for(app, control_plane) as http:
        response = await http.post(
            "/mcp", json=rpc("tools/call", {"name": name, "arguments": arguments}),
            headers={**MCP_HEADERS, "authorization": f"Bearer {keypair.sign(base_claims())}"},
        )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result.get("isError") is not True, result
    payload = json.loads(result["content"][0]["text"])

    assert payload["recall_mode"] == HOSTED_RECALL_MODE == "summarized"
    assert payload["count"] == 1
    if name == "kumiho_memory_engage":
        assert recall_manager.context_modes == ["summarized"]
        hit = payload["results"][0]
        assert "sibling_revisions" not in hit
        assert hit["sibling_count"] == 2
        assert "SIBLING-PROSE" not in result["content"][0]["text"]

    pinned = [r for r in compat_debug_log if "recall_mode pinned" in r.getMessage()]
    if requested == "full":
        assert len(pinned) == 1 and pinned[0].levelname == "DEBUG"
        message = pinned[0].getMessage()
        assert name in message
        assert marker not in message and "full" not in message
    else:
        assert pinned == []


def _call_params(name, arguments):
    import mcp.types as types

    return types.CallToolRequestParams(name=name, arguments=arguments)


@pytest.mark.parametrize("name", sorted(set(CONNECTOR_TOOLS) - RECALL_MODE_TOOLS))
def test_stray_recall_mode_is_dropped_rather_than_injected(name):
    """Regression: the pin used to add recall_mode to tools that never take it.

    Only two SDK handlers declare the argument. Rewriting it on the rest turned
    a stale client's stray field into an argument outside the served schema on
    every other call, instead of simply dropping it.
    """
    from kumiho_cloud_mcp._compat import _pin_recall_mode

    params = _call_params(name, {"user_text": "a note", "recall_mode": "full"})
    stripped = _pin_recall_mode(params)
    assert "recall_mode" not in (stripped.arguments or {}), name
    assert stripped.arguments["user_text"] == "a note"
    # Nothing carried, nothing to rewrite: the params object passes straight through.
    untouched = _call_params(name, {"user_text": "a note"})
    assert _pin_recall_mode(untouched) is untouched


@pytest.mark.parametrize("name", sorted(RECALL_MODE_TOOLS))
@pytest.mark.parametrize("requested", ["full", None])
def test_recall_mode_tools_are_still_pinned(name, requested):
    from kumiho_cloud_mcp._compat import _pin_recall_mode

    arguments = {"query": "which region"}
    if requested is not None:
        arguments["recall_mode"] = requested
    pinned = _pin_recall_mode(_call_params(name, arguments))
    assert pinned.arguments["recall_mode"] == HOSTED_RECALL_MODE, name
    assert pinned.arguments["query"] == "which region"


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
