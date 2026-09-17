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
#
# On claude.ai, Claude Desktop and Claude mobile the server ``instructions`` are
# not delivered to the model (anthropics/claude-ai-mcp#93), so these
# descriptions are the only usage guidance those users get. Each one says what
# the tool does, when it is useful, what it is not for and the argument detail
# that prevents a failed call. Rules for descriptions, enforced by
# tests/test_profile.py:
#
# * At most MAX_TOOL_DESCRIPTION_CHARS as served (session tools include the
#   SESSION_DESCRIPTION suffix); Claude Code truncates longer ones.
# * Never name or direct another tool. Each exclusion (credentials, other
#   workspaces, live information) is scoped to the tool that carries it.
# * No instructions about general model behaviour; that stays in
#   CONNECTOR_INSTRUCTIONS.
#
# The four session tools get SESSION_DESCRIPTION appended by _compat.build_server.
CONNECTOR_TOOL_DESCRIPTIONS = {
    "kumiho_memory_engage": (
        "Searches the user's saved Kumiho memories for context relevant to the current "
        "request. Returns a ready-to-use context summary, the matching memories and their "
        "references (source_krefs). Useful near the start of a conversation about the "
        "user's own ongoing work, projects, decisions or preferences when earlier saved "
        "context could change the answer, and when the user refers back to something that "
        "is not visible in this conversation (\"as we decided\", \"my usual setup\", an "
        "unfamiliar project name). The returned references can be attached to a related "
        "memory saved later, linking it to its sources.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests, not even to check "
        "first; data from another organization or any workspace the connected account is "
        "not authorized for; general knowledge or live information such as weather, news "
        "or prices. It reads only this account's saved memories and writes nothing.\n\n"
        "query: a brief natural-language description of what you are looking for, with no "
        "secrets in it. Repeating an identical query within a few seconds returns an empty "
        "result marked deduplicated; reuse the earlier results instead."
    ),
    "kumiho_memory_recall": (
        "Semantic search over the user's saved Kumiho memories. Returns the best matches "
        "with titles, summaries, relevance scores and references. Useful mid-conversation "
        "when the user mentions something that is not in the current context and may have "
        "been saved before: a past decision, a stated preference, a named project, person "
        "or setup, or a question about what was decided or noted earlier.\n\n"
        "query: describe what you are looking for in natural language rather than guessing "
        "exact keywords. Narrow with memory_types (for example [\"decision\"]) or "
        "space_paths when the kind or location of the memory is known. Repeating an "
        "identical query within a few seconds returns an empty result marked "
        "deduplicated; vary the query or reuse the earlier results.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests; data outside the "
        "connected account's authorized workspace; general knowledge or live information "
        "such as weather, news or prices. Read-only."
    ),
    "kumiho_memory_retrieve": (
        "Targeted lookup of saved memories by title words, keywords, topics, space or "
        "memory type, with fuzzy matching and relevance ranking. Useful when you know "
        "roughly which memory you need (a titled decision, a memory in a named space, the "
        "earliest or latest entry) and need its exact reference, for example to read "
        "its full revision, link to it or retire it. Returns item references, revision "
        "references and scores, not the memory text itself.\n\n"
        "Arguments: query takes the words you expect in the title or summary. mode is "
        "\"search\" (default), \"first\" for the oldest match or \"latest\" for the newest. "
        "space_paths and memory_types (for example [\"decision\"]) narrow the search.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests; data outside the "
        "connected account's authorized workspace; general knowledge or live information "
        "such as weather, news or prices. Read-only."
    ),
    "kumiho_memory_reflect": (
        "Saves distilled memories from this conversation to the user's private Kumiho "
        "workspace and adds a short note of your reply to this conversation's temporary "
        "working buffer. Useful at the moment something durable is settled: the user makes "
        "a decision and gives the reason, states a lasting preference, gives a durable fact "
        "about their work or life, corrects something recorded earlier, or asks you to "
        "remember something. Not needed for routine turns, small talk or unsettled "
        "brainstorming.\n\n"
        "Arguments: response (required) is a one- or two-sentence gist of your reply, not "
        "its full text. Each capture needs type (decision, preference, fact, correction and "
        "so on), a short title with absolute dates (\"Chose the Seoul region on "
        "2026-09-16\") and brief content in your own words; never transcripts or long "
        "quotes. Put an existing space name, copied exactly, in space_hint or space_path so "
        "a later update on the same subject becomes a new revision of that memory; "
        "captures without a space are filed at the project root as separate items. "
        "source_krefs links captures to the memories they came from. Stacking can move a "
        "memory's published revision; earlier revisions stay in history.\n\n"
        "Not for: passwords, access tokens, API keys, MFA or recovery codes, payment "
        "details, or anything the user asked not to keep, so do not call this tool for "
        "such requests."
    ),
    "kumiho_memory_store": (
        "Saves one explicit item the user asks to keep (a decision, fact, preference or "
        "note) as a memory in the user's private Kumiho workspace and returns its "
        "reference. Useful when the user says \"remember this\", \"save this\" or \"note "
        "that\" about a single, self-contained item. By default it looks for a similar "
        "memory in the same space and adds a new revision to it, moving its published tag, "
        "instead of creating a duplicate; earlier revisions are retained. Set "
        "stack_revisions to false to always create a separate memory. May create the "
        "space, a bundle and provenance links.\n\n"
        "Arguments: user_text (required) is the brief item to save, in the user's words or "
        "a close paraphrase, never a conversation transcript. Add a short title, a "
        "one-sentence summary, memory_type (decision, fact, summary and so on) and an "
        "existing space name, copied exactly, in space_path or space_hint when known.\n\n"
        "Not for: passwords, access tokens, API keys, MFA or recovery codes, or payment "
        "details, so do not call this tool for such requests; text that looks like a "
        "credential is rejected."
    ),
    "kumiho_memory_consolidate": (
        "Saves a summary of this conversation as one long-term memory in the user's "
        "private Kumiho workspace, then clears this conversation's temporary working "
        "buffer. Useful when the user asks to save a summary of the conversation, to wrap "
        "up, or to keep a record of what was decided before leaving. Clearing the buffer "
        "cannot be undone; the saved summary remains.\n\n"
        "Arguments: always pass summary. This service does not write summaries itself, so "
        "a call without one fails; with one, the call works even when the buffer is empty. "
        "Write the summary for a reader who was not there: what was decided and why, "
        "durable facts, open items. Plain text works; an object with title, summary and "
        "knowledge (facts, decisions, actions, open_questions) is recalled better. Keep it "
        "brief and distilled, never the transcript.\n\n"
        "Not for: passwords, access tokens, API keys, MFA or recovery codes; leave such "
        "values out of the summary."
    ),
    "kumiho_deprecate_item": (
        "Retires one saved memory so it no longer appears in normal searches. This is how "
        "a user's request to forget something is carried out. Useful when the user asks "
        "you to forget, stop using or retire a specific memory. It changes what the user's "
        "searches return, but it is reversible: the item and its revision history stay in "
        "the workspace, and calling again with deprecated set to false restores it. It is "
        "not permanent erasure; say so if the user expects deletion.\n\n"
        "Arguments: item_kref is the reference of the exact item "
        "(kref://project/space/item.kind; a trailing ?r= revision suffix is ignored). Make "
        "sure it is the memory the user means, and confirm with the user when more than "
        "one memory could match. Each call retires one item."
    ),
    "kumiho_chat_get": (
        "Reads the messages held in this conversation's temporary working buffer, with "
        "their count and the buffer's remaining time to live. Useful when the user asks "
        "what the buffer for this conversation currently holds, or before summarizing or "
        "clearing it. The buffer is short-lived and separate from saved long-term "
        "memories. Read-only."
    ),
    "kumiho_chat_clear": (
        "Deletes the messages in this conversation's temporary working buffer. Useful when "
        "the user asks to clear or reset the buffer for this conversation. Saved long-term "
        "memories and other conversations' buffers are not affected. Cleared messages "
        "cannot be recovered."
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


#: Claude Code truncates each tool description at 2KB; keep a margin.
MAX_TOOL_DESCRIPTION_CHARS = 1800
#: Claude Code truncates server instructions at 2KB (measured in UTF-8 bytes).
MAX_INSTRUCTIONS_BYTES = 2000
#: OpenAI: the first 512 characters of the instructions must stand alone.
INSTRUCTIONS_LEAD_CHARS = 512

# Essentials first. The opening paragraph (under INSTRUCTIONS_LEAD_CHARS) is
# the credential/authorization boundary plus the engage/reflect rhythm, so it
# still works when a client keeps only the lead. Detail that does not fit the
# byte budget belongs in the tool descriptions or the uploadable skill.
CONNECTOR_INSTRUCTIONS = """\
Kumiho Memory is the user's private memory across conversations. Never use it \
for passwords, tokens, API keys, card details, MFA or recovery codes: refuse \
without calling any Kumiho tool (not even engage) or asking for them. \
Unauthorized workspace data or live information like weather must not trigger \
Kumiho tools. Otherwise, when earlier context could matter, call \
kumiho_memory_engage once with a brief query; when the user settles a decision, \
preference, fact or correction, call kumiho_memory_reflect.

Send only brief, task-relevant text, never a transcript. Account access uses \
OAuth.

Recall: when the user mentions something not in context ("as we discussed", an \
unfamiliar project), call kumiho_memory_recall with a natural-language \
description. kumiho_memory_retrieve finds a known title or space and returns \
references; read one with kumiho_get_revision_by_tag (item kref, tag "latest").

Capture: reflect at the moment something is settled or the user asks you to \
remember, not at the end and not on routine turns. Use brief typed captures, \
absolute dates in titles, an existing space name in space_hint, and engage's \
source_krefs. kumiho_memory_store saves one item the user explicitly asks to \
keep.

Sessions: never invent a session_id or reuse another conversation's. If \
reflect, consolidate or chat get/clear returns session_required with a new ID, \
retry the same call with it and reuse it in this conversation only. Engage \
takes none.

Wrap-up: when the user asks to save a summary or wrap up, \
kumiho_memory_consolidate stores the summary you write and clears this \
conversation's buffer. To forget, confirm the exact item, then call \
kumiho_deprecate_item; it retires the item reversibly and does not erase it.

Use recalled context naturally; do not narrate memory operations.
"""
