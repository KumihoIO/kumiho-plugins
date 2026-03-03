import { EventEmitter } from 'node:events';

/**
 * Type definitions for the Kumiho OpenClaw memory plugin.
 *
 * Two operating modes:
 *   - "cloud": HTTPS calls to Kumiho Cloud API (requires apiKey)
 *   - "local": Spawns kumiho-mcp Python process over stdio (requires pip install)
 *
 * In both modes raw conversations stay local in OpenClaw; only structured
 * summaries travel to the graph database.
 */
interface KumihoLLMConfig {
    provider?: "openai" | "anthropic";
    model?: string;
    apiKey?: string;
}
interface KumihoPrivacyConfig {
    /** Only send structured summaries, never raw text (default: true) */
    uploadSummariesOnly?: boolean;
    /** Keep raw chats and media on local filesystem (default: true) */
    localArtifacts?: boolean;
    /** Store voice/image transcription text in Kumiho (default: true) */
    storeTranscriptions?: boolean;
}
/** Configuration for local mode (MCP stdio bridge). */
interface KumihoLocalConfig {
    /** Python executable path. Default: "python" */
    pythonPath?: string;
    /**
     * MCP server command or Python module to spawn.
     * Default: "kumiho-mcp"
     *
     * Examples:
     *   "kumiho-mcp"              → runs the `kumiho-mcp` CLI entry point
     *   "kumiho.mcp_server"       → runs `python -m kumiho.mcp_server`
     *   "/path/to/venv/bin/kumiho-mcp" → explicit venv path
     */
    command?: string;
    /** Extra CLI args passed to the MCP server. */
    args?: string[];
    /** Extra environment variables for the child process. */
    env?: Record<string, string>;
    /** Working directory for the Python process. */
    cwd?: string;
    /** Request timeout in milliseconds (default: 30000). */
    timeout?: number;
}
interface KumihoPluginConfig {
    /**
     * Operating mode.
     *   "cloud" → HTTPS to Kumiho Cloud (needs apiKey)
     *   "local" → Spawn kumiho-mcp Python subprocess (needs pip install)
     * Default: "local"
     */
    mode?: "cloud" | "local";
    /** Kumiho Cloud API token (kh_live_... or kh_test_...). Required for cloud mode. */
    apiKey?: string;
    /** Kumiho Cloud API endpoint (cloud mode only) */
    endpoint?: string;
    /**
     * BFF (kumiho-FastAPI) base URL for creative capture pipeline.
     * Cloud mode default: same as endpoint.
     * Local mode default: http://localhost:8000
     */
    bffEndpoint?: string;
    /** Kumiho project name */
    project?: string;
    /** Stable user identity for cross-channel sessions */
    userId?: string;
    /** Auto-extract and store facts after each agent turn */
    autoCapture?: boolean;
    /** Auto-inject relevant memories before each agent turn */
    autoRecall?: boolean;
    /** Summarize locally before uploading */
    localSummarization?: boolean;
    /** Messages before auto-consolidation */
    consolidationThreshold?: number;
    /**
     * Seconds of inactivity before triggering consolidation automatically.
     * Resets on each user message. Set to 0 to disable. Default: 300 (5 min).
     */
    idleConsolidationTimeout?: number;
    /** Working memory TTL in seconds */
    sessionTtl?: number;
    /** Max memories per recall */
    topK?: number;
    /** Min similarity for recall (0-1) */
    searchThreshold?: number;
    /** Local artifact storage path */
    artifactDir?: string;
    /** Redact PII before upload */
    piiRedaction?: boolean;
    /**
     * Cron expression for Dream State schedule (e.g. "0 3 * * *").
     * Auto-loaded from ~/.kumiho/preferences.json if not set here.
     * Set to "off" or omit to disable.
     */
    dreamStateSchedule?: string;
    /**
     * LLM model for Dream State (memory classification/enrichment).
     * Lightweight model recommended — defaults to agent model if not set.
     */
    dreamStateModel?: KumihoLLMConfig;
    /**
     * LLM model for session consolidation (conversation summarization).
     * Smarter model recommended for richer summaries — defaults to agent model.
     */
    consolidationModel?: KumihoLLMConfig;
    /** LLM configuration for local summarization */
    llm?: KumihoLLMConfig;
    /** Privacy settings */
    privacy?: KumihoPrivacyConfig;
    /** Local mode settings (MCP stdio bridge) */
    local?: KumihoLocalConfig;
}
/** Resolved config with defaults applied. */
interface ResolvedConfig {
    mode: "cloud" | "local";
    apiKey: string;
    endpoint: string;
    /** BFF base URL for creative capture pipeline. */
    bffEndpoint: string;
    project: string;
    userId: string;
    autoCapture: boolean;
    autoRecall: boolean;
    localSummarization: boolean;
    consolidationThreshold: number;
    /** Seconds of inactivity before idle consolidation fires. 0 = disabled. */
    idleConsolidationTimeout: number;
    sessionTtl: number;
    topK: number;
    searchThreshold: number;
    artifactDir: string;
    piiRedaction: boolean;
    /** Cron expression for Dream State. "off" or empty = disabled. */
    dreamStateSchedule: string;
    /** LLM for Dream State runs. Empty provider = use agent default. */
    dreamStateModel: KumihoLLMConfig;
    /** LLM for session consolidation. Empty provider = use agent default. */
    consolidationModel: KumihoLLMConfig;
    llm: KumihoLLMConfig;
    privacy: Required<KumihoPrivacyConfig>;
    local: Required<Pick<KumihoLocalConfig, "pythonPath" | "command" | "timeout">> & Pick<KumihoLocalConfig, "args" | "env" | "cwd">;
}
type MemoryType = "summary" | "fact" | "decision" | "action" | "error";
type MemoryScope = "session" | "long-term" | "all";
interface MemoryEntry {
    kref: string;
    type: MemoryType;
    title: string;
    summary: string;
    topics: string[];
    score?: number;
    timestamp?: string;
    space?: string;
    metadata?: Record<string, unknown>;
}
interface MemoryStoreResult {
    item_kref: string;
    revision_kref: string;
    bundle_kref?: string;
    space_path: string;
    summary: string;
}
interface ChatMessage {
    role: "user" | "assistant" | "system";
    content: string;
    timestamp: string;
    metadata?: Record<string, unknown>;
}
interface WorkingMemoryState {
    messages: ChatMessage[];
    session_id: string;
    message_count: number;
    ttl_remaining: number;
}
type ChannelPlatform = "whatsapp" | "telegram" | "slack" | "discord" | "signal" | "imessage" | "webchat" | "google-chat" | "msteams" | "matrix" | string;
type ChannelType = "personal_dm" | "team_channel" | "group_dm";
interface ChannelInfo {
    platform: ChannelPlatform;
    channelType: ChannelType;
    platformUserId?: string;
    threadId?: string;
    device?: string;
    teamSlug?: string;
    groupId?: string;
}
interface ArtifactPointer {
    type: string;
    storage: "local";
    location: string;
    hash?: string;
    size_bytes?: number;
    metadata?: Record<string, unknown>;
}
interface RedactedEntity {
    type: "email" | "phone" | "ssn" | "credit_card" | "ip_address";
    placeholder: string;
    original: "[REDACTED]";
}
interface RedactionResult {
    text: string;
    entities: RedactedEntity[];
}
interface DreamStateStats {
    events_processed: number;
    revisions_assessed: number;
    deprecated: number;
    metadata_updated: number;
    tags_added: number;
    edges_created: number;
    duration_ms: number;
    errors: string[];
}
/**
 * The kind of creative output being captured.
 * Maps to Kumiho item kinds in the graph.
 */
type CreativeKind = "document" | "code" | "design" | "plan" | "analysis" | "other";
/**
 * Parameters for the creative_capture tool.
 *
 * The agent should pass the kref of the recalled memory that drove this
 * output as `sourceMemoryKref` so the creative artifact is linked back to
 * its cognitive origin in the graph (DERIVED_FROM edge).
 */
interface CreativeCaptureParams {
    /** Title / name of the creative artifact. */
    title: string;
    /** The content to capture (text body, code, summary, etc.). */
    content: string;
    /** Kind of creative output. */
    kind: CreativeKind;
    /**
     * Project space slug (e.g. "blog-post-jan25", "api-refactor").
     * This becomes the Kumiho space under the project root.
     */
    project: string;
    /** Optional topic tags for discovery. */
    tags?: string[];
    /**
     * Kref of the memory (conversation recall result) that produced or
     * inspired this output. Creates a DERIVED_FROM edge in the graph so the
     * creative artifact can be traced back to its cognitive origin.
     */
    sourceMemoryKref?: string;
    /** Extra metadata key-value pairs stored on the revision. */
    metadata?: Record<string, string>;
}
/** Response from the BFF creative capture endpoint. */
interface CreativeCaptureResult {
    /** Always true when the job was accepted. */
    queued: boolean;
    /** Job ID for polling /api/v1/apps/creative/jobs/{jobId}. */
    jobId: string;
    /** Human-readable status message. */
    message: string;
}
/** Parameters for the project_recall tool. */
interface ProjectRecallParams {
    /** Project space slug to search within. */
    project: string;
    /** Optional natural language query. Defaults to listing all items. */
    query?: string;
    /** Filter by creative kind. */
    kind?: CreativeKind;
    /** Max results to return. */
    limit?: number;
}
/** A creative item returned from project recall. */
interface CreativeItem {
    kref: string;
    title: string;
    kind: string;
    space: string;
    tags: string[];
    summary?: string;
    timestamp?: string;
    metadata?: Record<string, unknown>;
}

/**
 * MCP stdio transport bridge.
 *
 * Spawns `kumiho-mcp` (Python) as a child process and communicates over
 * stdin/stdout using MCP JSON-RPC 2.0. This lets the OpenClaw plugin use
 * the full kumiho-memory Python SDK without any HTTP deployment.
 *
 * Lifecycle:
 *   1. spawn() → starts the Python process
 *   2. initialize() → MCP handshake (capabilities exchange)
 *   3. call() → tool invocations via JSON-RPC
 *   4. close() → graceful shutdown
 *
 * Protocol reference: https://modelcontextprotocol.io/specification
 */

declare class McpBridgeError extends Error {
    readonly code: string;
    constructor(message: string, code: string);
}
interface McpServerCapabilities {
    tools?: {
        listChanged?: boolean;
    };
    resources?: {
        listChanged?: boolean;
    };
    prompts?: {
        listChanged?: boolean;
    };
}
interface McpToolDefinition {
    name: string;
    description?: string;
    inputSchema?: Record<string, unknown>;
}
interface McpBridgeOptions {
    /** Python executable path. Default: "python" */
    pythonPath?: string;
    /** MCP server module or command. Default: "kumiho-mcp" */
    command?: string;
    /** Extra args passed to the MCP server process. */
    args?: string[];
    /** Environment variables merged into the child process env. */
    env?: Record<string, string>;
    /** Working directory for the child process. */
    cwd?: string;
    /** Request timeout in milliseconds. Default: 30000 */
    timeout?: number;
    /** Logger for bridge-level events. */
    logger?: {
        info: (msg: string) => void;
        warn: (msg: string) => void;
        error: (msg: string) => void;
    };
}
declare class McpBridge extends EventEmitter {
    private proc;
    private reader;
    private nextId;
    private pending;
    private initialized;
    private serverCapabilities;
    private availableTools;
    private readonly pythonPath;
    private readonly command;
    private readonly args;
    private readonly childEnv;
    private readonly cwd;
    private readonly timeout;
    private readonly log;
    constructor(opts?: McpBridgeOptions);
    /**
     * Spawn the Python MCP server and perform the initialization handshake.
     */
    start(): Promise<void>;
    /**
     * Graceful shutdown: send a shutdown notification and kill the process.
     */
    close(): Promise<void>;
    get isRunning(): boolean;
    get capabilities(): McpServerCapabilities;
    get tools(): McpToolDefinition[];
    private initialize;
    /**
     * Call an MCP tool and return the parsed result.
     *
     * This is the primary method used by KumihoClient in local mode.
     */
    callTool<T = unknown>(toolName: string, args: Record<string, unknown>): Promise<T>;
    private sendRequest;
    private sendNotification;
    private handleLine;
    private rejectAllPending;
}

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

declare class KumihoApiError extends Error {
    readonly code: string;
    readonly status: number;
    constructor(message: string, code: string, status: number);
}
/**
 * Minimal interface that both HTTP and MCP transports implement.
 * call() sends a tool invocation and returns the parsed result.
 */
interface Transport {
    call<T>(tool: string, params: Record<string, unknown>): Promise<T>;
    start?(): Promise<void>;
    close?(): Promise<void>;
    ping(): Promise<boolean>;
    /** Return MCP-discovered tool definitions (available after start). */
    getDiscoveredTools?(): McpToolDefinition[];
}
declare function createTransport(config: ResolvedConfig, logger?: {
    info: (m: string) => void;
    warn: (m: string) => void;
    error: (m: string) => void;
}): Transport;
declare class KumihoClient {
    private readonly transport;
    private readonly project;
    constructor(transport: Transport, project: string);
    /** Start the underlying transport (needed for local/MCP mode). */
    start(): Promise<void>;
    /** Close the underlying transport. */
    close(): Promise<void>;
    /** Invoke any MCP tool by name. Used for pass-through asset management tools. */
    callTool<T = unknown>(name: string, params: Record<string, unknown>): Promise<T>;
    /** Return tool definitions discovered from the MCP backend (after start). */
    getDiscoveredTools(): McpToolDefinition[];
    chatAdd(sessionId: string, role: ChatMessage["role"], content: string, metadata?: Record<string, unknown>): Promise<void>;
    chatGet(sessionId: string, limit?: number): Promise<WorkingMemoryState>;
    chatClear(sessionId: string): Promise<void>;
    memoryStore(params: {
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
    }): Promise<MemoryStoreResult>;
    memoryRetrieve(params: {
        query: string;
        limit?: number;
        spacePaths?: string[];
        memoryTypes?: MemoryType[];
    }): Promise<MemoryEntry[]>;
    getRevision(kref: string): Promise<MemoryEntry>;
    getRevisions(krefs: string[]): Promise<MemoryEntry[]>;
    memoryDelete(kref: string): Promise<void>;
    memoryDeprecate(kref: string): Promise<void>;
    storeToolExecution(params: {
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
    }): Promise<MemoryStoreResult>;
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
    creativeEnqueue(params: CreativeCaptureParams, bffUrl: string, apiKey: string): Promise<CreativeCaptureResult>;
    triggerDreamState(modelConfig?: {
        provider?: string;
        model?: string;
        apiKey?: string;
    }): Promise<DreamStateStats>;
    ping(): Promise<boolean>;
}

/**
 * Client-side PII redaction.
 *
 * Detects and replaces common PII patterns before any data is sent to
 * Kumiho Cloud. Mirrors the regex patterns from the kumiho-memory Python
 * package (privacy.py).
 */

declare class PIIRedactor {
    private counters;
    /** Reset placeholder counters (useful between sessions). */
    reset(): void;
    private nextPlaceholder;
    /**
     * Detect and redact PII from text.
     * Returns the sanitized text and a list of redacted entities.
     */
    redact(text: string): RedactionResult;
    /**
     * Replace remaining PII placeholders with generic descriptors
     * for human-readable summaries.
     */
    anonymizeSummary(summary: string): string;
}

/**
 * Local artifact file management.
 *
 * Raw conversation logs, voice recordings, images, and other media are
 * stored locally. Only artifact *pointers* (path + hash + size) are sent
 * to Kumiho Cloud.
 */

declare class ArtifactManager {
    private readonly root;
    constructor(artifactDir?: string);
    /**
     * Save a conversation transcript as a local Markdown file.
     * Returns an artifact pointer for Kumiho metadata.
     */
    saveConversation(project: string, sessionId: string, messages: ChatMessage[], summary?: string): Promise<ArtifactPointer>;
    /**
     * Save a tool execution log as a local file.
     */
    saveExecutionLog(project: string, task: string, params: {
        status: string;
        exitCode?: number;
        durationMs?: number;
        stdout?: string;
        stderr?: string;
    }): Promise<ArtifactPointer>;
    /**
     * Copy an attachment into the artifact directory and return a pointer.
     */
    saveAttachment(project: string, sourcePath: string, mimeType: string, description?: string): Promise<ArtifactPointer>;
    /**
     * Get the local artifact directory path for a project.
     */
    getProjectDir(project: string): string;
}

/**
 * Auto-recall and auto-capture hooks for OpenClaw.
 *
 * - Auto-Recall (before_agent_start): Searches Kumiho for memories matching
 *   the current message and injects them into the agent context.
 *
 * - Auto-Capture (agent_end): After the agent responds, extracts noteworthy
 *   facts from the exchange and stores them in Kumiho Cloud.
 *
 * Both hooks respect the privacy model: only structured summaries are sent
 * to the cloud, and PII is redacted before upload.
 */

interface RecallResult {
    /** Context text to inject into the agent prompt. Empty if nothing recalled. */
    contextInjection: string;
    /** Memories that were recalled (for reference). */
    memories: MemoryEntry[];
    /** Session ID for continuing the conversation. */
    sessionId: string;
}
interface CaptureResult {
    /** Whether anything was captured and stored. */
    captured: boolean;
    /** Whether consolidation was triggered. */
    consolidated: boolean;
    /** Number of messages in current session after capture. */
    messageCount: number;
}

/**
 * Cross-channel session ID management.
 *
 * Session IDs are user-centric, not channel-specific. A user chatting on
 * WhatsApp at 9 AM and continuing on Slack at 2 PM shares the same session.
 *
 * Format: {context}:user-{hash}:{date}:{sequence}
 * Example: alice-personal:user-7f3a9b1c2d:20260203:001
 */

/**
 * Generate a channel-agnostic session ID.
 *
 * The same user talking on different channels within the same day gets the
 * same session prefix. A new sequence number is issued only when a fresh
 * session is explicitly requested (e.g. after consolidation).
 */
declare function generateSessionId(userId: string, context?: string, newSession?: boolean): Promise<string>;
/**
 * Map a channel to its memory space path.
 *
 * Follows the same logic as `get_memory_space()` in kumiho-memory Python.
 */
declare function getMemorySpace(channel: ChannelInfo, project?: string): string;
/**
 * Infer ChannelType from an OpenClaw channel identifier.
 *
 * OpenClaw channels expose a `type` string like "whatsapp", "slack", etc.
 * We map group channels and workspace channels to the appropriate type.
 */
declare function inferChannelType(_platform: string, isGroup?: boolean, isWorkspace?: boolean): ChannelType;

/**
 * User identity bootstrap.
 *
 * On first contact with a senderId, stores the user's OpenClaw identity
 * as an `agent.instructions` profile in Kumiho so the same identity is
 * available when the user connects from any other Kumiho-enabled platform
 * (Claude Code, LangChain, etc.).
 *
 * Silent — never throws. Errors are logged and skipped.
 */

interface UserIdentity {
    displayName?: string;
    platform?: string;
    timezone?: string;
    locale?: string;
    email?: string;
}
/**
 * Ensure a user identity profile exists in Kumiho for the given senderId.
 * No-ops if the profile already exists (one revision per user).
 * Called silently on first message; all errors are swallowed.
 */
declare function ensureUserIdentity(client: KumihoClient, config: ResolvedConfig, senderId: string, identity: UserIdentity): Promise<void>;

/**
 * Agent tool definitions for Kumiho memory.
 *
 * These tools are registered with OpenClaw's tool registry and can be
 * invoked by the AI agent during conversations for explicit memory
 * operations.
 *
 * Tools:
 *   memory_search   - Query memories by natural language
 *   memory_store    - Explicitly save a fact/decision/summary
 *   memory_get      - Retrieve a specific memory by kref
 *   memory_list     - List recent memories
 *   memory_forget   - Delete/deprecate a memory
 *   memory_consolidate - Trigger session consolidation
 *   memory_dream    - Trigger Dream State maintenance
 */

declare const TOOL_SCHEMAS: {
    readonly memory_search: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly query: {
                    readonly type: "string";
                    readonly description: "Natural language search query";
                };
                readonly scope: {
                    readonly type: "string";
                    readonly enum: readonly ["session", "long-term", "all"];
                    readonly description: "Memory scope to search (default: all)";
                };
                readonly limit: {
                    readonly type: "number";
                    readonly description: "Max results to return (default: 5)";
                };
                readonly spacePath: {
                    readonly type: "string";
                    readonly description: "Restrict search to a specific space path (e.g. CognitiveMemory/work/team-alpha)";
                };
            };
            readonly required: readonly ["query"];
        };
    };
    readonly memory_store: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly content: {
                    readonly type: "string";
                    readonly description: "The information to remember";
                };
                readonly type: {
                    readonly type: "string";
                    readonly enum: readonly ["fact", "decision", "summary"];
                    readonly description: "Memory type (default: fact)";
                };
                readonly title: {
                    readonly type: "string";
                    readonly description: "Short title for the memory";
                };
                readonly topics: {
                    readonly type: "array";
                    readonly items: {
                        readonly type: "string";
                    };
                    readonly description: "Topic tags for categorization";
                };
                readonly spaceHint: {
                    readonly type: "string";
                    readonly description: "Space path hint (e.g. personal/preferences)";
                };
            };
            readonly required: readonly ["content"];
        };
    };
    readonly memory_get: {
        readonly description: "Retrieve a specific memory by its kref identifier.";
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly kref: {
                    readonly type: "string";
                    readonly description: "The kref identifier of the memory to retrieve";
                };
            };
            readonly required: readonly ["kref"];
        };
    };
    readonly memory_list: {
        readonly description: "List stored memories. Returns recent memories from Kumiho long-term storage.";
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly scope: {
                    readonly type: "string";
                    readonly enum: readonly ["session", "long-term", "all"];
                    readonly description: "Memory scope (default: all)";
                };
                readonly limit: {
                    readonly type: "number";
                    readonly description: "Max memories to list (default: 10)";
                };
                readonly spacePath: {
                    readonly type: "string";
                    readonly description: "Filter by space path";
                };
            };
        };
    };
    readonly memory_forget: {
        readonly description: "Delete or deprecate a memory. Can target by kref or by search query.";
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly kref: {
                    readonly type: "string";
                    readonly description: "Kref of the memory to forget";
                };
                readonly query: {
                    readonly type: "string";
                    readonly description: "Search query to find memories to forget";
                };
            };
        };
    };
    readonly memory_consolidate: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly sessionId: {
                    readonly type: "string";
                    readonly description: "Session ID to consolidate (default: current session)";
                };
            };
        };
    };
    readonly memory_dream: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {};
        };
    };
    readonly creative_capture: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly title: {
                    readonly type: "string";
                    readonly description: "Title or name of the creative artifact";
                };
                readonly content: {
                    readonly type: "string";
                    readonly description: "The content body to capture (text, code, markdown, etc.)";
                };
                readonly kind: {
                    readonly type: "string";
                    readonly enum: readonly ["document", "code", "design", "plan", "analysis", "other"];
                    readonly description: "Kind of creative output (default: document)";
                };
                readonly project: {
                    readonly type: "string";
                    readonly description: string;
                };
                readonly tags: {
                    readonly type: "array";
                    readonly items: {
                        readonly type: "string";
                    };
                    readonly description: "Optional topic tags for discovery";
                };
                readonly sourceMemoryKref: {
                    readonly type: "string";
                    readonly description: string;
                };
                readonly metadata: {
                    readonly type: "object";
                    readonly description: "Extra metadata key-value pairs stored on the revision";
                };
            };
            readonly required: readonly ["title", "content", "project"];
        };
    };
    readonly project_recall: {
        readonly description: string;
        readonly parameters: {
            readonly type: "object";
            readonly properties: {
                readonly project: {
                    readonly type: "string";
                    readonly description: "Project space slug to search within";
                };
                readonly query: {
                    readonly type: "string";
                    readonly description: "Natural language search query (optional — omit to list all)";
                };
                readonly kind: {
                    readonly type: "string";
                    readonly enum: readonly ["document", "code", "design", "plan", "analysis", "other"];
                    readonly description: "Filter by creative kind";
                };
                readonly limit: {
                    readonly type: "number";
                    readonly description: "Max results to return (default: topK from config)";
                };
            };
            readonly required: readonly ["project"];
        };
    };
};
interface ToolContext {
    client: KumihoClient;
    config: ResolvedConfig;
    currentSessionId: string | null;
    logger: {
        info: (msg: string) => void;
        error: (msg: string) => void;
    };
}
type ToolName = keyof typeof TOOL_SCHEMAS;
declare const TOOL_HANDLERS: Record<ToolName, (ctx: ToolContext, params: Record<string, unknown>) => Promise<string>>;

interface PluginAPI {
    config: {
        plugins?: {
            entries?: Record<string, {
                config?: KumihoPluginConfig;
            }>;
        };
    };
    logger: {
        info: (msg: string) => void;
        warn: (msg: string) => void;
        error: (msg: string) => void;
    };
    runtime?: {
        injectContext?: (text: string) => void;
    };
    registerGatewayMethod: (name: string, handler: (ctx: {
        respond: (ok: boolean, data: unknown) => void;
        params?: Record<string, unknown>;
    }) => void) => void;
    registerCli: (fn: (ctx: {
        program: CLIProgram;
    }) => void, opts: {
        commands: string[];
    }) => void;
    registerService: (svc: {
        id: string;
        start: () => void | Promise<void>;
        stop: () => void | Promise<void>;
    }) => void;
    registerCommand: (cmd: {
        name: string;
        description: string;
        requireAuth: boolean;
        acceptsArgs: boolean;
        handler: (ctx: {
            senderId: string;
            channel: string;
            args?: string;
        }) => {
            text: string;
        };
    }) => void;
    on: (event: string, handler: (ctx: Record<string, unknown>) => void | Promise<void>) => void;
}
interface CLIProgram {
    command: (name: string) => CLICommand;
}
interface CLICommand {
    description: (desc: string) => CLICommand;
    argument: (name: string, desc: string) => CLICommand;
    option: (flags: string, desc: string) => CLICommand;
    action: (fn: (...args: unknown[]) => void | Promise<void>) => CLICommand;
}
declare const _default: {
    id: string;
    name: string;
    register(api: PluginAPI): void;
};

/**
 * Create a standalone Kumiho memory instance.
 *
 * Supports both modes:
 *
 * ```typescript
 * // Local mode (default) — uses kumiho-mcp Python subprocess
 * const memory = await createKumihoMemory({ mode: "local" });
 * await memory.start();
 *
 * // Cloud mode — uses Kumiho Cloud HTTPS API
 * const memory = await createKumihoMemory({
 *   mode: "cloud",
 *   apiKey: "kh_live_...",
 * });
 *
 * // Both modes share the same API
 * const recalled = await memory.recall("user preferences");
 * await memory.store("User prefers dark mode", { type: "fact" });
 *
 * // Clean up (important in local mode to stop the Python process)
 * await memory.close();
 * ```
 */
declare function createKumihoMemory(rawConfig?: KumihoPluginConfig): {
    client: KumihoClient;
    config: ResolvedConfig;
    /**
     * Start the memory system.
     * In local mode this spawns the kumiho-mcp Python process.
     * In cloud mode this is a no-op (HTTPS is stateless).
     */
    start(): Promise<void>;
    /**
     * Shut down the memory system.
     * In local mode this stops the kumiho-mcp Python process.
     */
    close(): Promise<void>;
    /** Search long-term memory. */
    recall(query: string, limit?: number): Promise<MemoryEntry[]>;
    /** Store a fact/decision/summary in long-term memory. */
    store(content: string, opts?: {
        type?: "fact" | "decision" | "summary";
        title?: string;
        topics?: string[];
        spaceHint?: string;
    }): Promise<MemoryStoreResult>;
    /** Add a message to working memory. */
    addMessage(sessionId: string, role: "user" | "assistant", content: string): Promise<void>;
    /** Get messages from working memory. */
    getMessages(sessionId: string, limit?: number): Promise<WorkingMemoryState>;
    /** Generate a session ID. */
    newSession(userId?: string, context?: string): Promise<string>;
    /** Auto-recall hook. */
    autoRecallHook(userMessage: string, channel?: ChannelInfo): Promise<RecallResult>;
    /** Auto-capture hook. */
    autoCaptureHook(assistantResponse: string, channel?: ChannelInfo): Promise<CaptureResult>;
    /** Trigger Dream State consolidation. */
    dream(): Promise<DreamStateStats>;
    /** Store tool execution result. */
    storeExecution(params: Parameters<(params: {
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
    }) => Promise<MemoryStoreResult>>[0]): Promise<MemoryStoreResult>;
    /** Check backend connectivity. */
    ping(): Promise<boolean>;
};

export { ArtifactManager, type ArtifactPointer, type ChannelInfo, type ChatMessage, type CreativeCaptureParams, type CreativeCaptureResult, type CreativeItem, type CreativeKind, type DreamStateStats, KumihoApiError, KumihoClient, type KumihoLocalConfig, type KumihoPluginConfig, McpBridge, McpBridgeError, type McpToolDefinition, type MemoryEntry, type MemoryScope, type MemoryType, PIIRedactor, type ProjectRecallParams, type ResolvedConfig, TOOL_HANDLERS, TOOL_SCHEMAS, type Transport, type UserIdentity, createKumihoMemory, createTransport, _default as default, ensureUserIdentity, generateSessionId, getMemorySpace, inferChannelType };
