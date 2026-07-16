# Kumiho Memory → ChatGPT connector

`kumiho-gpt-connect` is a one-command local installer that exposes your Kumiho
Memory to **ChatGPT** as a custom MCP connector.

ChatGPT only talks to **remote** MCP servers over HTTPS (it cannot run a local
stdio server the way Claude Desktop does). So this tool runs a small local
**gateway** — its own OAuth authorization server in front of the Kumiho MCP
server — and publishes it through a tunnel. You paste one URL into ChatGPT,
approve once with a PIN, and your memory is connected.

```
ChatGPT ──OAuth (code+PKCE, DCR)──▶ gateway ──Bearer-checked reverse proxy──▶ mcp-proxy ─stdio▶ kumiho-mcp ─▶ Kumiho CE
        ──MCP over HTTPS (tunnel)──▶
```

> **Free tier = Community Edition (CE).** This installer targets the local,
> self-hosted CE backend. Paid/managed Cloud uses Kumiho's hosted connector —
> you don't need this local tool for that.

> **Merged ChatGPT×Codex app (July 2026):** the connector contract this
> gateway implements (HTTPS + SSE/streamable-HTTP, OAuth 2.1 + PKCE + DCR,
> RFC 9728/8414/7591 discovery) is unchanged in the merged desktop app's
> developer mode. For the *coding* side of the merged app, the
> [`codex/`](../codex/README.md) plugin covers local stdio memory; bundling
> this connector into that plugin via `.app.json` is planned (Phase 3).

---

## Prerequisites

- **Python 3.10+**
- **Kumiho CE running locally** — the installer will point you at the one-liner
  that installs it (it runs the Docker one-shot for Neo4j + Redis and the
  `kumiho_server onboard` wizard). CE listens on `127.0.0.1:9190`.
- **A tunnel** — [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
  (recommended: free, stable hostname) or [ngrok](https://ngrok.com/).
- **ChatGPT Developer Mode** — available on Plus/Pro/Team/Enterprise/Edu
  (Settings ▸ Connectors ▸ Advanced ▸ Developer mode).

---

## Quick start

```bash
# no install — run straight from PyPI
uvx kumiho-gpt-connect install --tunnel cloudflare --cloudflare-hostname memory.example.com --tunnel-token <CF_TUNNEL_TOKEN>

# or install persistently
pipx install kumiho-gpt-connect
kumiho-gpt-connect install --tunnel ngrok --tunnel-token <NGROK_AUTHTOKEN>
```

`install` sets up the backend, tunnel, a one-time consent PIN, and an
auto-launch service so the gateway comes back after a reboot. It prints your
**connector URL** and **PIN**.

Then, in ChatGPT:

1. Settings ▸ Connectors ▸ Advanced ▸ **Developer mode** ▸ **Add custom connector**
2. Paste the connector URL (e.g. `https://memory.example.com/mcp`)
3. ChatGPT opens a browser consent page — **enter the PIN** the installer printed
4. Done. The Kumiho memory tools are now available in ChatGPT.

Get the URL and PIN again any time:

```bash
kumiho-gpt-connect url
```

---

## Backends: CE (free) vs Cloud (paid)

Selection is automatic and mirrors the SDK:

| | how to pick it | notes |
|---|---|---|
| **CE (free)** | default (no token) | tokenless loopback; needs local CE + a local Redis (`UPSTASH_REDIS_URL`, defaults to `redis://127.0.0.1:6379`) |
| **Cloud (paid)** | `install --token <KUMIHO_API_TOKEN>` | bridges the local gateway to managed Cloud; the cleaner paid path is Kumiho's **hosted** connector — no local tool needed |

---

## Tunnels

**Cloudflare (recommended, stable URL).** Create a named tunnel in the Zero
Trust dashboard, add a public hostname with an ingress rule pointing at
`http://127.0.0.1:8790`, and pass the tunnel token + hostname:

```bash
kumiho-gpt-connect install --tunnel cloudflare \
  --cloudflare-hostname memory.example.com --tunnel-token <CF_TUNNEL_TOKEN>
```

Without a hostname, Cloudflare runs a **quick tunnel** with an ephemeral
`*.trycloudflare.com` URL (changes each run — fine for testing).

**ngrok.** Pass your authtoken; a reserved domain (paid) gives a stable URL:

```bash
kumiho-gpt-connect install --tunnel ngrok --tunnel-token <NGROK_AUTHTOKEN>
```

`cloudflared` is downloaded automatically if not on PATH; ngrok is handled by
`pyngrok`.

---

## How authentication works

The gateway is its **own OAuth 2.1 authorization server** (authorization code +
PKCE, with Dynamic Client Registration). ChatGPT registers itself, you approve
once by entering the **PIN** on a local consent page, and every MCP request
then carries a short-lived signed Bearer token. **The token is never embedded
in the connector URL.**

The PIN is **one-time**: entering it approves *that* ChatGPT client and then
clears the PIN. That approved client is remembered, so ChatGPT reconnects (and
survives gateway restarts) without a PIN. Adding a *different* client — e.g.
re-adding the connector, which registers a new client — needs a fresh PIN:
run `kumiho-gpt-connect rotate-pin`. Wrong-PIN attempts are locked out after a
few tries.

Because CE has no authentication of its own, this gateway PIN/OAuth *is* the
security boundary for the tunnel — keep it single-user, and rotate the PIN with
`kumiho-gpt-connect rotate-pin` if needed.

---

## Commands

| command | what it does |
|---|---|
| `install` | set up backend, tunnel, PIN, and the auto-launch service |
| `serve` | run the gateway in the foreground (what the service runs) |
| `url` | print the connector URL and PIN |
| `status` | report gateway / CE / service health |
| `rotate-pin` | mint a new consent PIN (restart the service to apply) |
| `uninstall` | remove the auto-launch service |

State lives in `~/.kumiho/gpt/` (config, OAuth signing key, registered clients).

---

## Security notes

- **Single-user, by design.** CE is a single-user local edition; keep the
  connector to your own ChatGPT. Do not share the URL.
- **The tunnel exposes the gateway, not CE.** Only the OAuth-guarded MCP layer
  is public; the CE gRPC stays on loopback.
- **Full memory access.** The tools include read, write, and delete — treat the
  PIN and your ChatGPT connector like credentials.
- **Machine must be running.** The gateway + tunnel run on your machine; ChatGPT
  reaches your memory only while they're up (the auto-launch service keeps them
  running across reboots).

---

## Troubleshooting

- **"No local Kumiho CE server detected"** — install/start CE first
  (`kumiho_server onboard`); the installer prints the one-liner. `status` shows
  whether CE is up.
- **Connector URL unknown** — with a quick/ngrok (dynamic) tunnel the URL is
  only known once the gateway runs; run `serve` once, then `url`.
- **ChatGPT can't reach the connector** — confirm the tunnel is up and (for a
  Cloudflare named tunnel) that its ingress points at `127.0.0.1:8790`.
- **PIN changes didn't take** — restart the service so the gateway reloads it.
- **Registration rejected / `invalid_redirect_uri`** — for safety the gateway only
  accepts ChatGPT/OpenAI (`chatgpt.com`, `openai.com`) https redirect origins, so a
  mistakenly-entered PIN can't deliver the auth code anywhere else. If ChatGPT's
  callback host differs, set `KUMIHO_GPT_ALLOWED_REDIRECT_HOSTS` (comma-separated
  hostnames) before starting the gateway.
