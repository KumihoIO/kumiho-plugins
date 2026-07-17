import { describe, expect, it, vi } from "vitest";

import { handleMemoryConsolidate, type ToolContext } from "../tools.js";
import type { ResolvedConfig } from "../types.js";

const baseConfig: ResolvedConfig = {
  mode: "local",
  apiKey: "",
  endpoint: "",
  bffEndpoint: "",
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
  privacy: { uploadSummariesOnly: false, localArtifacts: true, storeTranscriptions: true },
  ce: { enabled: false, endpoint: "127.0.0.1:9190", redisUrl: "redis://127.0.0.1:6379" },
  local: { pythonPath: "python", command: "kumiho-mcp", timeout: 30000 },
};

function makeContext(client: Partial<ToolContext["client"]>, currentSessionId: string | null): ToolContext {
  return {
    client: client as ToolContext["client"],
    config: baseConfig,
    currentSessionId,
    logger: {
      info: vi.fn(),
      error: vi.fn(),
    },
  };
}

describe("handleMemoryConsolidate", () => {
  it("returns a friendly message when there is no active session", async () => {
    const result = await handleMemoryConsolidate(makeContext({}, null), {});
    expect(result).toBe("No active session to consolidate.");
  });

  it("uses the backend consolidation tool instead of rebuilding the summary locally", async () => {
    const consolidateSession = vi.fn().mockResolvedValue({
      success: true,
      summary: "Proper backend summary",
      store_result: {
        item_kref: "kref://item/123",
        revision_kref: "kref://revision/456",
        space_path: "CognitiveMemory/personal",
        summary: "Proper backend summary",
      },
    });
    const chatGet = vi.fn();
    const memoryStore = vi.fn();
    const chatClear = vi.fn();

    const ctx = makeContext(
      { consolidateSession, chatGet, memoryStore, chatClear },
      "personal:user-test:20260314:001",
    );

    const result = await handleMemoryConsolidate(ctx, {});

    expect(consolidateSession).toHaveBeenCalledWith("personal:user-test:20260314:001");
    expect(chatGet).not.toHaveBeenCalled();
    expect(memoryStore).not.toHaveBeenCalled();
    expect(chatClear).not.toHaveBeenCalled();
    expect(result).toContain("Session consolidated successfully.");
    expect(result).toContain("kref://item/123");
  });

  it("maps backend no-message errors to the existing empty-session response", async () => {
    const consolidateSession = vi.fn().mockResolvedValue({
      success: false,
      error: "No messages to consolidate",
    });

    const result = await handleMemoryConsolidate(
      makeContext({ consolidateSession }, "personal:user-test:20260314:001"),
      {},
    );

    expect(result).toBe("Session is empty, nothing to consolidate.");
  });
});

// ---------------------------------------------------------------------------
// Two-reflex agent tools — memory_engage / memory_reflect
// ---------------------------------------------------------------------------

import { handleMemoryEngage, handleMemoryReflect } from "../tools.js";

const memoryEntry = {
  kref: "kref://memory/1?r=1",
  type: "fact" as const,
  title: "Dark mode",
  summary: "User prefers dark mode",
  topics: [],
};

describe("handleMemoryEngage", () => {
  it("returns formatted results with source_krefs for reflect", async () => {
    const memoryEngage = vi.fn().mockResolvedValue({
      context: "ctx",
      results: [memoryEntry],
      sourceKrefs: ["kref://memory/1?r=1"],
      deduplicated: false,
    });
    const ctx = makeContext({ memoryEngage }, "sess-1");

    const result = await handleMemoryEngage(ctx, { query: "editor theme" });

    expect(memoryEngage).toHaveBeenCalledWith({
      query: "editor theme",
      limit: baseConfig.topK,
      spacePaths: undefined,
      minScore: baseConfig.searchThreshold,
      graphAugmented: undefined,
    });
    expect(result).toContain("Dark mode");
    expect(result).toContain("source_krefs");
    expect(result).toContain("kref://memory/1?r=1");
  });

  it("reports server-side deduplication instead of an empty miss", async () => {
    const memoryEngage = vi.fn().mockResolvedValue({
      context: "",
      results: [],
      sourceKrefs: [],
      deduplicated: true,
    });
    const ctx = makeContext({ memoryEngage }, "sess-1");

    const result = await handleMemoryEngage(ctx, { query: "same query" });

    expect(result).toContain("Deduplicated");
  });

  it("falls back to memory_search on pre-composite backends", async () => {
    const memoryEngage = vi.fn().mockRejectedValue(new Error("Unknown tool: kumiho_memory_engage"));
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const ctx = makeContext({ memoryEngage, memoryRetrieve }, "sess-1");

    const result = await handleMemoryEngage(ctx, { query: "anything" });

    expect(memoryRetrieve).toHaveBeenCalled();
    expect(result).toBe("No memories found matching your query.");
  });
});

describe("handleMemoryReflect", () => {
  it("reflects captures with the current session and default sourceKrefs from recall", async () => {
    const memoryReflect = vi.fn().mockResolvedValue({
      buffered: true,
      captures_stored: 1,
      edges_discovered: 0,
      stored_krefs: ["kref://capture/1?r=1"],
    });
    const ctx: ToolContext = {
      ...makeContext({ memoryReflect }, "sess-1"),
      getSourceKrefs: () => ["kref://memory/1?r=1"],
    };

    const result = await handleMemoryReflect(ctx, {
      response: "Noted the preference.",
      captures: [{ type: "preference", title: "Prefers dark mode (Jul 17)", content: "Dark mode" }],
    });

    expect(memoryReflect).toHaveBeenCalledWith({
      sessionId: "sess-1",
      response: "Noted the preference.",
      captures: [{ type: "preference", title: "Prefers dark mode (Jul 17)", content: "Dark mode" }],
      sourceKrefs: ["kref://memory/1?r=1"],
      spacePath: undefined,
    });
    expect(result).toContain("1 capture(s) stored");
    expect(result).toContain("kref://capture/1?r=1");
  });

  it("requires a non-empty response summary", async () => {
    const memoryReflect = vi.fn();
    const ctx = makeContext({ memoryReflect }, "sess-1");

    const result = await handleMemoryReflect(ctx, { response: "  " });

    expect(memoryReflect).not.toHaveBeenCalled();
    expect(result).toContain("non-empty");
  });

  it("falls back to per-capture memoryStore with provenance on pre-composite backends", async () => {
    const memoryReflect = vi.fn().mockRejectedValue(new Error("Unknown tool: kumiho_memory_reflect"));
    const memoryStore = vi.fn().mockResolvedValue({
      item_kref: "kref://item/1",
      revision_kref: "kref://item/1?r=1",
      space_path: "CognitiveMemory/personal",
      summary: "s",
    });
    const ctx = makeContext({ memoryReflect, memoryStore }, "sess-1");

    const result = await handleMemoryReflect(ctx, {
      response: "Stored the decision.",
      captures: [{ type: "decision", title: "Chose X on Jul 17", content: "X over Y" }],
      sourceKrefs: ["kref://memory/2?r=1"],
    });

    expect(memoryStore).toHaveBeenCalledOnce();
    const storeArgs = memoryStore.mock.calls[0][0] as { sourceRevisionKrefs?: string[] };
    expect(storeArgs.sourceRevisionKrefs).toEqual(["kref://memory/2?r=1"]);
    expect(result).toContain("kref://item/1?r=1");
  });
});
