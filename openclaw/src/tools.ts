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

import type { KumihoClient } from "./client.js";
import type { ResolvedConfig, MemoryScope, MemoryType, MemoryEntry } from "./types.js";
import { creativeCaptureHandler, projectRecallHandler } from "./creative.js";

// ---------------------------------------------------------------------------
// Tool parameter schemas (JSON Schema for OpenClaw tool registry)
// ---------------------------------------------------------------------------

export const TOOL_SCHEMAS = {
  memory_search: {
    description:
      "Search Kumiho long-term memory using a natural language query. " +
      "Returns relevant memories across all channels the user has interacted on.",
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
      "Explicitly store a fact, decision, or summary in Kumiho long-term memory. " +
      "Use this when the user asks you to remember something specific.",
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
          enum: ["document", "code", "design", "plan", "analysis", "other"],
          description: "Kind of creative output (default: document)",
        },
        project: {
          type: "string",
          description:
            "Project space slug (e.g. 'blog-post-jan25', 'api-refactor'). " +
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
      required: ["title", "content", "project"],
    },
  },

  project_recall: {
    description:
      "Search and list creative outputs stored in a project space. " +
      "Use this before continuing work on a project to recall previous outputs, " +
      "decisions, and artifacts. Returns krefs you can pass as sourceMemoryKref " +
      "when capturing new outputs derived from existing work.",
    parameters: {
      type: "object" as const,
      properties: {
        project: {
          type: "string",
          description: "Project space slug to search within",
        },
        query: {
          type: "string",
          description: "Natural language search query (optional — omit to list all)",
        },
        kind: {
          type: "string",
          enum: ["document", "code", "design", "plan", "analysis", "other"],
          description: "Filter by creative kind",
        },
        limit: {
          type: "number",
          description: "Max results to return (default: topK from config)",
        },
      },
      required: ["project"],
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
    topics: params.topics,
    spaceHint: params.spaceHint,
    tags: [type, "user-stored"],
  });

  ctx.logger.info(`Stored memory: ${result.item_kref}`);

  return `Memory stored successfully.\nKref: ${result.item_kref}\nSpace: ${result.space_path}`;
}

export async function handleMemoryGet(
  ctx: ToolContext,
  params: { kref: string },
): Promise<string> {
  const entry = await ctx.client.getRevision(params.kref);
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

  // Fetch working memory
  const working = await ctx.client.chatGet(sessionId, 200);
  if (working.messages.length === 0) {
    return "Session is empty, nothing to consolidate.";
  }

  // Build summary from messages (the cloud-side will handle LLM summarization)
  const conversationText = working.messages
    .map((m) => `${m.role}: ${m.content}`)
    .join("\n");

  const result = await ctx.client.memoryStore({
    type: "summary",
    title: `Session consolidation: ${sessionId}`,
    summary: `Consolidated ${working.message_count} messages from session ${sessionId}`,
    userText: conversationText,
    tags: ["consolidated", "summary"],
    metadata: {
      session_id: sessionId,
      message_count: working.message_count,
    },
  });

  // Clear working memory after consolidation
  await ctx.client.chatClear(sessionId);

  ctx.logger.info(`Consolidated session ${sessionId}: ${result.item_kref}`);

  return (
    `Session consolidated successfully.\n` +
    `Messages: ${working.message_count}\n` +
    `Kref: ${result.item_kref}\n` +
    `Space: ${result.space_path}`
  );
}

export async function handleMemoryDream(ctx: ToolContext): Promise<string> {
  const dm = ctx.config.dreamStateModel;
  const modelConfig = dm?.provider || dm?.model || dm?.apiKey ? dm : undefined;
  const stats = await ctx.client.triggerDreamState(modelConfig);

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
  project_recall: (ctx, p) =>
    projectRecallHandler(ctx, p as Parameters<typeof projectRecallHandler>[1]),
};
