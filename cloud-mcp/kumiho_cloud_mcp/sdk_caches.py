"""Keep SDK handles inside one tool call, including its worker threads.

Kumiho 0.13.0 caches Project and Item handles by tenant. Those handles retain
the client (and bearer token) that created them. Reusing one after OAuth token
rotation, or for a different member of the same tenant, reuses that identity.
Adapt only these two handle caches; the SDK still owns every operation.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_handles: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "kumiho_tool_handles", default=None
)


class _ToolHandleCache(MutableMapping):
    def __init__(self, name: str) -> None:
        self.name = name

    def _read(self) -> dict:
        scope = _handles.get()
        return scope[self.name] if scope is not None else {}

    def __getitem__(self, key):
        return self._read()[key]

    def __setitem__(self, key, value):
        scope = _handles.get()
        if scope is None:
            raise RuntimeError("SDK handle cache used outside a hosted tool call")
        scope[self.name][key] = value

    def __delitem__(self, key):
        del self._read()[key]

    def __iter__(self):
        return iter(self._read())

    def __len__(self):
        return len(self._read())


def install_sdk_cache_isolation() -> None:
    """Install once at startup, before requests; fail on an unknown SDK shape."""
    import kumiho.mcp_server as sdk

    names = ("_project_cache", "_bundle_cache")
    for name in names:
        cache = getattr(sdk, name, None)
        if not isinstance(cache, (dict, _ToolHandleCache)):
            raise RuntimeError(f"Cannot isolate SDK handle cache {name}")
    for name in names:
        if not isinstance(getattr(sdk, name), _ToolHandleCache):
            setattr(sdk, name, _ToolHandleCache(name))


@contextmanager
def sdk_cache_scope() -> Iterator[None]:
    # asyncio.to_thread copies this ContextVar. Concurrent calls get different
    # dictionaries. No handle survives into the next call; resetting also
    # restores an enclosing scope after cancellation or an exception.
    token = _handles.set({"_project_cache": {}, "_bundle_cache": {}})
    try:
        yield
    finally:
        _handles.reset(token)
