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

// ---------------------------------------------------------------------------
// Plugin configuration
// ---------------------------------------------------------------------------

export interface KumihoLLMConfig {
  provider?: "openai" | "anthropic";
  model?: string;
  apiKey?: string;
}

export interface KumihoPrivacyConfig {
  /** Only send structured summaries, never raw text (default: true) */
  uploadSummariesOnly?: boolean;
  /** Keep raw chats and media on local filesystem (default: true) */
  localArtifacts?: boolean;
  /** Store voice/image transcription text in Kumiho (default: true) */
  storeTranscriptions?: boolean;
}

/** Configuration for local mode (MCP stdio bridge). */
export interface KumihoLocalConfig {
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

export interface KumihoPluginConfig {
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
export interface ResolvedConfig {
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
  local: Required<Pick<KumihoLocalConfig, "pythonPath" | "command" | "timeout">> &
    Pick<KumihoLocalConfig, "args" | "env" | "cwd">;
}

// ---------------------------------------------------------------------------
// Memory types (aligned with kumiho-memory Python package)
// ---------------------------------------------------------------------------

export type MemoryType = "summary" | "fact" | "decision" | "action" | "error";
export type MemoryScope = "session" | "long-term" | "all";

export interface MemoryEntry {
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

export interface MemoryStoreResult {
  item_kref: string;
  revision_kref: string;
  bundle_kref?: string;
  space_path: string;
  summary: string;
}

export interface MemoryRetrieveResult {
  item_krefs: string[];
  revision_krefs: string[];
  spaces_used: string[];
}

// ---------------------------------------------------------------------------
// Working memory (Redis-backed short-term buffer)
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface WorkingMemoryState {
  messages: ChatMessage[];
  session_id: string;
  message_count: number;
  ttl_remaining: number;
}

// ---------------------------------------------------------------------------
// Channel / session types
// ---------------------------------------------------------------------------

export type ChannelPlatform =
  | "whatsapp"
  | "telegram"
  | "slack"
  | "discord"
  | "signal"
  | "imessage"
  | "webchat"
  | "google-chat"
  | "msteams"
  | "matrix"
  | string;

export type ChannelType = "personal_dm" | "team_channel" | "group_dm";

export interface ChannelInfo {
  platform: ChannelPlatform;
  channelType: ChannelType;
  platformUserId?: string;
  threadId?: string;
  device?: string;
  teamSlug?: string;
  groupId?: string;
}

// ---------------------------------------------------------------------------
// Artifact pointers (local files referenced but never uploaded)
// ---------------------------------------------------------------------------

export interface ArtifactPointer {
  type: string;
  storage: "local";
  location: string;
  hash?: string;
  size_bytes?: number;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Summarization output (from local LLM processing)
// ---------------------------------------------------------------------------

export interface KnowledgeFact {
  claim: string;
  certainty: "high" | "medium" | "low";
}

export interface KnowledgeDecision {
  decision: string;
  reason: string;
}

export interface KnowledgeAction {
  task: string;
  status: "done" | "failed" | "pending";
  exit_code?: number;
  duration_ms?: number;
}

export interface ExtractedKnowledge {
  facts?: KnowledgeFact[];
  decisions?: KnowledgeDecision[];
  actions?: KnowledgeAction[];
  open_questions?: string[];
}

export interface SummarizationResult {
  type: MemoryType;
  title: string;
  summary: string;
  knowledge: ExtractedKnowledge;
  classification: {
    topics: string[];
    entities?: string[];
  };
}

// ---------------------------------------------------------------------------
// PII redaction
// ---------------------------------------------------------------------------

export interface RedactedEntity {
  type: "email" | "phone" | "ssn" | "credit_card" | "ip_address";
  placeholder: string;
  original: "[REDACTED]";
}

export interface RedactionResult {
  text: string;
  entities: RedactedEntity[];
}

// ---------------------------------------------------------------------------
// Tool execution memory
// ---------------------------------------------------------------------------

export interface ToolExecutionParams {
  task: string;
  status: "done" | "failed" | "error" | "blocked";
  exitCode?: number;
  durationMs?: number;
  stdout?: string;
  stderr?: string;
  tools?: string[];
  topics?: string[];
  spaceHint?: string;
  openQuestions?: string[];
}

// ---------------------------------------------------------------------------
// Dream State
// ---------------------------------------------------------------------------

export interface DreamStateStats {
  events_processed: number;
  revisions_assessed: number;
  deprecated: number;
  metadata_updated: number;
  tags_added: number;
  edges_created: number;
  duration_ms: number;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Creative Memory
// ---------------------------------------------------------------------------

/**
 * The kind of creative output being captured.
 * Maps to Kumiho item kinds in the graph.
 */
export type CreativeKind =
  | "document"
  | "code"
  | "design"
  | "plan"
  | "analysis"
  | "other";

/**
 * Parameters for the creative_capture tool.
 *
 * The agent should pass the kref of the recalled memory that drove this
 * output as `sourceMemoryKref` so the creative artifact is linked back to
 * its cognitive origin in the graph (DERIVED_FROM edge).
 */
export interface CreativeCaptureParams {
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
export interface CreativeCaptureResult {
  /** Always true when the job was accepted. */
  queued: boolean;
  /** Job ID for polling /api/v1/apps/creative/jobs/{jobId}. */
  jobId: string;
  /** Human-readable status message. */
  message: string;
}

/** Parameters for the project_recall tool. */
export interface ProjectRecallParams {
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
export interface CreativeItem {
  kref: string;
  title: string;
  kind: string;
  space: string;
  tags: string[];
  summary?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}
