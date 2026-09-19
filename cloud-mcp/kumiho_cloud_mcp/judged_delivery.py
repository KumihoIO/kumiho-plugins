"""Judged delivery, decided per tenant rather than per process.

``kumiho-memory``'s context optimization widens an engage recall and has the
Kumiho server's ``Evaluate`` RPC decide which of the candidates are delivered.
``Evaluate`` is a paid Kumiho Cloud capability, so on a shared server the answer
to "is this on?" belongs to the *caller*, not to the deployment: an unentitled
tenant's first call in each back-off window otherwise pays for a widened recall
and an RPC that comes back ``PERMISSION_DENIED``.

The switch cannot be an environment variable here. This process serves many
tenants at once and mutating ``os.environ`` per request is a cross-tenant leak
by construction (the same reason the tenant itself travels in a contextvar).
``kumiho_memory.context_optimization.judged_delivery`` is that contextvar's
public setter, and :func:`judged_delivery_for` binds it around one request from
the caller's verified tier claim.

The hosted dependency floor includes this override, but
``KUMIHO_CLOUD_MCP_JUDGED_DELIVERY`` remains off by default. While it is off no
override is set, so ``kumiho-memory`` reads its own environment. Keep both this
switch and ``KUMIHO_MEMORY_CONTEXT_OPT_ENABLED`` off during an image-only rollout.
The compatibility loader still tolerates a missing module or symbol for explicit
development compatibility modes: requests run unwrapped, with one startup
warning if the operator had asked for the feature.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Iterator, Optional

logger = logging.getLogger("kumiho.cloud_mcp.judged_delivery")

#: Operator switch. Off unless set, so deploying this changes nothing.
JUDGED_DELIVERY_ENV = "KUMIHO_CLOUD_MCP_JUDGED_DELIVERY"

#: The control plane's tier claim, on both token formats (``mcp_access`` and
#: ``service_token``; see tests/contract/as_fixture.json). The lower-cased
#: ``memory_tier`` claim rides alongside it and is deliberately not read: the
#: entitlement the server enforces is keyed by these codes.
TIER_CLAIM = "tenant_tier"

#: Tiers whose tenants the Kumiho server lets call ``Evaluate``. Matched
#: case-insensitively but exactly — a tier the control plane has not minted yet
#: is unknown, and unknown is off.
ENTITLED_TIERS = frozenset({"STUDIO", "STUDIO_PRO", "ENTERPRISE"})

_TRUTHY = ("1", "true", "yes", "on")


def _load_override():
    """``kumiho_memory``'s per-request setter, or ``None`` on an older release.

    Resolved once: an installed package does not grow the symbol mid-process.
    """
    try:
        from kumiho_memory.context_optimization import judged_delivery
    except Exception:  # noqa: BLE001 - older release, or no kumiho_memory at all
        return None
    return judged_delivery if callable(judged_delivery) else None


_override_cm = _load_override()


def switch_enabled() -> bool:
    """Whether the operator has turned per-tenant judged delivery on."""
    return (os.environ.get(JUDGED_DELIVERY_ENV) or "").strip().lower() in _TRUTHY


def tier_of(principal: Any) -> str:
    """The caller's tier code, upper-cased; ``""`` when the token carries none."""
    claims = getattr(principal, "claims", None) or {}
    return str(claims.get(TIER_CLAIM) or "").strip().upper()


def is_entitled(principal: Any) -> bool:
    """Whether this caller's tier may spend an ``Evaluate`` request."""
    return tier_of(principal) in ENTITLED_TIERS


@contextlib.contextmanager
def judged_delivery_for(principal: Any) -> Iterator[Optional[bool]]:
    """Bind ``kumiho-memory``'s judged-delivery switch for one request.

    Yields the value bound, or ``None`` when nothing was bound — which is the
    whole of the behaviour when the operator switch is off or the installed
    ``kumiho-memory`` predates the override.
    """
    if _override_cm is None or not switch_enabled():
        yield None
        return
    enabled = is_entitled(principal)
    with _override_cm(enabled):
        yield enabled


def warn_if_unsupported() -> None:
    """One startup line when the switch asks for what this build cannot do."""
    if _override_cm is None and switch_enabled():
        logger.warning(
            "judged delivery requested but unavailable",
            extra={
                "env": JUDGED_DELIVERY_ENV,
                "reason": (
                    "the installed kumiho-memory has no "
                    "context_optimization.judged_delivery"
                ),
            },
        )


__all__ = [
    "ENTITLED_TIERS",
    "JUDGED_DELIVERY_ENV",
    "TIER_CLAIM",
    "is_entitled",
    "judged_delivery_for",
    "switch_enabled",
    "tier_of",
    "warn_if_unsupported",
]
