"""A correction routed to the corrected memory's space stacks onto it.

The reflect description tells Claude to capture a correction with the space a
recall result shows (``"/CognitiveMemory/preferences"``), and to treat a stored
reference that is not a new revision of the old item as "did not stack". Both
claims are about installed code, not the server, so they are pinned here on the
real path: the real ``tool_memory_reflect``, the real SDK ``tool_memory_store``
with its space normalization, stacking search and lexical gate, over the
recording graph fakes from ``test_tenant_scoped_writes``.

The texts are the observed case (claude.ai, 2026-09-18): the favorite color
saved as blue, then corrected to black in the same language.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc
from test_tenant_scoped_writes import Ledger, RecordingClient, _FakeRedisBuffer

from kumiho_cloud_mcp.app import create_app

pytestmark = pytest.mark.anyio

TENANT = "tenant-correction"
PROJECT = "CognitiveMemory"

ORIGINAL = {
    "type": "preference",
    "title": "좋아하는 색깔은 파란색",
    "content": "사용자가 좋아하는 색깔은 파란색이다.",
}
CORRECTION = {
    "type": "preference",
    "title": "좋아하는 색깔은 검은색",
    "content": "사용자가 좋아하는 색깔은 검은색이다.",
}


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture
def recording_clients(monkeypatch, ledger: Ledger) -> List[RecordingClient]:
    import kumiho_cloud_mcp.clients as clients_module

    built: List[RecordingClient] = []

    def _fake(*, target: str, token, metadata) -> RecordingClient:
        client = RecordingClient(target=target, token=token, metadata=metadata, ledger=ledger)
        built.append(client)
        return client

    monkeypatch.setattr(clients_module, "_construct_client", _fake)
    return built


@pytest.fixture(autouse=True)
def hermetic_memory(monkeypatch):
    """No Redis, and no SDK or manager cache carried between tests."""
    import kumiho.mcp_server as mcp_server
    import kumiho_memory
    import kumiho_memory.mcp_tools as memory_tools

    monkeypatch.setattr(kumiho_memory, "RedisMemoryBuffer", _FakeRedisBuffer)

    def _clear() -> None:
        mcp_server._project_cache.clear()
        mcp_server._known_spaces.clear()
        mcp_server._bundle_cache.clear()
        mcp_server._space_registry_cache.clear()
        memory_tools._tenant_managers.clear()
        memory_tools._manager = None

    _clear()
    try:
        yield
    finally:
        _clear()


@pytest.fixture(scope="module")
def real_server():
    from kumiho_cloud_mcp._compat import build_server

    return build_server()


@pytest.fixture
def app(settings, recording_clients, real_server):
    return create_app(settings, server_factory=lambda: real_server)


async def _reflect(http, token: str, capture: Dict[str, Any], space_hint: str = "") -> dict:
    capture = dict(capture)
    if space_hint:
        capture["space_hint"] = space_hint
    arguments = {
        "session_id": f"claude:{TENANT}:001",
        "response": "Noted.",
        "captures": [capture],
        "discover_edges": False,
    }
    response = await http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "kumiho_memory_reflect", "arguments": arguments}),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload.get("isError") is not True, payload
    result = json.loads(payload["content"][0]["text"])
    assert result["captures_stored"] == 1, result
    return result


def _revision(clients: List[RecordingClient], kref: str):
    for client in clients:
        for project in client.projects.values():
            for item in project.items.values():
                for revision in item.revisions:
                    if revision.kref.uri == kref:
                        return revision
    raise AssertionError(f"no revision {kref}")


def _token(keypair) -> str:
    return keypair.sign(
        base_claims(tenant_id=TENANT, tenant_slug="correction", sub="user-correction", jti="jti-c")
    )


@pytest.mark.parametrize(
    "form", ["bare", "project-qualified"],
)
def test_space_hint_forms_resolve_to_one_space(form):
    """Pure check of the normalization reflect's space_hint goes through."""
    from kumiho.mcp_server import _normalize_space_path

    for space in ("preferences", "work/infra"):
        hint = space if form == "bare" else f"{PROJECT}/{space}"
        expected = f"/{PROJECT}/{space}"
        assert _normalize_space_path(PROJECT, hint) == expected
        assert _normalize_space_path(PROJECT, f"/{hint}") == expected


@pytest.mark.parametrize("space", ["preferences", "work/infra"])
@pytest.mark.parametrize("form", ["result", "bare", "project-qualified"])
async def test_a_correction_in_the_results_space_stacks_onto_the_old_memory(
    app, control_plane, keypair, ledger, recording_clients, space, form,
):
    token = _token(keypair)
    async with client_for(app, control_plane) as http:
        first = (await _reflect(http, token, ORIGINAL, space_hint=space))["stored_krefs"][0]

        # What recall shows as the memory's space: the revision's "space" metadata.
        shown = _revision(recording_clients, first).metadata["space"]
        assert shown == f"/{PROJECT}/{space}"
        hint = {"result": shown, "bare": space, "project-qualified": f"{PROJECT}/{space}"}[form]

        second = (await _reflect(http, token, CORRECTION, space_hint=hint))["stored_krefs"][0]

    # A new item's first revision is ?r=1; the stacked correction is ?r=2 of it.
    assert first.endswith("?r=1"), first
    assert second.endswith("?r=2"), second
    assert second.rsplit("?r=", 1)[0] == first.rsplit("?r=", 1)[0]
    assert first.startswith(f"kref://{PROJECT}/{space}/"), first

    corrected = _revision(recording_clients, second)
    assert corrected.metadata["space"] == shown
    assert corrected.metadata["memory_type"] == "preference"
    assert corrected.tags == ["published"]

    # Nowhere did the project prefix double.
    doubled = f"{PROJECT}/{PROJECT}"
    assert all(doubled not in kref for kref in ledger.kref_owner), sorted(ledger.kref_owner)
    for call in ledger.calls:
        if call["method"] == "project.create_space":
            assert call["segment"] != PROJECT, call
            assert doubled not in call["parent"], call
        if call["method"] == "client.search":
            assert call["context"] == f"{PROJECT}/{space}", call


async def test_an_unrouted_correction_is_a_new_item(app, control_plane, keypair, recording_clients):
    """The observed failure, and the signal the description names for it."""
    token = _token(keypair)
    async with client_for(app, control_plane) as http:
        first = (await _reflect(http, token, ORIGINAL, space_hint="preferences"))["stored_krefs"][0]
        second = (await _reflect(http, token, CORRECTION))["stored_krefs"][0]

    assert first.endswith("?r=1"), first
    # Not a new revision of the old item: a separate item at the project root.
    assert second.endswith("?r=1"), second
    assert second.rsplit("?r=", 1)[0] != first.rsplit("?r=", 1)[0]
    assert second.startswith(f"kref://{PROJECT}/") and second.count("/") == 3, second
    # Both values stay published side by side, which is what the retire fixes.
    assert _revision(recording_clients, first).tags == ["published"]
    assert _revision(recording_clients, second).tags == ["published"]
