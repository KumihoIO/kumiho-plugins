"""Every graph write lands in the caller's own tenant. (kumiho-memory #22)

The concern issue #22 raises is specific and worth restating precisely, because
the whole design reads *as if* it were already answered. ``kumiho_memory_reflect``
does not carry a client: it calls the SDK's **module-level**
``kumiho.mcp_server.tool_memory_store``, which resolves its client through
``kumiho.get_client()``. That prefers the ``use_client`` contextvar and falls
back to the process default. Every tenant's project is called
``CognitiveMemory``, so a store that resolved the process default would write a
plausible-looking memory into the wrong graph and nothing downstream would look
wrong.

Three things are asserted here, and nothing is mocked between reflect and the
graph — the real ``tool_memory_reflect``, the real per-tenant manager, the real
``tool_memory_store`` with its real caches, stacking search and gates:

1. **Attribution.** Every graph call made while serving tenant A's request went
   to tenant A's client, and none to B's — checked per call by comparing the
   receiving client against ``current_request().tenant_id`` at the moment of
   the call, with both tenants in flight at once.
2. **No ambient fallback.** ``kumiho._default_client`` is ``None`` at every
   single graph call and afterwards, so nothing resolved through the process
   default and got lucky.
3. **No cross-tenant reference.** The krefs reflect reports back for A name
   items and revisions created on A's client, and A's stacked second revision
   lands on A's own first item — the SDK's process caches are keyed by tenant,
   and this is what proves it end to end rather than by inspection.

A second test covers the other direction: hosted mode with a request context
but **no** bound client must raise before touching a graph at all.

The fakes here are richer than ``conftest.FakeKumihoClient`` on purpose. Mocking
``tool_memory_store`` would remove exactly the code under test.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

import anyio
import pytest
from conftest import MCP_HEADERS, base_claims, client_for, rpc

from kumiho_cloud_mcp._compat import RequestContext, request_context
from kumiho_cloud_mcp.app import create_app

pytestmark = pytest.mark.anyio

TENANT_A = "tenant-scoped-a"
TENANT_B = "tenant-scoped-b"

#: Long enough to clear the SDK's 8-token lexical-overlap floor, and different
#: enough between tenants that a leaked write is obvious in a kref.
CAPTURE = {
    TENANT_A: {
        "space": "alpha-workspace",
        "title": "Alpha team chose gRPC streaming for the ingest pipeline",
        "content": (
            "The alpha team decided on 2026-09-02 to use gRPC streaming for the "
            "ingest pipeline because batch upload latency was unacceptable."
        ),
    },
    TENANT_B: {
        "space": "beta-workspace",
        "title": "Beta team standardised deployment on immutable images",
        "content": (
            "The beta team decided on 2026-09-02 to standardise deployment on "
            "immutable container images built once per release candidate."
        ),
    },
}


# ---------------------------------------------------------------------------
# recording graph fakes
# ---------------------------------------------------------------------------


class Ledger:
    """Every graph call, with who received it and who was being served."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.kref_owner: Dict[str, str] = {}
        self._lock = threading.Lock()

    def record(self, owner: str, method: str, **detail: Any) -> None:
        import kumiho

        from kumiho_cloud_mcp._compat import current_request

        ctx = current_request()
        bound = kumiho._client_context_var.get()
        entry = {
            "owner": owner,
            "method": method,
            "ctx_tenant": getattr(ctx, "tenant_id", None),
            "bound_owner": getattr(bound, "owner", None),
            # Captured at the moment of the call, not afterwards: a default
            # client that existed only during the request would otherwise be
            # invisible.
            "default_client_set": kumiho._default_client is not None,
        }
        entry.update(detail)
        with self._lock:
            self.calls.append(entry)

    def own(self, uri: str, owner: str) -> str:
        with self._lock:
            self.kref_owner[uri] = owner
        return uri

    def for_tenant(self, tenant: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["ctx_tenant"] == tenant]

    def methods(self, tenant: str) -> set:
        return {c["method"] for c in self.for_tenant(tenant)}


class _Kref:
    def __init__(self, uri: str) -> None:
        self.uri = uri


class _Revision:
    def __init__(self, item: "_Item", number: int, metadata: Dict[str, str]) -> None:
        self.item = item
        self.number = number
        self.metadata = dict(metadata)
        self.tags: List[str] = []
        self.kref = _Kref(
            item.ledger.own(f"{item.kref.uri}?r={number}", item.owner)
        )
        self.owner = item.owner

    def tag(self, name: str) -> None:
        self.item.ledger.record(
            self.owner, "revision.tag", revision_kref=self.kref.uri, tag=name
        )
        self.tags.append(name)

    def create_edge(self, target: "_Revision", edge_type: str) -> Any:
        self.item.ledger.record(
            self.owner,
            "revision.create_edge",
            revision_kref=self.kref.uri,
            target_kref=target.kref.uri,
            edge_type=edge_type,
        )

        class _Edge:
            target_kref = target.kref

        return _Edge()

    def create_artifact(self, name: str, location: str) -> Any:  # pragma: no cover
        self.item.ledger.record(self.owner, "revision.create_artifact", name=name)

        class _Artifact:
            kref = _Kref(f"{self.kref.uri}#{name}")

        return _Artifact()


class _Item:
    def __init__(self, project: "_Project", name: str, kind: str, space: str) -> None:
        self.project = project
        self.ledger = project.ledger
        self.owner = project.owner
        self.item_name = name
        self.name = name
        self.kind = kind
        self.space = space
        self.revisions: List[_Revision] = []
        path = space.strip("/")
        self.kref = _Kref(self.ledger.own(f"kref://{path}/{name}.{kind}", self.owner))
        self.members: List[_Item] = []

    # -- revisions -------------------------------------------------------
    def create_revision(self, metadata: Optional[Dict[str, str]] = None) -> _Revision:
        self.ledger.record(
            self.owner,
            "item.create_revision",
            item_kref=self.kref.uri,
            title=(metadata or {}).get("title", ""),
        )
        revision = _Revision(self, len(self.revisions) + 1, metadata or {})
        self.revisions.append(revision)
        return revision

    def get_revision_by_tag(self, tag: str) -> Optional[_Revision]:
        self.ledger.record(
            self.owner, "item.get_revision_by_tag", item_kref=self.kref.uri, tag=tag
        )
        for revision in reversed(self.revisions):
            if tag in revision.tags:
                return revision
        return None

    def get_revision(self, selector: str = "latest") -> Optional[_Revision]:
        self.ledger.record(
            self.owner, "item.get_revision", item_kref=self.kref.uri, selector=selector
        )
        return self.revisions[-1] if self.revisions else None

    # -- bundles ---------------------------------------------------------
    def add_member(self, item: "_Item") -> None:
        self.ledger.record(
            self.owner,
            "bundle.add_member",
            bundle_kref=self.kref.uri,
            item_kref=item.kref.uri,
            # The load-bearing detail: a bundle owned by one tenant must never
            # be handed an item minted on another tenant's client.
            member_owner=item.owner,
        )
        self.members.append(item)


class _SearchResult:
    def __init__(self, item: _Item, score: float) -> None:
        self.item = item
        self.score = score


class _Project:
    def __init__(self, client: "RecordingClient", name: str) -> None:
        self.client = client
        self.ledger = client.ledger
        self.owner = client.owner
        self.name = name
        self.spaces: List[str] = []
        self.items: Dict[str, _Item] = {}
        self.bundles: Dict[str, _Item] = {}

    def create_space(self, segment: str, parent_path: str = "") -> Any:
        self.ledger.record(
            self.owner, "project.create_space", segment=segment, parent=parent_path
        )
        self.client.pause()
        path = f"{parent_path.rstrip('/')}/{segment}"
        self.spaces.append(path)
        return path

    def get_spaces(self, recursive: bool = False) -> List[Any]:  # pragma: no cover
        self.ledger.record(self.owner, "project.get_spaces", recursive=recursive)

        class _Space:
            def __init__(self, path: str) -> None:
                self.path = path

        return [_Space(p) for p in self.spaces]

    def create_item(self, item_name: str, kind: str, parent_path: str = "") -> _Item:
        self.ledger.record(
            self.owner,
            "project.create_item",
            item_name=item_name,
            kind=kind,
            parent=parent_path,
        )
        self.client.pause()
        item = _Item(self, item_name, kind, parent_path)
        self.items[f"{parent_path}/{item_name}.{kind}"] = item
        return item

    def get_item(self, item_name: str, kind: str, parent_path: str = "") -> _Item:
        self.ledger.record(self.owner, "project.get_item", item_name=item_name)
        return self.items[f"{parent_path}/{item_name}.{kind}"]

    def create_bundle(self, bundle_name: str, parent_path: str = "") -> _Item:
        self.ledger.record(
            self.owner, "project.create_bundle", bundle_name=bundle_name, parent=parent_path
        )
        bundle = _Item(self, bundle_name, "bundle", parent_path)
        self.bundles[f"{parent_path}/{bundle_name}"] = bundle
        return bundle

    def get_bundle(self, bundle_name: str, parent_path: str = "") -> _Item:  # pragma: no cover
        self.ledger.record(self.owner, "project.get_bundle", bundle_name=bundle_name)
        return self.bundles[f"{parent_path}/{bundle_name}"]


class RecordingClient:
    """A kumiho client that records every graph call it is asked to make.

    ``owner`` is the tenant id off the ``x-tenant-id`` metadata the pool built
    the client with, which is the same value the request context carries — so
    "did this call reach the right client" is a comparison of two independently
    derived strings, not of one string with itself.
    """

    def __init__(self, *, target: str, token: Optional[str], metadata, ledger: Ledger) -> None:
        self.target = target
        self.token = token
        self.metadata = list(metadata)
        self.ledger = ledger
        self.closed = False
        self.delay = 0.0
        self.projects: Dict[str, _Project] = {}

    @property
    def owner(self) -> Optional[str]:
        return self.tenant_id

    @property
    def tenant_id(self) -> Optional[str]:
        for key, value in self.metadata:
            if key == "x-tenant-id":
                return value
        return None

    def pause(self) -> None:
        """Blocking, like a gRPC round trip — the handler thread yields here."""
        if self.delay:
            time.sleep(self.delay)

    # -- graph surface ---------------------------------------------------
    def get_project(self, name: str) -> _Project:
        self.ledger.record(self.owner, "client.get_project", project=name)
        self.pause()
        return self.projects.setdefault(name, _Project(self, name))

    def create_project(self, **kwargs: Any) -> _Project:  # pragma: no cover
        self.ledger.record(self.owner, "client.create_project", project=kwargs.get("name"))
        return self.projects.setdefault(kwargs["name"], _Project(self, kwargs["name"]))

    def search(self, query: str, *, context_filter: str = "", **kwargs: Any) -> List[_SearchResult]:
        """Return this client's own items in the searched space, scored high.

        High enough (0.90) to clear the strong band, so the second capture in a
        space stacks onto the first. That is deliberate: stacking is the path
        that reads an existing item and moves its ``published`` tag, so it is
        the path where writing to the wrong client would do the most damage.
        """
        self.ledger.record(self.owner, "client.search", query=query[:60], context=context_filter)
        self.pause()
        space = f"/{context_filter.strip('/')}"
        results: List[_SearchResult] = []
        for project in self.projects.values():
            for item in project.items.values():
                if item.space == space:
                    results.append(_SearchResult(item, 0.90))
        return results

    def get_revision(self, kref: str) -> _Revision:
        self.ledger.record(self.owner, "client.get_revision", kref=kref)
        for project in self.projects.values():
            for item in project.items.values():
                for revision in item.revisions:
                    if revision.kref.uri == kref:
                        return revision
        # A source kref this tenant does not own resolves to a detached stub
        # rather than raising, so the test sees the *call* attribution rather
        # than an exception the store path would swallow.
        project = self.projects.setdefault("CognitiveMemory", _Project(self, "CognitiveMemory"))
        stub = _Item(project, "unknown-source", "conversation", "/CognitiveMemory")
        return _Revision(stub, 1, {})

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture
def recording_clients(monkeypatch, ledger: Ledger) -> List[RecordingClient]:
    import kumiho_cloud_mcp.clients as clients_module

    built: List[RecordingClient] = []

    def _fake(*, target: str, token: Optional[str], metadata) -> RecordingClient:
        client = RecordingClient(target=target, token=token, metadata=metadata, ledger=ledger)
        client.delay = 0.02
        built.append(client)
        return client

    monkeypatch.setattr(clients_module, "_construct_client", _fake)
    return built


class _FakeRedisBuffer:
    """Stands in for Upstash. Not the subject: the graph is.

    Reflect buffers the assistant turn before it stores anything, and that hop
    is Redis. Keeping it in-process keeps the test hermetic without touching
    the store path, which is what #22 is about.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.tenant_id = kwargs.get("tenant_id")
        self.messages: List[Dict[str, Any]] = []

    async def add_message(self, **kwargs: Any) -> Dict[str, Any]:
        self.messages.append(kwargs)
        return {"message_count": len(self.messages), "created_bucket": len(self.messages) == 1}

    async def close(self) -> None:  # pragma: no cover - teardown convenience
        return None


@pytest.fixture(autouse=True)
def hermetic_memory(monkeypatch):
    """No Redis, and no state carried in or out through the process caches.

    The SDK's project/space/bundle caches and kumiho-memory's per-tenant
    manager cache are module globals keyed by tenant. Clearing them on both
    sides of the test keeps one test's tenants from being answered out of
    another's cache — which would make a leak *less* likely to show, i.e. it
    would weaken exactly the assertion this file exists for.
    """
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
    """The real Kumiho MCP server — the point is the real store path."""
    from kumiho_cloud_mcp._compat import build_server

    return build_server()


@pytest.fixture
def app(settings, recording_clients, real_server):
    return create_app(settings, server_factory=lambda: real_server)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _reflect(http, token: str, tenant: str, *, round_: int, source_kref: str = "") -> dict:
    capture = CAPTURE[tenant]
    arguments: Dict[str, Any] = {
        # Explicit, so session resolution never reaches the active-session
        # pointer; the buffer is not what this test is about.
        "session_id": f"claude:{tenant}:001",
        "response": f"Answered {tenant} in round {round_}.",
        "captures": [
            {
                "type": "decision",
                "title": capture["title"],
                "content": capture["content"],
                "space_hint": capture["space"],
            }
        ],
        # Edge discovery needs an LLM and is not part of the write path.
        "discover_edges": False,
    }
    if source_kref:
        arguments["source_krefs"] = [source_kref]

    response = await http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "kumiho_memory_reflect", "arguments": arguments}),
        headers={**MCP_HEADERS, "authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload.get("isError") is not True, payload
    return json.loads(payload["content"][0]["text"])


def _tokens(keypair):
    return {
        TENANT_A: keypair.sign(
            base_claims(tenant_id=TENANT_A, tenant_slug="alpha", sub="user-alpha", jti="jti-a")
        ),
        TENANT_B: keypair.sign(
            base_claims(tenant_id=TENANT_B, tenant_slug="beta", sub="user-beta", jti="jti-b")
        ),
    }


# ---------------------------------------------------------------------------
# 1. two tenants, concurrently, through the real store path
# ---------------------------------------------------------------------------


async def test_reflect_writes_only_to_the_calling_tenants_client(
    app, control_plane, keypair, ledger, recording_clients
):
    import kumiho

    tokens = _tokens(keypair)
    results: Dict[str, list] = {TENANT_A: [], TENANT_B: []}

    async with client_for(app, control_plane) as http:

        async def call(tenant: str, round_: int, source: str = "") -> None:
            results[tenant].append(await _reflect(http, tokens[tenant], tenant, round_=round_, source_kref=source))

        # Round 1 concurrently: both tenants mint their first item, with the
        # requests genuinely overlapping (the fakes block for 20 ms inside the
        # handler thread, so the event loop interleaves them).
        async with anyio.create_task_group() as tg:
            tg.start_soon(call, TENANT_A, 1)
            tg.start_soon(call, TENANT_B, 1)

        first = {t: results[t][0]["stored_krefs"][0] for t in (TENANT_A, TENANT_B)}

        # Round 2 concurrently, each citing its OWN round-1 revision as a
        # source. This is the stacking path: the search finds the round-1 item
        # and the store moves `published` onto a new revision of it.
        async with anyio.create_task_group() as tg:
            tg.start_soon(call, TENANT_A, 2, first[TENANT_A])
            tg.start_soon(call, TENANT_B, 2, first[TENANT_B])

    # -- the writes happened at all ---------------------------------------
    for tenant in (TENANT_A, TENANT_B):
        for result in results[tenant]:
            assert result["captures_stored"] == 1, result
            assert result["stored_krefs"], result

    assert ledger.calls, "the real store path made no graph calls at all"

    # -- 1. attribution ----------------------------------------------------
    # Every graph call, compared against the tenant being served at that
    # instant. One mismatch anywhere is a cross-tenant write.
    misrouted = [c for c in ledger.calls if c["owner"] != c["ctx_tenant"]]
    assert misrouted == [], f"graph calls reached the wrong tenant's client: {misrouted[:5]}"

    assert {c["owner"] for c in ledger.for_tenant(TENANT_A)} == {TENANT_A}
    assert {c["owner"] for c in ledger.for_tenant(TENANT_B)} == {TENANT_B}
    assert TENANT_B not in {c["owner"] for c in ledger.for_tenant(TENANT_A)}
    assert TENANT_A not in {c["owner"] for c in ledger.for_tenant(TENANT_B)}

    # The client bound by `use_client` is the client that was actually used.
    assert all(c["bound_owner"] == c["owner"] for c in ledger.calls)

    # And the whole surface #22 names was exercised, not just one cheap call.
    for tenant in (TENANT_A, TENANT_B):
        assert {
            "client.get_project",
            "client.search",
            "client.get_revision",
            "project.create_item",
            "item.create_revision",
        } <= ledger.methods(tenant), ledger.methods(tenant)

    # -- 2. no ambient fallback -------------------------------------------
    assert not any(c["default_client_set"] for c in ledger.calls)
    assert kumiho._default_client is None
    assert kumiho._client_context_var.get() is None

    # -- 3. no cross-tenant reference -------------------------------------
    for tenant, other in ((TENANT_A, TENANT_B), (TENANT_B, TENANT_A)):
        for result in results[tenant]:
            for kref in result["stored_krefs"]:
                assert ledger.kref_owner[kref] == tenant, kref
                assert CAPTURE[other]["space"] not in kref, kref

    # Stacking landed on this tenant's own first item, not anyone else's: the
    # round-2 revision is r=2 of the round-1 item.
    for tenant in (TENANT_A, TENANT_B):
        first_kref, second_kref = (r["stored_krefs"][0] for r in results[tenant])
        assert first_kref.endswith("?r=1"), first_kref
        assert second_kref.endswith("?r=2"), second_kref
        assert second_kref.rsplit("?r=", 1)[0] == first_kref.rsplit("?r=", 1)[0]

    # A bundle only ever gained members minted on its own client.
    members = [c for c in ledger.calls if c["method"] == "bundle.add_member"]
    assert members, "the store path never reached the bundle"
    assert all(c["member_owner"] == c["owner"] for c in members)

    # One client per tenant, and no third one appeared from anywhere.
    assert {c.tenant_id for c in recording_clients} == {TENANT_A, TENANT_B}


async def test_tenant_caches_do_not_serve_the_second_tenant(
    app, control_plane, keypair, ledger, recording_clients
):
    """A serial A-then-B run: the SDK's project cache must miss for B.

    Run serially on purpose. Concurrency proves the contextvars hold; this
    proves the *caches* do, which is the failure that only appears when the
    second tenant arrives after the first has already populated them. An
    unprefixed ``_project_cache`` would hand B the ``Project`` handle — and so
    the channel and credentials — that A cached, and no assertion about the
    request context would notice.
    """
    import kumiho.mcp_server as mcp_server

    tokens = _tokens(keypair)
    async with client_for(app, control_plane) as http:
        await _reflect(http, tokens[TENANT_A], TENANT_A, round_=1)
        assert [c for c in ledger.for_tenant(TENANT_A) if c["method"] == "client.get_project"]

        await _reflect(http, tokens[TENANT_B], TENANT_B, round_=1)

    # B fetched its own project rather than reusing A's cached handle.
    b_projects = [c for c in ledger.for_tenant(TENANT_B) if c["method"] == "client.get_project"]
    assert b_projects, "tenant B was served project state cached for tenant A"
    assert all(c["owner"] == TENANT_B for c in b_projects)

    # Both tenants are present in the cache, under separate keys.
    keys = list(mcp_server._project_cache)
    assert len(keys) == 2
    assert any(k.startswith(TENANT_A) for k in keys)
    assert any(k.startswith(TENANT_B) for k in keys)

    assert [c for c in ledger.calls if c["owner"] != c["ctx_tenant"]] == []


# ---------------------------------------------------------------------------
# 2. fail closed: hosted, in a request, with no client bound
# ---------------------------------------------------------------------------


def test_store_refuses_to_run_without_a_bound_client(monkeypatch, ledger):
    """No ``use_client`` means no store — not a store against the default.

    Function level on purpose: the transport cannot produce this state (the
    RS always enters ``use_client``), which is exactly why the guard needs its
    own test. The default client is poisoned so that "it fell back and the
    fallback happened to be empty" cannot pass as "it refused".
    """
    import kumiho
    from kumiho.mcp_server import tool_memory_store

    touched: List[str] = []

    class _PoisonedClient:
        def __getattr__(self, name: str) -> Any:
            touched.append(name)
            raise AssertionError(
                f"the process-default client was used for {name!r}: a hosted store "
                "resolved ambient credentials instead of the request's own client"
            )

    monkeypatch.setattr(kumiho, "_default_client", _PoisonedClient())

    ctx = RequestContext(
        tenant_id="tenant-fail-closed",
        user_id="user-fail-closed",
        auth_token="token-fail-closed",
        context="claude",
    )

    with request_context(ctx):
        # Hosted, in a request, and deliberately *not* inside use_client.
        assert kumiho._client_context_var.get() is None
        with pytest.raises(RuntimeError) as excinfo:
            tool_memory_store(
                project="CognitiveMemory",
                space_hint="fail-closed",
                memory_type="decision",
                title="This must never reach a graph",
                summary="A hosted store with no bound client has to raise.",
                assistant_text="A hosted store with no bound client has to raise.",
            )

    message = str(excinfo.value)
    assert "use_client" in message
    assert "hosted" in message.lower()
    # Nothing was attempted against the default client, and nothing at all was
    # attempted against a graph.
    assert touched == []
    assert ledger.calls == []


def test_the_guard_is_hosted_only(monkeypatch):
    """Sanity: the refusal is the *hosted* rule, not a broken import.

    Outside a request context and outside hosted mode the SDK is free to
    auto-configure, and this test pins that the two paths are actually
    different code rather than the exception coming from somewhere incidental.
    """
    import kumiho.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "hosted_mode", lambda: False)
    configured: List[bool] = []
    monkeypatch.setattr(
        mcp_server.kumiho,
        "auto_configure_from_discovery",
        lambda *a, **k: configured.append(True),
    )

    assert mcp_server._ensure_configured() is True
    assert configured == [True]
