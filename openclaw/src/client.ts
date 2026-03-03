/**
 * Kumiho client with pluggable transport.
 *
 * Two transports:
 *   - HttpTransport  → HTTPS calls to Kumiho Cloud (cloud mode)
 *   - McpTransport   → JSON-RPC over stdin/stdout to kumiho-mcp (local mode)
 *
 * Every public method on KumihoClient delegates to this.transport.call(),
 * so all higher-level code (tools, hooks) works identically in both modes.
 */

import type {
  ChatMessage,
  CreativeCaptureParams,
  CreativeCaptureResult,
  MemoryEntry,
  MemoryStoreResult,
  MemoryType,
  ResolvedConfig,
  WorkingMemoryState,
  DreamStateStats,
} from "./types.js";
import { McpBridge, type McpToolDefinition } from "./mcp-bridge.js";

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export class KumihoApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "KumihoApiError";
  }
}

// ---------------------------------------------------------------------------
// Transport interface
// ---------------------------------------------------------------------------

/**
 * Minimal interface that both HTTP and MCP transports implement.
 * call() sends a tool invocation and returns the parsed result.
 */
export interface Transport {
  call<T>(tool: string, params: Record<string, unknown>): Promise<T>;
  start?(): Promise<void>;
  close?(): Promise<void>;
  ping(): Promise<boolean>;
  /** Return MCP-discovered tool definitions (available after start). */
  getDiscoveredTools?(): McpToolDefinition[];
}

// ---------------------------------------------------------------------------
// HTTP transport (cloud mode)
// ---------------------------------------------------------------------------

export class HttpTransport implements Transport {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeout: number;

  constructor(config: ResolvedConfig) {
    this.baseUrl = config.endpoint.replace(/\/+$/, "");
    this.apiKey = config.apiKey;
    this.timeout = 30_000;
  }

  async call<T>(tool: string, params: Record<string, unknown>): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(`${this.baseUrl}/api/v1/mcp/tools`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ tool, arguments: params }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => "");
        const code =
          res.status === 401
            ? "UNAUTHORIZED"
            : res.status === 429
              ? "RATE_LIMIT"
              : "API_ERROR";
        throw new KumihoApiError(
          `Kumiho API ${tool} failed: ${res.status} ${body}`,
          code,
          res.status,
        );
      }

      return (await res.json()) as T;
    } catch (err) {
      if (err instanceof KumihoApiError) throw err;
      if ((err as Error).name === "AbortError") {
        throw new KumihoApiError("Kumiho API timeout", "TIMEOUT", 0);
      }
      throw new KumihoApiError(
        `Kumiho API network error: ${(err as Error).message}`,
        "NETWORK_ERROR",
        0,
      );
    } finally {
      clearTimeout(timer);
    }
  }

  async ping(): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5_000);
      try {
        const res = await fetch(`${this.baseUrl}/health`, {
          headers: { Authorization: `Bearer ${this.apiKey}` },
          signal: controller.signal,
        });
        return res.ok;
      } finally {
        clearTimeout(timer);
      }
    } catch {
      return false;
    }
  }
}

// ---------------------------------------------------------------------------
// MCP stdio transport (local mode)
// ---------------------------------------------------------------------------

export class McpTransport implements Transport {
  private bridge: McpBridge;

  constructor(
    config: ResolvedConfig,
    logger?: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void },
  ) {
    this.bridge = new McpBridge({
      pythonPath: config.local.pythonPath,
      command: config.local.command,
      args: config.local.args,
      env: config.local.env,
      cwd: config.local.cwd,
      timeout: config.local.timeout,
      logger,
    });
  }

  async start(): Promise<void> {
    await this.bridge.start();
  }

  async close(): Promise<void> {
    await this.bridge.close();
  }

  async call<T>(tool: string, params: Record<string, unknown>): Promise<T> {
    return this.bridge.callTool<T>(tool, params);
  }

  async ping(): Promise<boolean> {
    return this.bridge.isRunning;
  }

  /** Expose discovered tools from the Python MCP server. */
  get discoveredTools() {
    return this.bridge.tools;
  }

  getDiscoveredTools(): McpToolDefinition[] {
    return this.bridge.tools;
  }
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createTransport(
  config: ResolvedConfig,
  logger?: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void },
): Transport {
  if (config.mode === "local") {
    return new McpTransport(config, logger);
  }
  return new HttpTransport(config);
}

// ---------------------------------------------------------------------------
// Client (unchanged public API — delegates to transport)
// ---------------------------------------------------------------------------

export class KumihoClient {
  private readonly transport: Transport;
  private readonly project: string;

  constructor(transport: Transport, project: string) {
    this.transport = transport;
    this.project = project;
  }

  /** Start the underlying transport (needed for local/MCP mode). */
  async start(): Promise<void> {
    await this.transport.start?.();
  }

  /** Close the underlying transport. */
  async close(): Promise<void> {
    await this.transport.close?.();
  }

  // -----------------------------------------------------------------------
  // Generic tool invocation (pass-through to any MCP tool)
  // -----------------------------------------------------------------------

  /** Invoke any MCP tool by name. Used for pass-through asset management tools. */
  async callTool<T = unknown>(name: string, params: Record<string, unknown>): Promise<T> {
    return this.transport.call<T>(name, params);
  }

  /** Return tool definitions discovered from the MCP backend (after start). */
  getDiscoveredTools(): McpToolDefinition[] {
    return this.transport.getDiscoveredTools?.() ?? [];
  }

  // -----------------------------------------------------------------------
  // Working memory (Redis-backed short-term buffer)
  // -----------------------------------------------------------------------

  async chatAdd(
    sessionId: string,
    role: ChatMessage["role"],
    content: string,
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    await this.transport.call("kumiho_chat_add", {
      project: this.project,
      session_id: sessionId,
      role,
      message: content,
      metadata,
    });
  }

  async chatGet(sessionId: string, limit = 20): Promise<WorkingMemoryState> {
    return this.transport.call<WorkingMemoryState>("kumiho_chat_get", {
      project: this.project,
      session_id: sessionId,
      limit,
    });
  }

  async chatClear(sessionId: string): Promise<void> {
    await this.transport.call("kumiho_chat_clear", {
      project: this.project,
      session_id: sessionId,
    });
  }

  // -----------------------------------------------------------------------
  // Long-term memory storage
  // -----------------------------------------------------------------------

  async memoryStore(params: {
    spaceHint?: string;
    userText?: string;
    assistantText?: string;
    type: MemoryType;
    title: string;
    summary: string;
    topics?: string[];
    artifactLocation?: string;
    metadata?: Record<string, unknown>;
    tags?: string[];
    bundleName?: string;
    sourceRevisionKrefs?: string[];
  }): Promise<MemoryStoreResult> {
    return this.transport.call<MemoryStoreResult>("kumiho_memory_store", {
      project: this.project,
      space_hint: params.spaceHint,
      user_text: params.userText,
      assistant_text: params.assistantText,
      type: params.type,
      title: params.title,
      summary: params.summary,
      topics: params.topics,
      artifact_location: params.artifactLocation,
      metadata: params.metadata,
      tags: params.tags,
      bundle_name: params.bundleName,
      source_revision_krefs: params.sourceRevisionKrefs,
    });
  }

  // -----------------------------------------------------------------------
  // Long-term memory retrieval
  // -----------------------------------------------------------------------

  async memoryRetrieve(params: {
    query: string;
    limit?: number;
    spacePaths?: string[];
    memoryTypes?: MemoryType[];
  }): Promise<MemoryEntry[]> {
    const raw = await this.transport.call<{
      item_krefs: string[];
      revision_krefs: string[];
      spaces_used: string[];
      revisions?: MemoryEntry[];
    }>("kumiho_memory_retrieve", {
      project: this.project,
      query: params.query,
      limit: params.limit,
      space_paths: params.spacePaths,
      memory_types: params.memoryTypes,
    });

    if (raw.revisions) return raw.revisions;

    return (raw.revision_krefs ?? []).map((kref, i) => ({
      kref,
      type: "summary" as MemoryType,
      title: "",
      summary: "",
      topics: [],
      space: raw.spaces_used[i],
    }));
  }

  // -----------------------------------------------------------------------
  // Revision details
  // -----------------------------------------------------------------------

  async getRevision(kref: string): Promise<MemoryEntry> {
    return this.transport.call<MemoryEntry>("kumiho_get_revision", { kref });
  }

  async getRevisions(krefs: string[]): Promise<MemoryEntry[]> {
    return Promise.all(krefs.map((k) => this.getRevision(k)));
  }

  // -----------------------------------------------------------------------
  // Memory management
  // -----------------------------------------------------------------------

  async memoryDelete(kref: string): Promise<void> {
    await this.transport.call("kumiho_memory_delete", {
      project: this.project,
      kref,
    });
  }

  async memoryDeprecate(kref: string): Promise<void> {
    await this.transport.call("kumiho_memory_deprecate", {
      project: this.project,
      kref,
      deprecated: true,
    });
  }

  // -----------------------------------------------------------------------
  // Tool execution memory
  // -----------------------------------------------------------------------

  async storeToolExecution(params: {
    task: string;
    status: string;
    exitCode?: number;
    durationMs?: number;
    stdout?: string;
    stderr?: string;
    tools?: string[];
    topics?: string[];
    spaceHint?: string;
    openQuestions?: string[];
  }): Promise<MemoryStoreResult> {
    const isError =
      ["failed", "error", "blocked"].includes(params.status) ||
      (params.exitCode != null && params.exitCode !== 0);

    return this.memoryStore({
      type: isError ? "error" : "action",
      title: `${isError ? "Failed" : "Completed"}: ${params.task}`,
      summary: isError
        ? `Task "${params.task}" failed (exit ${params.exitCode ?? "N/A"}): ${params.stderr?.slice(0, 200) ?? "unknown error"}`
        : `Successfully executed: ${params.task}`,
      topics: params.topics ?? [],
      spaceHint: params.spaceHint,
      tags: [isError ? "error" : "action", params.status, "published"],
      metadata: {
        task: params.task,
        status: params.status,
        exit_code: params.exitCode,
        duration_ms: params.durationMs,
        tools: params.tools,
        open_questions: params.openQuestions,
      },
    });
  }

  // -----------------------------------------------------------------------
  // Creative Memory (BFF async pipeline)
  // -----------------------------------------------------------------------

  /**
   * Fire a creative capture job to the kumiho-FastAPI BFF.
   *
   * Returns immediately with a job ID — the BFF runs the 7-step graph
   * pipeline (ensureSpace → createItem → createRevision → createArtifact →
   * createEdge → memoryStore → discoverEdges) as a BackgroundTask so the
   * agent turn is never blocked.
   *
   * @param params   Creative capture request
   * @param bffUrl   BFF base URL (e.g. "https://api.kumiho.cloud" or "http://localhost:8000")
   * @param apiKey   Bearer token forwarded to the BFF
   */
  async creativeEnqueue(
    params: CreativeCaptureParams,
    bffUrl: string,
    apiKey: string,
  ): Promise<CreativeCaptureResult> {
    const url = `${bffUrl.replace(/\/+$/, "")}/api/v1/apps/creative/capture`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10_000);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: params.title,
          content: params.content,
          kind: params.kind,
          project: params.project,
          tags: params.tags ?? [],
          source_memory_kref: params.sourceMemoryKref,
          metadata: params.metadata ?? {},
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new KumihoApiError(
          `Creative capture BFF error: ${res.status} ${body}`,
          "BFF_ERROR",
          res.status,
        );
      }

      return (await res.json()) as CreativeCaptureResult;
    } catch (err) {
      if (err instanceof KumihoApiError) throw err;
      if ((err as Error).name === "AbortError") {
        throw new KumihoApiError("Creative capture BFF timeout", "TIMEOUT", 0);
      }
      throw new KumihoApiError(
        `Creative capture BFF network error: ${(err as Error).message}`,
        "NETWORK_ERROR",
        0,
      );
    } finally {
      clearTimeout(timer);
    }
  }

  // -----------------------------------------------------------------------
  // Dream State
  // -----------------------------------------------------------------------

  async triggerDreamState(modelConfig?: {
    provider?: string;
    model?: string;
    apiKey?: string;
  }): Promise<DreamStateStats> {
    const params: Record<string, unknown> = { project: this.project };
    if (modelConfig?.provider) params.provider = modelConfig.provider;
    if (modelConfig?.model) params.model = modelConfig.model;
    if (modelConfig?.apiKey) params.api_key = modelConfig.apiKey;
    return this.transport.call<DreamStateStats>("kumiho_memory_dream_state", params);
  }

  // -----------------------------------------------------------------------
  // Health check
  // -----------------------------------------------------------------------

  async ping(): Promise<boolean> {
    return this.transport.ping();
  }
}
