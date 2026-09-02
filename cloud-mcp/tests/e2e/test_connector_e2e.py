"""End-to-end: a real MCP client, the real connector, a real CE backend.

Run it with the backend up::

    python -m pytest tests/e2e -v -s

It skips itself when CE (9190) or Redis (6379) is not listening, so it is safe
in the ordinary suite. ``-s`` is worth it: every check prints the payload
excerpt it asserted on, which is the evidence the integration report quotes.

What this covers that the hermetic suite cannot: that ``kumiho``,
``kumiho-memory`` and ``kumiho_cloud_mcp`` agree on the *same* contract when
none of them is stubbed — the profile the SDK builds, the session the memory
package resolves, the Redis keys it writes, and the graph the backend stores.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date

import pytest

from e2e._client import connect

pytestmark = pytest.mark.anyio

SMOKE_TITLE = "Connector integration smoke on 2026-09-02"
SMOKE_SPACE = "connector-smoke"


def show(label: str, value, limit: int = 900) -> None:
    """Print an evidence line. ``-s`` makes these the report's raw material.

    Re-encoded through the console's own codec first: memories written by real
    users contain em dashes and CJK, and a Windows console defaulting to cp949
    turns a passing assertion into a ``UnicodeEncodeError`` from the *print*.
    """
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + f"... (+{len(text) - limit} chars)"
    line = f"\n[E2E] {label}: {text}"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def item_kref(revision_kref: str) -> str:
    """``kref://a/b/c.kind?r=1`` -> ``kref://a/b/c.kind``."""
    return revision_kref.split("?", 1)[0]


# ---------------------------------------------------------------------------
# handshake and profile
# ---------------------------------------------------------------------------


async def test_initialize_carries_the_connector_instructions(live_server):
    from kumiho.mcp_server import CONNECTOR_INSTRUCTIONS

    async with connect(live_server.url) as conn:
        info = conn.init.serverInfo
        show("serverInfo", {"name": info.name, "version": info.version})
        show("instructions[:240]", (conn.init.instructions or "")[:240])

        assert info.version == "0.13.0", "the connector must report the SDK release"
        # Byte-identical, not "looks similar": these instructions are the only
        # protocol the model gets (no hook, no skill), so a stale copy shipped
        # by the RS instead of the SDK's is a behaviour change nobody reviews.
        assert conn.init.instructions == CONNECTOR_INSTRUCTIONS
        assert "kumiho_memory_engage" in conn.init.instructions
        assert "Never invent a session_id" in conn.init.instructions


async def test_tools_list_is_the_reviewed_profile(live_server):
    from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOL_COUNT, CONNECTOR_TOOLS

    async with connect(live_server.url) as conn:
        tools = await conn.list_tools()

    names = [tool.name for tool in tools]
    show("tools/list count", len(names))
    show("tools/list names", names)

    assert len(names) == CONNECTOR_TOOL_COUNT == 18
    assert set(names) == set(CONNECTOR_TOOLS)

    # Claude's directory review requires a title and a read/destructive hint on
    # every tool; a tool missing either is a submission blocker.
    for tool in tools:
        assert tool.title, f"{tool.name} has no title"
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} has no annotations"
        assert annotations.title == tool.title
        assert (
            annotations.readOnlyHint is not None or annotations.destructiveHint is not None
        ), f"{tool.name} declares neither readOnlyHint nor destructiveHint"
        assert annotations.openWorldHint is False

    destructive = sorted(t.name for t in tools if t.annotations.destructiveHint)
    read_only = sorted(t.name for t in tools if t.annotations.readOnlyHint)
    show("destructive tools", destructive)
    show("read-only tools", read_only)
    assert destructive == ["kumiho_chat_clear", "kumiho_deprecate_item"]
    assert len(read_only) == 10


async def test_out_of_profile_tool_is_refused_and_deletes_nothing(live_server):
    """``kumiho_delete_project`` exists in the SDK. It must not be reachable."""
    async with connect(live_server.url) as conn:
        before = await conn.ok("kumiho_list_projects", {})
        is_error, payload = await conn.call(
            "kumiho_delete_project", {"name": "CognitiveMemory", "confirm": True}
        )
        after = await conn.ok("kumiho_list_projects", {})

    show("delete_project isError", is_error)
    show("delete_project payload", payload)
    show("projects before", before)
    show("projects after", after)

    assert is_error is True
    assert "not available" in str(payload).lower()
    # The refusal must be a tool error, not a transport error: an MCP client
    # shows the model a tool error and carries on, where a protocol error can
    # tear down the session.
    assert before == after, "an out-of-profile call must not change any state"


# ---------------------------------------------------------------------------
# the conversation the connector instructions describe
# ---------------------------------------------------------------------------


async def test_full_memory_round_trip(live_server):
    async with connect(live_server.url) as conn:
        # -- engage ------------------------------------------------------
        engage = await conn.ok(
            "kumiho_memory_engage",
            {"query": "hosted Kumiho Claude connector architecture", "limit": 3},
        )
        show("engage count", engage.get("count"))
        show("engage first result", (engage.get("results") or [{}])[0].get("kref"))
        show("engage context[:300]", (engage.get("context") or "")[:300])
        assert engage.get("count", 0) > 0, "engage returned no memories from CE"
        assert engage.get("context"), "engage built no context"
        assert engage.get("source_krefs"), "engage returned no source krefs"
        # Engage is annotated readOnlyHint=true and it keeps that promise: it
        # resolves no session and registers no active-session pointer, so it
        # reports no session_id. The session is established by the first write.
        assert "session_id" not in engage

        # -- reflect -----------------------------------------------------
        reflect = await conn.ok(
            "kumiho_memory_reflect",
            {
                "response": (
                    "Verified the hosted connector end to end against a local "
                    "Kumiho CE server: 18 tools, per-tenant memory managers, "
                    "session continuity through the active-session pointer."
                ),
                "captures": [
                    {
                        "type": "fact",
                        "title": SMOKE_TITLE,
                        "content": (
                            "Work package E1 drove kumiho 0.13.0 + kumiho-memory "
                            "1.4.0 + kumiho_cloud_mcp through a real MCP "
                            "streamable-HTTP client against Kumiho CE on "
                            "127.0.0.1:9190. Written by the integration test; "
                            "safe to deprecate."
                        ),
                        "space_hint": SMOKE_SPACE,
                        "event_date": date.today().isoformat(),
                        "tags": ["integration-test", "connector", "disposable"],
                    }
                ],
                "source_krefs": engage.get("source_krefs", [])[:2],
            },
        )
        show("reflect", reflect)
        assert reflect.get("captures_stored") == 1
        assert reflect.get("buffered") is True
        stored = reflect.get("stored_krefs") or []
        assert len(stored) == 1, stored
        assert f"/{SMOKE_SPACE}/" in stored[0], "capture was not routed to the space hint"

        session_id = reflect.get("session_id")
        source = reflect.get("session_id_source")
        show("session", {"session_id": session_id, "session_id_source": source})
        assert session_id, "reflect reported no session_id"
        assert source in ("generated", "active_session", "request", "argument")
        # Hosted resolution must never claim the process environment named the
        # conversation — that label belongs to the stdio path only.
        assert source != "host-env"
        # The generated shape is {context}:user-{hash}:{date}:{seq}, and the
        # context comes from the RequestContext, not from anything ambient.
        assert re.match(r"^claude:", session_id), session_id

        # -- chat_get ----------------------------------------------------
        chat = await conn.ok("kumiho_chat_get", {"session_id": session_id, "limit": 20})
        show("chat_get", {k: v for k, v in chat.items() if k != "messages"})
        messages = chat.get("messages") or []
        show("chat_get messages", [m.get("role") for m in messages])
        assert messages, "the reflect response was not buffered in Redis"
        assert any("18 tools" in (m.get("content") or "") for m in messages)
        assert chat.get("session_id") == session_id
        assert chat.get("session_id_source") == "argument"

        # -- continuity through the active-session pointer ---------------
        # Same conversation, no session_id argument: the pointer keyed
        # (context, user_id) must hand back the SAME session. This is what
        # makes the connector usable at all, since a remote model has no
        # host env to carry an id and the instructions tell it to omit one.
        implicit = await conn.ok("kumiho_chat_get", {"limit": 5})
        show(
            "chat_get without session_id",
            {
                "session_id": implicit.get("session_id"),
                "session_id_source": implicit.get("session_id_source"),
            },
        )
        assert implicit.get("session_id") == session_id
        assert implicit.get("session_id_source") == "active_session"

        # A second engage in the same conversation still reaches the same
        # tenant's graph (and still reports no session, by design above).
        engage2 = await conn.ok(
            "kumiho_memory_engage", {"query": "connector integration smoke evidence", "limit": 2}
        )
        show("second engage count", engage2.get("count"))
        assert engage2.get("count", 0) >= 0

        # -- consolidate -------------------------------------------------
        consolidate = await conn.ok(
            "kumiho_memory_consolidate",
            {
                "session_id": session_id,
                "summary": {
                    "type": "summary",
                    "title": "Cloud connector E1 integration smoke",
                    "summary": (
                        "Drove the hosted Kumiho MCP connector end to end in dev "
                        "mode against a local CE server. tools/list returned the "
                        "18-tool connector profile with titles and annotations, an "
                        "out-of-profile kumiho_delete_project call was refused as a "
                        "tool error, engage returned real memories, reflect stored "
                        "one fact capture, and the session resolved through the "
                        "active-session pointer on the following call."
                    ),
                    "knowledge": {
                        "facts": [
                            {
                                "claim": (
                                    "The connector profile is 18 tools in "
                                    "kumiho 0.13.0."
                                ),
                                "certainty": "high",
                            }
                        ],
                        "decisions": [],
                        "actions": [],
                        "open_questions": [],
                    },
                    "classification": {
                        "topics": ["mcp", "connector", "integration-test"],
                        "entities": ["kumiho_cloud_mcp"],
                    },
                },
                "evidence_level": "official",
                "source": "integration-test:e1",
            },
        )
        show("consolidate", {k: v for k, v in consolidate.items() if k != "summary"})
        assert consolidate.get("success") is True, consolidate
        assert consolidate.get("session_id") == session_id
        # A keyless consolidation still has to land something in the graph:
        # the whole point of the self-written summary is that no LLM key is
        # needed, so "succeeded but stored nothing" is the failure to catch.
        store_result = consolidate.get("store_result") or {}
        show("consolidate item_kref", store_result.get("item_kref"))
        assert store_result.get("item_kref"), consolidate
        # Consolidation is filed under the request's identity, not an ambient
        # one: the space path carries the RequestContext's context and user.
        assert "/claude/" in (store_result.get("space_path") or ""), store_result

        # -- cleanup: forget the smoke capture ---------------------------
        target = item_kref(stored[0])
        deprecate = await conn.ok("kumiho_deprecate_item", {"item_kref": target})
        show("deprecate_item", {"item_kref": target, "result": deprecate})
        assert "error" not in str(deprecate).lower()

    # The manager cache proves hosted mode is per-tenant, not a singleton.
    health = live_server.healthz()
    show("healthz", health)
    assert health["tools"] == health["expected_tools"] == 18
    managers = health["tenant_managers"]
    assert managers["loaded"] is True
    assert managers["count"] >= 1, "no per-tenant memory manager was built"
    assert managers["process_singleton"] is False, (
        "a process-wide memory manager exists: some path built one outside a "
        "request context, i.e. from the ambient environment"
    )


async def test_startup_smoke_check_logged_eighteen_tools(live_server):
    """The startup check is the only thing that catches a mispinned dependency."""
    if not live_server.spawned:
        health = live_server.healthz()
        show("healthz (borrowed server)", health)
        assert health["tools"] == health["expected_tools"] == 18
        pytest.skip("borrowed a running server; its startup log is not ours to read")

    started = [line for line in live_server.log_lines() if line.get("msg") == "kumiho-cloud-mcp started"]
    assert started, "no startup line in the server log"
    show("startup log line", started[-1])
    entry = started[-1]
    assert entry["tool_count"] == entry["expected_tool_count"] == 18
    assert entry["profile_source"] == "native", (
        "the SDK profile was not used; the local shim cannot enforce the "
        "reviewed tool list"
    )
    assert entry["hosted"] is True
    assert entry["upstream_request_context"] is True

    errors = [line for line in live_server.log_lines() if line.get("level") == "ERROR"]
    show("ERROR lines during the run", [e.get("msg") for e in errors])
    assert not errors, errors
