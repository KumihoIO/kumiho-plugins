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
