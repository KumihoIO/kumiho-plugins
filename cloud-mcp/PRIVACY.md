# Kumiho Memory — Privacy Policy (DRAFT)

> **Draft for review.** This is the engineering description of what the hosted
> Kumiho Memory connector actually does with data, written so it can become the
> published policy at `https://kumiho.io/privacy`. It has not been through legal
> review. Placeholders are marked **[TBD]**. Do not link the Claude Connectors
> Directory submission at this file — link the published page.

Last updated: **[TBD]** · Effective: **[TBD]**

---

## Who we are

Kumiho **[legal entity name, registered address — TBD]** ("Kumiho", "we")
operates the Kumiho memory platform, including the hosted Claude connector at
`https://mcp.kumiho.cloud`.

Data protection contact: **privacy@kumiho.io** **[TBD — confirm the address
exists and is monitored]**.

## What this policy covers

The hosted connector and the Kumiho platform behind it: your account, your
workspace, and the memories stored in it. It covers data you give us and data
Claude sends us on your behalf when you use the connector.

It does not cover Anthropic's handling of your Claude conversations. That is
Anthropic's privacy policy, and it applies to everything you type into Claude
whether or not this connector is installed.

---

## What we collect

### Account data

Email address, display name, and an authentication identifier from Google
Firebase. If you sign in with Google, we receive your email address and name from
Google; we do not receive your Google password. We store your workspace
membership, role, and plan.

### Memory content

This is the substance of the service. When you use the connector, Claude sends us:

- **Memories.** Text you asked Claude to remember, or that Claude judged
  settled — decisions and their rationale, preferences, facts, corrections —
  along with a type, timestamps, and links to related memories.
- **Recall queries.** The natural-language description Claude uses to search
  your memory graph.
- **A short-term conversation buffer.** Recent turns Claude has explicitly added
  to the buffer so it can consolidate them later.

**We do not receive your Claude conversations.** Only the arguments of the
specific tool calls Claude makes reach us. If Claude never calls a memory tool
during a conversation, nothing about that conversation reaches Kumiho.

Claude decides when to write a memory. If you tell Claude not to store
something, it will not, and you can ask it to forget anything already stored.

### Technical data

For each request: timestamp, HTTP method and path, response status, your
workspace id, the identifier of the credential used, and a non-reversible
fingerprint of it. We log the **identifier**, never the credential itself. We do
not log memory content or recall queries.

We keep IP addresses at the edge for abuse prevention **[TBD — confirm
Cloudflare log retention setting]**.

### What we do not collect

No advertising identifiers, no cross-site tracking, no third-party analytics on
the connector endpoint. The connector serves no cookies.

---

## Why we process it, and on what basis

| Purpose | Lawful basis (GDPR Art. 6) |
|---|---|
| Storing and recalling your memories — the service itself | Performance of a contract |
| Authenticating you and isolating your workspace | Performance of a contract |
| Keeping the service up, debugging, preventing abuse | Legitimate interests |
| Billing on paid plans | Performance of a contract |
| Legal and tax records | Legal obligation |

We do not process your data for marketing without separate opt-in consent.

## We do not train on your data

Your memories are not used to train or fine-tune any model — not ours, not a
third party's. We do not sell your data and we do not share it with data brokers.

Recall uses an embedding model to turn text into vectors for semantic search.
That model is run by us, is not trained on your content, and the vectors stay in
your workspace.

---

## Where it lives

Your workspace is provisioned in one region and its data stays there:

| Region | Location |
|---|---|
| `us-east-1` | United States (N. Virginia) |
| `eu-west-1` | Ireland |
| `ap-northeast-2` | South Korea (Seoul) |

Account and workspace-directory records are held in **[TBD — Supabase project
region]**. For users in the EEA/UK whose workspace is outside it, transfers rely
on the European Commission's Standard Contractual Clauses **[TBD — confirm SCCs
are executed with each sub-processor]**.

### Sub-processors

| Sub-processor | Purpose |
|---|---|
| Amazon Web Services | Compute and storage |
| Neo4j (Aura or self-managed on AWS) | Memory graph |
| Upstash | Short-term buffer (Redis) |
| Supabase | Accounts and workspace directory |
| Google Firebase | Authentication |
| Cloudflare | Edge routing and DDoS protection |

We publish changes to this list at `https://kumiho.io/privacy#subprocessors`
**[TBD]** and notify workspace owners before a new sub-processor starts
processing.

---

## How long we keep it

- **Memories:** until you delete them or delete the workspace. There is no
  automatic expiry — a memory system that quietly forgot would be worse than
  useless.
- **Short-term buffer:** expires automatically, within hours.
- **Request logs:** 30 days **[TBD — confirm]**.
- **Account records:** for the life of the account, then 30 days.
- **Backups:** purged within 30 days of deletion.

## Your choices

**Read everything.** The Kumiho dashboard lists every memory in your workspace,
with when and how it was captured. You can also ask Claude directly.

**Export.** Machine-readable export of your whole workspace from the dashboard.

**Delete.** Any single memory, from the dashboard or by asking Claude to forget
it — "forget what I told you about X" retires it so it is no longer recalled.
Deleting the workspace removes the graph, the buffer and the backups.

**Disconnect.** Removing the connector in Claude revokes its access token.
Deliberately, it does **not** delete your memories: reconnecting restores them.
Delete the workspace if you want the data gone.

**Correct.** Tell Claude the correct version. Corrections supersede the old
memory rather than sitting alongside it.

Under GDPR you also have rights of access, rectification, erasure, restriction,
portability and objection, and the right to complain to your supervisory
authority. Under CCPA/CPRA you have rights to know, delete, correct, and to opt
out of sale or sharing — we do neither. Email **privacy@kumiho.io**; we respond
within 30 days.

---

## Security

- TLS 1.2 or better on every connection, including the internal gRPC hop between
  the connector and the memory backend.
- Encryption at rest on all stores, provider-managed.
- **Per-workspace isolation.** The connector holds no ambient credentials. Every
  request carries its own token, and the workspace it names scopes every backend
  call for that request and no other. Tokens are verified against the control
  plane's published signing keys on arrival.
- Access tokens live one hour. Workspace API keys live a year and can be revoked
  from the dashboard; revocation takes effect within 60 seconds.
- Kumiho staff do not read workspace content. Support access to a specific
  workspace requires an explicit, time-boxed grant from its owner and is logged.
- Breach notification without undue delay and within 72 hours where GDPR
  requires it. **[TBD — document the internal incident process]**

## Children

The service is not for anyone under 16, and we do not knowingly collect their
data. If you believe a child has given us data, email **privacy@kumiho.io** and
we will delete it.

## Changes

Material changes are announced by email to workspace owners and posted here at
least 14 days before they take effect. The "last updated" date always reflects
the current version.

## Contact

**privacy@kumiho.io** · **support@kumiho.io** · **[postal address — TBD]**
· EU/UK representative **[TBD — required under GDPR Art. 27 if Kumiho has no EU
establishment]**

---

## Open items before publication

1. Legal entity name, registered address, and EU/UK Art. 27 representative.
2. Confirm `privacy@kumiho.io` exists and is monitored.
3. Confirm actual retention for request logs and Cloudflare edge logs.
4. Confirm the Supabase project region and whether SCCs are executed with every
   sub-processor.
5. Legal review of the GDPR/CCPA sections.
6. Decide whether the workspace-export feature promised above exists yet; if it
   does not, either build it or soften the wording before publishing.
7. Publish the sub-processor list at a stable anchor and keep it current.
