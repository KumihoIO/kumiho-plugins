"""Per-request, tenant-scoped Kumiho gRPC clients.

The rule from plan §2.1 is that hosted mode never touches ``~/.kumiho`` and
never mutates ``os.environ``. So routing is resolved by calling the control
plane's discovery endpoint directly (rather than through the SDK's
``DiscoveryManager``, which writes an encrypted cache file keyed by machine id)
and the client is constructed explicitly with the caller's own token.

Clients are pooled per ``(tenant_id, token_id)`` because building a gRPC
channel is expensive and a Claude conversation is many small requests. The
entry never outlives the token that created it.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import anyio
import anyio.to_thread
import httpx

from .auth import Principal
from .settings import Settings

logger = logging.getLogger("kumiho.cloud_mcp.clients")

#: Routing answers are small, but `tenant_id` comes from a token, so the map is
#: attacker-influenced in exactly the way an unbounded cache should never be.
DISCOVERY_CACHE_MAX = 1024

#: Floor on a pooled client's lifetime. A client built outside the pool is a
#: client nothing ever closes, so every one goes in and eviction does the
#: closing. The pool key carries the jti, so a near-expired credential still
#: cannot have its channel reused by a different token.
MIN_CLIENT_TTL_SECONDS = 30.0

#: Constructor switches that keep a client off the operator's ambient
#: credentials and off the local discovery cache file.
REQUIRED_CLIENT_KWARGS = ("skip_auth_token_load", "enable_auto_login", "use_discovery")


class ClientContractError(RuntimeError):
    """The installed SDK cannot build a client without ambient credentials."""


class RoutingError(RuntimeError):
    """Discovery could not resolve a regional server for the tenant."""


class DiscoveryRouter:
    """Resolves ``tenant -> regional gRPC target`` via the control plane."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Ordered so the sweep can evict least-recently-used, and bounded so a
        # stream of tokens for distinct tenant ids cannot grow it without limit.
        self._cache: "OrderedDict[str, Tuple[float, Optional[str]]]" = OrderedDict()
        self._lock = anyio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def attach(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _sweep_locked(self, now: float) -> None:
        for key in [k for k, (deadline, _) in self._cache.items() if deadline <= now]:
            self._cache.pop(key, None)
        while len(self._cache) > DISCOVERY_CACHE_MAX:
            self._cache.popitem(last=False)

    async def resolve(self, principal: Principal) -> Optional[str]:
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(principal.tenant_id)
            if cached and cached[0] > now:
                self._cache.move_to_end(principal.tenant_id)
                return cached[1]
            if cached:
                self._cache.pop(principal.tenant_id, None)

        hint = principal.tenant_slug or principal.tenant_id
        client = self._client or httpx.AsyncClient(timeout=self.settings.http_timeout_seconds)
        owns = self._client is None
        try:
            response = await client.post(
                self.settings.discovery_url,
                json={"tenant_hint": hint},
                headers={"Authorization": f"Bearer {principal.token}"},
                timeout=self.settings.http_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise RoutingError(f"discovery request failed: {exc}") from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code >= 400:
            raise RoutingError(f"discovery returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RoutingError("discovery returned invalid JSON") from exc

        region = payload.get("region") if isinstance(payload, dict) else None
        if not isinstance(region, dict):
            raise RoutingError("discovery response has no region")
        target = region.get("grpc_authority") or region.get("server_url")
        if not isinstance(target, str) or not target:
            raise RoutingError("discovery response has no usable endpoint")

        async with self._lock:
            self._cache[principal.tenant_id] = (
                time.monotonic() + self.settings.discovery_cache_seconds,
                target,
            )
            self._cache.move_to_end(principal.tenant_id)
            self._sweep_locked(time.monotonic())
        logger.info(
            "resolved tenant routing",
            extra={"tenant_id": principal.tenant_id, "target": target},
        )
        return target


@dataclass
class _PooledClient:
    """One channel, plus the bookkeeping that decides when it may be closed."""

    client: Any
    expires_at: float
    leases: int = 0
    #: Removed from the pool. Closed as soon as the last borrower lets go.
    retired: bool = False


class ClientLease:
    """A borrowed client. ``release()`` exactly once, always."""

    __slots__ = ("_pool", "_entry", "_released")

    def __init__(self, pool: "ClientPool", entry: _PooledClient) -> None:
        self._pool = pool
        self._entry = entry
        self._released = False

    @property
    def client(self) -> Any:
        return self._entry.client

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._pool._release(self._entry)


class ClientPool:
    """Bounded LRU of gRPC clients keyed by ``(tenant_id, token_id)``.

    ``token_id`` is in the key so a rotated credential never reuses the channel
    built for the previous one, and the entry expires no later than the token
    it was built from.

    Every client is reachable from the pool, and every client leaves it through
    :meth:`_close` — expiry, LRU overflow, replacement and shutdown alike. A
    client handed out but never pooled would be a channel nothing ever closes,
    which is why :data:`MIN_CLIENT_TTL_SECONDS` floors the TTL rather than
    letting a near-expired token opt out of pooling.

    Eviction never closes a channel somebody is still using: an evicted entry is
    *retired* (out of the map, unreachable to new borrowers) and closed when its
    last lease is released.
    """

    def __init__(self, settings: Settings, router: DiscoveryRouter) -> None:
        self.settings = settings
        self.router = router
        self._entries: "OrderedDict[Tuple[str, str], _PooledClient]" = OrderedDict()
        self._lock = anyio.Lock()

    def _key(self, principal: Principal) -> Tuple[str, str]:
        return (principal.tenant_id, principal.token_id or principal.user_id)

    # -- public ----------------------------------------------------------
    async def acquire(self, principal: Principal) -> ClientLease:
        """Borrow a client. The caller must ``release()`` the lease."""
        key = self._key(principal)
        now = time.monotonic()

        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                entry.leases += 1
                self._entries.move_to_end(key)
                return ClientLease(self, entry)
            retired = self._retire_locked(now)

        await self._close_all(retired)

        client = await self._build(principal)
        entry = _PooledClient(
            client=client, expires_at=time.monotonic() + self._ttl_for(principal), leases=1
        )

        async with self._lock:
            # A concurrent acquire for the same key may have won the race. Ours
            # is already leased, so it becomes the pooled one and theirs retires
            # — whoever holds that one keeps working until they let go.
            displaced = self._entries.pop(key, None)
            if displaced is not None:
                displaced.retired = True
            self._entries[key] = entry
            self._entries.move_to_end(key)
            evicted = self._retire_locked(time.monotonic())
            if displaced is not None and displaced.leases <= 0:
                evicted.append(displaced)

        await self._close_all(evicted)
        return ClientLease(self, entry)

    @contextlib.asynccontextmanager
    async def lease(self, principal: Principal) -> AsyncIterator[Any]:
        """``async with pool.lease(p) as client:`` — release is automatic."""
        leased = await self.acquire(principal)
        try:
            yield leased.client
        finally:
            await leased.release()

    async def aclose(self) -> None:
        """Shutdown: close everything, leased or not."""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.retired = True
        await self._close_all(entries)

    # -- internals -------------------------------------------------------
    def _ttl_for(self, principal: Principal) -> float:
        ttl = 15 * 60.0
        if principal.expires_at:
            ttl = min(ttl, principal.expires_at - time.time())
        return max(ttl, MIN_CLIENT_TTL_SECONDS)

    def _retire_locked(self, now: float) -> List[_PooledClient]:
        """Drop expired and over-cap entries from the map; return the closable ones."""
        retired: List[_PooledClient] = []
        for key in [k for k, e in self._entries.items() if e.expires_at <= now]:
            entry = self._entries.pop(key)
            entry.retired = True
            retired.append(entry)
        while len(self._entries) > self.settings.client_cache_max:
            _, entry = self._entries.popitem(last=False)
            entry.retired = True
            retired.append(entry)
        return [e for e in retired if e.leases <= 0]

    async def _release(self, entry: _PooledClient) -> None:
        async with self._lock:
            entry.leases -= 1
            close_now = entry.retired and entry.leases <= 0
        if close_now:
            await self._close(entry.client)

    async def _close_all(self, entries: List[_PooledClient]) -> None:
        for entry in entries:
            await self._close(entry.client)

    async def _close(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            await anyio.to_thread.run_sync(close)
        except Exception:  # noqa: BLE001 - a leaked channel is worse than a log line
            logger.debug("client close failed", exc_info=True)

    async def _build(self, principal: Principal) -> Any:
        metadata = [("x-tenant-id", principal.tenant_id)]

        if self.settings.dev:
            target = self.settings.local_server_endpoint
            logger.info(
                "building CE dev client",
                extra={"tenant_id": principal.tenant_id, "target": target},
            )
            return await anyio.to_thread.run_sync(
                lambda: _construct_client(target=target, token=None, metadata=metadata)
            )

        # Outside dev mode, a client that cannot be told to ignore ~/.kumiho is
        # not a degraded client — it is the wrong caller's credentials.
        problems = client_construction_problems()
        if problems:
            raise ClientContractError("; ".join(problems))

        target = await self.router.resolve(principal)
        if not target:
            raise RoutingError("no regional server for tenant")
        logger.info(
            "building tenant client",
            extra={
                "tenant_id": principal.tenant_id,
                "target": target,
                "token_id": principal.token_id,
            },
        )
        return await anyio.to_thread.run_sync(
            lambda: _construct_client(target=target, token=principal.token, metadata=metadata)
        )


_construction_problems_cache: Optional[List[str]] = None


def client_construction_problems(*, refresh: bool = False) -> List[str]:
    """Ways the installed SDK cannot build a client free of ambient credentials.

    ``_construct_client`` used to filter its keyword arguments against the real
    signature and merely *warn* when one was missing. That is fail-open in the
    worst possible place: drop ``skip_auth_token_load`` on an old SDK and a
    request runs as whatever identity is sitting in the operator's
    ``~/.kumiho``. Outside dev mode that has to stop the process, so the answer
    is computed here and consumed twice — once at startup through
    ``app._dependency_problems``, and again before any client is built.
    """
    global _construction_problems_cache
    if _construction_problems_cache is not None and not refresh:
        return list(_construction_problems_cache)

    problems: List[str] = []
    try:
        from kumiho.client import _Client
    except Exception as exc:  # noqa: BLE001 - any import failure is the same story
        problems.append(
            f"kumiho.client._Client is not importable ({exc}), so a client could only be "
            "built through kumiho.connect(), which has no skip_auth_token_load switch and "
            "may fall back to the operator's ~/.kumiho credentials"
        )
    else:
        try:
            accepted = set(inspect.signature(_Client.__init__).parameters)
        except (TypeError, ValueError):  # pragma: no cover - exotic classes
            accepted = set()
        missing = [name for name in REQUIRED_CLIENT_KWARGS if name not in accepted]
        if missing:
            problems.append(
                "the installed kumiho client does not accept "
                + ", ".join(missing)
                + "; without those switches a hosted request can pick up the operator's "
                "~/.kumiho credentials or write the local discovery cache file"
            )

    _construction_problems_cache = list(problems)
    return list(problems)


def _construct_client(*, target: str, token: Optional[str], metadata) -> Any:
    """Build a kumiho client without touching the local credential cache.

    ``kumiho.connect`` is the documented entry point, but it has no
    ``skip_auth_token_load`` switch — and without that switch an operator's
    stray ``~/.kumiho`` token can stand in for a caller who presented none. So
    the underlying class is used directly, and a signature that cannot take the
    hosting switches is a hard error rather than a filtered-down call: see
    :func:`client_construction_problems`, which the pool consults before it gets
    here and which ``app._dependency_problems`` consults at startup.
    """
    try:
        from kumiho.client import _Client
    except Exception as exc:  # noqa: BLE001 - no safe fallback exists
        raise ClientContractError(
            f"kumiho.client._Client is not importable ({exc}); refusing to fall back to "
            "kumiho.connect(), which cannot be told to ignore ~/.kumiho"
        ) from exc

    kwargs: Dict[str, Any] = {
        "target": target,
        "auth_token": token,
        "default_metadata": metadata,
        "use_discovery": False,
        "enable_auto_login": False,
        "skip_auth_token_load": True,
    }
    try:
        accepted = set(inspect.signature(_Client.__init__).parameters)
    except (TypeError, ValueError):  # pragma: no cover - exotic classes
        accepted = set(kwargs)

    missing = [name for name in REQUIRED_CLIENT_KWARGS if name not in accepted]
    if missing:
        raise ClientContractError(
            "the installed kumiho client does not accept " + ", ".join(missing) + "; "
            "a hosted request could pick up the operator's ~/.kumiho credentials"
        )

    return _Client(**{k: v for k, v in kwargs.items() if k in accepted})
