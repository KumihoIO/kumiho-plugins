# Privacy and User Control (Codex)

Kumiho uses Codex's selected CE or Cloud backend. Selected captures, summaries,
and metadata are persisted there. Artifact pointers are paths, not file
uploads; some specialized operations, such as skill ingestion, intentionally
store document text. Do not promise that every tool automatically redacts all
sensitive data. Screen what you send. Local CE also does not make Codex model
processing local.

Never send credentials, tokens, passwords, private keys, session cookies,
credential-bearing URLs, payment details, or off-record material to memory.
For sensitive personal context or information about other people, establish
explicit storage intent before capturing it. Keep raw transcripts and full
tool logs out of memory calls; use minimal sanitized evidence.

- "What do you know about me?": use the turn's one summarized engage with
  `limit=3`; show supported facts and their provenance, and offer targeted
  follow-up rather than dumping all spaces.
- "Forget X": resolve the exact relevant item, then call
  `kumiho_deprecate_item(item_kref=..., deprecated=true)`. Do not guess a target
  when several match. Explain that deprecation is not permanent erasure.
- "Include forgotten memories": use `kumiho_fulltext_search` with the user's
  query, `include_deprecated=true`, and a bounded `limit`. Do not broaden other
  retrieval automatically.
- "Don't remember this session": stop capture/artifact/consolidation, call
  `kumiho_chat_clear` for the current Codex thread (omit `session_id`), and
  explain that this clears working memory, not already stored long-term items.

For changed names/tone/language, use `$kumiho-personalize`: the published
identity is shared across hosts, not a Codex-only preference file. Preserve
history; never delete revisions just to change a preference.
