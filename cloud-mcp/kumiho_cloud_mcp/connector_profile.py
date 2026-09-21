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
# * Each common intent has one owner, expressed only by which intents a
#   description claims, never by naming a tool. Engage owns general recall
#   (start of a conversation, the user referring back, what was decided or
#   noted before) and claims nothing about recency. Retrieve owns newest and
#   oldest lookups ("what did I save recently?") besides exact references.
#   Reflect owns capture ("remember this", a settled decision), so a plain
#   "remember this" never routes to store. Store and recall claim none of these.
# * Engage promises no particular number of memories. An empty successful
#   search is valid, but a backend failure can also return zero. Explicit
#   repeated calls run retrieval again. Preserve failure signals and limit
#   conclusions to the retrieved candidates: a judge does not prove the whole graph irrelevant.
# * Retrieve's recency wording must match the SDK. kumiho 0.13.2 implements
#   mode "latest": newest first by the returned revision's own created_at (so
#   an updated memory moves forward), with a created_at list aligned with
#   revision_krefs. A query, keywords or topics keep only relevant matches,
#   still in date order; when nothing matches it falls back to the scoped
#   listing (score 0.0), also in date order. Search is fuzzy, so even an
#   unrelated query can return weak non-zero matches. "first" returns the single
#   oldest memory, or with a query the oldest relevant match. Both honour
#   space_paths and memory_types, and 0.13.2 checks space_paths one path
#   segment at a time, so a space is no longer matched by a same-prefixed name.
#   Mode text also folds case, spaces and hyphens, so "Most Recent" selects
#   latest instead of falling through to search. The default search mode is
#   relevance-ranked. tests/test_profile.py checks the served mode schema text
#   for this release.
# * A correction names the memory it corrects. kumiho-memory 1.5.1's reflect
#   takes a capture-level "revises" and passes it to the SDK store's item_kref
#   (kumiho 0.13.2), which skips the similarity search: the new text becomes a
#   revision of that item, the item's own space is where it lands, and a
#   space_hint passed alongside is ignored for placement. A kref that does not
#   resolve is an error, not a new memory. A published revision on the named
#   item moves to the correction, so recall stops serving the text just
#   corrected. Without "revises" the older path stands, which is why the
#   description still asks for the memory's type and language and a restated
#   subject: reflect hands a capture's space_hint to the store as an explicit
#   space_path (verbatim, not slugged) and only asks for stacking when it is
#   non-empty, and the similarity gate needs lexical overlap, so an unrouted
#   correction becomes a second item. The SDK's _normalize_space_path adds the
#   project name only when the path does not already start with it, so a
#   result's "/CognitiveMemory/preferences", "CognitiveMemory/preferences" and
#   "preferences" name one space and never a doubled path. Reflect reports no
#   stacked flag for one capture: a stacked write is a higher ?r= revision of
#   the same item, a new item is ?r=1.
#   tests/test_correction_stacking.py pins both on the real store path.
#
# The four session tools get SESSION_DESCRIPTION appended by _compat.build_server.
CONNECTOR_TOOL_DESCRIPTIONS = {
    "kumiho_memory_engage": (
        "Searches the user's saved Kumiho memories for context relevant to the current "
        "request. Returns a ready-to-use context summary, the matching memories and their "
        "references (source_krefs); how many come back varies. A successful search can "
        "return no matches. Check backend_error: a backend error means retrieval failed. "
        "When optimization.status is applied, zero means no "
        "retrieved candidates passed the evaluation; it does not prove no relevant "
        "memory exists. Useful near the "
        "start of a conversation about the "
        "user's own ongoing work, projects, decisions or preferences when earlier saved "
        "context could change the answer; when the user refers back to something that is "
        "not visible in this conversation (\"as we decided\", \"my usual setup\", an "
        "unfamiliar project name); and when the user asks what you remember about them or "
        "what was decided or noted before on a topic (\"what do you remember about me?\", "
        "\"what did we decide about pricing?\"). Matches are ranked by relevance to the "
        "query, not by date. The returned references can be attached to a related memory "
        "saved later, linking it to its sources.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests, not even to check "
        "first; data from another organization or any workspace the connected account is "
        "not authorized for; general knowledge or live information such as weather, news "
        "or prices. It reads only this account's saved memories and writes nothing.\n\n"
        "query: a brief natural-language description of what you are looking for, with no "
        "secrets in it. Each explicit call runs retrieval again, including an immediate "
        "repeat of the same query."
    ),
    "kumiho_memory_recall": (
        "Filtered semantic search over the user's saved Kumiho memories. Returns the best "
        "matches with titles, summaries, relevance scores and references. Useful when the "
        "search has to be limited by memory type (memory_types, for example [\"decision\"] "
        "or [\"preference\"]) or by location (space_paths, for example one project's "
        "space), such as listing the decisions filed in a known space.\n\n"
        "query: describe what you are looking for in natural language rather than guessing "
        "exact keywords. Each explicit call runs retrieval again, including an immediate "
        "repeat of the same query.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests; data outside the "
        "connected account's authorized workspace; general knowledge or live information "
        "such as weather, news or prices. Read-only."
    ),
    "kumiho_memory_retrieve": (
        "Lookup of the user's saved memories by date or by a known title, keyword, topic or "
        "space. Useful when the user asks for their most recent or oldest memories (\"what "
        "did I save recently?\", \"my latest note on the launch plan\", \"the newest "
        "decision in this space\", \"the first thing I saved\"), and when you already know "
        "which memory you need and need its exact item or revision reference, for example "
        "to link to it or retire it. Returns item references, revision references and "
        "scores, not the memory text; a returned revision can be read for its text.\n\n"
        "Arguments: for the most recent memories, set mode to \"latest\". Results come "
        "newest first by last update (an older memory that was updated moves forward) and "
        "carry created_at dates. A query, keywords or topics narrow them to relevant "
        "matches, still newest first: for \"my latest note on the launch plan\", set query "
        "to \"launch plan\". Matching is fuzzy, and when nothing matches the newest memories "
        "in scope come back with score 0, so check that results fit. mode \"first\" returns "
        "the single oldest memory, or with a query the oldest relevant match. Both modes "
        "honor space_paths (for example one project's space) and memory_types (for example "
        "[\"decision\"]). In the default search mode, results are ranked by relevance; query "
        "takes the words you expect in the title or summary, and keywords and topics add "
        "terms.\n\n"
        "Not for: saving, finding or checking passwords, access tokens, API keys, MFA or "
        "recovery codes, so do not call this tool for such requests; data outside the "
        "connected account's authorized workspace; general knowledge or live information "
        "such as weather, news or prices. Read-only."
    ),
    "kumiho_memory_reflect": (
        "Saves distilled memories to the user's private Kumiho workspace and a short note "
        "of your reply to this conversation's temporary working buffer. Useful when the "
        "user asks you to remember, save or note something (\"remember this\", \"save "
        "this\", \"note that\", in any language), and when something durable is settled: a "
        "decision with its reason, a lasting preference, a durable fact about the user, or "
        "a correction to something saved earlier. Not needed for routine turns or small "
        "talk.\n\n"
        "Arguments: response (required) is a one- or two-sentence gist of your reply. Each "
        "capture needs type (decision, preference, fact and so on), a short title with "
        "absolute dates (\"Chose Seoul on 2026-09-16\") and brief content in your own "
        "words, never transcripts. space_hint takes an existing space, copied exactly as "
        "results show it (\"/CognitiveMemory/preferences\"); without one a capture is "
        "filed at the project root as a separate item. source_krefs links captures to "
        "their sources.\n\n"
        "To correct a saved memory, set that capture's revises to the memory's reference as "
        "search results return it; the capture becomes that memory's new revision. Keep its "
        "memory type and language, and restate the subject. If you cannot tell which memory "
        "to revise, save normally and retire the old memory by its reference only when "
        "stored_krefs names a different item.\n\n"
        "Not for: passwords, access tokens, API keys, MFA or recovery codes, payment "
        "details, or anything the user asked not to keep, so do not call this tool for "
        "such requests."
    ),
    "kumiho_memory_store": (
        "Writes one pre-formed memory entry to the user's private Kumiho workspace and "
        "returns its reference. The entry arrives as it should be kept: its text plus, "
        "optionally, a title, summary, memory type and space. Useful for a single "
        "self-contained entry whose content and placement are already decided. By default "
        "it looks for a similar memory in the same space and adds a new revision to it "
        "instead of creating a duplicate; earlier revisions are retained. Set stack_revisions to false to always create a separate memory. May "
        "create the space, a bundle and provenance links.\n\n"
        "Arguments: user_text (required) is the entry's brief text, in the user's words or "
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
        "you to forget, stop using or retire a specific memory, and to retire the memory a "
        "user's correction replaced when the correction did not stack as a new revision of "
        "it. It changes what the user's searches return, but it is reversible: the item "
        "and its revision history stay in the workspace, and calling again with deprecated "
        "set to false restores it. It is not permanent erasure; say so if the user expects "
        "deletion.\n\n"
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

#: Hosted recall is always summarized. The SDK's other mode reads artifacts
#: back, and hosted deployments write none (kumiho_memory's
#: ``_read_artifact_content`` returns "" when hosted), so its only effect here
#: is keeping the prose of every earlier revision in engage results. Revision
#: history stays reachable by reference. The local plugin keeps the choice.
HOSTED_RECALL_MODE = "summarized"

#: Connector tools whose SDK input schema declares ``recall_mode``.
#: ``build_server`` hides that property from every served schema and pins the
#: argument for these tools at dispatch, including when a caller omits it.
#: tests/test_profile.py fails when the SDK's set drifts from this one.
RECALL_MODE_TOOLS = frozenset({"kumiho_memory_engage", "kumiho_memory_recall"})


#: Claude Code truncates each tool description at 2KB; keep a margin.
MAX_TOOL_DESCRIPTION_CHARS = 1800
#: Claude Code truncates server instructions at 2KB (measured in UTF-8 bytes).
MAX_INSTRUCTIONS_BYTES = 2000
#: OpenAI: the first 512 characters of the instructions must stand alone.
INSTRUCTIONS_LEAD_CHARS = 512

# Essentials first. The opening paragraph (under INSTRUCTIONS_LEAD_CHARS) is
# the credential/authorization boundary plus the engage/reflect rhythm, so it
# still works when a client keeps only the lead. Later paragraphs route the
# same intents as the descriptions: engage for general recall, retrieve for
# newest or oldest memories, recall for type- or space-filtered search, reflect
# for capture, store only as a lower-level write. Detail that does not fit the
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

Recall: engage also covers the user referring back ("as we discussed", an \
unfamiliar project) and what was decided or noted before. For the newest or \
oldest memories ("what did I save recently?"), call kumiho_memory_retrieve with \
mode "latest" or "first" and, for a topic, a query; it also finds exact \
references by title or space. kumiho_memory_recall filters search by memory \
type or space. Read a result with kumiho_get_revision_by_tag (item kref, tag \
"latest").

Capture: reflect when the user asks you to remember, save or note something, \
and at the moment something is settled; not at the end and not on routine \
turns. Use brief typed captures, absolute dates in titles, an existing space \
name in space_hint, and engage's source_krefs. kumiho_memory_store is a \
lower-level write of one pre-formed entry.

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
