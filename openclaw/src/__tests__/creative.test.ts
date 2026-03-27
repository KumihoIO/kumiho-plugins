import { describe, it, expect, vi } from "vitest";
import {
  creativeRecallHandler,
  creativeCaptureHandler,
  creativeJobStatusHandler,
  type CreativeToolContext,
} from "../creative.js";
import type { ResolvedConfig } from "../types.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseConfig: ResolvedConfig = {
  mode: "local",
  apiKey: "",
  endpoint: "",
  bffEndpoint: "https://api.kumiho.cloud",
  project: "CognitiveMemory",
  userId: "test-user",
  autoCapture: false,
  autoRecall: false,
  localSummarization: false,
  consolidationThreshold: 20,
  idleConsolidationTimeout: 300,
  sessionTtl: 3600,
  topK: 10,
  searchThreshold: 0.5,
  artifactDir: "/tmp",
  piiRedaction: false,
  dreamStateSchedule: "off",
  dreamStateModel: {},
  consolidationModel: {},
  llm: {},
  hostLlmApiKey: "",
  hostLlmProvider: "",
  privacy: { uploadSummariesOnly: true, localArtifacts: true, storeTranscriptions: true },
  local: { pythonPath: "python", command: "kumiho-mcp", timeout: 30000 },
};

function makeCtx(
  callToolImpl: (tool: string, params: Record<string, unknown>) => Promise<unknown>,
  config: ResolvedConfig = baseConfig,
): CreativeToolContext {
  return {
    client: { callTool: callToolImpl } as unknown as CreativeToolContext["client"],
    config,
    logger: { info: vi.fn(), error: vi.fn() },
  };
}

// ---------------------------------------------------------------------------
// creativeRecallHandler
// ---------------------------------------------------------------------------

describe("creativeRecallHandler", () => {
  it("returns error when space is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeRecallHandler(ctx, {});
    expect(result).toContain("`space`");
    expect(result).toContain("required");
  });

  it("calls kumiho_fulltext_search when query is provided", async () => {
    const callTool = vi.fn().mockResolvedValue({
      results: [
        { item: { kref: "kref://openclaw/blog-drafts/post-1", name: "Blog Post 1", kind: "document" }, score: 0.92 },
      ],
    });
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, {
      space: "blog-drafts",
      creativeProject: "openclaw",
      query: "blog post about TypeScript",
    });

    expect(callTool).toHaveBeenCalledOnce();
    const [toolName, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(toolName).toBe("kumiho_fulltext_search");
    expect(params.context).toBe("openclaw/blog-drafts");
    expect(params.query).toBe("blog post about TypeScript");
    expect(result).toContain("Blog Post 1");
    expect(result).toContain("kref://openclaw/blog-drafts/post-1");
    expect(result).toContain("0.92");
  });

  it("calls kumiho_search_items when no query is provided", async () => {
    const callTool = vi.fn().mockResolvedValue({
      items: [
        { kref: "kref://openclaw/blog-drafts/post-2", name: "Blog Post 2", kind: "document" },
      ],
    });
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, {
      space: "blog-drafts",
      creativeProject: "openclaw",
    });

    expect(callTool).toHaveBeenCalledOnce();
    const [toolName, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(toolName).toBe("kumiho_search_items");
    expect(params.context_filter).toBe("openclaw/blog-drafts");
    expect(result).toContain("Blog Post 2");
    expect(result).toContain("kref://openclaw/blog-drafts/post-2");
  });

  it("defaults creativeProject to config.project when not specified", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx = makeCtx(callTool, { ...baseConfig, project: "my-project" });

    await creativeRecallHandler(ctx, { space: "drafts" });

    const [, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(params.context_filter).toBe("my-project/drafts");
  });

  it("passes kind_filter when kind is provided (list mode)", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx = makeCtx(callTool);

    await creativeRecallHandler(ctx, { space: "drafts", kind: "code" });

    const [toolName, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(toolName).toBe("kumiho_search_items");
    expect(params.kind_filter).toBe("code");
  });

  it("passes kind when kind is provided (search mode)", async () => {
    const callTool = vi.fn().mockResolvedValue({ results: [] });
    const ctx = makeCtx(callTool);

    await creativeRecallHandler(ctx, { space: "drafts", query: "refactor", kind: "code" });

    const [toolName, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(toolName).toBe("kumiho_fulltext_search");
    expect(params.kind).toBe("code");
  });

  it("returns empty-space message when no items found", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, { space: "empty-space", creativeProject: "openclaw" });

    expect(result).toContain("No creative items found");
    expect(result).toContain("openclaw/empty-space");
  });

  it("returns error text on callTool failure", async () => {
    const callTool = vi.fn().mockRejectedValue(new Error("MCP timeout"));
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, { space: "blog-drafts" });

    expect(result).toContain("MCP timeout");
  });

  it("handles missing results/items gracefully (treats as empty)", async () => {
    // Server returns neither results nor items key
    const callTool = vi.fn().mockResolvedValue({});
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, { space: "blog-drafts" });
    expect(result).toContain("No creative items found");
  });

  it("formats relevance score to 2 decimal places", async () => {
    const callTool = vi.fn().mockResolvedValue({
      results: [
        { item: { kref: "kref://p/s/x", name: "Item X" }, score: 0.87654 },
      ],
    });
    const ctx = makeCtx(callTool);

    const result = await creativeRecallHandler(ctx, { space: "s", query: "x" });
    expect(result).toContain("0.88");
  });
});

// ---------------------------------------------------------------------------
// creativeCaptureHandler
// ---------------------------------------------------------------------------

describe("creativeCaptureHandler", () => {
  it("returns error when title is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeCaptureHandler(ctx, { content: "c", creativeProject: "p", project: "s" });
    expect(result).toContain("`title`");
  });

  it("returns error when content is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeCaptureHandler(ctx, { title: "t", creativeProject: "p", project: "s" });
    expect(result).toContain("`content`");
  });

  it("returns error when creativeProject is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeCaptureHandler(ctx, { title: "t", content: "c", project: "s" });
    expect(result).toContain("`creativeProject`");
  });

  it("returns error when project (space) is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeCaptureHandler(ctx, { title: "t", content: "c", creativeProject: "p" });
    expect(result).toContain("`project`");
  });

  it("returns error when bffEndpoint is not configured", async () => {
    const ctx = makeCtx(vi.fn(), { ...baseConfig, bffEndpoint: "" });
    const result = await creativeCaptureHandler(ctx, {
      title: "t", content: "c", creativeProject: "p", project: "s",
    });
    expect(result).toContain("bffEndpoint");
  });

  it("calls creativeEnqueue and returns job ID", async () => {
    const creativeEnqueue = vi.fn().mockResolvedValue({ jobId: "job-123" });
    const ctx: CreativeToolContext = {
      client: { creativeEnqueue } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeCaptureHandler(ctx, {
      title: "My Post",
      content: "Some content",
      creativeProject: "openclaw",
      project: "blog-drafts",
    });

    expect(creativeEnqueue).toHaveBeenCalledOnce();
    expect(result).toContain("job-123");
    expect(result).toContain("My Post");
    expect(result).toContain("openclaw/blog-drafts");
  });

  it("includes sourceMemoryKref in output when provided", async () => {
    const creativeEnqueue = vi.fn().mockResolvedValue({ jobId: "job-456" });
    const ctx: CreativeToolContext = {
      client: { creativeEnqueue } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeCaptureHandler(ctx, {
      title: "Post",
      content: "Content",
      creativeProject: "openclaw",
      project: "drafts",
      sourceMemoryKref: "kref://CognitiveMemory/personal/memory-1",
    });

    expect(result).toContain("kref://CognitiveMemory/personal/memory-1");
  });
});

// ---------------------------------------------------------------------------
// creativeRecallHandler — auth_token pass-through (cross-project scope fix)
// ---------------------------------------------------------------------------

describe("creativeRecallHandler — getToken / auth_token", () => {
  it("passes auth_token from getToken to kumiho_fulltext_search", async () => {
    const callTool = vi.fn().mockResolvedValue({ results: [] });
    const ctx: CreativeToolContext = {
      client: { callTool } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
      getToken: vi.fn().mockResolvedValue("tok-abc123"),
    };

    await creativeRecallHandler(ctx, { space: "blog-drafts", creativeProject: "openclaw", query: "test" });

    const [, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(params.auth_token).toBe("tok-abc123");
  });

  it("passes auth_token from getToken to kumiho_search_items (list mode)", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx: CreativeToolContext = {
      client: { callTool } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
      getToken: vi.fn().mockResolvedValue("tok-xyz789"),
    };

    await creativeRecallHandler(ctx, { space: "blog-drafts", creativeProject: "openclaw" });

    const [, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(params.auth_token).toBe("tok-xyz789");
  });

  it("omits auth_token when getToken is not provided", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx = makeCtx(callTool); // no getToken

    await creativeRecallHandler(ctx, { space: "blog-drafts" });

    const [, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(params.auth_token).toBeUndefined();
  });

  it("omits auth_token when getToken returns empty string", async () => {
    const callTool = vi.fn().mockResolvedValue({ items: [] });
    const ctx: CreativeToolContext = {
      client: { callTool } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
      getToken: vi.fn().mockResolvedValue(""),
    };

    await creativeRecallHandler(ctx, { space: "blog-drafts" });

    const [, params] = callTool.mock.calls[0] as [string, Record<string, unknown>];
    expect(params.auth_token).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// creativeJobStatusHandler
// ---------------------------------------------------------------------------

describe("creativeJobStatusHandler", () => {
  it("returns error when jobId is missing", async () => {
    const ctx = makeCtx(vi.fn());
    const result = await creativeJobStatusHandler(ctx, { jobId: "" });
    expect(result).toContain("`jobId`");
    expect(result).toContain("required");
  });

  it("returns error when bffEndpoint is not configured", async () => {
    const ctx = makeCtx(vi.fn(), { ...baseConfig, bffEndpoint: "" });
    const result = await creativeJobStatusHandler(ctx, { jobId: "job-abc" });
    expect(result).toContain("bffEndpoint");
  });

  it("returns pending status correctly", async () => {
    const getCreativeJobStatus = vi.fn().mockResolvedValue({ status: "pending" });
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeJobStatusHandler(ctx, { jobId: "job-pending" });
    expect(result).toContain("job-pending");
    expect(result).toContain("pending");
    expect(result).toContain("still running");
  });

  it("returns processing status correctly", async () => {
    const getCreativeJobStatus = vi.fn().mockResolvedValue({ status: "processing" });
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeJobStatusHandler(ctx, { jobId: "job-proc" });
    expect(result).toContain("processing");
    expect(result).toContain("still running");
  });

  it("returns krefs when job is done", async () => {
    const getCreativeJobStatus = vi.fn().mockResolvedValue({
      status: "done",
      result: {
        item_kref: "kref://openclaw/blog-drafts/post-1.document",
        revision_kref: "kref://openclaw/blog-drafts/post-1.document?r=1",
        memory_kref: "kref://CognitiveMemory/personal/mem-1.conversation?r=1",
        space: "openclaw/blog-drafts",
      },
    });
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeJobStatusHandler(ctx, { jobId: "job-done" });
    expect(result).toContain("done");
    expect(result).toContain("kref://openclaw/blog-drafts/post-1.document");
    expect(result).toContain("kref://openclaw/blog-drafts/post-1.document?r=1");
    expect(result).toContain("kref://CognitiveMemory/personal/mem-1.conversation?r=1");
    expect(result).toContain("openclaw/blog-drafts");
  });

  it("returns error message when job failed", async () => {
    const getCreativeJobStatus = vi.fn().mockResolvedValue({
      status: "failed",
      error: "Neo4j write timeout",
    });
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeJobStatusHandler(ctx, { jobId: "job-fail" });
    expect(result).toContain("failed");
    expect(result).toContain("Neo4j write timeout");
  });

  it("uses getToken for auth when provided", async () => {
    const getCreativeJobStatus = vi.fn().mockResolvedValue({ status: "pending" });
    const getToken = vi.fn().mockResolvedValue("tok-fresh");
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
      getToken,
    };

    await creativeJobStatusHandler(ctx, { jobId: "job-abc" });
    expect(getToken).toHaveBeenCalledOnce();
    expect(getCreativeJobStatus).toHaveBeenCalledWith("job-abc", baseConfig.bffEndpoint, "tok-fresh");
  });

  it("returns error text on client failure", async () => {
    const getCreativeJobStatus = vi.fn().mockRejectedValue(new Error("network error"));
    const ctx: CreativeToolContext = {
      client: { getCreativeJobStatus } as unknown as CreativeToolContext["client"],
      config: baseConfig,
      logger: { info: vi.fn(), error: vi.fn() },
    };

    const result = await creativeJobStatusHandler(ctx, { jobId: "job-err" });
    expect(result).toContain("network error");
  });
});
