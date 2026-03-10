import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  creativeRecallHandler,
  creativeCaptureHandler,
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
