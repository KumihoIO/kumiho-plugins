# Privacy & Trust

## What stays local
Full conversation transcripts, tool logs, images — local files only. Cloud stores summaries + pointers, not content.

## What gets redacted
PII redacted from summaries before reaching cloud. Raw PII never crosses the privacy boundary.

## Never store
Credentials, API keys, tokens, passwords, payment details, anything marked off-record.

## Ask before storing
Sensitive personal context (health, finances, relationships, legal), info about other people.

## User control
- **"What do you know about me?"** → `kumiho_memory_recall` with broad query, share transparently
- **"Forget X"** → `kumiho_deprecate_item(item_kref, deprecated=true)` immediately
- **"Show everything including forgotten"** → search with `include_deprecated=true`
- **"Don't remember this session"** → skip artifact/consolidation, `kumiho_chat_clear`

Nothing is silently overwritten — old revisions preserved. Dream State has safety guards (published items never auto-deprecated, 50% circuit breaker).