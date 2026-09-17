"""Bind buffer identities before the SDK can consult a shared active pointer."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Optional

SESSION_TOOLS = frozenset({
    "kumiho_memory_reflect", "kumiho_memory_consolidate", "kumiho_chat_get", "kumiho_chat_clear",
})
SESSION_DESCRIPTION = (
    "Reuse the session_id returned for this conversation only. If the host supplies no "
    "conversation ID, the first call without session_id returns session_required and a new "
    "ID without reading or writing memory; retry with that ID. Never reuse an ID from "
    "another conversation or invent one."
)
# Each session tool's hosted description lives in
# connector_profile.CONNECTOR_TOOL_DESCRIPTIONS; build_server appends
# SESSION_DESCRIPTION to it.
_ISSUED_ID = re.compile(r"km1_[0-9a-f]{32}_[0-9a-f]{32,64}\Z")


class SessionError(ValueError):
    def __init__(self, code: str, description: str, session_id: Optional[str] = None):
        super().__init__(description)
        self.payload = {"error": code, "error_description": description}
        if session_id:
            self.payload["session_id"] = session_id


def resolve_buffer_session(ctx: Any, arguments: dict) -> str:
    """IDs are scoped to authenticated identity, stable across token rotation.

    The prefix is a namespace, not a secret or an authorization credential.
    The request identity is authenticated independently on every HTTP call.
    """
    scope = json.dumps([ctx.tenant_id, ctx.user_id, ctx.client_id, ctx.context], separators=(",", ":"))
    prefix = "km1_" + hashlib.sha256(scope.encode()).hexdigest()[:32] + "_"

    def scoped(raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 512:
            raise SessionError("invalid_session", "session_id must be a nonblank string of at most 512 characters")
        if raw.startswith("km1_"):
            if not _ISSUED_ID.fullmatch(raw) or not raw.startswith(prefix):
                raise SessionError("invalid_session", "session_id does not belong to this authenticated identity")
            return raw
        return prefix + hashlib.sha256(raw.encode()).hexdigest()

    bound = scoped(ctx.session_id) if ctx.session_id is not None else None
    explicit = scoped(arguments["session_id"]) if arguments.get("session_id") is not None else None
    if bound and explicit and bound != explicit:
        raise SessionError("invalid_session", "session_id conflicts with the host conversation")
    if bound or explicit:
        return bound or explicit
    raise SessionError("session_required", SESSION_DESCRIPTION, prefix + uuid.uuid4().hex)
