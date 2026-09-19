"""Judged delivery is switched on per tenant, from the verified tier claim.

The override itself belongs to ``kumiho-memory``; what this service owns is the
decision (which tier, which operator switch) and the guarantee that the
decision reaches exactly one request. The stand-in bound here is the same
contextvar-plus-context-manager shape the real one has, so the concurrency test
below fails for a process-wide switch just as it would in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import sys
import types as pytypes
from typing import Any, List, Optional

import anyio
import mcp.types as types
import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc, service_claims
from mcp.server.lowlevel import Server

from kumiho_cloud_mcp import judged_delivery as jd
from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.auth import Principal

#: What the stand-in override binds; read back the way a tool handler would.
_bound: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "test_judged_delivery", default=None,
)


@contextlib.contextmanager
def _stand_in(enabled: bool):
    """``kumiho_memory.context_optimization.judged_delivery``, reimplemented."""
    token = _bound.set(bool(enabled))
    try:
        yield bool(enabled)
    finally:
        _bound.reset(token)


@pytest.fixture
def override_available(monkeypatch):
    """Pretend the installed kumiho-memory carries the override."""
    monkeypatch.setattr(jd, "_override_cm", _stand_in)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(jd.JUDGED_DELIVERY_ENV, "1")


def principal(tier: Any = ..., **overrides: Any) -> Principal:
    claims = dict(base_claims())
    if tier is ...:
        pass
    elif tier is None:
        claims.pop(jd.TIER_CLAIM, None)
    else:
        claims[jd.TIER_CLAIM] = tier
    claims.update(overrides)
    return Principal(
        tenant_id=str(claims.get("tenant_id")), user_id=str(claims.get("sub")),
        token="test-token", kind="oauth", claims=claims, scopes=["memory"],
    )


def bound_for(subject: Principal) -> Optional[bool]:
    with jd.judged_delivery_for(subject) as value:
        assert _bound.get() == value
        return _bound.get()


# ---------------------------------------------------------------------------
# The tier matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["STUDIO", "STUDIO_PRO", "ENTERPRISE"])
def test_an_entitled_tier_turns_judged_delivery_on(tier, override_available, switch_on):
    assert bound_for(principal(tier)) is True


@pytest.mark.parametrize("tier", ["studio", "Studio_Pro", " enterprise "])
def test_the_tier_code_is_matched_case_insensitively(tier, override_available, switch_on):
    assert bound_for(principal(tier)) is True


@pytest.mark.parametrize("tier", [
    "FREE", "PRO", "pro", "STUDIO PRO", "STUDIOPRO", "STUDIO_PRO_PLUS", "", "   ", None, 0,
])
def test_every_other_tier_turns_it_off(tier, override_available, switch_on):
    """Including the tier the control plane has not minted yet: unknown is off."""
    assert bound_for(principal(tier)) is False


def test_a_missing_claim_turns_it_off(override_available, switch_on):
    subject = principal(None)
    assert jd.TIER_CLAIM not in subject.claims
    assert bound_for(subject) is False


def test_a_service_token_without_a_tier_turns_it_off(override_available, switch_on):
    subject = Principal(
        tenant_id="tenant-bbbb", user_id="service:svc-token-1", token="t",
        kind="service", claims=dict(service_claims()), scopes=["memory"],
    )
    assert bound_for(subject) is False


def test_the_dev_principal_has_no_tier(override_available, switch_on):
    """Dev mode disables auth; it must not hand itself a paid entitlement."""
    subject = Principal(
        tenant_id="dev", user_id="dev", token="", kind="dev", claims={"dev_mode": "ce"},
    )
    assert bound_for(subject) is False


# ---------------------------------------------------------------------------
# The operator switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["STUDIO", "STUDIO_PRO", "ENTERPRISE", "FREE", None])
def test_no_override_is_set_while_the_switch_is_off(tier, override_available, monkeypatch):
    """Off means *nothing bound*, not bound-to-false: kumiho-memory's own
    environment has to keep deciding exactly as it does today."""
    monkeypatch.delenv(jd.JUDGED_DELIVERY_ENV, raising=False)
    assert bound_for(principal(tier)) is None


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_switch_accepts_the_usual_spellings(raw, monkeypatch):
    monkeypatch.setenv(jd.JUDGED_DELIVERY_ENV, raw)
    assert jd.switch_enabled() is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "maybe"])
def test_anything_else_leaves_the_switch_off(raw, monkeypatch):
    monkeypatch.setenv(jd.JUDGED_DELIVERY_ENV, raw)
    assert jd.switch_enabled() is False


def test_the_switch_is_off_when_unset(monkeypatch):
    monkeypatch.delenv(jd.JUDGED_DELIVERY_ENV, raising=False)
    assert jd.switch_enabled() is False


# ---------------------------------------------------------------------------
# A kumiho-memory that predates the override
# ---------------------------------------------------------------------------


def test_a_pinned_release_without_the_override_changes_nothing(monkeypatch, switch_on):
    monkeypatch.setattr(jd, "_override_cm", None)
    assert bound_for(principal("STUDIO")) is None


def test_a_missing_module_is_not_an_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "kumiho_memory.context_optimization", None)
    assert jd._load_override() is None


def test_a_module_without_the_symbol_is_not_an_error(monkeypatch):
    module = pytypes.ModuleType("kumiho_memory.context_optimization")
    monkeypatch.setitem(sys.modules, "kumiho_memory.context_optimization", module)
    assert jd._load_override() is None


def test_the_symbol_is_used_when_it_is_there(monkeypatch):
    module = pytypes.ModuleType("kumiho_memory.context_optimization")
    module.judged_delivery = _stand_in  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kumiho_memory.context_optimization", module)
    assert jd._load_override() is _stand_in


def test_one_warning_when_the_operator_asked_for_what_is_missing(
    monkeypatch, switch_on, caplog,
):
    monkeypatch.setattr(jd, "_override_cm", None)
    with caplog.at_level(logging.WARNING, logger="kumiho.cloud_mcp.judged_delivery"):
        jd.warn_if_unsupported()
    assert len(caplog.records) == 1
    assert jd.JUDGED_DELIVERY_ENV in caplog.records[0].env


def test_silence_when_the_operator_did_not_ask(monkeypatch, caplog):
    monkeypatch.setattr(jd, "_override_cm", None)
    monkeypatch.delenv(jd.JUDGED_DELIVERY_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="kumiho.cloud_mcp.judged_delivery"):
        jd.warn_if_unsupported()
    assert caplog.records == []


def test_silence_when_the_override_is_there(monkeypatch, switch_on, caplog, override_available):
    with caplog.at_level(logging.WARNING, logger="kumiho.cloud_mcp.judged_delivery"):
        jd.warn_if_unsupported()
    assert caplog.records == []


# ---------------------------------------------------------------------------
# Two tenants through one process
# ---------------------------------------------------------------------------


def test_the_binding_does_not_outlive_the_request(override_available, switch_on):
    with jd.judged_delivery_for(principal("STUDIO")):
        assert _bound.get() is True
    assert _bound.get() is None


def test_nested_requests_in_one_task_do_not_bleed(override_available, switch_on):
    with jd.judged_delivery_for(principal("ENTERPRISE")):
        with jd.judged_delivery_for(principal("FREE")):
            assert _bound.get() is False
        assert _bound.get() is True


def test_overlapping_tenants_keep_their_own_answer(override_available, switch_on):
    """The unit-level version of the leak this whole design exists to stop."""
    seen = {}

    async def request(label, tier, entered, other):
        with jd.judged_delivery_for(principal(tier)):
            entered.set()
            await other.wait()
            seen[label] = _bound.get()

    async def overlapping():
        paid, free = asyncio.Event(), asyncio.Event()
        await asyncio.gather(
            request("paid", "STUDIO", paid, free),
            request("free", "FREE", free, paid),
        )

    asyncio.run(overlapping())

    assert seen == {"paid": True, "free": False}
    assert _bound.get() is None


# ---------------------------------------------------------------------------
# End to end: two tiers, overlapping, through /mcp
# ---------------------------------------------------------------------------


def build_probe_server(delay: float = 0.05) -> Server:
    """A one-tool server that reports the override from inside a worker thread.

    Blocking, via ``asyncio.to_thread``, because that is how every real Kumiho
    tool handler runs — and therefore where a contextvar that did not
    propagate would show up.
    """

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(
            name="judged", description="Report the judged-delivery override.",
            inputSchema={"type": "object", "properties": {}},
        )])

    async def on_call_tool(ctx, params):
        await asyncio.sleep(delay)
        value = await asyncio.to_thread(_bound.get)
        return types.CallToolResult(content=[types.TextContent(
            type="text", text=json.dumps({"judged": value}),
        )])

    server = Server("kumiho-judged-probe", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    server.__kumiho_profile_source__ = "stub"  # type: ignore[attr-defined]
    return server


@pytest.fixture
def app(settings, fake_clients):
    return create_app(settings, server_factory=build_probe_server)


async def _judged(http, token: str) -> Optional[bool]:
    response = await http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "judged", "arguments": {}}),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload.get("isError") is not True, payload
    return json.loads(payload["content"][0]["text"])["judged"]


@pytest.mark.anyio
async def test_two_tiers_at_once_never_see_each_others_switch(
    app, control_plane, keypair, override_available, switch_on,
):
    paid = keypair.sign(base_claims(tenant_id="tenant-paid", tenant_tier="STUDIO_PRO"))
    free = keypair.sign(base_claims(tenant_id="tenant-free", tenant_tier="FREE"))
    seen: List[tuple] = []

    async with client_for(app, control_plane) as http:

        async def call(label: str, token: str) -> None:
            seen.append((label, await _judged(http, token)))

        async with anyio.create_task_group() as tg:
            for _ in range(4):
                tg.start_soon(call, "paid", paid)
                tg.start_soon(call, "free", free)

    assert len(seen) == 8
    for label, value in seen:
        assert value is (label == "paid"), (label, value)


@pytest.mark.anyio
async def test_a_request_binds_nothing_while_the_switch_is_off(
    app, control_plane, keypair, override_available, monkeypatch,
):
    monkeypatch.delenv(jd.JUDGED_DELIVERY_ENV, raising=False)
    token = keypair.sign(base_claims(tenant_tier="ENTERPRISE"))
    async with client_for(app, control_plane) as http:
        assert await _judged(http, token) is None


@pytest.mark.anyio
async def test_a_request_binds_nothing_without_the_override(
    app, control_plane, keypair, switch_on, monkeypatch,
):
    monkeypatch.setattr(jd, "_override_cm", None)
    token = keypair.sign(base_claims(tenant_tier="ENTERPRISE"))
    async with client_for(app, control_plane) as http:
        assert await _judged(http, token) is None
