# n8n-nodes-kumiho

This is an n8n community node for [Kumiho](https://kumiho.io), a graph-native Asset Operations System (revision ledger + lineage graph + events).

## Features

This package intentionally ships **two nodes** (there is no third “MCP node” in this package):

- **Kumiho Event Trigger**: Streaming (SSE) event trigger (created/updated/deleted/tagged) with cursor persistence.
- **Kumiho Action**: One node covering Projects, Spaces, Items, Revisions, Artifacts, Bundles, Graph, and Kref resolve.
 
MCP tools: use n8n's built-in **MCP Client** node pointed at Kumiho's MCP endpoint (works great with the **AI Agent** node).

For full operation-by-operation documentation, see the usage guide below.

## Documentation

- Detailed usage: [docs/usage.md](docs/usage.md)
- FastAPI endpoint map: [docs/fastapi-endpoint-map.md](docs/fastapi-endpoint-map.md)

## Installation

1. Go to **Settings > Community Nodes** in your n8n instance.
2. Click **Install a new node**.
3. Enter `n8n-nodes-kumiho`.
4. Click **Install**.

## Node Reference

- **Kumiho Event Trigger**: Trigger workflows from Kumiho events via SSE `GET /api/v1/events/stream`.
- **Kumiho Action**: Create/read/update/delete Kumiho resources via FastAPI routes under `/api/v1/*`.

### Event Trigger behavior (high-level)

- **Transport**: Server-Sent Events (SSE) stream.
- **Cursor persistence**: the node stores a cursor in workflow static data to resume after reconnects.
- **Filtering**: the node supports server-side filtering based on the configured Path Filter (Project/Space), plus optional client-side filtering for more specific matching.

For the full set of filters and their exact behavior, see [docs/usage.md](docs/usage.md).

### MCP tools (n8n native MCP Client)

- **Server URL**: `${BASE_URL}/api/v1/mcp/tools`
- **Auth headers**: send at least `X-Kumiho-Token: <service token>` (and `x-tenant-id` if your deployment requires it)

For parameters and examples, see [docs/usage.md](docs/usage.md).

### Using MCP tools with the AI Agent node

If you want an agentic workflow (LLM chooses which Kumiho tool to call), use n8n's **AI Agent** node with the built-in **MCP Client** as a tool.

1. Add an **AI Agent** node to your workflow.
2. Add an **MCP Client** node and configure it:
	- **Server URL**: `${BASE_URL}/api/v1/mcp/tools`
	- **Headers**: `X-Kumiho-Token: <service token>` (and `x-tenant-id` if required)
3. In the **AI Agent** node, add the **MCP Client** as an available tool.
4. Prompt the agent with the task you want, for example:
	- “Find the latest published revision for `kref://my-project/...` and summarize what changed.”
	- “Create a bundle for all revisions tagged `ready_for_release` under `my-project/my-space`.”

Tip: use **Kumiho Action** for deterministic CRUD steps (create/tag/search/etc.), and use **AI Agent + MCP Client** when you want tool selection and reasoning in one node.

## Testing the Event Trigger

To test the **Kumiho Event Trigger** node:

1. Add the **Kumiho Event Trigger** node to your workflow.
2. Set:
	- **Trigger Type**: `Revision`
	- **Stream Action**: `Created`
3. (Optional) Set **Path Filter (Project/Space)** to limit events (e.g. `my-project/my-space`).
4. Set **Reconnect Delay (Seconds)** to `10`.
5. Click **Listen for Event** in n8n.
6. In another window (or via CLI/SDK), create a new revision in Kumiho.
7. The node should emit an event with routing key `revision.created`.

## Troubleshooting

If events are not being caught:
- Ensure your **Base URL** in credentials points to the correct Kumiho API endpoint (e.g., `https://api.kumiho.cloud`).
- Check that the **Service Token** has permissions for the tenant you are working in.
- The node uses a **Cursor** to resume after reconnects. The cursor is stored in workflow static data, and can be overridden via **Advanced → Cursor**.

### Windows: `spawn npm ENOENT` when installing from UI

On some Windows setups, n8n's **Community Nodes** installer fails with:

`Error loading package "n8n-nodes-kumiho": spawn npm ENOENT`

This happens when the n8n process tries to spawn `npm` directly (Windows usually provides `npm.cmd`, which requires running via `cmd.exe /c`).

Workaround: manually install the package into your n8n user folder and restart n8n:

1. Create the community nodes folder:
	- PowerShell: `mkdir $env:USERPROFILE\.n8n\nodes -Force`
2. Install the package:
	- PowerShell: `cd $env:USERPROFILE\.n8n\nodes; cmd.exe /c npm i n8n-nodes-kumiho`
3. Restart n8n.

If you are running n8n via `npx`, make sure you restart the running process after installing.

### Error handling (production)

- All API calls send `x-correlation-id`.
	- When a request fails, the node includes the correlation id (when available) in the error message to simplify support/debugging.
- When the upstream API returns a standard error envelope, the node maps it into the thrown error:
	- `error.code` (string)
	- `error.message` (string)
	- `error.retryable` (boolean)
	- `error.retry_after_ms` (number, optional)
	- `correlation_id` (string)

Security note: the node redacts the `X-Kumiho-Token` header from thrown errors so secrets don't leak into n8n logs or UI error details.

### Timeouts and retry budget

The request helper enforces a per-request timeout and an overall retry budget.

- `KUMIHO_N8N_REQUEST_TIMEOUT_MS` (default: 60000)
- `KUMIHO_N8N_RETRY_BUDGET_MS` (default: 60000)

If the retry budget is exceeded, the node throws an error with code `client_retry_budget_exceeded`.

### Idempotency

For write operations, the nodes may send `x-idempotency-key`. Some create endpoints intentionally disable idempotency headers for compatibility; where idempotency is not used, retries rely on server-side uniqueness and/or client-side GET fallback.

## Version History

- **0.2.0**: Switched Event Trigger to SSE streaming.
- **0.1.3**: Fixed `emit` call format and trigger activation race condition.
- **0.1.2**: Fixed event polling timeout and client initialization. Added cursor persistence.
- **0.1.1**: Fixed UI labels for node properties.
- **0.1.0**: Initial release.
