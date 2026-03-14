// src/creative.ts
function coerceKind(raw) {
  if (typeof raw === "string" && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(raw)) {
    return raw;
  }
  return "document";
}
async function creativeCaptureHandler(ctx, params) {
  const title = (params.title ?? "").trim();
  const content = (params.content ?? "").trim();
  const creativeProject = (params.creativeProject ?? "").trim();
  const project = (params.project ?? "").trim();
  if (!title) return "creative_capture: `title` is required.";
  if (!content) return "creative_capture: `content` is required.";
  if (!creativeProject) return "creative_capture: `creativeProject` is required. Specify the Kumiho project for creative outputs (e.g. 'blog-posts', 'marketing'). Do NOT use CognitiveMemory unless on a free-tier plan limited to 1 project.";
  if (!project) return "creative_capture: `project` (space slug) is required.";
  const bffUrl = ctx.config.bffEndpoint;
  if (!bffUrl) {
    return "Creative capture is not configured: `bffEndpoint` is missing.\nSet `bffEndpoint` in the Kumiho plugin config (defaults to https://api.kumiho.cloud).";
  }
  const captureParams = {
    title,
    content,
    kind: coerceKind(params.kind),
    creativeProject,
    project,
    tags: Array.isArray(params.tags) ? params.tags : [],
    sourceMemoryKref: params.sourceMemoryKref,
    metadata: params.metadata ?? {}
  };
  try {
    const token = ctx.getToken ? await ctx.getToken() : ctx.config.apiKey;
    const result = await ctx.client.creativeEnqueue(
      captureParams,
      bffUrl,
      token || ctx.config.apiKey
    );
    ctx.logger.info(
      `Creative capture queued: "${title}" \u2192 ${creativeProject}/${project} (job: ${result.jobId})`
    );
    return [
      `Creative capture queued.`,
      ``,
      `Job ID : ${result.jobId}`,
      `Title  : "${title}"`,
      `Kind   : ${captureParams.kind}`,
      `Project: ${creativeProject}/${project}`,
      params.sourceMemoryKref ? `Linked to memory kref: ${params.sourceMemoryKref}` : "",
      ``,
      `The content is being processed and linked to your memory graph in the background.`,
      `Use creative_job_status with this job ID to check progress and retrieve the resulting krefs.`
    ].filter((l) => l !== void 0).join("\n");
  } catch (err) {
    const msg = err.message;
    ctx.logger.error(`creative_capture failed: ${msg}`);
    return `Creative capture failed: ${msg}`;
  }
}
async function creativeJobStatusHandler(ctx, params) {
  const jobId = (params.jobId ?? "").trim();
  if (!jobId) return "creative_job_status: `jobId` is required.";
  const bffUrl = ctx.config.bffEndpoint;
  if (!bffUrl) {
    return "Creative job status is not configured: `bffEndpoint` is missing.";
  }
  try {
    const token = ctx.getToken ? await ctx.getToken() : ctx.config.apiKey;
    const job = await ctx.client.getCreativeJobStatus(
      jobId,
      bffUrl,
      token || ctx.config.apiKey
    );
    const lines = [
      `Job ID : ${jobId}`,
      `Status : ${job.status}`
    ];
    if (job.status === "done" && job.result) {
      lines.push("");
      if (job.result.item_kref) lines.push(`Item Kref     : ${job.result.item_kref}`);
      if (job.result.revision_kref) lines.push(`Revision Kref : ${job.result.revision_kref}`);
      if (job.result.memory_kref) lines.push(`Memory Kref   : ${job.result.memory_kref}`);
      if (job.result.space) lines.push(`Space         : ${job.result.space}`);
    } else if (job.status === "failed") {
      lines.push(`Error  : ${job.error ?? "unknown"}`);
    } else if (job.status === "pending" || job.status === "processing") {
      lines.push("", "The pipeline is still running. Check again shortly.");
    }
    return lines.join("\n");
  } catch (err) {
    const msg = err.message;
    ctx.logger.error(`creative_job_status failed: ${msg}`);
    return `Creative job status check failed: ${msg}`;
  }
}
async function creativeRecallHandler(ctx, params) {
  const space = (params.space ?? "").trim();
  if (!space) return "creative_recall: `space` (space slug) is required.";
  const kumihoProject = (params.creativeProject ?? ctx.config.project).trim();
  const context = `${kumihoProject}/${space}`;
  const kindFilter = params.kind ? coerceKind(params.kind) : void 0;
  const query = params.query?.trim();
  const limit = params.limit ?? ctx.config.topK;
  try {
    let items;
    const authToken = ctx.getToken ? await ctx.getToken() : "";
    if (query) {
      const raw = await ctx.client.callTool(
        "kumiho_fulltext_search",
        {
          query,
          context,
          kind: kindFilter,
          limit,
          include_revision_metadata: true,
          ...authToken ? { auth_token: authToken } : {}
        }
      );
      items = (raw.results ?? []).map((r) => ({ ...r.item, score: r.score }));
    } else {
      const raw = await ctx.client.callTool(
        "kumiho_search_items",
        {
          context_filter: context,
          kind_filter: kindFilter,
          include_metadata: true,
          ...authToken ? { auth_token: authToken } : {}
        }
      );
      items = raw.items ?? [];
    }
    if (items.length === 0) {
      return `No creative items found in space "${context}". Capture something first using creative_capture.`;
    }
    const lines = [`## Space: ${context}`, ""];
    for (const item of items) {
      lines.push(`### ${item.name || "(untitled)"}`);
      if (item.kind) lines.push(`Kind: ${item.kind}`);
      if (item.score != null) lines.push(`Relevance: ${item.score.toFixed(2)}`);
      lines.push(`Kref: ${item.kref}`);
      lines.push("");
    }
    return lines.join("\n");
  } catch (err) {
    const msg = err.message;
    ctx.logger.error(`creative_recall failed: ${msg}`);
    return `Creative recall failed: ${msg}`;
  }
}

// src/tools.ts
function resolveDreamModelConfig(cfg) {
  const dm = cfg.dreamStateModel ?? {};
  const explicitProvider = dm.provider || cfg.llm.provider;
  const explicitApiKey = dm.apiKey || cfg.llm.apiKey;
  if (explicitProvider && !explicitApiKey && cfg.hostLlmProvider && explicitProvider !== cfg.hostLlmProvider) {
    return void 0;
  }
  const provider = explicitProvider || cfg.hostLlmProvider;
  const model = dm.model || cfg.llm.model;
  const apiKey = explicitApiKey || cfg.hostLlmApiKey;
  if (!provider || !apiKey) return void 0;
  return {
    provider,
    model,
    apiKey
  };
}
var TOOL_SCHEMAS = {
  memory_search: {
    description: "Search Kumiho long-term memory using a natural language query. Use this proactively when the user asks about past decisions, preferences, prior work, or anything that might have been discussed in previous conversations. Never say 'I don't remember' without searching first.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Natural language search query"
        },
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope to search (default: all)"
        },
        limit: {
          type: "number",
          description: "Max results to return (default: 5)"
        },
        spacePath: {
          type: "string",
          description: "Restrict search to a specific space path (e.g. CognitiveMemory/work/team-alpha)"
        }
      },
      required: ["query"]
    }
  },
  memory_store: {
    description: "Store a fact, decision, preference, or summary in Kumiho long-term memory. Use proactively when the user states a preference, makes a decision, gives a correction, or asks you to remember something. Also store your own significant outputs (plans, analyses, decisions).",
    parameters: {
      type: "object",
      properties: {
        content: {
          type: "string",
          description: "The information to remember"
        },
        type: {
          type: "string",
          enum: ["fact", "decision", "summary"],
          description: "Memory type (default: fact)"
        },
        title: {
          type: "string",
          description: "Short title for the memory"
        },
        topics: {
          type: "array",
          items: { type: "string" },
          description: "Topic tags for categorization"
        },
        spaceHint: {
          type: "string",
          description: "Space path hint (e.g. personal/preferences)"
        }
      },
      required: ["content"]
    }
  },
  memory_get: {
    description: "Retrieve a specific memory by its kref identifier.",
    parameters: {
      type: "object",
      properties: {
        kref: {
          type: "string",
          description: "The kref identifier of the memory to retrieve"
        },
        includeArtifact: {
          type: "boolean",
          description: "Also fetch the artifact file location (default: false). Only needed for deep lookups where you need the raw content path."
        }
      },
      required: ["kref"]
    }
  },
  memory_list: {
    description: "List stored memories. Returns recent memories from Kumiho long-term storage.",
    parameters: {
      type: "object",
      properties: {
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope (default: all)"
        },
        limit: {
          type: "number",
          description: "Max memories to list (default: 10)"
        },
        spacePath: {
          type: "string",
          description: "Filter by space path"
        }
      }
    }
  },
  memory_forget: {
    description: "Delete or deprecate a memory. Can target by kref or by search query.",
    parameters: {
      type: "object",
      properties: {
        kref: {
          type: "string",
          description: "Kref of the memory to forget"
        },
        query: {
          type: "string",
          description: "Search query to find memories to forget"
        }
      }
    }
  },
  memory_consolidate: {
    description: "Trigger consolidation of the current session's working memory into long-term storage. Summarizes the conversation, redacts PII, and stores a structured summary in Kumiho Cloud.",
    parameters: {
      type: "object",
      properties: {
        sessionId: {
          type: "string",
          description: "Session ID to consolidate (default: current session)"
        }
      }
    }
  },
  memory_dream: {
    description: "Trigger a Dream State consolidation cycle. Reviews recent memories, deprecates stale ones, adds tags, and creates relationship edges.",
    parameters: {
      type: "object",
      properties: {}
    }
  },
  creative_capture: {
    description: "Capture a creative output (document, code, plan, etc.) into the Kumiho graph. The artifact is stored asynchronously \u2014 this tool returns immediately with a job ID. Pass sourceMemoryKref with the kref of the recalled memory that produced this output so the graph links the creative artifact back to its cognitive origin (DERIVED_FROM edge).",
    parameters: {
      type: "object",
      properties: {
        title: {
          type: "string",
          description: "Title or name of the creative artifact"
        },
        content: {
          type: "string",
          description: "The content body to capture (text, code, markdown, etc.)"
        },
        kind: {
          type: "string",
          description: "Item kind \u2014 any URL-safe string (e.g. 'document', 'code', 'blog-post', 'email', 'design', 'plan', 'analysis'). Default: document."
        },
        creativeProject: {
          type: "string",
          description: "Kumiho project name for creative outputs (e.g. 'blog-posts', 'marketing'). Must NOT be CognitiveMemory \u2014 creative outputs live in their own project. Exception: free-tier users limited to 1 project may use CognitiveMemory as fallback."
        },
        project: {
          type: "string",
          description: "Space slug within the creative project (e.g. 'blog-post-jan25', 'api-refactor'). Groups outputs from the same project together in the graph."
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "Optional topic tags for discovery"
        },
        sourceMemoryKref: {
          type: "string",
          description: "Kref of the recalled memory that inspired or drove this output. Creates a DERIVED_FROM edge so the artifact can be traced to its cognitive origin."
        },
        metadata: {
          type: "object",
          description: "Extra metadata key-value pairs stored on the revision"
        }
      },
      required: ["title", "content", "creativeProject", "project"]
    }
  },
  creative_job_status: {
    description: "Check the status of a creative capture job. Returns the resulting krefs (item_kref, revision_kref, memory_kref) when the pipeline completes, which can be shared with sub-agents or referenced in follow-up operations.",
    parameters: {
      type: "object",
      properties: {
        jobId: {
          type: "string",
          description: "The job ID returned by creative_capture"
        }
      },
      required: ["jobId"]
    }
  },
  creative_recall: {
    description: "Search and list creative outputs stored in a space. Use this before continuing work on a project to recall previous outputs, decisions, and artifacts. Returns krefs you can pass as sourceMemoryKref when capturing new outputs derived from existing work.",
    parameters: {
      type: "object",
      properties: {
        creativeProject: {
          type: "string",
          description: "Kumiho project name for creative outputs (e.g. 'blog-posts', 'marketing'). Defaults to the plugin's configured project if omitted. Must match the creativeProject used in creative_capture."
        },
        space: {
          type: "string",
          description: "Space slug to search within (e.g. 'blog-drafts', 'api-refactor'). Matches the `project` (space slug) parameter of creative_capture."
        },
        query: {
          type: "string",
          description: "Natural language search query (optional \u2014 omit to list all)"
        },
        kind: {
          type: "string",
          description: "Filter by item kind (e.g. 'document', 'code', 'blog-post')"
        },
        limit: {
          type: "number",
          description: "Max results to return (default: topK from config)"
        }
      },
      required: ["space"]
    }
  }
};
function formatMemoryEntry(entry) {
  const parts = [`[${entry.type}] ${entry.title || "(untitled)"}`];
  if (entry.summary) parts.push(entry.summary);
  if (entry.topics?.length) parts.push(`Topics: ${entry.topics.join(", ")}`);
  if (entry.kref) parts.push(`Kref: ${entry.kref}`);
  if (entry.score != null) parts.push(`Score: ${entry.score.toFixed(2)}`);
  return parts.join("\n");
}
async function handleMemorySearch(ctx, params) {
  const limit = params.limit ?? ctx.config.topK;
  const spacePaths = params.spacePath ? [params.spacePath] : void 0;
  const results = await ctx.client.memoryRetrieve({
    query: params.query,
    limit,
    spacePaths
  });
  if ((params.scope === "session" || params.scope === "all") && ctx.currentSessionId) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const sessionSection = working.messages.filter(
        (m) => m.content.toLowerCase().includes(params.query.toLowerCase())
      ).map((m) => `[session] ${m.role}: ${m.content}`).join("\n");
      if (sessionSection) {
        const longTermSection = results.length > 0 ? results.map(formatMemoryEntry).join("\n\n") : "No long-term memories found.";
        return `## Long-term Memories

${longTermSection}

## Session Memories

${sessionSection}`;
      }
    }
  }
  if (results.length === 0) {
    return "No memories found matching your query.";
  }
  return results.map(formatMemoryEntry).join("\n\n");
}
async function handleMemoryStore(ctx, params) {
  const type = params.type ?? "fact";
  const title = params.title ?? params.content.slice(0, 60) + (params.content.length > 60 ? "..." : "");
  const result = await ctx.client.memoryStore({
    type,
    title,
    summary: params.content,
    userText: params.content,
    assistantText: "",
    topics: params.topics,
    spaceHint: params.spaceHint,
    tags: [type, "user-stored"]
  });
  ctx.logger.info(`Stored memory: ${result.item_kref}`);
  return `Memory stored successfully.
Kref: ${result.item_kref}
Space: ${result.space_path}`;
}
async function handleMemoryGet(ctx, params) {
  const entry = await ctx.client.getRevision(params.kref, params.includeArtifact ?? false);
  return formatMemoryEntry(entry);
}
async function handleMemoryList(ctx, params) {
  const limit = params.limit ?? 10;
  const sections = [];
  if (params.scope !== "session") {
    const results = await ctx.client.memoryRetrieve({
      query: "*",
      limit,
      spacePaths: params.spacePath ? [params.spacePath] : void 0
    });
    if (results.length > 0) {
      sections.push(
        "## Long-term Memories\n\n" + results.map(formatMemoryEntry).join("\n\n")
      );
    } else {
      sections.push("## Long-term Memories\n\nNo memories stored yet.");
    }
  }
  if ((params.scope === "session" || params.scope === "all" || !params.scope) && ctx.currentSessionId) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const msgs = working.messages.map((m) => `- **${m.role}** (${m.timestamp}): ${m.content.slice(0, 100)}`).join("\n");
      sections.push(
        `## Session Memories (${working.message_count} messages, TTL: ${working.ttl_remaining}s)

${msgs}`
      );
    }
  }
  return sections.join("\n\n") || "No memories found.";
}
async function handleMemoryForget(ctx, params) {
  if (params.kref) {
    await ctx.client.memoryDeprecate(params.kref);
    return `Memory deprecated: ${params.kref}`;
  }
  if (params.query) {
    const results = await ctx.client.memoryRetrieve({
      query: params.query,
      limit: 5
    });
    if (results.length === 0) {
      return "No memories found matching your query.";
    }
    const deprecated = [];
    for (const entry of results) {
      await ctx.client.memoryDeprecate(entry.kref);
      deprecated.push(entry.kref);
    }
    return `Deprecated ${deprecated.length} memories:
${deprecated.join("\n")}`;
  }
  return "Please provide either a kref or a query to identify memories to forget.";
}
async function handleMemoryConsolidate(ctx, params) {
  const sessionId = params.sessionId ?? ctx.currentSessionId;
  if (!sessionId) {
    return "No active session to consolidate.";
  }
  const working = await ctx.client.chatGet(sessionId, 200);
  if (working.messages.length === 0) {
    return "Session is empty, nothing to consolidate.";
  }
  const conversationText = working.messages.map((m) => `${m.role}: ${m.content}`).join("\n");
  const result = await ctx.client.memoryStore({
    type: "summary",
    title: `Session consolidation: ${sessionId}`,
    summary: `Consolidated ${working.message_count} messages from session ${sessionId}`,
    userText: conversationText,
    tags: ["consolidated", "summary"],
    metadata: {
      session_id: sessionId,
      message_count: working.message_count
    }
  });
  await ctx.client.chatClear(sessionId);
  ctx.logger.info(`Consolidated session ${sessionId}: ${result.item_kref}`);
  return `Session consolidated successfully.
Messages: ${working.message_count}
Kref: ${result.item_kref}
Space: ${result.space_path}`;
}
async function handleMemoryDream(ctx) {
  const modelConfig = resolveDreamModelConfig(ctx.config);
  const stats = await ctx.client.triggerDreamState(modelConfig);
  if (!stats.success && stats.errors.length > 0) {
    return `Dream State failed:
${stats.errors.map((e) => `  - ${e}`).join("\n")}`;
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
    `Duration: ${stats.duration_ms}ms`
  ];
  if (stats.report_kref) {
    lines.push(`Report kref: ${stats.report_kref}`);
  }
  if (stats.errors.length > 0) {
    lines.push("", `Errors: ${stats.errors.length}`, ...stats.errors.map((e) => `  - ${e}`));
  }
  return lines.join("\n");
}
var TOOL_HANDLERS = {
  memory_search: (ctx, p) => handleMemorySearch(ctx, p),
  memory_store: (ctx, p) => handleMemoryStore(ctx, p),
  memory_get: (ctx, p) => handleMemoryGet(ctx, p),
  memory_list: (ctx, p) => handleMemoryList(ctx, p),
  memory_forget: (ctx, p) => handleMemoryForget(ctx, p),
  memory_consolidate: (ctx, p) => handleMemoryConsolidate(ctx, p),
  memory_dream: (ctx) => handleMemoryDream(ctx),
  creative_capture: (ctx, p) => creativeCaptureHandler(ctx, p),
  creative_job_status: (ctx, p) => creativeJobStatusHandler(ctx, p),
  creative_recall: (ctx, p) => creativeRecallHandler(ctx, p)
};
export {
  TOOL_HANDLERS,
  TOOL_SCHEMAS,
  handleMemoryConsolidate,
  handleMemoryDream,
  handleMemoryForget,
  handleMemoryGet,
  handleMemoryList,
  handleMemorySearch,
  handleMemoryStore
};
