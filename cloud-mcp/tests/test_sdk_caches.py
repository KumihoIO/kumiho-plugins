"""Real SDK cache lookups must use the current caller after credential changes."""

import asyncio
from types import SimpleNamespace

import kumiho
import kumiho.mcp_server as sdk
import pytest
from kumiho.request_context import RequestContext, request_context

from kumiho_cloud_mcp.sdk_caches import install_sdk_cache_isolation, sdk_cache_scope


class Client:
    def __init__(self, credential):
        self.credential = credential

    def get_project(self, name):
        client = self
        return SimpleNamespace(
            name=name, _client=client,
            create_bundle=lambda *a, **kw: SimpleNamespace(_client=client),
        )


@pytest.fixture(autouse=True)
def isolated_sdk(monkeypatch):
    monkeypatch.setattr(sdk, "_project_cache", {})
    monkeypatch.setattr(sdk, "_bundle_cache", {})
    install_sdk_cache_isolation()


def handles():
    project = sdk._get_project_cached("CognitiveMemory")
    bundle = sdk._get_or_create_bundle(project, "/CognitiveMemory/review", "session")
    return project, bundle


@pytest.mark.parametrize("next_tenant,next_user", [
    ("tenant", "alice"),  # Same user, rotated or renewed OAuth token.
    ("tenant", "bob"),    # Same tenant, different permissions.
    ("other", "bob"),
])
def test_old_project_and_bundle_credentials_do_not_survive(next_tenant, next_user):
    old, fresh = Client("expired"), Client("fresh")
    with kumiho.use_client(old), request_context(RequestContext("tenant", "alice", "old")), sdk_cache_scope():
        before = handles()
        assert handles() == before  # Still cache within one tool invocation.
    with kumiho.use_client(fresh), request_context(RequestContext(next_tenant, next_user, "new")), sdk_cache_scope():
        after = handles()
        assert all(handle._client is fresh for handle in after)
        assert all(a is not b for a, b in zip(after, before, strict=True))
    assert len(sdk._project_cache) == len(sdk._bundle_cache) == 0


@pytest.mark.anyio
async def test_concurrent_same_tenant_calls_and_worker_threads_keep_their_client():
    ready = 0
    gate = asyncio.Event()

    async def call(index):
        nonlocal ready
        client = Client(str(index))
        context = RequestContext("shared-tenant", f"user-{index}", f"fake-token-{index}")
        with kumiho.use_client(client), request_context(context), sdk_cache_scope():
            before = await asyncio.to_thread(handles)
            ready += 1
            if ready == 12:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            after = await asyncio.to_thread(handles)
            assert before == after
            assert all(handle._client is client for handle in after)

    await asyncio.gather(*(call(i) for i in range(12)))


def test_exception_resets_scope_and_does_not_retain_handles():
    client = Client("synthetic")
    with kumiho.use_client(client), request_context(RequestContext("t", "u", "fake")):
        with sdk_cache_scope():
            outer = handles()
            with pytest.raises(ValueError), sdk_cache_scope():
                assert handles() != outer
                raise ValueError("synthetic failure")
            assert handles() == outer
        assert len(sdk._project_cache) == len(sdk._bundle_cache) == 0
        with pytest.raises(RuntimeError, match="outside a hosted tool call"):
            handles()


def test_unknown_sdk_cache_shape_fails_before_accepting_requests(monkeypatch):
    monkeypatch.setattr(sdk, "_bundle_cache", None)
    with pytest.raises(RuntimeError, match="Cannot isolate SDK handle cache"):
        install_sdk_cache_isolation()
