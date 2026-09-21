"""Cancellation-safe per-key gates; idle gates retain no credentials."""

from contextlib import asynccontextmanager

import anyio


class KeyedGate:
    def __init__(self):
        self._lock = anyio.Lock()
        self._entries = {}

    @asynccontextmanager
    async def hold(self, key):
        async with self._lock:
            entry = self._entries.setdefault(key, [anyio.Lock(), 0])
            entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    entry[1] -= 1
                    if not entry[1]:
                        del self._entries[key]
