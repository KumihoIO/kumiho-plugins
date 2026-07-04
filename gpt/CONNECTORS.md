# ChatGPT connector contract

This plugin publishes **one remote MCP connector** for ChatGPT.

## Endpoint

- **URL:** `https://<your-tunnel-host>/mcp` (streamable-HTTP; `/sse` + `/messages` are also served as an SSE fallback)
- **Transport:** HTTPS via a tunnel (Cloudflare Tunnel or ngrok) → loopback gateway (`127.0.0.1:8790`)
- **Auth:** OAuth 2.1 — authorization code + PKCE (S256), Dynamic Client Registration. First connect opens a consent page that asks for the installer's one-time **PIN**. No secret is placed in the URL.

## Discovery

The gateway advertises standard metadata so ChatGPT can bootstrap the flow:

- `GET /.well-known/oauth-protected-resource` (RFC 9728) — names the authorization server
- `GET /.well-known/oauth-authorization-server` (RFC 8414)
- `POST /register` (RFC 7591 DCR), `GET /authorize`, `POST /token`, `GET /jwks`

An unauthenticated `GET /mcp` returns `401` with
`WWW-Authenticate: Bearer resource_metadata="…"` — the pointer ChatGPT follows.

## Tools

The connector exposes whatever the core `kumiho` MCP server auto-discovers,
including the `kumiho-memory` tools:

- `kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_recall`
- `kumiho_chat_add` / `kumiho_chat_get` / `kumiho_chat_clear`
- `kumiho_memory_ingest`, `kumiho_memory_consolidate`, `kumiho_memory_dream_state`
- plus the graph tools (`kumiho_search_items`, `kumiho_get_item`, …)

## Backend

Behind the gateway, `mcp-proxy` runs the stdio `kumiho-mcp`, which routes to:

- **CE** (`127.0.0.1:9190`, tokenless loopback) — the free/default backend, or
- **Cloud** (control-plane discovery) — when a Kumiho API token is present.

## Verify

```bash
kumiho-gpt-connect status   # gateway / CE / service health
kumiho-gpt-connect url      # connector URL + PIN
curl -s https://<host>/.well-known/oauth-authorization-server | jq .issuer
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/mcp   # expect 401
```
