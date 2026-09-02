"""The Kumiho hosted MCP resource server.

``uvicorn kumiho_cloud_mcp.app:app --host 0.0.0.0 --port 8080``

Shape of a request to ``/mcp``:

1. :class:`~kumiho_cloud_mcp.auth.Authenticator` turns the ``Authorization`` or
   ``x-api-key`` header into a :class:`~kumiho_cloud_mcp.auth.Principal`, or
   raises and we answer 401 with the RFC 9728 challenge Claude follows to find
   the authorization server.
2. A ``RequestContext`` and a tenant-scoped gRPC client are built (the client
   pooled, keyed by tenant + token id, never outliving the token).
3. ``with kumiho.use_client(client), request_context(ctx), redis_token_bridge(...)``
   wraps the streamable-HTTP session manager for the whole request, so every
   tool handler — which runs in a worker thread via ``asyncio.to_thread`` and
   therefore inherits the contextvars — sees exactly one tenant.

Nothing tenant-scoped is stored in a module global or in ``os.environ``.
"""

from __future__ import annotations

import contextvars
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Any, Dict, Optional

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from . import __version__
from ._compat import (
    HAVE_UPSTREAM_REQUEST_CONTEXT,
    PROVIDER_NAMES,
    RequestContext,
    build_server,
    redis_token_bridge,
    request_context,
)
from .auth import Authenticator, AuthError, Principal, challenge_header
from .clients import ClientPool, DiscoveryRouter, RoutingError
from .connector_profile import CONNECTOR_TOOL_COUNT, CONNECTOR_TOOLS
from .logging_setup import configure_logging
from .middleware import BodyLimitMiddleware, SecurityHeadersMiddleware, TimeoutMiddleware
from .settings import DEV_TENANT_HEADER, Settings, dev_identity, load_settings

logger = logging.getLogger("kumiho.cloud_mcp")

_sse_session_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "kumiho_cloud_mcp_sse_session", default=None
)


class _ASGIPassthrough(Response):
    """A Starlette ``Response`` that is really a raw ASGI app.

    ``Mount("/mcp", ...)`` only matches ``/mcp/``-prefixed paths and lets the
    router 307 the bare ``/mcp`` — fatal for a POST. Returning this from a
    normal ``Route`` gives the streamable-HTTP manager the untouched
    ``scope``/``receive``/``send`` on the exact path clients actually call.
    """

    def __init__(self, asgi_app) -> None:  # noqa: D107
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send) -> None:  # noqa: D102
        await self.asgi_app(scope, receive, send)


class _RecordingWriters(dict):
    """``SseServerTransport._read_stream_writers`` that reports new session ids.

    Registration happens synchronously inside the connecting task, so the
    contextvar set here is the session id of *this* connection — no snapshot
    diffing, no race with a concurrent connect.
    """

    def __setitem__(self, key, value) -> None:  # noqa: D105
        _sse_session_var.set(getattr(key, "hex", str(key)))
        super().__setitem__(key, value)


def _auth_response(settings: Settings, exc: AuthError) -> JSONResponse:
    """401/403 carrying the exact challenge Claude's connector client parses."""
    if exc.status == 403:
        error: Optional[str] = "insufficient_scope"
    else:
        error = exc.code if exc.token_present else None
    return JSONResponse(
        {
            "error": error or "unauthorized",
            "error_description": exc.description,
        },
        status_code=exc.status,
        headers={
            "WWW-Authenticate": challenge_header(settings, error=error),
            "Cache-Control": "no-store",
        },
    )


def _context_for(principal: Principal, request: Request) -> RequestContext:
    return RequestContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        auth_token=principal.token,
        context="claude",
        session_id=request.headers.get("x-kumiho-session-id") or None,
        client_id=principal.client_id,
        scopes=list(principal.scopes),
        tenant_slug=principal.tenant_slug,
        region_code=principal.region_code,
        token_id=principal.token_id,
    )


def _installed_version(module_name: str) -> Optional[str]:
    """``__version__`` of an already-imported dependency, or ``None``."""
    import sys

    module = sys.modules.get(module_name)
    version = getattr(module, "__version__", None) if module is not None else None
    return version if isinstance(version, str) else None


def _tenant_manager_stats() -> Dict[str, Any]:
    """How many tenants hold a live ``kumiho_memory`` manager right now.

    Read through ``sys.modules`` rather than importing: on a request-free
    process (a health probe right after boot) ``kumiho_memory.mcp_tools`` may
    genuinely not be loaded yet, and a health endpoint must not be the thing
    that loads it.
    """
    import sys

    module = sys.modules.get("kumiho_memory.mcp_tools")
    if module is None:
        return {"loaded": False, "count": 0}
    cache = getattr(module, "_tenant_managers", None)
    singleton = getattr(module, "_manager", None)
    try:
        count = len(cache) if cache is not None else 0
    except Exception:  # noqa: BLE001 - introspection must never break /healthz
        count = -1
    return {
        "loaded": True,
        "count": count,
        "max": getattr(cache, "max_entries", None),
        "idle_ttl_seconds": getattr(cache, "idle_ttl", None),
        # Must stay False in hosted mode: a process singleton means some path
        # built a manager with no request context, i.e. from ambient env.
        "process_singleton": singleton is not None,
    }


def _pool_size(pool: Any) -> int:
    """Cached gRPC clients. Best-effort: ``ClientPool`` is WP-C/E2's file."""
    entries = getattr(pool, "_entries", None)
    try:
        return len(entries) if entries is not None else -1
    except Exception:  # noqa: BLE001
        return -1


#: Minimum sibling releases the connector contract (plan §2.1-§2.3) needs.
MIN_KUMIHO_VERSION = (0, 13, 0)
MIN_KUMIHO_MEMORY_VERSION = (1, 4, 0)


class StartupContractError(RuntimeError):
    """A dependency too old to serve the connector profile safely."""


def _version_tuple(raw: object) -> Optional[tuple]:
    """``"1.4.0rc1"`` -> ``(1, 4, 0)``; ``None`` when unparseable."""
    if not isinstance(raw, str):
        return None
    parts = []
    for chunk in raw.split(".")[:3]:
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _dependency_problems() -> list:
    """Every way the installed siblings fall short of the contract."""
    import inspect as _inspect

    problems: list = []

    try:
        import kumiho  # noqa: F401
        import kumiho.mcp_server as _ms
    except Exception as exc:  # noqa: BLE001 - a missing SDK is fatal either way
        return [f"kumiho is not importable: {exc}"]

    found = _version_tuple(getattr(kumiho, "__version__", None))
    if found is None:
        problems.append("kumiho reports no parseable __version__")
    elif found < MIN_KUMIHO_VERSION:
        problems.append(
            "kumiho %s is older than the required %s"
            % (kumiho.__version__, ".".join(map(str, MIN_KUMIHO_VERSION)))
        )

    try:
        params = _inspect.signature(_ms.create_mcp_server).parameters
    except (TypeError, ValueError):  # pragma: no cover - C callables
        params = {}
    if "profile" not in params:
        problems.append(
            "kumiho.mcp_server.create_mcp_server has no profile= parameter, so the "
            "connector profile would be enforced by a local shim instead of the SDK"
        )

    try:
        import kumiho_memory
    except Exception as exc:  # noqa: BLE001
        problems.append(f"kumiho_memory is not importable: {exc}")
    else:
        found = _version_tuple(getattr(kumiho_memory, "__version__", None))
        if found is None:
            problems.append("kumiho_memory reports no parseable __version__")
        elif found < MIN_KUMIHO_MEMORY_VERSION:
            problems.append(
                "kumiho-memory %s is older than the required %s"
                % (kumiho_memory.__version__, ".".join(map(str, MIN_KUMIHO_MEMORY_VERSION)))
            )

    return problems


def _enforce_dependency_contract(settings: Settings) -> None:
    """Refuse to start a production process on a shimmed dependency set.

    The shim in :mod:`kumiho_cloud_mcp._compat` exists so this service could be
    developed before WP-A and WP-B landed, and it degrades gracefully — which
    is exactly the danger now. A deployment that picked up an old ``kumiho``
    would come up healthy, serve a *different* tool set than the one the Claude
    directory listing was reviewed against, and lose the SDK's tenant-keyed
    caches and hosted guards while doing it. That must be a crash, not a log
    line, so the deploy fails instead of the tenants.

    Dev mode is exempt (it is how the shim gets exercised at all), and
    ``KUMIHO_MCP_ALLOW_SHIM=1`` is the documented dev-only override.
    """
    problems = _dependency_problems()
    if not problems:
        return
    detail = "; ".join(problems)
    if settings.dev or settings.allow_shim:
        logger.warning(
            "dependency contract not met; continuing because this is a dev run",
            extra={"problems": problems, "allow_shim": settings.allow_shim, "dev": settings.dev},
        )
        return
    logger.error("dependency contract not met", extra={"problems": problems})
    raise StartupContractError(
        f"kumiho-cloud-mcp cannot serve the connector profile: {detail}. "
        f"Install kumiho>={'.'.join(map(str, MIN_KUMIHO_VERSION))} and "
        f"kumiho-memory>={'.'.join(map(str, MIN_KUMIHO_MEMORY_VERSION))}, or set "
        "KUMIHO_MCP_ALLOW_SHIM=1 (development only — the shim cannot enforce the "
        "reviewed tool profile)."
    )


def create_app(settings: Optional[Settings] = None, *, server_factory=None) -> Starlette:
    """Build the ASGI application. Importable as ``kumiho_cloud_mcp.app:app``."""

    settings = settings or load_settings()
    configure_logging(settings.log_level)

    if settings.hosted:
        # The one env var hosted mode *does* own. Several SDK and
        # kumiho_memory guards read the process flag between requests (module
        # import, background sweeps), not only inside a request context, so it
        # must be set before anything else imports them.
        os.environ["KUMIHO_MCP_HOSTED"] = "1"
        # Decision Memory assumes a local git checkout. There is no repo on a
        # hosted box, and the kumiho_code_* tools it enables are not in the
        # connector profile — a stray value would only mislead.
        if os.environ.pop("KUMIHO_MEMORY_DECISIONS", None) is not None:
            logger.warning("ignoring KUMIHO_MEMORY_DECISIONS: not supported in hosted mode")
    if settings.dev:
        os.environ.setdefault("KUMIHO_LOCAL_SERVER_ENDPOINT", settings.local_server_endpoint)
        # kumiho-memory >= 1.4.0 arms its direct-Redis escape hatch only when
        # KUMIHO_HOSTED_LOCAL_REDIS=1 *and* KUMIHO_MCP_HOSTED=1 (set just
        # above), and then reads KUMIHO_LOCAL_REDIS_URL first, UPSTASH_REDIS_URL
        # second. Set the documented name explicitly rather than leaning on the
        # UPSTASH_* fallback: those are the ambient single-tenant credentials
        # everywhere else, and dev mode should not depend on them.
        os.environ.setdefault("KUMIHO_HOSTED_LOCAL_REDIS", "1")
        os.environ.setdefault("KUMIHO_LOCAL_REDIS_URL", settings.local_redis_url)
        os.environ.setdefault("KUMIHO_UPSTASH_REDIS_URL", settings.local_redis_url)
        os.environ.setdefault("UPSTASH_REDIS_URL", settings.local_redis_url)

    _enforce_dependency_contract(settings)

    authenticator = Authenticator(settings)
    router = DiscoveryRouter(settings)
    pool = ClientPool(settings, router)

    mcp_server = (server_factory or build_server)()
    profile_source = getattr(mcp_server, "__kumiho_profile_source__", "unknown")

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,
        json_response=settings.json_response,
        stateless=True,
    )

    sse_sessions: Dict[str, str] = {}

    async def _smoke_check_tools() -> list:
        """Ask the server what it will actually expose, and complain if wrong.

        A short ``tools/list`` at startup is the only way to catch a mispinned
        ``kumiho-memory`` before a user does: the profile names 18 tools, and a
        dependency that predates ``kumiho_memory_space_profile`` /
        ``kumiho_memory_decompose`` silently exposes 16.
        """
        import mcp.types as types

        handler = mcp_server.request_handlers.get(types.ListToolsRequest)
        if handler is None:  # pragma: no cover - a server with no tools
            logger.error("mcp server registered no tools/list handler")
            return []
        try:
            result = await handler(types.ListToolsRequest(method="tools/list"))
        except Exception:  # noqa: BLE001 - never let the check stop startup
            logger.exception("tool smoke check failed")
            return []
        names = [tool.name for tool in getattr(result.root, "tools", [])]
        if len(names) != CONNECTOR_TOOL_COUNT:
            logger.error(
                "connector exposes the wrong number of tools",
                extra={
                    "expected": CONNECTOR_TOOL_COUNT,
                    "actual": len(names),
                    "missing": sorted(set(CONNECTOR_TOOLS) - set(names)),
                    "unexpected": sorted(set(names) - set(CONNECTOR_TOOLS)),
                    "hint": "pin kumiho-memory>=1.3.0 and kumiho>=0.13.0",
                },
            )
        return names

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=settings.http_timeout_seconds,
                    headers={"user-agent": f"kumiho-cloud-mcp/{__version__}"},
                )
            )
            authenticator.attach(http)
            router.attach(http)
            await stack.enter_async_context(session_manager.run())
            exposed = await _smoke_check_tools()
            app.state.exposed_tools = exposed
            logger.info(
                "kumiho-cloud-mcp started",
                extra={
                    "version": __version__,
                    "public_url": settings.public_url,
                    "issuer": settings.issuer,
                    "dev_mode": settings.dev_mode,
                    "profile_source": profile_source,
                    "upstream_request_context": HAVE_UPSTREAM_REQUEST_CONTEXT,
                    "request_context_providers": PROVIDER_NAMES,
                    "hosted": settings.hosted,
                    "tool_count": len(exposed),
                    "expected_tool_count": CONNECTOR_TOOL_COUNT,
                },
            )
            try:
                yield
            finally:
                await pool.aclose()

    # ---- plain routes --------------------------------------------------

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "kumiho-cloud-mcp",
                "version": __version__,
                "mcp_endpoint": settings.public_url,
                "dev_mode": settings.dev_mode,
                "profile_source": profile_source,
                "tools": len(getattr(_request.app.state, "exposed_tools", []) or []),
                "expected_tools": CONNECTOR_TOOL_COUNT,
                # How many tenants currently hold a memory manager in this
                # process. The number is the load-bearing evidence that hosted
                # mode is per-tenant and not a singleton: it must track the
                # number of distinct tenants seen, never stick at 1.
                "tenant_managers": _tenant_manager_stats(),
                "clients": _pool_size(pool),
                "sdk": {
                    "kumiho": _installed_version("kumiho"),
                    "kumiho_memory": _installed_version("kumiho_memory"),
                    "upstream_request_context": HAVE_UPSTREAM_REQUEST_CONTEXT,
                },
            }
        )

    async def index(_request: Request) -> HTMLResponse:
        return HTMLResponse(
            f"""<!doctype html><meta charset="utf-8">
<title>Kumiho Memory - MCP endpoint</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:44rem;margin:4rem auto;padding:0 1.5rem}}
code{{background:#f3f3f3;padding:.1em .35em;border-radius:.25em}}</style>
<h1>Kumiho Memory &mdash; MCP endpoint</h1>
<p>This is an MCP resource server, not a website. Point an MCP client at
<code>{settings.public_url}</code>.</p>
<p>In Claude Code:<br><code>claude mcp add --transport http kumiho-memory {settings.public_url}</code></p>
<p><a href="{settings.resource_documentation}">Documentation</a> &middot;
<a href="{settings.prm_url}">Protected resource metadata</a></p>
"""
        )

    async def protected_resource_metadata(_request: Request) -> JSONResponse:
        # RFC 9728. ``resource`` must equal the MCP URL exactly as the user
        # entered it, or Claude refuses the connection.
        return JSONResponse(
            settings.protected_resource_metadata(),
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # ---- the MCP endpoint ----------------------------------------------

    def _apply_dev_tenant(principal: Principal, headers) -> Principal:
        """Let ``x-kumiho-dev-tenant`` pick a fake tenant — dev mode only.

        Guarded on ``settings.dev``, i.e. the mode that has already turned
        authentication off; outside it the header is never read and a caller's
        tenant comes from their verified token alone. Two client sessions
        carrying different labels are two different tenants all the way down —
        manager cache entry, Redis key prefix, active-session pointer — which
        is what makes an isolation test over the real transport possible.
        """
        if not settings.dev:
            return principal
        label = headers.get(DEV_TENANT_HEADER)
        if not label or not label.strip():
            return principal
        tenant_id, tenant_slug, user_id, token_id = dev_identity(label)
        return replace(
            principal,
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            user_id=user_id,
            token_id=token_id,
        )

    async def _authorize(scope, receive, send) -> Optional[Principal]:
        request = Request(scope, receive)
        try:
            return _apply_dev_tenant(
                await authenticator.authenticate(request.headers), request.headers
            )
        except AuthError as exc:
            logger.info(
                "auth rejected",
                extra={
                    "path": scope.get("path"),
                    "reason": exc.code,
                    "token_present": exc.token_present,
                },
            )
            await _auth_response(settings, exc)(scope, receive, send)
            return None

    async def _client_or_503(principal: Principal, scope, receive, send):
        try:
            return await pool.get(principal)
        except RoutingError as exc:
            logger.warning(
                "routing failed",
                extra={"tenant_id": principal.tenant_id, "error": str(exc)[:200]},
            )
            await JSONResponse(
                {
                    "error": "service_unavailable",
                    "error_description": (
                        "Could not resolve a Kumiho server for this tenant. Try again shortly."
                    ),
                },
                status_code=503,
            )(scope, receive, send)
            return None

    async def mcp_asgi(scope, receive, send) -> None:
        if scope["type"] != "http":  # pragma: no cover - no websockets here
            return
        principal = await _authorize(scope, receive, send)
        if principal is None:
            return
        client = await _client_or_503(principal, scope, receive, send)
        if client is None:
            return

        ctx = _context_for(principal, Request(scope, receive))
        logger.info(
            "mcp request",
            extra={
                "http_method": scope.get("method"),
                "tenant_id": principal.tenant_id,
                "token_id": principal.token_id,
                "token_kind": principal.kind,
                "token_fp": principal.token_fp,
                "client_id": principal.client_id,
            },
        )

        import kumiho  # lazy: keeps import order flexible and tests stubbable

        with kumiho.use_client(client), request_context(ctx), redis_token_bridge(principal.token):
            await session_manager.handle_request(scope, receive, send)

    async def mcp_route(_request: Request) -> Response:
        return _ASGIPassthrough(mcp_asgi)

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(settings.mcp_path, mcp_route, methods=["GET", "POST", "DELETE"]),
        Route(
            settings.mcp_path + "/{rest:path}",
            mcp_route,
            methods=["GET", "POST", "DELETE"],
        ),
    ]

    # ---- optional SSE fallback (legacy MCP clients) ---------------------

    if settings.enable_sse:
        from mcp.server.sse import SseServerTransport

        sse_transport = SseServerTransport("/messages/")
        sse_transport._read_stream_writers = _RecordingWriters(  # type: ignore[assignment]
            sse_transport._read_stream_writers
        )

        async def sse_endpoint(scope, receive, send) -> None:
            principal = await _authorize(scope, receive, send)
            if principal is None:
                return
            client = await _client_or_503(principal, scope, receive, send)
            if client is None:
                return
            ctx = _context_for(principal, Request(scope, receive))

            import kumiho

            with kumiho.use_client(client), request_context(ctx), redis_token_bridge(
                principal.token
            ):
                async with sse_transport.connect_sse(scope, receive, send) as (read, write):
                    session_id = _sse_session_var.get()
                    if session_id:
                        sse_sessions[session_id] = principal.tenant_id
                    try:
                        await mcp_server.run(
                            read, write, mcp_server.create_initialization_options()
                        )
                    finally:
                        if session_id:
                            sse_sessions.pop(session_id, None)

        async def messages_endpoint(scope, receive, send) -> None:
            principal = await _authorize(scope, receive, send)
            if principal is None:
                return
            session_id = Request(scope, receive).query_params.get("session_id", "")
            bound = sse_sessions.get(session_id)
            if bound is not None and bound != principal.tenant_id:
                logger.warning(
                    "sse session/tenant mismatch",
                    extra={"tenant_id": principal.tenant_id},
                )
                await JSONResponse(
                    {
                        "error": "forbidden",
                        "error_description": "session belongs to another tenant",
                    },
                    status_code=403,
                )(scope, receive, send)
                return
            await sse_transport.handle_post_message(scope, receive, send)

        routes.extend(
            [
                Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
                Mount("/messages/", app=messages_endpoint),
            ]
        )

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(SecurityHeadersMiddleware),
            Middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes),
            Middleware(TimeoutMiddleware, seconds=settings.request_timeout_seconds),
        ],
    )
    app.state.settings = settings
    app.state.authenticator = authenticator
    app.state.discovery_router = router
    app.state.client_pool = pool
    app.state.mcp_server = mcp_server
    app.state.session_manager = session_manager
    app.state.profile_source = profile_source
    return app


_singleton: Optional[Starlette] = None


def __getattr__(name: str) -> Any:
    """Build the process-wide app lazily on first ``kumiho_cloud_mcp.app:app``.

    Lazy so that importing this module for :func:`create_app` (tests, tooling)
    does not construct an MCP server or read the ambient environment.
    """
    global _singleton
    if name == "app":
        if _singleton is None:
            _singleton = create_app()
        return _singleton
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "kumiho_cloud_mcp.app:app",
        host="0.0.0.0",
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    main()


# ``app`` is served by the module __getattr__ above (PEP 562), which is why it
# is not a module-level name.
__all__ = ["app", "create_app", "main"]  # noqa: F822
