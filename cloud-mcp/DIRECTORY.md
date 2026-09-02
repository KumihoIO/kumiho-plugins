# Kumiho Memory — Claude Connectors Directory submission pack

Everything the submission form at
`claude.ai/admin-settings/directory/submissions/new` asks for, in the order it
asks for it, plus the connection instructions for the two non-directory paths
(Team custom connector, Claude Code).

Submitting requires a Claude.ai **Team or Enterprise** organisation and the
**Owner** role — that part is Morpheus's (plan §4). Fields marked
**[needs Morpheus]** cannot be filled from this repo.

---

## 1. Listing

**Name** (≤100)

```
Kumiho Memory
```

**Tagline** (≤55 — 40 chars)

```
Memory that remembers why, not just what
```

**Description** (≤2000 — 1 481 chars)

```
Kumiho Memory gives Claude a memory that survives the conversation.

Most memory tools store text and hope similarity search finds it again. Kumiho
stores a graph: every memory is a typed node — a decision, a preference, a fact,
a correction — linked to the things it is about and to the reasoning that
produced it. Recall follows those links, so asking "why did we choose Postgres
here?" returns the decision, the rationale, the alternatives that were rejected,
and what changed since.

Connect it once and Claude starts each conversation knowing who you are, how you
like to work, and what you settled last time. When something gets decided
mid-conversation, Claude writes it down; when you contradict an old memory,
Claude supersedes it rather than accumulating two conflicting versions.

What you get:
• Cross-conversation memory keyed to you, not to a chat window
• Semantic recall over a typed knowledge graph, not a flat vector store
• Decisions captured with their rationale and rejected alternatives
• Consolidation that merges near-duplicates instead of hoarding them
• Explicit forgetting — you can tell Claude to forget something and it does

Your memories live in your own Kumiho workspace, isolated per tenant, and are
never used to train models. You can read, export, or delete everything from the
Kumiho dashboard at any time.

Kumiho Memory is free to start. Sign in with your Kumiho account when you
connect; a workspace is created for you if you do not have one.
```

**Categories** (1–5)

1. Productivity
2. Knowledge & Documentation
3. Developer Tools
4. Research

**Server URL**

```
https://mcp.kumiho.cloud/mcp
```

**Transport**: Streamable HTTP (SSE fallback available at `/sse` + `/messages/`).

**Authentication**: OAuth 2.1 — `oauth_dcr` and `oauth_cimd` both supported.
The authorization server is `https://control.kumiho.cloud`; its RFC 8414
metadata advertises `code_challenge_methods_supported: ["S256"]`,
`token_endpoint_auth_methods_supported: ["none", "client_secret_post"]` and
`client_id_metadata_document_supported: true`, so Claude may select CIMD.
`oauth_anthropic_creds` can be added later by emailing `mcp-review@anthropic.com`.

**Permanent slug**

```
kumiho-memory
```

| Field | Value |
|---|---|
| Documentation URL | `https://kumiho.io/docs/connect/claude` **[needs Morpheus]** |
| Privacy policy URL | `https://kumiho.io/privacy` — draft in [`PRIVACY.md`](PRIVACY.md) **[needs Morpheus]** |
| Support contact | `support@kumiho.io` **[needs Morpheus]** |
| Icon | 512×512 PNG, transparent background **[needs Morpheus]** |

---

## 2. Tools

18 tools. Every one carries a `title` and either `readOnlyHint` or
`destructiveHint`, as the directory requires.

### Memory

| Tool | Title | Read-only | Destructive | What it does |
|---|---|:--:|:--:|---|
| `kumiho_memory_engage` | Engage memory before responding | ✓ | | One call at the start of a conversation: identity, preferences, the most relevant prior memories, and an active session id. |
| `kumiho_memory_recall` | Recall memories | ✓ | | Semantic search over the memory graph for a described need. |
| `kumiho_memory_retrieve` | Retrieve memories | ✓ | | Fetch specific memories by id or filter. |
| `kumiho_memory_store` | Store a memory | | | Write one memory with its type and content. |
| `kumiho_memory_reflect` | Reflect and capture memories | | | Capture several typed memories at once — the normal way something settled gets written down. |
| `kumiho_memory_consolidate` | Consolidate the session into long-term memory | | | Fold the conversation's working buffer into durable memory, merging near-duplicates. |
| `kumiho_memory_decompose` | Decompose a memory into the typed graph | | | Split an overloaded memory into typed nodes and edges so later recall can bridge through shared entities. |
| `kumiho_memory_space_profile` | Profile memory spaces | | | Summarise what a memory space contains and how it is being used. |

### Knowledge graph

| Tool | Title | Read-only | Destructive | What it does |
|---|---|:--:|:--:|---|
| `kumiho_list_projects` | List projects | ✓ | | List the projects in the workspace. |
| `kumiho_get_spaces` | List spaces | ✓ | | List the memory/knowledge spaces in a project. |
| `kumiho_search_items` | Search items | ✓ | | Search knowledge items by name, kind or context. |
| `kumiho_get_item` | Get an item | ✓ | | Read one knowledge item and its metadata. |
| `kumiho_get_revision_by_tag` | Get a revision by tag | ✓ | | Read a specific tagged revision of an item. |
| `kumiho_get_provenance_summary` | Summarize provenance | ✓ | | Where a piece of knowledge came from and what it was derived from. |
| `kumiho_create_space` | Create a space | | | Create a new space to organise memories. |
| `kumiho_deprecate_item` | **Forget a memory** | | ✓ | Retire a memory so it stops being recalled. Confirm with the user first. |

### Conversation buffer

| Tool | Title | Read-only | Destructive | What it does |
|---|---|:--:|:--:|---|
| `kumiho_chat_get` | Read the chat buffer | ✓ | | Read the short-term buffer for the active session. |
| `kumiho_chat_clear` | Clear the chat buffer | | ✓ | Discard the short-term buffer for the active session. |

`openWorldHint` is `false` on all 18: every tool acts on the user's own Kumiho
workspace and nothing else.

**Not exposed.** The Kumiho SDK defines 63 tools; the connector profile is a
curated 18. Project and space deletion, revision deletion, bundle management,
edge surgery and the git-dependent `kumiho_code_*` tools are all absent — and
the resource server refuses `tools/call` for any name outside the profile, so
hiding them from `tools/list` is not the only thing keeping them unreachable.
`kumiho_memory_dream_state` is held back for v1 because hosted tenants run the
keyless core and Dream State needs an LLM budget that is not metered yet.

---

## 3. Use cases

**Pick up where you left off.** "What were we doing with the billing migration?"
Claude recalls the decision to move to Stripe, the two things that blocked it,
and the deadline you set — from a conversation three weeks ago in a different
chat window.

**Stop re-explaining yourself.** Preferences ("I write Python, not TypeScript";
"give me the answer before the reasoning"; "call me Kacy") are stored once and
recalled at the start of every conversation, in Claude Desktop and Claude Code
alike.

**Keep the why, not just the what.** When a decision is made mid-conversation,
Claude captures the rationale and the alternatives that were rejected. Six months
later "why is this a queue and not a cron job?" has an answer that is not
archaeology.

**Onboard a teammate — or a new Claude.** Ask for a summary of everything known
about a project and get the decisions, constraints and open questions, sourced
back to when each was recorded.

**Correct the record.** "Actually we dropped that approach" supersedes the old
memory rather than adding a contradictory one, so recall does not return both.

**Forget on request.** "Forget what I told you about the acquisition" retires
that memory. It is a destructive tool; Claude confirms first.

---

## 4. Before you connect

- **You need a Kumiho account.** Signing in during the connect flow creates a
  free workspace if you do not have one. No credit card.
- **Memories are stored on Kumiho's servers**, in a graph database isolated per
  workspace, in the region your workspace is provisioned in. They are not stored
  in Claude.
- **Claude decides what is worth remembering.** It writes memories when something
  is settled — a decision, a preference, a durable fact. Tell it not to store
  something and it will not.
- **Everything is reviewable.** Read, export or delete any memory from the
  Kumiho dashboard, or ask Claude to forget it in conversation.
- **Nothing is used to train models** — not Kumiho's, not anyone's.
- **Team and Enterprise admins** can connect Kumiho once for the whole
  organisation using a workspace API key; see §7.

---

## 5. Data handling

*Answers to the submission form's data-handling section.*

**What data does the connector read?**
Only what the user's own Kumiho workspace contains: memories, knowledge items and
their revisions, provenance edges, and the short-term buffer for the active
conversation. Access is scoped by the OAuth access token to exactly one
workspace; the token carries the workspace id and every backend call is filtered
by it.

**What data does the connector write?**
Memories the user asks Claude to remember, or that Claude judges settled —
decisions with their rationale, preferences, facts, corrections — plus the
conversation's short-term buffer, and spaces the user asks to create. Writes go
to the user's own workspace and nowhere else.

**What data leaves Claude?**
The arguments of each tool call: the text of what is being remembered, and the
natural-language query when recalling. Full conversation transcripts are never
sent — only the specific content passed to a memory tool.

**Where is data stored, and for how long?**
In the user's Kumiho workspace: a per-tenant Neo4j graph plus a Redis
short-term buffer, hosted in the workspace's region (`us-east-1`, `eu-west-1` or
`ap-northeast-2`). Memories persist until the user deletes them or deletes the
workspace. The short-term buffer expires within hours.

**Who can access it?**
Members of that Kumiho workspace. Kumiho staff do not read workspace content;
support access requires an explicit, time-boxed grant from the workspace owner.

**Is data used for training?**
No. Not by Kumiho and not by any third party.

**Sub-processors**
AWS (compute, region-local storage), Neo4j Aura or self-managed Neo4j on AWS
(graph), Upstash (Redis buffer), Supabase (accounts and workspace directory),
Google Firebase (authentication), Cloudflare (edge). Current list:
`https://kumiho.io/privacy#subprocessors` **[needs Morpheus]**.

**Deletion**
Per memory from the dashboard or by asking Claude to forget it; whole-workspace
deletion from the dashboard removes the graph, the buffer and the backups within
30 days. Disconnecting the connector revokes the token — it does not delete
memories, which is deliberate: reconnecting restores them.

**Encryption**
TLS 1.2+ in transit everywhere, including the internal gRPC hop. At rest:
provider-managed encryption on all stores.

**Compliance acknowledgements** — the form's seven checkboxes are Morpheus's to
tick; nothing in this implementation contradicts any of them. **[needs Morpheus]**

---

## 6. Test account

The reviewer needs an account whose memory is already populated, or `engage`
returns an empty result and the connector looks broken. **[needs Morpheus]** to
create the account and put the credentials in the form.

Script for populating it — run once, signed in as the test user:

> Please remember the following about me, using your memory tools:
>
> 1. My name is Alex Reviewer and I work as a backend engineer at Northwind.
> 2. I prefer answers first and reasoning second, and I write Go and Python.
> 3. On 12 March we decided to use Postgres rather than DynamoDB for the events
>    service, because the query patterns turned out to be relational and the
>    write volume was two orders of magnitude below what DynamoDB is for. We
>    rejected DynamoDB and also rejected sharded MySQL (nobody on the team had
>    run it in anger).
> 4. The events service deploy is blocked on the schema migration review.
> 5. I am allergic to meetings before 10am.

Then, in a **new** conversation, the reviewer can verify:

| Prompt | Expected |
|---|---|
| "What do you know about me?" | Name, role, and the answer-first preference — via `kumiho_memory_engage`. |
| "Why did we pick Postgres for the events service?" | The rationale *and* both rejected alternatives — via `kumiho_memory_recall`. |
| "What is blocking the events deploy?" | The schema migration review. |
| "Actually, we unblocked the migration review yesterday." | Claude supersedes the old memory rather than storing a contradiction. |
| "Forget that I am allergic to morning meetings." | Claude confirms, then calls `kumiho_deprecate_item`. |

---

## 7. Connecting without the directory

### Team / Enterprise custom connector (`static_headers`)

An admin connects Kumiho once for the whole organisation with a workspace API
key. Everyone in the org then shares that one workspace — which is the point for
a team memory, and worth stating plainly because it is *not* per-user isolation.

1. In the Kumiho dashboard → **API Keys** → **Create key**. Name it
   `claude-team`. Copy it; it is shown once.
2. In Claude → **Settings → Connectors → Add custom connector**.
3. Fill in:
   - **Name**: `Kumiho Memory`
   - **URL**: `https://mcp.kumiho.cloud/mcp`
   - **Authentication**: *Custom headers*
   - Header name `x-api-key`, value the key from step 1.
4. Save, then open a new conversation and ask "what do you remember about me?".

Revoking the key in the dashboard disconnects everyone within 60 seconds — the
resource server introspects the key against the control plane on every request
and caches the answer for at most a minute.

### Claude Code

```bash
claude mcp add --transport http kumiho-memory https://mcp.kumiho.cloud/mcp
```

Claude Code runs the OAuth flow in your browser on first use (loopback redirect,
PKCE S256). Add `--scope user` to make it available in every project.

With a workspace API key instead of OAuth:

```bash
claude mcp add --transport http kumiho-memory https://mcp.kumiho.cloud/mcp \
  --header "x-api-key: $KUMIHO_API_KEY"
```

### Any other MCP client

Point it at `https://mcp.kumiho.cloud/mcp` over streamable HTTP. Unauthenticated
requests answer `401` with

```
WWW-Authenticate: Bearer resource_metadata="https://mcp.kumiho.cloud/.well-known/oauth-protected-resource", scope="memory"
```

which is enough for any RFC 9728-aware client to discover the authorization
server on its own.

---

## 8. Hosted connector vs the Claude Code plugin

The connector is the **lite tier**. The plugin
(`claude plugin install kumiho-memory@kumiho-plugins`) is the **pro tier**, and
stays that way: it can run code on your machine, and a remote MCP server cannot.

| | Hosted connector (this) | Claude Code / Cowork plugin |
|---|---|---|
| Install | Click **+** in the directory | `claude plugin install`, then `/kumiho-onboard` |
| Runs where | Kumiho's servers | Your machine (isolated venv) |
| Local Python / venv | None | Yes, managed for you |
| Works in Claude.ai, Desktop, mobile | **Yes** | No (Claude Code and Cowork only) |
| Works in Claude Code | Yes | Yes |
| Tools | 18 curated | 63 |
| Recall at session start | Claude calls `engage` (guided by server instructions) | **Automatic** — SessionStart hook injects it before the first token |
| Session rotation on `/clear` | No | **Yes** |
| Capture | Claude calls `reflect` when something settles | Same, plus host-side transcript mining |
| Decision Memory over git | No — there is no repo on a hosted box | **Yes** — `kumiho_code_why`, commit + session mining |
| History backfill from past sessions | No | **Yes** — `/kumiho-backfill` over Claude Code, Codex and ChatGPT history |
| Local conversation artifacts | No | **Yes** — raw transcripts stay on your machine |
| Dream State consolidation | Not in v1 | **Yes** — `/dream-state` |
| Auto-approve memory tool calls | No (Claude asks) | Yes |
| Self-hosted CE backend | No | **Yes** — point it at your own `kumiho-server` |
| Same memory graph | **Yes — one workspace, both tiers** | **Yes** |

They share a workspace: connect the hosted connector on your phone, and what it
remembers is there in Claude Code the next morning.

---

## 9. Pre-submission checklist

- [ ] Claude.ai Team/Enterprise org with the Owner role **[Morpheus]**
- [ ] `mcp.kumiho.cloud` resolves to the edge worker; App Runner origin healthy
- [ ] `GET https://mcp.kumiho.cloud/.well-known/oauth-protected-resource` returns
      `resource` **exactly** equal to `https://mcp.kumiho.cloud/mcp`
- [ ] Unauthenticated `POST /mcp` returns 401 with the `resource_metadata`
      challenge
- [ ] `https://control.kumiho.cloud/.well-known/oauth-authorization-server`
      serves RFC 8414 metadata with `S256` **[WP-D]**
- [ ] DCR and CIMD both complete a full authorization-code + PKCE flow **[WP-D]**
- [ ] `https://claude.ai/oauth/claude-code-client-metadata` is accepted as a CIMD
      `client_id` **[WP-D]**
- [ ] Cloudflare WAF exempts `160.79.104.0/21` from the non-browser UA challenge
      on `mcp.kumiho.cloud` and `control.kumiho.cloud` **[Morpheus]**
- [ ] Discovery, registration and token endpoints all answer well under 10 s;
      refresh under 30 s
- [ ] `tools/list` shows exactly 18 tools, each with a title and hints
- [ ] Privacy policy, docs page, icon and support address are live **[Morpheus]**
- [ ] Test account populated with the §6 script **[Morpheus]**
