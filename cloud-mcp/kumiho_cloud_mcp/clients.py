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

import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import anyio
import anyio.to_thread
import httpx

from .auth import Principal
from .settings import Settings

logger = logging.getLogger("kumiho.cloud_mcp.clients")


class RoutingError(RuntimeError):
    """Discovery could not resolve a regional server for the tenant."""


class DiscoveryRouter:
    """Resolves ``tenant -> regional gRPC target`` via the control plane."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: Dict[str, Tuple[float, Optional[str]]] = {}
        self._lock = anyio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def attach(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, principal: Principal) -> Optional[str]:
        cached = self._cache.get(principal.tenant_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]

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
        logger.info(
            "resolved tenant routing",
            extra={"tenant_id": principal.tenant_id, "target": target},
        )
        return target


class ClientPool:
    """Bounded LRU of gRPC clients keyed by ``(tenant_id, token_id)``.

    ``token_id`` is in the key so a rotated credential never reuses the channel
    built for the previous one, and the entry expires no later than the token
    it was built from.
    """

    def __init__(self, settings: Settings, router: DiscoveryRouter) -> None:
        self.settings = settings
        self.router = router
        self._entries: "OrderedDict[Tuple[str, str], Tuple[float, Any]]" = OrderedDict()
        self._lock = anyio.Lock()

    def _key(self, principal: Principal) -> Tuple[str, str]:
        return (principal.tenant_id, principal.token_id or principal.user_id)

    async def get(self, principal: Principal) -> Any:
        key = self._key(principal)
        now = time.monotonic()

        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self._entries.move_to_end(key)
                return entry[1]
            if entry:
                self._entries.pop(key, None)

        client = await self._build(principal)

        # TTL never exceeds the token lifetime; 15 min otherwise.
        ttl = 15 * 60.0
        if principal.expires_at:
            remaining = principal.expires_at - time.time()
            ttl = max(0.0, min(ttl, remaining))
        if ttl <= 0:
            return client  # token about to expire: use once, do not pool

        async with self._lock:
            self._entries[key] = (time.monotonic() + ttl, client)
            self._entries.move_to_end(key)
            while len(self._entries) > self.settings.client_cache_max:
                self._entries.popitem(last=False)
        return client

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

    async def aclose(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for _, client in entries:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await anyio.to_thread.run_sync(close)
                except Exception:  # noqa: BLE001 - best effort on shutdown
                    logger.debug("client close failed", exc_info=True)


def _construct_client(*, target: str, token: Optional[str], metadata) -> Any:
    """Build a kumiho client without touching the local credential cache.

    ``kumiho.connect`` is the documented entry point, but it has no
    ``skip_auth_token_load`` switch — and without that switch an operator's
    stray ``~/.kumiho`` token can stand in for a caller who presented none.
    Prefer the underlying class when it exposes the switch, and fall back
    through ``connect`` on older SDKs (which is why the parameter list is
    filtered against the real signature rather than assumed).
    """
    import inspect

    client_cls = None
    try:
        from kumiho.client import _Client as client_cls  # type: ignore  # noqa: F811
    except ImportError:  # pragma: no cover - SDK layout changed
        client_cls = None

    if client_cls is not None:
        kwargs: Dict[str, Any] = {
            "target": target,
            "auth_token": token,
            "default_metadata": metadata,
            "use_discovery": False,
            "enable_auto_login": False,
            "skip_auth_token_load": True,
        }
        try:
            accepted = set(inspect.signature(client_cls.__init__).parameters)
        except (TypeError, ValueError):  # pragma: no cover - exotic classes
            accepted = set(kwargs)
        dropped = [name for name in kwargs if name not in accepted]
        if dropped:
            logger.warning(
                "installed kumiho client does not accept some hosting arguments",
                extra={"dropped": dropped},
            )
        return client_cls(**{k: v for k, v in kwargs.items() if k in accepted})

    import kumiho  # type: ignore  # pragma: no cover

    return kumiho.connect(  # pragma: no cover
        endpoint=target,
        token=token,
        enable_auto_login=False,
        use_discovery=False,
        default_metadata=list(metadata),
    )
