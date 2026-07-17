/**
 * Agent tool definitions for Kumiho memory.
 *
 * These tools are registered with OpenClaw's tool registry and can be
 * invoked by the AI agent during conversations for explicit memory
 * operations.
 *
 * Tools:
 *   memory_engage   - Two-reflex: recall + context building before responding
 *   memory_reflect  - Two-reflex: buffer response + store captures with provenance
 *   memory_search   - Query memories by natural language
 *   memory_store    - Explicitly save a fact/decision/summary
 *   memory_get      - Retrieve a specific memory by kref
 *   memory_list     - List recent memories
 *   memory_forget   - Delete/deprecate a memory
 *   memory_consolidate - Trigger session consolidation
 *   memory_dream    - Trigger Dream State maintenance
 */

import { isUnknownToolError, type KumihoClient } from "./client.js";
import type {
  ResolvedConfig,
  MemoryScope,
  MemoryType,
  MemoryEntry,
  KumihoLLMConfig,
  ReflectCapture,
} from "./types.js";
import { creativeCaptureHandler, creativeJobStatusHandler, creativeRecallHandler } from "./creative.js";
import { GEMINI_OPENAI_BASE_URL, normalizeConfiguredLlmProvider } from "./llm.js";

// ---------------------------------------------------------------------------
// LLM key resolution for Dream State
// ---------------------------------------------------------------------------

/**
 * Resolve LLM model config for Dream State.
 * Falls back from explicit Dream State config to generic llm config to
 * inherited host/runtime credentials.
 */
function resolveDreamModelConfig(
  cfg: ResolvedConfig,
): Pick<KumihoLLMConfig, "provider" | "model" | "apiKey" | "baseUrl"> | undefined {
  const dm = cfg.dreamStateModel ?? {};
  const explicitProvider = normalizeConfiguredLlmProvider(dm.provider || cfg.llm.provider);
  const inheritedProvider = normalizeConfiguredLlmProvider(cfg.hostLlmProvider);
  const explicitApiKey = dm.apiKey || cfg.llm.apiKey;
  const explicitBaseUrl = dm.baseUrl || cfg.llm.baseUrl;
  if (
    explicitProvider &&
    !explicitApiKey &&
    cfg.hostLlmProvider &&
    explicitProvider !== cfg.hostLlmProvider
  ) {
    return undefined;
  }

  const provider = explicitProvider || inheritedProvider;
  const model = dm.model || cfg.llm.model;
  const apiKey = explicitApiKey || cfg.hostLlmApiKey;
  if (!provider || !apiKey) return undefined;
  const baseUrl = explicitBaseUrl || (provider === "gemini" ? GEMINI_OPENAI_BASE_URL : "");
  return {
    provider,
    model,
    apiKey,
    ...(baseUrl ? { baseUrl } : {}),
  };
}

// ---------------------------------------------------------------------------
// Tool parameter schemas (JSON Schema for OpenClaw tool registry)
// ---------------------------------------------------------------------------

export const TOOL_SCHEMAS = {
  memory_engage: {
    description:
      "Engage memory before responding — recall + context building in one call. " +
      "Use when the topic might have deeper history than the auto-recalled context shows. " +
      "Returns recalled memories and source_krefs; hold the source_krefs and pass them to " +
      "memory_reflect so new captures get provenance edges. At most one engage per response — " +
      "the server deduplicates identical queries within a short window (vary the query if " +
      "results come back deduplicated). Never say \"I don't remember\" without engaging first.",
    parameters: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Natural-language query derived from the user's current message",
        },
        limit: {
          type: "number",
          description: "Max results to return (default: 5)",
        },
        spacePath: {
          type: "string",
          description:
            "Restrict search to a specific space path (e.g. CognitiveMemory/Skills for skill discovery)",
        },
        graphAugmented: {
          type: "boolean",
          description:
            "Enable graph-augmented recall for indirect or chain-of-decision questions (default: false)",
        },
      },
      required: ["query"],
    },
  },

  memory_reflect: {
    description:
      "Reflect after responding — store structured captures (decisions, preferences, facts, " +
      "corrections) with provenance links, and buffer a summary of your response for session " +
      "continuity. Use after a substantive exchange; when the user says \"remember this\" you " +
      "MUST capture it here. Use absolute dates in capture titles (\"on Mar 8\", not \"today\"). " +
      "Pass sourceKrefs from memory_engage (or the auto-recalled memory krefs) to create " +
      "DERIVED_FROM edges. Skip this tool entirely for trivial exchanges — the plugin already " +
      "buffers your full response automatically.",
    parameters: {
      type: "object" as const,
      properties: {
        response: {
          type: "string",
          description:
            "One-line summary of the response you are giving (must be non-empty)",
        },
        captures: {
          type: "array",
          description:
            "Structured facts to store. Each becomes a graph memory with provenance links.",
          items: {
            type: "object",
            properties: {
              type: {
                type: "string",
                description:
                  "Memory type: decision, preference, fact, correction, architecture, " +
                  "implementation, synthesis, reflection, summary, skill",
              },
              title: {
                type: "string",
                description: "Short title with absolute dates (e.g. 'Chose gRPC on Mar 27')",
              },
              content: { type: "string", description: "Content to store" },
              tags: {
                type: "array",
                items: { type: "string" },
                description: "Classification tags",
              },
              spaceHint: {
                type: "string",
                description:
                  "Space path hint for this capture; overrides the call-level spaceHint",
              },
              eventDate: {
                type: "string",
                description:
                  "ISO-8601 date the captured event actually happened (YYYY-MM-DD). " +
                  "Omit when unknown; never guess.",
              },
            },
            required: ["type", "title", "content"],
          },
        },
        sourceKrefs: {
          type: "array",
          items: { type: "string" },
          description:
            "Krefs from memory_engage results (or auto-recalled memories) — creates " +
            "DERIVED_FROM edges to the memories that informed this response",
        },
        spaceHint: {
          type: "string",
          description: "Default space path for captures (e.g. personal/preferences)",
        },
      },
      required: ["response"],
    },
  },

  memory_search: {
    description:
      "Search Kumiho long-term memory using a natural language query. " +
      "Use this proactively when the user asks about past decisions, preferences, prior work, " +
      "or anything that might have been discussed in previous conversations. " +
      "Never say 'I don't remember' without searching first. " +
      "Also use with spacePath 'CognitiveMemory/Skills' to discover behavioral skills " +
      "(creative output tracking, graph traversal, privacy rules, session management).",
    parameters: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Natural language search query",
        },
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope to search (default: all)",
        },
        limit: {
          type: "number",
          description: "Max results to return (default: 5)",
        },
        spacePath: {
          type: "string",
          description:
            "Restrict search to a specific space path (e.g. CognitiveMemory/work/team-alpha)",
        },
      },
      required: ["query"],
    },
  },

  memory_store: {
    description:
      "Store a fact, decision, preference, or summary in Kumiho long-term memory. " +
      "Use proactively when the user states a preference, makes a decision, gives a correction, " +
      "or asks you to remember something. Also store your own significant outputs (plans, analyses, decisions).",
    parameters: {
      type: "object" as const,
      properties: {
        content: {
          type: "string",
          description: "The information to remember",
        },
        type: {
          type: "string",
          enum: ["fact", "decision", "summary"],
          description: "Memory type (default: fact)",
        },
        title: {
          type: "string",
          description: "Short title for the memory",
        },
        topics: {
          type: "array",
          items: { type: "string" },
          description: "Topic tags for categorization",
        },
        spaceHint: {
          type: "string",
          description: "Space path hint (e.g. personal/preferences)",
        },
      },
      required: ["content"],
    },
  },

  memory_get: {
    description: "Retrieve a specific memory by its kref identifier.",
    parameters: {
      type: "object" as const,
      properties: {
        kref: {
          type: "string",
          description: "The kref identifier of the memory to retrieve",
        },
        includeArtifact: {
          type: "boolean",
          description:
            "Also fetch the artifact file location (default: false). " +
            "Only needed for deep lookups where you need the raw content path.",
        },
      },
      required: ["kref"],
    },
  },

  memory_list: {
    description:
      "List stored memories. Returns recent memories from Kumiho long-term storage.",
    parameters: {
      type: "object" as const,
      properties: {
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope (default: all)",
        },
        limit: {
          type: "number",
          description: "Max memories to list (default: 10)",
        },
        spacePath: {
          type: "string",
          description: "Filter by space path",
        },
      },
    },
  },

  memory_forget: {
    description:
      "Delete or deprecate a memory. Can target by kref or by search query.",
    parameters: {
      type: "object" as const,
      properties: {
        kref: {
          type: "string",
          description: "Kref of the memory to forget",
        },
        query: {
          type: "string",
          description: "Search query to find memories to forget",
        },
      },
    },
  },

  memory_consolidate: {
    description:
      "Trigger consolidation of the current session's working memory into " +
      "long-term storage. Summarizes the conversation, redacts PII, and stores " +
      "a structured summary in Kumiho Cloud.",
    parameters: {
      type: "object" as const,
      properties: {
        sessionId: {
          type: "string",
          description: "Session ID to consolidate (default: current session)",
        },
      },
    },
  },

  memory_dream: {
    description:
      "Trigger a Dream State consolidation cycle. Reviews recent memories, " +
      "deprecates stale ones, adds tags, and creates relationship edges.",
    parameters: {
      type: "object" as const,
      properties: {},
    },
  },

  creative_capture: {
    description:
      "Capture a creative output (document, code, plan, etc.) into the Kumiho graph. " +
      "The artifact is stored asynchronously — this tool returns immediately with a job ID. " +
      "Pass sourceMemoryKref with the kref of the recalled memory that produced this output " +
      "so the graph links the creative artifact back to its cognitive origin (DERIVED_FROM edge).",
    parameters: {
      type: "object" as const,
      properties: {
        title: {
          type: "string",
          description: "Title or name of the creative artifact",
        },
        content: {
          type: "string",
          description: "The content body to capture (text, code, markdown, etc.)",
        },
        kind: {
          type: "string",
          description:
            "Item kind — any URL-safe string (e.g. 'document', 'code', 'blog-post', " +
            "'email', 'design', 'plan', 'analysis'). Default: document.",
        },
        creativeProject: {
          type: "string",
          description:
            "Kumiho project name for creative outputs (e.g. 'blog-posts', 'marketing'). " +
            "Must NOT be CognitiveMemory — creative outputs live in their own project. " +
            "Exception: free-tier users limited to 1 project may use CognitiveMemory as fallback.",
        },
        project: {
          type: "string",
          description:
            "Space slug within the creative project (e.g. 'blog-post-jan25', 'api-refactor'). " +
            "Groups outputs from the same project together in the graph.",
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "Optional topic tags for discovery",
        },
        sourceMemoryKref: {
          type: "string",
          description:
            "Kref of the recalled memory that inspired or drove this output. " +
            "Creates a DERIVED_FROM edge so the artifact can be traced to its cognitive origin.",
        },
        metadata: {
          type: "object",
          description: "Extra metadata key-value pairs stored on the revision",
        },
      },
      required: ["title", "content", "creativeProject", "project"],
    },
  },

  creative_job_status: {
    description:
      "Check the status of a creative capture job. Returns the resulting krefs " +
      "(item_kref, revision_kref, memory_kref) when the pipeline completes, which can be " +
      "shared with sub-agents or referenced in follow-up operations.",
    parameters: {
      type: "object" as const,
      properties: {
        jobId: {
          type: "string",
          description: "The job ID returned by creative_capture",
        },
      },
      required: ["jobId"],
    },
  },

  creative_recall: {
    description:
      "Search and list creative outputs stored in a space. " +
      "Use this before continuing work on a project to recall previous outputs, " +
      "decisions, and artifacts. Returns krefs you can pass as sourceMemoryKref " +
      "when capturing new outputs derived from existing work.",
    parameters: {
      type: "object" as const,
      properties: {
        creativeProject: {
          type: "string",
          description:
            "Kumiho project name for creative outputs (e.g. 'blog-posts', 'marketing'). " +
            "Defaults to the plugin's configured project if omitted. " +
            "Must match the creativeProject used in creative_capture.",
        },
        space: {
          type: "string",
          description:
            "Space slug to search within (e.g. 'blog-drafts', 'api-refactor'). " +
            "Matches the `project` (space slug) parameter of creative_capture.",
        },
        query: {
          type: "string",
          description: "Natural language search query (optional — omit to list all)",
        },
        kind: {
          type: "string",
          description: "Filter by item kind (e.g. 'document', 'code', 'blog-post')",
        },
        limit: {
          type: "number",
          description: "Max results to return (default: topK from config)",
        },
      },
      required: ["space"],
    },
  },
} as const;

// ---------------------------------------------------------------------------
// Tool handler context
// ---------------------------------------------------------------------------

export interface ToolContext {
  client: KumihoClient;
  config: ResolvedConfig;
  currentSessionId: string | null;
  logger: { info: (msg: string) => void; error: (msg: string) => void };
  getToken?: () => Promise<string>;
  /** Krefs recalled for the current turn — default provenance for memory_reflect. */
  getSourceKrefs?: () => string[];
}

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------

function formatMemoryEntry(entry: MemoryEntry): string {
  const parts = [`[${entry.type}] ${entry.title || "(untitled)"}`];
  if (entry.summary) parts.push(entry.summary);
  if (entry.topics?.length) parts.push(`Topics: ${entry.topics.join(", ")}`);
  if (entry.kref) parts.push(`Kref: ${entry.kref}`);
  if (entry.score != null) parts.push(`Score: ${entry.score.toFixed(2)}`);
  return parts.join("\n");
}

export async function handleMemoryEngage(
  ctx: ToolContext,
  params: { query: string; limit?: number; spacePath?: string; graphAugmented?: boolean },
): Promise<string> {
  const limit = params.limit ?? ctx.config.topK;
  const spacePaths = params.spacePath ? [params.spacePath] : undefined;

  // The composite engage is fixed to the server's default project — for a
  // custom project, go straight to the project-scoped retrieve path (same
  // guard the automatic recall hook enforces).
  if (ctx.config.project === "CognitiveMemory") {
    try {
      const engaged = await ctx.client.memoryEngage({
        query: params.query,
        limit,
        spacePaths,
        minScore: ctx.config.searchThreshold,
        graphAugmented: params.graphAugmented,
      });

      if (!engaged.deduplicated) {
        if (engaged.results.length === 0) {
          return "No memories found matching your query.";
        }
        const body = engaged.results.map(formatMemoryEntry).join("\n\n");
        return (
          `${body}\n\n` +
          `source_krefs (pass to memory_reflect for provenance):\n` +
          engaged.sourceKrefs.map((k) => `- ${k}`).join("\n")
        );
      }
      // Deduplicated: an identical recall consumed the server's dedup window
      // — usually the plugin's automatic prompt-build prefetch, whose results
      // are cached for the NEXT turn and are NOT in the agent's context.
      // Fall through to retrieve (no dedup guard) so the agent still gets
      // real results instead of a pointer to context that doesn't exist.
    } catch (err) {
      if (!isUnknownToolError(err)) throw err;
      // Pre-composite backend — same recall, no server-side context building.
    }
  }

  return handleMemorySearch(ctx, { query: params.query, limit, spacePath: params.spacePath });
}

/**
 * Store captures directly via memory_store with DERIVED_FROM provenance —
 * the reflect path for custom projects, sessionless contexts, and
 * pre-composite backends. Response buffering is not replicated here; the
 * plugin's auto-capture hook already buffers the real assistant response.
 */
async function storeCapturesDirect(
  ctx: ToolContext,
  captures: ReflectCapture[],
  sourceKrefs: string[],
  spaceHint?: string,
): Promise<string> {
  const krefs: string[] = [];
  for (const cap of captures) {
    const stored = await ctx.client.memoryStore({
      type: (cap.type as MemoryType) ?? "fact",
      title: cap.title,
      summary: cap.content,
      assistantText: cap.content,
      tags: cap.tags,
      spaceHint: cap.spaceHint ?? spaceHint,
      sourceRevisionKrefs: sourceKrefs.length ? sourceKrefs : undefined,
      // Valid-time: mirror the composite path's event_date stamping so
      // temporal recall works the same on the fallback path.
      metadata: cap.eventDate ? { event_date: cap.eventDate } : undefined,
    });
    krefs.push(stored.revision_kref || stored.item_kref);
  }
  return `Stored ${krefs.length} capture(s):\n${krefs.map((k) => `- ${k}`).join("\n")}`;
}

export async function handleMemoryReflect(
  ctx: ToolContext,
  params: {
    response: string;
    captures?: ReflectCapture[];
    sourceKrefs?: string[];
    spaceHint?: string;
  },
): Promise<string> {
  const response = params.response?.trim();
  if (!response) {
    return "memory_reflect requires a non-empty response summary.";
  }

  const sourceKrefs =
    params.sourceKrefs?.length ? params.sourceKrefs : ctx.getSourceKrefs?.() ?? [];
  const captures = params.captures ?? [];
  const sessionId = ctx.currentSessionId;

  // The composite reflect buffers into a session and is fixed to the
  // server's default project — use it only when both hold. Everything else
  // (custom project, sessionless context, pre-composite backend) stores the
  // captures directly so "remember this" is never silently dropped.
  if (sessionId && ctx.config.project === "CognitiveMemory") {
    try {
      const result = await ctx.client.memoryReflect({
        sessionId,
        response,
        captures: params.captures,
        sourceKrefs,
        spacePath: params.spaceHint,
      });

      const lines = [
        `Reflected: response buffered, ${result.captures_stored} capture(s) stored.`,
      ];
      if (result.stored_krefs?.length) {
        lines.push(...result.stored_krefs.map((k) => `- ${k}`));
      }
      if (result.edges_discovered > 0) {
        lines.push(`Edges discovered: ${result.edges_discovered}`);
      }
      return lines.join("\n");
    } catch (err) {
      if (!isUnknownToolError(err)) throw err;
      // Pre-composite backend — fall through to direct capture stores.
    }
  }

  if (captures.length === 0) {
    return (
      "Nothing stored — no captures were provided and the composite reflect " +
      "is unavailable here (no active session, custom project, or a backend " +
      "without kumiho_memory_reflect). The plugin buffers your response " +
      "automatically; call memory_reflect with captures to store facts."
    );
  }
  return storeCapturesDirect(ctx, captures, sourceKrefs, params.spaceHint);
}

export async function handleMemorySearch(
  ctx: ToolContext,
  params: { query: string; scope?: MemoryScope; limit?: number; spacePath?: string },
): Promise<string> {
  const limit = params.limit ?? ctx.config.topK;
  const spacePaths = params.spacePath ? [params.spacePath] : undefined;

  // Search long-term memory
  const results = await ctx.client.memoryRetrieve({
    query: params.query,
    limit,
    spacePaths,
  });

  // If scope is "session" and we have a session, also search working memory
  if (
    (params.scope === "session" || params.scope === "all") &&
    ctx.currentSessionId
  ) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const sessionSection = working.messages
        .filter((m) =>
          m.content.toLowerCase().includes(params.query.toLowerCase()),
        )
        .map((m) => `[session] ${m.role}: ${m.content}`)
        .join("\n");

      if (sessionSection) {
        const longTermSection =
          results.length > 0
            ? results.map(formatMemoryEntry).join("\n\n")
            : "No long-term memories found.";

        return `## Long-term Memories\n\n${longTermSection}\n\n## Session Memories\n\n${sessionSection}`;
      }
    }
  }

  if (results.length === 0) {
    return "No memories found matching your query.";
  }

  return results.map(formatMemoryEntry).join("\n\n");
}

export async function handleMemoryStore(
  ctx: ToolContext,
  params: {
    content: string;
    type?: MemoryType;
    title?: string;
    topics?: string[];
    spaceHint?: string;
  },
): Promise<string> {
  const type = params.type ?? "fact";
  const title =
    params.title ?? params.content.slice(0, 60) + (params.content.length > 60 ? "..." : "");

  const result = await ctx.client.memoryStore({
    type,
    title,
    summary: params.content,
    userText: params.content,
    assistantText: "",
    topics: params.topics,
    spaceHint: params.spaceHint,
    tags: [type, "user-stored"],
  });

  ctx.logger.info(`Stored memory: ${result.item_kref}`);

  return `Memory stored successfully.\nKref: ${result.item_kref}\nSpace: ${result.space_path}`;
}

export async function handleMemoryGet(
  ctx: ToolContext,
  params: { kref: string; includeArtifact?: boolean },
): Promise<string> {
  const entry = await ctx.client.getRevision(params.kref, params.includeArtifact ?? false);
  return formatMemoryEntry(entry);
}

export async function handleMemoryList(
  ctx: ToolContext,
  params: { scope?: MemoryScope; limit?: number; spacePath?: string },
): Promise<string> {
  const limit = params.limit ?? 10;
  const sections: string[] = [];

  // Long-term memories
  if (params.scope !== "session") {
    const results = await ctx.client.memoryRetrieve({
      query: "*",
      limit,
      spacePaths: params.spacePath ? [params.spacePath] : undefined,
    });

    if (results.length > 0) {
      sections.push(
        "## Long-term Memories\n\n" +
          results.map(formatMemoryEntry).join("\n\n"),
      );
    } else {
      sections.push("## Long-term Memories\n\nNo memories stored yet.");
    }
  }

  // Session memories
  if (
    (params.scope === "session" || params.scope === "all" || !params.scope) &&
    ctx.currentSessionId
  ) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const msgs = working.messages
        .map((m) => `- **${m.role}** (${m.timestamp}): ${m.content.slice(0, 100)}`)
        .join("\n");
      sections.push(
        `## Session Memories (${working.message_count} messages, TTL: ${working.ttl_remaining}s)\n\n${msgs}`,
      );
    }
  }

  return sections.join("\n\n") || "No memories found.";
}

export async function handleMemoryForget(
  ctx: ToolContext,
  params: { kref?: string; query?: string },
): Promise<string> {
  if (params.kref) {
    await ctx.client.memoryDeprecate(params.kref);
    return `Memory deprecated: ${params.kref}`;
  }

  if (params.query) {
    const results = await ctx.client.memoryRetrieve({
      query: params.query,
      limit: 5,
    });

    if (results.length === 0) {
      return "No memories found matching your query.";
    }

    const deprecated: string[] = [];
    for (const entry of results) {
      await ctx.client.memoryDeprecate(entry.kref);
      deprecated.push(entry.kref);
    }

    return `Deprecated ${deprecated.length} memories:\n${deprecated.join("\n")}`;
  }

  return "Please provide either a kref or a query to identify memories to forget.";
}

export async function handleMemoryConsolidate(
  ctx: ToolContext,
  params: { sessionId?: string },
): Promise<string> {
  const sessionId = params.sessionId ?? ctx.currentSessionId;
  if (!sessionId) {
    return "No active session to consolidate.";
  }

  const result = await ctx.client.consolidateSession(sessionId);
  if (!result.success) {
    if ((result.error ?? "").includes("No messages to consolidate")) {
      return "Session is empty, nothing to consolidate.";
    }
    return `Session consolidation failed: ${result.error ?? "unknown error"}`;
  }

  if (!result.store_result) {
    return "Session consolidated, but no store result was returned.";
  }

  ctx.logger.info(`Consolidated session ${sessionId}: ${result.store_result.item_kref}`);

  return (
    `Session consolidated successfully.\n` +
    `Kref: ${result.store_result.item_kref}\n` +
    `Space: ${result.store_result.space_path}`
  );
}

export async function handleMemoryDream(ctx: ToolContext): Promise<string> {
  const modelConfig = resolveDreamModelConfig(ctx.config);
  const stats = await ctx.client.triggerDreamState(modelConfig);

  if (!stats.success && stats.errors.length > 0) {
    return `Dream State failed:\n${stats.errors.map((e) => `  - ${e}`).join("\n")}`;
  }

  const lines = [
    "Dream State consolidation complete.",
    "",
    `Events processed: ${stats.events_processed}`,
    `Revisions assessed: ${stats.revisions_assessed}`,
    `Deprecated: ${stats.deprecated}`,
    `Tags added: ${stats.tags_added}`,
    `Metadata updated: ${stats.metadata_updated}`,
    `Edges created: ${stats.edges_created}`,
    `Duration: ${stats.duration_ms}ms`,
  ];

  if (stats.report_kref) {
    lines.push(`Report kref: ${stats.report_kref}`);
  }

  if (stats.errors.length > 0) {
    lines.push("", `Errors: ${stats.errors.length}`, ...stats.errors.map((e) => `  - ${e}`));
  }

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Tool dispatch map
// ---------------------------------------------------------------------------

export type ToolName = keyof typeof TOOL_SCHEMAS;

export const TOOL_HANDLERS: Record<
  ToolName,
  (ctx: ToolContext, params: Record<string, unknown>) => Promise<string>
> = {
  memory_engage: (ctx, p) =>
    handleMemoryEngage(ctx, p as Parameters<typeof handleMemoryEngage>[1]),
  memory_reflect: (ctx, p) =>
    handleMemoryReflect(ctx, p as Parameters<typeof handleMemoryReflect>[1]),
  memory_search: (ctx, p) =>
    handleMemorySearch(ctx, p as Parameters<typeof handleMemorySearch>[1]),
  memory_store: (ctx, p) =>
    handleMemoryStore(ctx, p as Parameters<typeof handleMemoryStore>[1]),
  memory_get: (ctx, p) =>
    handleMemoryGet(ctx, p as Parameters<typeof handleMemoryGet>[1]),
  memory_list: (ctx, p) =>
    handleMemoryList(ctx, p as Parameters<typeof handleMemoryList>[1]),
  memory_forget: (ctx, p) =>
    handleMemoryForget(ctx, p as Parameters<typeof handleMemoryForget>[1]),
  memory_consolidate: (ctx, p) =>
    handleMemoryConsolidate(ctx, p as Parameters<typeof handleMemoryConsolidate>[1]),
  memory_dream: (ctx) => handleMemoryDream(ctx),
  creative_capture: (ctx, p) =>
    creativeCaptureHandler(ctx, p as Parameters<typeof creativeCaptureHandler>[1]),
  creative_job_status: (ctx, p) =>
    creativeJobStatusHandler(ctx, p as { jobId: string }),
  creative_recall: (ctx, p) =>
    creativeRecallHandler(ctx, p as Parameters<typeof creativeRecallHandler>[1]),
};
