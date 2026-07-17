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

import type { SupportedLlmProvider } from "./llm.js";

// ---------------------------------------------------------------------------
// Plugin configuration
// ---------------------------------------------------------------------------

export interface KumihoLLMConfig {
  provider?: SupportedLlmProvider;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}

export interface KumihoPrivacyConfig {
  /** Only send structured summaries, never raw text (default: true) */
  uploadSummariesOnly?: boolean;
  /** Keep raw chats and media on local filesystem (default: true) */
  localArtifacts?: boolean;
  /** Store voice/image transcription text in Kumiho (default: true) */
  storeTranscriptions?: boolean;
}

/**
 * Self-hosted Community Edition (CE) settings — local mode only.
 *
 * When enabled, the spawned Python SDK talks to a local kumiho-server CE
 * over gRPC instead of Kumiho Cloud: control-plane discovery and cloud auth
 * are skipped, and working memory uses a local Redis. Mirrors the Claude
 * plugin's KUMIHO_CLAUDE_MODE=ce behavior.
 */
export interface KumihoCEConfig {
  /** Route the local Python SDK at a self-hosted CE server. Default: false. */
  enabled?: boolean;
  /** CE gRPC endpoint (host:port). Setting this alone also enables CE. Default: "127.0.0.1:9190". */
  endpoint?: string;
  /** Local Redis URL for CE working memory. Default: "redis://127.0.0.1:6379". */
  redisUrl?: string;
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
   * BFF base URL for creative capture pipeline.
   * Defaults to the Kumiho Cloud API endpoint.
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
  /** Self-hosted Community Edition settings (local mode only) */
  ce?: KumihoCEConfig;
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
  /** Direct-call-capable LLM API key inherited from the host gateway when available. */
  hostLlmApiKey: string;
  /** LLM provider name corresponding to hostLlmApiKey ("anthropic" | "openai" | ""). */
  hostLlmProvider: string;
  privacy: Required<KumihoPrivacyConfig>;
  /** Self-hosted CE routing. enabled=false in cloud mode and by default. */
  ce: Required<KumihoCEConfig>;
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
// Composite two-reflex types (engage / reflect)
// ---------------------------------------------------------------------------

/** Result of kumiho_memory_engage — recall + context building in one call. */
export interface EngageResult {
  /** Pre-built context string from the backend (title + summary lines). */
  context: string;
  /** Recalled memories mapped into MemoryEntry shape. */
  results: MemoryEntry[];
  /** Krefs of the recalled memories — pass to reflect for DERIVED_FROM edges. */
  sourceKrefs: string[];
  /** True when the server deduplicated an identical recall within its window. */
  deduplicated?: boolean;
}

/** A structured capture stored via kumiho_memory_reflect. */
export interface ReflectCapture {
  /** decision, preference, fact, correction, architecture, implementation, synthesis, reflection, summary, skill */
  type: string;
  /** Short title with absolute dates (e.g. "Chose gRPC on Mar 27"). */
  title: string;
  content: string;
  tags?: string[];
  /** Space path hint for this capture. Overrides the call-level spacePath. */
  spaceHint?: string;
  /** ISO-8601 date the captured event actually happened (valid-time). */
  eventDate?: string;
}

/** Result of kumiho_memory_reflect. */
export interface ReflectResult {
  buffered: boolean;
  captures_stored: number;
  edges_discovered: number;
  stored_krefs: string[];
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
  success: boolean;
  events_processed: number;
  revisions_assessed: number;
  deprecated: number;
  metadata_updated: number;
  tags_added: number;
  edges_created: number;
  /** ISO timestamp of the last processed event (backward-compat cursor). */
  cursor?: string | null;
  duration_ms: number;
  errors: string[];
  /** Kref of the generated Dream State report item, if any. */
  report_kref?: string;
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
  | "other"
  | (string & {});

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
   * Kumiho project for creative outputs (e.g. 'blog-posts', 'marketing').
   * Must NOT be CognitiveMemory — creative outputs live in their own project.
   * The only exception is free-tier users limited to 1 project, where the
   * space is created under CognitiveMemory as a fallback.
   */
  creativeProject: string;
  /**
   * Project space slug (e.g. "blog-post-jan25", "api-refactor").
   * This becomes the Kumiho space under the creative project.
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

/** Parameters for the creative_recall tool. */
export interface CreativeRecallParams {
  /**
   * Kumiho project name for creative outputs (e.g. 'blog-posts', 'marketing').
   * Defaults to the plugin's configured project if omitted.
   * Mirrors the `creativeProject` parameter of creative_capture.
   */
  creativeProject?: string;
  /** Space slug to search within (e.g. 'blog-drafts', 'api-refactor'). */
  space: string;
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
