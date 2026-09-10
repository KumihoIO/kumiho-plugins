# Privacy & Trust

## What stays local
Use the configured CE or Cloud backend within the user's authorized scope. Selected summaries, structured experience records, and sanitized pattern proposals can be stored there. Artifact pointers are paths, not automatic file uploads; do not promise that every operation keeps all content local. Host-model processing is a separate boundary.

## What gets redacted
Experience/pattern tools screen credentials and redact recognized PII before storage. These checks are best effort, not proof that arbitrary text is safe. Screen every field you send, and never include secrets or off-record material.

## Never store
Credentials, API keys, tokens, passwords, payment details, anything marked off-record.

## Ask before storing
Sensitive personal context (health, finances, relationships, legal), info about other people.

## User control
- **"What do you know about me?"** → `kumiho_memory_engage` with broad query, share transparently
- **"Forget X"** → `kumiho_deprecate_item(item_kref, deprecated=true)` immediately
- **"Show everything including forgotten"** → `kumiho_fulltext_search(query=..., include_deprecated=true)` — the only search tool that accepts this parameter
- **"Don't remember this session"** → skip artifact/consolidation, `kumiho_chat_clear`

Nothing is silently overwritten — old revisions preserved. Dream State has safety guards (published items never auto-deprecated, 50% circuit breaker).