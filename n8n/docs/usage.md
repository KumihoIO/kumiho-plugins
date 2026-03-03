# kumiho-n8n usage

Last updated: 2026-01-03

This package intentionally ships **two nodes**:

- **Kumiho Event Trigger** (SSE event trigger)
- **Kumiho Action** (REST-style Resource + Operation)

MCP tools (including AI Agent tool-use) are handled via n8n’s built-in **MCP Client** node.

Related docs:

- Endpoint map: [fastapi-endpoint-map.md](fastapi-endpoint-map.md)

---

## 1) Installation & setup

### Install into n8n (community node)

1. In n8n, open **Settings → Community Nodes**.
2. Choose **Install**.
3. Install package: `n8n-nodes-kumiho`.
4. Restart n8n if required.

### Credentials (required)

All nodes use the same credential type: **Kumiho API**.

Credential fields:

- **Base URL**
  - Default: `https://api.kumiho.cloud`
  - This should be the origin for your Kumiho FastAPI instance (no trailing slash needed).
- **Service Token** (required)
  - Sent as the `X-Kumiho-Token` header.
  - You may paste either the raw token or a `Bearer <token>` string; the nodes normalize it.
- **Tenant ID (Optional)**
  - Sent as `x-tenant-id` when set.
  - If omitted, the request helper attempts to extract a tenant identifier from common JWT claims on the service token.
- **User Token (Optional)**
  - Sent as `Authorization: Bearer <userToken>`.
  - Must be a JWT (it must contain two dots). If you paste a user id / uid instead of an ID token, the node throws.

Credential test:

- n8n tests the credential using `GET {{$credentials.baseUrl}}/api/v1/tenant/whoami` with the configured headers.

---

## 2) Concepts used across nodes

### Kref patterns

Kumiho uses `kref://` URIs to identify objects.

Common examples:

- Project subtree: `kref://my-project/**`
- Space subtree: `kref://my-project/my-space/**`
- Item: `kref://my-project/my-space/my-item.model`
- Revision (query parameter): `kref://my-project/my-space/my-item.model?r=1`

### Path inputs (Project/Space paths)

Several node fields accept “paths” like `my-project` or `my-project/Assets`.

The nodes accept both:

- `my-project/my-space`
- `/my-project/my-space`

They normalize to a leading `/` internally.

Important restrictions for path fields (like **Space Path**, **Parent Path**, **Space Path** for item/bundle creation):

- Only URL-safe segments: letters, numbers, `.`, `_`, `-`
- No spaces

### Metadata inputs

The Action node accepts Metadata in multiple places (for Item/Revision/Artifact/Bundle/Graph operations).

Rules:

- Metadata must be a JSON object (dictionary).
- If you provide a string, it must be valid JSON.
- Non-string values are converted to strings.

Example metadata:

```json
{
  "source": "n8n",
  "shot": 120,
  "department": "layout"
}
```

### Output shaping for list/search operations

For list-like reads (List Projects, List Spaces, Item Search, List Artifacts, Get Artifacts by Location, Bundle Member/History list, List Edges), the node can shape output:

- **Output Format**
  - **Split Into Items** (default): emits one n8n item per entry.
  - **Single Item (Array)**: emits one n8n item whose JSON contains the full (or limited) array.
- **Return All**
  - When true: returns all results.
  - When false: applies **Limit**.

---

## 3) Node: Kumiho Event Trigger

This trigger connects to `GET /api/v1/events/stream` (Server-Sent Events / SSE) and emits one n8n item per matching event.

### Parameters

- **Trigger Type**: `Item | Revision | Artifact | Edge`
- **Stream Action**:
  - For most types: `Created | Updated | Deleted`
  - For `Revision`: `Created | Updated | Deleted | Tagged`
- **Path Filter (Project/Space)** (optional)
  - Limits events to a project/space subtree.
  - Examples:
    - `my-project`
    - `my-project/my-space`
    - `kref://my-project/my-space/**`
- **Item Name Filter** (optional)
  - Further limit events to a specific item name extracted from kref (e.g. `hero` from `hero.image`).
  - Supports `*` and `?` wildcards.
- **Item Kind Filter** (optional)
  - Further limit events to a specific item kind extracted from kref (e.g. `image` from `hero.image`).
  - Supports `*` and `?` wildcards.
- **Poll Interval (Seconds)**: used as a reconnect delay if the SSE connection drops.
- **Advanced → Cursor** (optional)
  - If set, starts streaming from this cursor.
  - If unset, the node resumes from the cursor persisted in workflow static data.
- **Advanced → Routing Key Name Filter** (optional)
  - A post-filter applied client-side.
  - For `Item`, it matches the **routing key name segment** (e.g. `image` in `item.image.created`, or `metadata` in `item.metadata.updated`).
  - Use this when you need to distinguish different `item.<name>.<action>` streams (e.g. `metadata` vs `image`).
  - For `Revision` + `Tagged`, it matches the tag name in event `details`.
  - For `Revision` + `Updated`, it matches the **routing key name segment** (e.g. `metadata` in `revision.metadata.updated`).
  - For `Artifact`, it matches the artifact name (from event `details` or `?a=` in kref).
  - Supports `*` and `?` wildcards, or you can provide a full routing key pattern like `item.*.created`.

### Filtering behavior (server-side + client-side)

The node requests events with:

- `routing_key_filter = "{triggerType}.{streamAction}"`
  - Item: uses `item.*.{streamAction}` for Created; uses `item.*.updated,item.metadata.updated` for Updated; uses `item.deleted,item.deprecated` for Deleted
  - Revision: uses `revision.*.updated,revision.metadata.updated` for Updated; uses `revision.deleted,revision.deprecated` for Deleted
  - Artifact: uses `artifact.*.updated,artifact.metadata.updated` for Updated; uses `artifact.deleted,artifact.deprecated` for Deleted
- `kref_filter` derived from **Path Filter**:
  - If you provide a simple path with no wildcards, it normalizes to `kref://.../**`.
  - If you include wildcards (`*` or `?`), the node assumes you know what you’re doing and passes it through.

Then it applies additional client-side safety checks to ensure subtree semantics and to apply the **Item Name/Kind** filters and **Routing Key Name Filter**.

### Output

Each emitted item is the raw event JSON (shape depends on the server). Common fields:

- `routing_key`
- `kref`
- `timestamp`
- `details`
- `cursor`

### Notes

- Some deployments may not emit `*.updated` events consistently. If you’re not seeing events, try `Created`, `Deleted`, or `Tagged` (for revisions).

---

## 4) Node: Kumiho Action

This is the main node for calling Kumiho’s REST-style endpoints. It follows the n8n pattern:

- **Resource**: `Project | Space | Item | Revision | Artifact | Bundle | Graph | Kref`
- **Operation**: `Create | Read | Update | Delete`

### General validation guardrails

- **Kref fields** used in Revision operations are validated to start with `kref://`.
  - Common paste issue `kref: //...` is normalized to `kref://...`.
- **Path fields** reject handlebars-like template strings (`{{ ... }}`); in n8n you should use expressions like `={{ $json.kref }}`.
- Several create operations are “idempotent-ish”:
  - Project Create: on transient 5xx, the node attempts `GET /api/v1/projects/{name}`.
  - Space Create: on transient 5xx, the node attempts `GET /api/v1/spaces/by-path?path=...`.
  - Bundle Create: on 409 conflict, it computes the bundle kref and attempts `GET /api/v1/bundles/by-kref?kref=...`.

### Resource reference

#### Project

**Create → Create Project**

- Inputs:
  - **Project Name** (required)
  - **Description** (optional)
- Request: `POST /api/v1/projects` body `{ name, description? }`
- Output: project JSON (with `metadata` stripped by the node)

**Read → Get Project**

- Inputs: **Project Name**
- Request: `GET /api/v1/projects/{name}`
- Output: project JSON (with `metadata` stripped)

**Read → List Projects**

- Inputs: Output shaping (**Output Format**, **Return All**, **Limit**)
- Request: `GET /api/v1/projects`
- Output: array, either split or single item

**Delete → Delete Project**

- Inputs:
  - **Project Name**
  - **Force Delete** (optional)
- Request: `DELETE /api/v1/projects/{name}?force=...`

#### Space

**Create → Create Space**

- Inputs:
  - **Parent Path** (required)
  - **Space Name** (required)
- Request: `POST /api/v1/spaces` body `{ parent_path, name }`

**Read → Get Space (by Path)**

- Inputs: **Space Path**
- Request: `GET /api/v1/spaces/by-path?path=...`

**Read → List Spaces**

- Inputs:
  - **Parent Path**
  - **Recursive** (optional)
  - Output shaping (Output Format / Return All / Limit)
- Request: `GET /api/v1/spaces?parent_path=...&recursive=...`
- Output: array

**Delete → Delete Space (by Path)**

- Inputs:
  - **Space Path**
  - **Force Delete** (optional)
- Request: `DELETE /api/v1/spaces/by-path?path=...&force=...`

#### Item

**Create → Create Item**

- Inputs:
  - **Space Path** (required)
  - **Item Name** (required)
  - **Kind** (required, default `model`)
  - **Metadata** (optional)
- Request: `POST /api/v1/items` body `{ space_path, item_name, kind, metadata }`

**Read → Get Item (by Kref)**

- Inputs: **Item Kref** (or the generic **Kref** field)
- Request: `GET /api/v1/items/by-kref?kref=...`

**Read → Get Item (by Name / Kind)**

- Inputs:
  - **Space Path**
  - **Item Name**
  - **Kind**
- Request: `GET /api/v1/items/by-path?space_path=...&item_name=...&kind=...`

**Read → Search Items**

- Inputs:
  - **Context Filter** (optional; supports wildcards; leading `/` is stripped)
  - **Name Filter** (optional)
  - **Kind Filter** (optional)
  - Output shaping
- Request: `GET /api/v1/items/search?context_filter=...&name_filter=...&kind_filter=...`
- Output: array

**Update → Update Metadata**

- Inputs: **Item Kref** (or generic **Kref**) + **Metadata**
- Request: `PATCH /api/v1/items/by-kref?kref=...` body `{ metadata }`

**Update → Set Attribute**

- Inputs: **Item Kref** + **Attribute Key** + **Attribute Value**
- Request: `POST /api/v1/attributes` body `{ kref, key, value }`

**Update → Deprecate**

- Inputs: **Item Kref** + **Deprecated** (boolean)
- Request: `POST /api/v1/items/deprecate?kref=...&deprecated=...`

**Delete → Delete Item**

- Inputs:
  - **Item Kref** (or generic **Kref**)
  - **Force Delete** (optional)
- Request: `DELETE /api/v1/items/by-kref?kref=...&force=...`

#### Revision

**Create → Create Revision**

- Inputs:
  - **Item Kref**
  - **Revision Number** (optional; non-negative integer)
  - **Metadata** (optional)
- Request: `POST /api/v1/revisions` body `{ item_kref, metadata, number? }`

**Create → Create Edge** (under Revision)

- Inputs:
  - **Source Kref** (must start with `kref://`)
  - **Target Kref** (must start with `kref://`)
  - **Edge Type**
  - **Metadata** (optional)
- Request: `POST /api/v1/graph/edges` body `{ source_kref, target_kref, edge_type, metadata }`

**Read → Get Revision (by Kref)**

- Inputs:
  - **Revision Kref** (or generic **Kref**)
  - **Revision Number** (optional; sent as `r`)
- Request: `GET /api/v1/revisions/by-kref?kref=...&r=...`

**Read → Get Revision (by Tag)**

- Inputs:
  - **Item Kref**
  - **Tag** (default `latest`; sent as `t`)
- Request: `GET /api/v1/revisions/by-kref?kref=...&t=...`

**Read → Get Revision (as of Timestamp)**

- Inputs:
  - **Item Kref** (required)
  - **Tag** (default `published`; the tag to query)
  - **Timestamp** (required; supports `YYYYMMDDHHMM` format like `202506011430` or ISO 8601 format like `2025-06-01T14:30:00Z`)
- Request: `GET /api/v1/revisions/as-of?item_kref=...&tag=...&time=...`
- Use case: Time-travel queries for reproducible builds and historical analysis. Answers questions like "What was the published revision on June 1st, 2025?"

**Read → List Revision Artifacts**

- Inputs:
  - **Revision Kref**
  - **Revision Number** (optional; sent as `r`)
  - Output shaping
- Request: `GET /api/v1/artifacts?revision_kref=...&r=...`

**Read → List Revision Tags**

- Inputs:
  - **Kref** (revision kref)
  - **Revision Number** (optional; sent as `r`)
- Request: `GET /api/v1/revisions/by-kref?kref=...&r=...`
- Output: revision JSON (contains tags in server-defined shape)

**Read → Has Tag**

- Inputs: **Item Kref** + **Tag**
- Request: `GET /api/v1/revisions/by-kref?kref=...&t=...`
- Output: `{ "has_tag": true|false, "tag": "..." }` (404 becomes `has_tag=false`)

**Read → Was Tagged**

- Inputs: **Kref** + **Tag** + optional **Revision Number**
- Request: `GET /api/v1/revisions/tags/history?kref=...&tag=...&r=...`

**Read → List Edges / Get Dependencies / Find Path / Analyze Impact**

- List Edges:
  - Inputs: **Revision Kref**, optional **Edge Type Filter**, **Direction**, output shaping
  - Request: `GET /api/v1/graph/edges?revision_kref=...&edge_type=...&direction=...`
- Get Dependencies:
  - Inputs: **Revision Kref**, **Max Depth**, optional **Edge Types** (comma-separated)
  - Request: `GET /api/v1/graph/dependencies?revision_kref=...&max_depth=...&edge_types[]=...`
- Find Path:
  - Inputs: **Source Kref**, **Target Kref**, **Max Depth**, optional **Edge Types**
  - Request: `GET /api/v1/graph/path?source_kref=...&target_kref=...&max_depth=...&edge_types[]=...`
- Analyze Impact:
  - Inputs: **Revision Kref**, **Max Depth**, optional **Edge Types**
  - Request: `GET /api/v1/graph/impact?revision_kref=...&max_depth=...&edge_types[]=...`

**Update → Update Metadata**

- Inputs: **Revision Kref** + **Metadata**
- Request: `PATCH /api/v1/revisions/by-kref?kref=...` body `{ metadata }`

**Update → Deprecate**

- Inputs: **Revision Kref** + **Deprecated**
- Request: `POST /api/v1/revisions/deprecate?kref=...&deprecated=...`

**Update → Set Tag / Remove Tag**

- Inputs: **Revision Kref** + **Revision Tag**
- Requests:
  - Set: `POST /api/v1/revisions/tags?kref=...` body `{ tag }`
  - Remove: `DELETE /api/v1/revisions/tags?kref=...&tag=...`

**Delete → Delete Revision**

- Inputs:
  - **Revision Kref**
  - **Force Delete** (optional)
- Request: `DELETE /api/v1/revisions/by-kref?kref=...&force=...`

#### Artifact

**Create → Create Artifact**

- Inputs:
  - **Revision Kref**
  - **Name**
  - **Location**
  - **Metadata** (optional)
- Request: `POST /api/v1/artifacts` body `{ revision_kref, name, location, metadata }`

**Read → Get Artifact (by Revision Kref + Name)**

- Inputs: **Revision Kref** + **Artifact Name**
- Request: `GET /api/v1/artifacts/by-kref?revision_kref=...&name=...`

**Read → Get Artifacts (by Location)**

- Inputs: **Location** + output shaping
- Request: `GET /api/v1/artifacts/by-location?location=...`

**Update → Update Metadata / Deprecate**

- Inputs: **Artifact Kref** + metadata or deprecated flag
- Requests:
  - Update metadata: `PATCH /api/v1/artifacts/by-kref?kref=...` body `{ metadata }`
  - Deprecate: `POST /api/v1/artifacts/deprecate?kref=...&deprecated=...`

**Delete → Delete Artifact**

- Inputs: **Artifact Kref** (+ optional Force Delete)
- Request: `DELETE /api/v1/artifacts/by-kref?kref=...&force=...`

#### Bundle

**Create → Create Bundle**

- Inputs: **Space Path** + **Bundle Name** + optional **Metadata**
- Request: `POST /api/v1/bundles` body `{ space_path, bundle_name, metadata }`

**Read → Get Bundle**

- Inputs:
  - **Space Path** + **Bundle Name**
  - Optional: **Advanced: Bundle Kref Override** + **Bundle Kref (Override)**
- Request: `GET /api/v1/bundles/by-kref?kref=...`

**Read → List Bundle Members**

- Inputs: **Bundle Kref**, optional **Member Revision Number**, output shaping
- Request: `GET /api/v1/bundles/members?bundle_kref=...&revision_number=...`

**Read → Bundle History**

- Inputs: **Bundle Kref**, output shaping
- Request: `GET /api/v1/bundles/history?bundle_kref=...`

**Update → Add Member / Remove Member**

- Inputs: **Bundle Kref** + **Item Kref** + optional **Metadata**
- Requests:
  - Add: `POST /api/v1/bundles/members/add` body `{ bundle_kref, item_kref, metadata }`
  - Remove: `POST /api/v1/bundles/members/remove` body `{ bundle_kref, item_kref, metadata }`

**Delete → Delete Bundle**

- Inputs: **Bundle Kref** (+ optional Force Delete)
- Request: `DELETE /api/v1/bundles/by-kref?kref=...&force=...`

#### Graph

This resource exposes the same graph operations as the Revision read modes.

- **Create → Create Edge**: `POST /api/v1/graph/edges`
- **Read → List Edges / Get Dependencies / Find Path / Analyze Impact**: `GET /api/v1/graph/*`

Note: Unlike the Revision “Create Edge” mode, Graph Create Edge does not validate `kref://` prefix. If you want strict validation, use Revision → Create → Create Edge.

#### Kref

**Read → Resolve**

- Inputs:
  - **Kref** (required)
  - Optional (if shown in your node UI):
    - **Resolve Revision Number** (`r`)
    - **Resolve Tag** (`t`)
    - **Resolve Artifact Name** (`a`)
  - Note: the execute logic supports `r/t/a`, but the optional fields are currently gated behind a legacy display mode in the node definition.
- Request: `GET /api/v1/resolve?kref=...&r=...&t=...&a=...`

---

## 5) MCP tools (use n8n native MCP Client)

This package does not ship a custom MCP client node anymore.

To let an n8n **AI Agent** (or a regular workflow) use Kumiho’s MCP tools, use n8n’s built-in **MCP Client** node:

- **Server URL**: `https://api.kumiho.cloud/api/v1/mcp/tools`
- **Transport**: HTTP Streamable
- **Headers**:
  - `X-Kumiho-Token: <service token>`
  - `x-tenant-id: <tenant id>` (only if your deployment requires it)

Once connected, n8n will discover the available MCP tools and make them available for tool-use.

---

## 6) Reliability, timeouts, retries

The request helper (`kumihoRequest`) adds:

- `x-correlation-id` (derived from the n8n execution id when available)
- `x-client: n8n-nodes-kumiho`
- `x-request-time`

Timeout and retry tuning (environment variables):

- `KUMIHO_N8N_REQUEST_TIMEOUT_MS` (default: `60000`)
- `KUMIHO_N8N_RETRY_BUDGET_MS` (default: `60000`)

If the retry budget is exceeded, the node throws an error with code `client_retry_budget_exceeded`.

Security note:

- The helper redacts `X-Kumiho-Token` and `Authorization` from errors surfaced to n8n UI/logs.

---

## 7) Troubleshooting

### “Missing Kumiho service token”

- Ensure the **Kumiho API** credential is selected on the node.
- Ensure **Service Token** is non-empty.

### “User Token must be a Firebase ID token (JWT), not a UID”

- The **User Token** field expects an ID token (JWT), not a user id.
- Either paste a real JWT, or leave **User Token** blank if you only need service-token endpoints.

### Tenant routing issues (401/403/404)

- If your service token does not include a tenant claim, set **Tenant ID (Optional)**.
- If you have multiple tenants, ensure the token is authorized for the tenant you’re targeting.

### Debugging request failures

When a request fails, errors typically include:

- HTTP status
- `correlation_id`
- Kumiho error `code`, `message`, and retryability when the upstream returns a standard error envelope

Use the `correlation_id` to find the request in server logs.
