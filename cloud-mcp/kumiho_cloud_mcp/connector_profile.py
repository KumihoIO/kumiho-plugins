"""Reviewed hosted tool allowlist and conversation instructions.

SDK handlers remain authoritative for implementation. The hosted wrapper always
intersects their catalog with this list, including the native profile path, so
SDK upgrades cannot silently expose new tools. Hosted session instructions
intentionally differ from stdio's active-session fallback.
"""

from __future__ import annotations

from typing import Dict, Tuple

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}


def _ann(title: str, base: Dict[str, object], **over: object) -> Dict[str, object]:
    out: Dict[str, object] = {"title": title}
    out.update(base)
    out.update(over)
    return out


#: name -> MCP ``ToolAnnotations`` payload, in the SDK's listing order.
CONNECTOR_TOOL_ANNOTATIONS: Dict[str, Dict[str, object]] = {
    "kumiho_list_projects": _ann("List projects", _READ),
    "kumiho_get_spaces": _ann("List spaces", _READ),
    "kumiho_get_item": _ann("Get an item", _READ),
    "kumiho_search_items": _ann("Search items", _READ),
    "kumiho_memory_store": _ann("Store a memory", _WRITE, destructiveHint=True),
    "kumiho_memory_retrieve": _ann("Retrieve memories", _READ),
    "kumiho_get_revision_by_tag": _ann("Get a revision by tag", _READ),
    "kumiho_get_provenance_summary": _ann("Summarize provenance", _READ),
    "kumiho_create_space": _ann("Create a space", _WRITE),
    "kumiho_deprecate_item": _ann("Forget a memory", _DESTRUCTIVE),
    "kumiho_chat_get": _ann("Read the chat buffer", _READ),
    "kumiho_chat_clear": _ann("Clear the chat buffer", _DESTRUCTIVE),
    "kumiho_memory_consolidate": _ann("Consolidate the session into long-term memory", _WRITE, destructiveHint=True),
    "kumiho_memory_recall": _ann("Recall memories", _READ),
    "kumiho_memory_engage": _ann("Engage memory before responding", _READ),
    "kumiho_memory_reflect": _ann("Reflect and capture memories", _WRITE, destructiveHint=True),
    "kumiho_memory_space_profile": _ann("Profile memory spaces", _WRITE),
    "kumiho_memory_decompose": _ann("Decompose a memory into the typed graph", _WRITE, destructiveHint=True),
}

# Hosted descriptions disclose optional destructive modes as well as defaults.
CONNECTOR_TOOL_DESCRIPTIONS = {
    "kumiho_memory_engage": (
        "Retrieve relevant prior memories and their references when the user wants "
        "help with an authorized private-workspace memory task. Use a brief, non-sensitive "
        "query. Do not call this or any other memory tool to handle requests to store "
        "passwords, tokens or MFA recovery codes, access an unauthorized workspace, "
        "or answer unrelated live-information questions. It does not store a conversation buffer."
    ),
    "kumiho_memory_store": (
        "Store a user-authorized memory in the private workspace. Provide only the brief "
        "relevant text, never credentials or a full conversation transcript. Creates a "
        "revision and may move an existing memory's published tag to it when stacking; "
        "earlier revisions are retained. May create spaces, bundles and provenance links."
    ),
    "kumiho_memory_decompose": (
        "Add typed entities, facts and relationships to a stored workspace memory. "
        "Supersession can replace an older fact's accepted status and mark dependent "
        "evidence stale; prior revisions remain in history. Requires ontology support."
    ),
}

#: Exact tool names exposed by the ``connector`` profile, in listing order.
CONNECTOR_TOOLS: Tuple[str, ...] = tuple(CONNECTOR_TOOL_ANNOTATIONS)

#: How many tools a correctly-installed connector exposes. The startup smoke
#: check in :mod:`kumiho_cloud_mcp.app` logs an error when the live count
#: differs — normally because ``kumiho-memory`` is pinned too old.
#:
#: ``kumiho_memory_dream_state`` is deliberately absent in v1: hosted tenants
#: run the keyless core, and Dream State wants an LLM budget nobody is metering
#: yet (plan §1 decision 10, §5).
CONNECTOR_TOOL_COUNT = 18

assert len(CONNECTOR_TOOLS) == CONNECTOR_TOOL_COUNT, (
    f"connector profile must expose exactly {CONNECTOR_TOOL_COUNT} tools"
)


CONNECTOR_INSTRUCTIONS = """\
Kumiho Memory gives you a persistent, graph-native memory that survives across \
conversations. There is no local hook or skill here — the protocol below is \
yours to run.

Only send the brief, task-relevant information the user intends to store or \
retrieve. Never request passwords, access tokens, payment credentials or an \
entire conversation transcript. All account authentication uses OAuth.

Before any tool call, check whether the request is an authorized memory task. \
If it asks to store credentials (passwords, tokens, MFA or recovery codes), \
refuse without calling any memory tool, including engage, and do not ask for \
the secret values. Do not transmit a secret even as a search query. Requests \
for another organization's unauthorized data or unrelated live information \
(such as current weather) must not trigger any Kumiho tool. These rules take \
priority over the engage/recall/reflect protocol below.

At the start of a conversation that might have history, call \
`kumiho_memory_engage` once with a brief relevant, non-sensitive `query`. It \
returns the most relevant prior memories and the krefs they came from; keep \
those krefs for `kumiho_memory_reflect`.

Never invent a `session_id` or reuse one from another conversation. If the \
host supplies no conversation ID, the first session-scoped call without one \
returns `session_required` and a new ID without accessing memory. Retry the \
same call with that ID, then pass it on every reflect, consolidate, chat-get \
or chat-clear call in this conversation. Session results echo the ID. \
Engage is read-only and needs no buffer ID. Never infer a conversation ID \
from the user's identity or an active-session pointer.

During the conversation, call `kumiho_memory_recall` when the user refers to \
something you do not have in context ("like we discussed", "the usual setup", \
a project name you have not seen). Recall is semantic — describe what you are \
looking for in natural language rather than guessing at keywords.

When something is settled — a decision with its rationale, a stable preference, \
a durable fact, or a correction to something you had wrong — call \
`kumiho_memory_reflect` with typed captures. Reflect at the moment the thing is \
settled, not at the end. Do not store transient chatter, secrets, credentials, \
or anything the user asked you not to keep.

`kumiho_memory_consolidate` folds the session into long-term memory and \
`kumiho_memory_decompose` splits an overloaded memory into the typed graph; \
`kumiho_deprecate_item` is how a user forgets something — treat it as \
destructive and confirm first.

Never announce memory operations. Use what you recall; do not narrate that you \
recalled it.
"""
