"""Two tenants, one process, the real stack — and no leak between them.

``tests/test_concurrency.py`` proves the same property with a stub server and a
fake gRPC client: it shows the *wiring* never crosses. This shows the wiring is
attached to something real — that a second tenant gets its own memory manager,
its own Redis key space, its own session pointer and its own conversation
buffer, through a live CE backend and a live Redis.

Both tenants are dev identities selected by ``x-kumiho-dev-tenant``, which is
only honoured under ``KUMIHO_MCP_DEV_MODE=ce``. There is no way to obtain two
*real* tenants on a CE box, and CE does not enforce ``x-tenant-id`` on graph
calls the way the managed backend does, so the graph layer is not what this
test is claiming to prove. What it does prove is the layer where a hosted leak
would actually happen: the per-tenant manager cache, the Redis namespace, and
the active-session pointer.
"""

from __future__ import annotations

import json

import anyio
import pytest
from e2e._client import connect
from e2e.conftest import REDIS_HOST, REDIS_PORT

from kumiho_cloud_mcp.settings import DEV_TENANT_HEADER, dev_identity

pytestmark = pytest.mark.anyio

ALPHA = "iso-alpha"
BETA = "iso-beta"

ALPHA_MARKER = "ALPHA-ONLY-MARKER-a1b2c3"
BETA_MARKER = "BETA-ONLY-MARKER-d4e5f6"


def show(label: str, value, limit: int = 700) -> None:
    import sys

    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(
        value, (dict, list)
    ) else str(value)
    if len(text) > limit:
        text = text[:limit] + f"... (+{len(text) - limit} chars)"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(f"\n[ISO] {label}: {text}".encode(encoding, "replace").decode(encoding, "replace"))


async def _buffer_and_read(url: str, label: str, marker: str, results: dict) -> None:
    """One tenant's whole conversation: buffer a marker, then read it back."""
    async with connect(url, headers={DEV_TENANT_HEADER: label}) as conn:
        reflect = await conn.ok(
            "kumiho_memory_reflect",
            # No captures: this is about the session/Redis layer, and a capture
            # would drag the graph (and CE's project state) into the assertion.
            {"response": marker, "captures": []},
        )
        chat = await conn.ok("kumiho_chat_get", {"limit": 50})
        results[label] = {
            "session_id": reflect.get("session_id"),
            "session_id_source": reflect.get("session_id_source"),
            "chat_session_id": chat.get("session_id"),
            "chat_session_id_source": chat.get("session_id_source"),
            "messages": [m.get("content", "") for m in (chat.get("messages") or [])],
        }


async def test_two_dev_tenants_do_not_see_each_other(live_server):
    results: dict = {}
    async with anyio.create_task_group() as tg:
        tg.start_soon(_buffer_and_read, live_server.url, ALPHA, ALPHA_MARKER, results)
        tg.start_soon(_buffer_and_read, live_server.url, BETA, BETA_MARKER, results)

    alpha, beta = results[ALPHA], results[BETA]
    show("alpha", {k: v for k, v in alpha.items() if k != "messages"})
    show("beta", {k: v for k, v in beta.items() if k != "messages"})
    show("alpha messages", alpha["messages"])
    show("beta messages", beta["messages"])

    # 1. Different sessions. The generated id embeds a hash of the user, and
    #    the dev header moves the user with the tenant, so two tenants can
    #    never land on one conversation.
    assert alpha["session_id"] and beta["session_id"]
    assert alpha["session_id"] != beta["session_id"]

    # 2. Each tenant's id-less read resolves through ITS OWN active-session
    #    pointer, which is what a real connector client relies on every turn.
    assert alpha["chat_session_id"] == alpha["session_id"]
    assert beta["chat_session_id"] == beta["session_id"]
    assert alpha["chat_session_id_source"] == "active_session"
    assert beta["chat_session_id_source"] == "active_session"

    # 3. The buffers themselves. This is the assertion that would have caught a
    #    shared manager, a shared Redis prefix or a shared pointer.
    assert any(ALPHA_MARKER in m for m in alpha["messages"])
    assert not any(BETA_MARKER in m for m in alpha["messages"])
    assert any(BETA_MARKER in m for m in beta["messages"])
    assert not any(ALPHA_MARKER in m for m in beta["messages"])


async def test_redis_keys_are_namespaced_per_tenant(live_server):
    """The key space, read straight out of Redis rather than through the API."""
    redis = pytest.importorskip("redis.asyncio", reason="redis-py not installed")

    alpha_tenant = dev_identity(ALPHA)[0]
    beta_tenant = dev_identity(BETA)[0]

    client = redis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}", decode_responses=True)
    try:
        keys = [k async for k in client.scan_iter(match="kumiho:memory:*", count=500)]
    finally:
        await client.aclose()

    alpha_keys = [k for k in keys if alpha_tenant in k]
    beta_keys = [k for k in keys if beta_tenant in k]
    show("alpha redis keys", alpha_keys)
    show("beta redis keys", beta_keys)

    assert alpha_keys, f"no Redis keys for tenant {alpha_tenant}"
    assert beta_keys, f"no Redis keys for tenant {beta_tenant}"
    # No key belongs to both. The prefix is kumiho:memory:{tenant_id}:… so this
    # is the property the control-plane proxy also enforces in production.
    assert not (set(alpha_keys) & set(beta_keys))
    for key in alpha_keys:
        assert beta_tenant not in key
    for key in beta_keys:
        assert alpha_tenant not in key


async def test_healthz_shows_one_manager_per_tenant(live_server):
    health = live_server.healthz()
    show("healthz", health)
    managers = health["tenant_managers"]
    # default dev tenant (from the round-trip test) + alpha + beta. A singleton
    # implementation would sit at 1 forever.
    assert managers["count"] >= 2, managers
    assert managers["process_singleton"] is False
    assert health["clients"] >= 2, health


async def test_no_log_line_mixes_the_two_tenants(live_server):
    """A leak often shows up in an error message before it shows up in data."""
    if not live_server.spawned:
        pytest.skip("borrowed a running server; its log is not ours to read")

    alpha_tenant = dev_identity(ALPHA)[0]
    beta_tenant = dev_identity(BETA)[0]

    mixed = []
    errors = []
    for line in live_server.log_lines():
        blob = json.dumps(line, default=str)
        if alpha_tenant in blob and beta_tenant in blob:
            mixed.append(line)
        if line.get("level") in ("ERROR", "CRITICAL"):
            errors.append(line)

    show("log lines mentioning both tenants", mixed)
    show("ERROR/CRITICAL lines", [e.get("msg") for e in errors])
    assert not mixed, "a single log record named both tenants"
    assert not errors, errors

    # And no traceback anywhere: an exception escaping a tool handler is how a
    # foreign identifier would reach the other tenant's error text.
    raw = live_server.log_path.read_text(encoding="utf-8", errors="replace")
    assert "Traceback (most recent call last)" not in raw
