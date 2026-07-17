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
  EngageResult,
  KumihoLLMConfig,
  MemoryEntry,
  MemoryStoreResult,
  MemoryType,
  ReflectCapture,
  ReflectResult,
  ResolvedConfig,
  WorkingMemoryState,
  DreamStateStats,
} from "./types.js";
import { McpBridge, type McpToolDefinition } from "./mcp-bridge.js";

function coerceString(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function coerceMemoryType(value: unknown): MemoryType {
  switch (value) {
    case "summary":
    case "fact":
    case "decision":
    case "action":
    case "error":
      return value;
    default:
      return "summary";
  }
}

function parseTopics(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((topic) => coerceString(topic).trim())
      .filter(Boolean);
  }

  if (typeof value !== "string") return [];

  const trimmed = value.trim();
  if (!trimmed) return [];

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (Array.isArray(parsed)) {
      return parsed
        .map((topic) => coerceString(topic).trim())
        .filter(Boolean);
    }
  } catch {
    // Fall back to the legacy comma-delimited metadata format.
  }

  return trimmed
    .split(",")
    .map((topic) => topic.trim())
    .filter(Boolean);
}

function coerceMetadata(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function mapMemoryEntry(
  value: Record<string, unknown>,
  fallback?: { kref?: string; score?: number; space?: string },
): MemoryEntry {
  const metadata = coerceMetadata(value.metadata);
  const space =
    coerceString(value.space) ||
    coerceString(metadata.space) ||
    fallback?.space;
  return {
    kref: coerceString(value.kref) || fallback?.kref || "",
    type: coerceMemoryType(value.type ?? metadata.type),
    title: coerceString(value.title) || coerceString(metadata.title),
    summary: coerceString(value.summary) || coerceString(metadata.summary),
    topics: parseTopics(value.topics ?? metadata.topics),
    score: typeof value.score === "number" ? value.score : fallback?.score,
    timestamp: coerceString(value.timestamp) || coerceString(value.created_at),
    space,
    metadata,
  };
}

/** Memory tools return revision krefs (`...?r=N`); item-level ops need the item kref. */
function toItemKref(kref: string): string {
  return kref.split("?")[0];
}

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

/**
 * True when the error indicates the backend does not expose the tool at all
 * (pre-composite kumiho-memory or a cloud API without the tool). Used to
 * fall back from engage/reflect to the legacy retrieve/store path.
 */
export function isUnknownToolError(err: unknown): boolean {
  const message = (err as Error)?.message?.toLowerCase() ?? "";
  return (
    message.includes("unknown tool") ||
    message.includes("tool not found") ||
    message.includes("method not found")
  );
}

// ---------------------------------------------------------------------------
// Transport interface
// ---------------------------------------------------------------------------

/**
 * Minimal interface that both HTTP and MCP transports implement.
 * call() sends a tool invocation and returns the parsed result.
 */
export interface Transport {
  call<T>(tool: string, params: Record<string, unknown>, timeoutMs?: number): Promise<T>;
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

  async call<T>(tool: string, params: Record<string, unknown>, timeoutMs?: number): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs ?? this.timeout);

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

    // Self-hosted CE: point the Python SDK at the local kumiho-server and
    // run tokenless. A cached cloud token or inherited endpoint would flip
    // the SDK back to control-plane discovery, so both are cleared here.
    // Applied at construction so the standalone createKumihoMemory() path
    // gets CE routing too, not just the plugin's ensureRuntimeStarted().
    if (config.ce.enabled) {
      this.bridge.addEnv({
        KUMIHO_LOCAL_SERVER_ENDPOINT: config.ce.endpoint,
        KUMIHO_AUTH_TOKEN: "",
        KUMIHO_SERVER_ENDPOINT: "",
        KUMIHO_SERVER_ADDRESS: "",
        UPSTASH_REDIS_URL: config.ce.redisUrl,
      });
    }
  }

  /** Inject env vars before the subprocess is spawned. Must be called before start(). */
  addEnv(vars: Record<string, string>): void {
    this.bridge.addEnv(vars);
  }

  async start(): Promise<void> {
    await this.bridge.start();
  }

  async close(): Promise<void> {
    await this.bridge.close();
  }

  async call<T>(tool: string, params: Record<string, unknown>, timeoutMs?: number): Promise<T> {
    return this.bridge.callTool<T>(tool, params, timeoutMs);
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
    if (!content?.trim()) return; // skip empty messages
    await this.transport.call("kumiho_chat_add", {
      project: this.project,
      session_id: sessionId,
      role,
      message: content.trim(),
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

  async consolidateSession(sessionId: string): Promise<{
    success: boolean;
    summary?: string;
    error?: string;
    store_result?: MemoryStoreResult;
  }> {
    return this.transport.call<{
      success: boolean;
      summary?: string;
      error?: string;
      store_result?: MemoryStoreResult;
    }>(
      "kumiho_memory_consolidate",
      { session_id: sessionId },
      5 * 60 * 1000,
    );
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
    // kumiho_memory_store silently drops unknown args: the type must be
    // sent as `memory_type`, and there is no `topics` field — topics are
    // folded into tags (searchable) and metadata.topics (comma-delimited,
    // the format mapMemoryEntry reads back into MemoryEntry.topics).
    // The server defaults absent tags to ["published"]; the fold must not
    // suppress that default for topics-only callers. Topic tags must come
    // BEFORE "published" — the server freezes a revision the moment
    // "published" lands and silently rejects every later tag.
    const tags = params.topics?.length
      ? [...params.topics, ...(params.tags ?? ["published"])]
      : params.tags;
    const metadata = params.topics?.length
      ? { ...params.metadata, topics: params.topics.join(",") }
      : params.metadata;
    return this.transport.call<MemoryStoreResult>("kumiho_memory_store", {
      project: this.project,
      space_hint: params.spaceHint,
      user_text: params.userText,
      assistant_text: params.assistantText,
      memory_type: params.type,
      title: params.title,
      summary: params.summary,
      artifact_location: params.artifactLocation,
      metadata,
      tags,
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
      item_krefs?: string[];
      revision_krefs?: string[];
      spaces_used?: string[];
      scores?: number[];
      results?: Array<Record<string, unknown>>;
    }>("kumiho_memory_retrieve", {
      project: this.project,
      query: params.query,
      limit: params.limit,
      space_paths: params.spacePaths,
      memory_types: params.memoryTypes,
    });

    if (Array.isArray(raw.results) && raw.results.length > 0) {
      return raw.results
        .map((result) => mapMemoryEntry(result))
        .filter((entry) => Boolean(entry.kref));
    }

    // Resolve each revision_kref via getRevision() which maps metadata
    // fields (title, summary, type) into MemoryEntry shape.
    const krefs = raw.revision_krefs ?? [];
    if (krefs.length === 0) return [];

    const entries = await Promise.all(
      krefs.map((kref, i) =>
        this.getRevision(kref)
          .then((entry) => ({
            ...entry,
            score: raw.scores?.[i],
            space: entry.space || raw.spaces_used?.[i],
          }))
          .catch(() => ({
            kref,
            type: "summary" as MemoryType,
            title: "",
            summary: "",
            topics: [] as string[],
            space: raw.spaces_used?.[i],
            score: raw.scores?.[i],
          })),
      ),
    );

    return entries;
  }

  // -----------------------------------------------------------------------
  // Composite two-reflex tools (engage / reflect)
  // -----------------------------------------------------------------------

  /**
   * Engage memory before responding — recall + context building in one call.
   *
   * The backend deduplicates identical queries within a short window and
   * returns `deduplicated: true` with empty results; callers should treat
   * that as "already recalled this turn", not as a miss.
   *
   * Note: the backend composite tools operate on the server's default
   * project (CognitiveMemory) — there is no per-call project parameter.
   */
  async memoryEngage(params: {
    query: string;
    limit?: number;
    spacePaths?: string[];
    memoryTypes?: string[];
    minScore?: number;
    graphAugmented?: boolean;
  }): Promise<EngageResult> {
    const raw = await this.transport.call<{
      context?: string;
      results?: Array<Record<string, unknown>>;
      source_krefs?: string[];
      deduplicated?: boolean;
    }>("kumiho_memory_engage", {
      query: params.query,
      limit: params.limit,
      space_paths: params.spacePaths,
      memory_types: params.memoryTypes,
      min_score: params.minScore,
      graph_augmented: params.graphAugmented,
    });

    const results = (raw.results ?? [])
      .map((entry) => mapMemoryEntry(entry))
      .filter((entry) => Boolean(entry.kref));

    return {
      context: coerceString(raw.context),
      results,
      sourceKrefs: Array.isArray(raw.source_krefs)
        ? raw.source_krefs.filter((k): k is string => typeof k === "string" && k.length > 0)
        : results.map((entry) => entry.kref),
      deduplicated: raw.deduplicated === true,
    };
  }

  /**
   * Reflect after responding — buffer the assistant response and store
   * structured captures with DERIVED_FROM provenance edges in one call.
   *
   * `response` must be non-empty (the backend rejects empty buffer writes).
   * Capture stores + edge discovery can be slow, so this uses a 2-minute
   * timeout instead of the default 30s.
   */
  async memoryReflect(params: {
    sessionId: string;
    response: string;
    captures?: ReflectCapture[];
    sourceKrefs?: string[];
    spacePath?: string;
    discoverEdges?: boolean;
  }): Promise<ReflectResult> {
    return this.transport.call<ReflectResult>(
      "kumiho_memory_reflect",
      {
        session_id: params.sessionId,
        response: params.response,
        captures: params.captures?.map((cap) => ({
          type: cap.type,
          title: cap.title,
          content: cap.content,
          tags: cap.tags,
          space_hint: cap.spaceHint,
          event_date: cap.eventDate,
        })),
        source_krefs: params.sourceKrefs,
        space_path: params.spacePath,
        discover_edges: params.discoverEdges,
      },
      2 * 60 * 1000,
    );
  }

  // -----------------------------------------------------------------------
  // Revision details
  // -----------------------------------------------------------------------

  /**
   * Fetch a revision and map it to a MemoryEntry.
   *
   * The raw MCP revision stores title/summary/type in its `metadata` dict,
   * so we extract those into top-level MemoryEntry fields.
   *
   * When `includeArtifact` is true, also fetches the default artifact's
   * content (file location) and attaches it as `artifactLocation`.
   */
  async getRevision(kref: string, includeArtifact = false): Promise<MemoryEntry> {
    const raw = await this.transport.call<{
      kref: string;
      item_kref: string;
      metadata?: Record<string, unknown>;
      tags?: string[];
      created_at?: string;
      default_artifact?: string;
    }>("kumiho_get_revision", { kref });

    const entry = mapMemoryEntry(
      {
        kref: raw.kref,
        created_at: raw.created_at,
        metadata: raw.metadata,
      },
      { kref: raw.kref },
    );

    if (includeArtifact && raw.default_artifact) {
      try {
        const artifactKref = `${raw.kref}&a=${raw.default_artifact}`;
        const artifact = await this.transport.call<{
          location?: string;
          metadata?: Record<string, string>;
        }>("kumiho_get_artifact", { artifact_kref: artifactKref });
        if (artifact.location) {
          entry.metadata = { ...entry.metadata, artifact_location: artifact.location };
        }
      } catch {
        // Artifact fetch is best-effort
      }
    }

    return entry;
  }

  async getRevisions(krefs: string[], includeArtifact = false): Promise<MemoryEntry[]> {
    return Promise.all(krefs.map((k) => this.getRevision(k, includeArtifact)));
  }

  // -----------------------------------------------------------------------
  // Memory management
  // -----------------------------------------------------------------------

  async memoryDelete(kref: string): Promise<void> {
    // force: stored memories always have at least one revision.
    await this.transport.call("kumiho_delete_item", {
      item_kref: toItemKref(kref),
      force: true,
    });
  }

  async memoryDeprecate(kref: string): Promise<void> {
    await this.transport.call("kumiho_deprecate_item", {
      item_kref: toItemKref(kref),
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
      userText: params.stdout ?? "",
      assistantText: params.stderr ?? "",
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
          "X-Kumiho-Token": apiKey,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: params.title,
          content: params.content,
          kind: params.kind,
          creative_project: params.creativeProject,
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

      const raw = (await res.json()) as Record<string, unknown>;
      return {
        queued: raw.queued,
        jobId: (raw.job_id ?? raw.jobId) as string,
        message: raw.message,
      } as CreativeCaptureResult;
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
  // Creative job status
  // -----------------------------------------------------------------------

  async getCreativeJobStatus(
    jobId: string,
    bffUrl: string,
    apiKey: string,
  ): Promise<{ job_id: string; status: string; result?: Record<string, unknown>; error?: string }> {
    const url = `${bffUrl.replace(/\/+$/, "")}/api/v1/apps/creative/jobs/${encodeURIComponent(jobId)}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10_000);

    try {
      const res = await fetch(url, {
        method: "GET",
        headers: { "X-Kumiho-Token": apiKey },
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new KumihoApiError(
          `Creative job status error: ${res.status} ${body}`,
          "BFF_ERROR",
          res.status,
        );
      }

      return (await res.json()) as {
        job_id: string;
        status: string;
        result?: Record<string, unknown>;
        error?: string;
      };
    } catch (err) {
      if (err instanceof KumihoApiError) throw err;
      if ((err as Error).name === "AbortError") {
        throw new KumihoApiError("Creative job status timeout", "TIMEOUT", 0);
      }
      throw new KumihoApiError(
        `Creative job status network error: ${(err as Error).message}`,
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

  async triggerDreamState(modelConfig?: Pick<KumihoLLMConfig, "provider" | "model" | "apiKey" | "baseUrl">): Promise<DreamStateStats> {
    const params: Record<string, unknown> = { project: this.project };
    if (modelConfig?.provider) params.provider = modelConfig.provider;
    if (modelConfig?.model) params.model = modelConfig.model;
    if (modelConfig?.apiKey) params.api_key = modelConfig.apiKey;
    if (modelConfig?.baseUrl) params.base_url = modelConfig.baseUrl;
    // Dream State can take minutes (event collection + LLM assessment).
    // Use a 5-minute timeout instead of the default 30s.
    return this.transport.call<DreamStateStats>(
      "kumiho_memory_dream_state",
      params,
      5 * 60 * 1000,
    );
  }

  // -----------------------------------------------------------------------
  // Health check
  // -----------------------------------------------------------------------

  async ping(): Promise<boolean> {
    return this.transport.ping();
  }
}
