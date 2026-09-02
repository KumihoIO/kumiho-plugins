# kumiho-mcp-edge

Cloudflare Worker in front of `mcp.kumiho.cloud`. It does three things and
nothing else:

1. **CORS for `https://claude.ai`** — including the exact request headers the
   MCP streamable-HTTP transport sends (`authorization`, `content-type`,
   `x-api-key`, `mcp-session-id`, `mcp-protocol-version`, `last-event-id`) and
   exposing `mcp-session-id` and `www-authenticate` back to the browser.
   Without the exposed `mcp-session-id` a browser client cannot continue a
   session past its first request; without `www-authenticate` it cannot see the
   401 challenge that starts the OAuth flow.
2. **A per-IP brake** (600 req/min by default). MCP is chatty — in stateless
   mode every JSON-RPC call is its own HTTP request — so this is deliberately
   loose. Real quotas belong in the control plane, per tenant.
3. **Never caches.** Every response is tenant data or an auth challenge.
   `Cache-Control: no-store` and `X-Robots-Tag: noindex` are set at the edge as
   well as at the origin.

Bodies stream through in both directions: MCP answers a POST with an SSE
stream, and buffering it here would break the transport.

## Deploy

```bash
npm install
npm run typecheck
npx wrangler deploy          # production; needs CLOUDFLARE_API_TOKEN + ACCOUNT_ID
```

`ORIGIN_URL` in `wrangler.toml` is a placeholder; the GitHub Actions workflow
rewrites it to the App Runner service URL before deploying.

## What Morpheus has to do once

- Point `mcp.kumiho.cloud` at this worker (DNS record + the route in
  `wrangler.toml` — the zone is `kumiho.cloud`).
- Exempt Anthropic's egress range `160.79.104.0/21` from the Cloudflare WAF's
  non-browser user-agent challenge on this hostname. Claude's connector fetches
  are server-side; a managed challenge on `/.well-known/oauth-protected-resource`
  or `/mcp` breaks the connector with no useful error.
