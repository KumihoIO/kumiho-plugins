import { describe, expect, it, vi } from "vitest";

import { KumihoApiError, KumihoClient, isUnknownToolError, type Transport } from "../client.js";

function makeTransport(call: ReturnType<typeof vi.fn>): Transport {
  return {
    call,
    ping: vi.fn().mockResolvedValue(true),
  };
}

describe("KumihoClient memory retrieval", () => {
  it("uses rich recall results directly when the backend provides them", async () => {
    const call = vi.fn().mockResolvedValue({
      results: [
        {
          kref: "kref://memory/1?r=7",
          type: "summary",
          title: "OAuth fix rollout",
          summary: "OpenAI OAuth inheritance is host-only; direct memory LLM uses API key.",
          topics: ["oauth", "setup", "memory"],
          score: 0.94,
          created_at: "2026-03-15T12:00:00Z",
          space: "CognitiveMemory/personal",
          metadata: {
            title: "OAuth fix rollout",
            summary: "OpenAI OAuth inheritance is host-only; direct memory LLM uses API key.",
          },
        },
      ],
      count: 1,
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "oauth setup" });

    expect(results).toEqual([
      {
        kref: "kref://memory/1?r=7",
        type: "summary",
        title: "OAuth fix rollout",
        summary: "OpenAI OAuth inheritance is host-only; direct memory LLM uses API key.",
        topics: ["oauth", "setup", "memory"],
        score: 0.94,
        timestamp: "2026-03-15T12:00:00Z",
        space: "CognitiveMemory/personal",
        metadata: {
          title: "OAuth fix rollout",
          summary: "OpenAI OAuth inheritance is host-only; direct memory LLM uses API key.",
        },
      },
    ]);
    expect(call).toHaveBeenCalledTimes(1);
  });

  it("keeps title and summary when revision metadata stores topics as a comma-delimited string", async () => {
    const call = vi
      .fn()
      .mockResolvedValueOnce({
        item_krefs: ["kref://memory/item/1"],
        revision_krefs: ["kref://memory/item/1?r=3"],
        spaces_used: ["CognitiveMemory/personal"],
        scores: [0.88],
      })
      .mockResolvedValueOnce({
        kref: "kref://memory/item/1?r=3",
        item_kref: "kref://memory/item/1",
        created_at: "2026-03-15T12:01:00Z",
        metadata: {
          type: "summary",
          title: "arXiv endorsement prep",
          summary: "User revised an endorsement email and asked for a stronger academic tone.",
          topics: "email,arxiv,endorsement",
          space: "CognitiveMemory/personal",
        },
      });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "endorsement email" });

    expect(results).toEqual([
      {
        kref: "kref://memory/item/1?r=3",
        type: "summary",
        title: "arXiv endorsement prep",
        summary: "User revised an endorsement email and asked for a stronger academic tone.",
        topics: ["email", "arxiv", "endorsement"],
        score: 0.88,
        timestamp: "2026-03-15T12:01:00Z",
        space: "CognitiveMemory/personal",
        metadata: {
          type: "summary",
          title: "arXiv endorsement prep",
          summary: "User revised an endorsement email and asked for a stronger academic tone.",
          topics: "email,arxiv,endorsement",
          space: "CognitiveMemory/personal",
        },
      },
    ]);
  });

  it("accepts revision metadata topics that are already arrays", async () => {
    const call = vi.fn().mockResolvedValue({
      kref: "kref://memory/item/1?r=4",
      item_kref: "kref://memory/item/1",
      created_at: "2026-03-15T12:02:00Z",
      metadata: {
        type: "fact",
        title: "Gemini consolidation works",
        summary: "Gemini structured output succeeded after adapter fixes.",
        topics: ["gemini", "structured output"],
        space: "CognitiveMemory/work",
      },
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const result = await client.getRevision("kref://memory/item/1?r=4");

    expect(result).toEqual({
      kref: "kref://memory/item/1?r=4",
      type: "fact",
      title: "Gemini consolidation works",
      summary: "Gemini structured output succeeded after adapter fixes.",
      topics: ["gemini", "structured output"],
      timestamp: "2026-03-15T12:02:00Z",
      space: "CognitiveMemory/work",
      metadata: {
        type: "fact",
        title: "Gemini consolidation works",
        summary: "Gemini structured output succeeded after adapter fixes.",
        topics: ["gemini", "structured output"],
        space: "CognitiveMemory/work",
      },
    });
  });
});

describe("KumihoClient memory storage wire contract", () => {
  // kumiho_memory_store silently drops unknown args, so the exact wire
  // field names are load-bearing: `type`/`topics` used to be discarded
  // by the server (every memory stored as memory_type="summary").
  it("sends memory_type and folds topics into tags + metadata", async () => {
    const call = vi.fn().mockResolvedValue({ item_kref: "kref://x", revision_kref: "kref://x?r=1" });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryStore({
      type: "decision",
      title: "Chose gRPC",
      summary: "gRPC over REST for the control plane.",
      topics: ["grpc", "architecture"],
      tags: ["user-stored"],
    });

    const [tool, payload] = call.mock.calls[0] as [string, Record<string, unknown>];
    expect(tool).toBe("kumiho_memory_store");
    expect(payload.memory_type).toBe("decision");
    expect(payload).not.toHaveProperty("type");
    expect(payload).not.toHaveProperty("topics");
    expect(payload.tags).toEqual(["grpc", "architecture", "user-stored"]);
    expect(payload.metadata).toEqual({ topics: "grpc,architecture" });
  });

  it("seeds the server's default published tag when folding topics without caller tags", async () => {
    const call = vi.fn().mockResolvedValue({ item_kref: "kref://x", revision_kref: "kref://x?r=1" });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryStore({
      type: "fact",
      title: "t",
      summary: "s",
      topics: ["grpc"],
    });

    const [, payload] = call.mock.calls[0] as [string, Record<string, unknown>];
    // Server-side: tag_list = tags or ["published"] — a topics-only fold
    // must not turn the default off. Topics precede "published" because
    // the server freezes the revision once "published" is applied.
    expect(payload.tags).toEqual(["grpc", "published"]);
  });

  it("omits the topics fold entirely when no topics are given", async () => {
    const call = vi.fn().mockResolvedValue({ item_kref: "kref://x", revision_kref: "kref://x?r=1" });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryStore({
      type: "fact",
      title: "t",
      summary: "s",
      tags: ["user-stored"],
    });

    const [, payload] = call.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.tags).toEqual(["user-stored"]);
    expect(payload.metadata).toBeUndefined();
  });
});

describe("KumihoClient memory management wire contract", () => {
  it("deprecates via kumiho_deprecate_item with the item kref derived from a revision kref", async () => {
    const call = vi.fn().mockResolvedValue({});
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryDeprecate("kref://CognitiveMemory/facts/note.conversation?r=3");

    expect(call).toHaveBeenCalledWith("kumiho_deprecate_item", {
      item_kref: "kref://CognitiveMemory/facts/note.conversation",
      deprecated: true,
    });
  });

  it("deletes via kumiho_delete_item with force for revision-bearing items", async () => {
    const call = vi.fn().mockResolvedValue({});
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryDelete("kref://CognitiveMemory/facts/note.conversation?r=3");

    expect(call).toHaveBeenCalledWith("kumiho_delete_item", {
      item_kref: "kref://CognitiveMemory/facts/note.conversation",
      force: true,
    });
  });

  it("passes item krefs through unchanged", async () => {
    const call = vi.fn().mockResolvedValue({});
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await client.memoryDeprecate("kref://CognitiveMemory/facts/note.conversation");

    expect(call).toHaveBeenCalledWith("kumiho_deprecate_item", {
      item_kref: "kref://CognitiveMemory/facts/note.conversation",
      deprecated: true,
    });
  });
});

// ---------------------------------------------------------------------------
// Composite two-reflex tools — memoryEngage / memoryReflect
// ---------------------------------------------------------------------------

describe("KumihoClient memoryEngage", () => {
  it("maps params to snake_case and results to MemoryEntry shape", async () => {
    const call = vi.fn().mockResolvedValue({
      context: "recalled context",
      results: [
        {
          kref: "kref://memory/1?r=2",
          type: "fact",
          title: "Dark mode",
          summary: "User prefers dark mode",
          created_at: "2026-07-01T00:00:00Z",
          score: 0.9,
        },
      ],
      source_krefs: ["kref://memory/1?r=2"],
      count: 1,
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const result = await client.memoryEngage({
      query: "editor theme",
      limit: 3,
      spacePaths: ["CognitiveMemory/personal"],
      minScore: 0.3,
      graphAugmented: true,
    });

    expect(call).toHaveBeenCalledWith("kumiho_memory_engage", {
      query: "editor theme",
      limit: 3,
      space_paths: ["CognitiveMemory/personal"],
      memory_types: undefined,
      min_score: 0.3,
      graph_augmented: true,
    });
    expect(result.context).toBe("recalled context");
    expect(result.sourceKrefs).toEqual(["kref://memory/1?r=2"]);
    expect(result.results[0].title).toBe("Dark mode");
    expect(result.results[0].timestamp).toBe("2026-07-01T00:00:00Z");
    expect(result.deduplicated).toBe(false);
  });

  it("surfaces the server-side dedup flag", async () => {
    const call = vi.fn().mockResolvedValue({
      context: "",
      results: [],
      source_krefs: [],
      deduplicated: true,
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const result = await client.memoryEngage({ query: "same query twice" });

    expect(result.deduplicated).toBe(true);
    expect(result.results).toEqual([]);
  });
});

describe("KumihoClient memoryReflect", () => {
  it("maps captures to snake_case and uses an extended timeout", async () => {
    const call = vi.fn().mockResolvedValue({
      buffered: true,
      captures_stored: 1,
      edges_discovered: 2,
      stored_krefs: ["kref://capture/1?r=1"],
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const result = await client.memoryReflect({
      sessionId: "personal:user-x:20260717:001",
      response: "Summarized the CE rollout plan.",
      captures: [
        {
          type: "decision",
          title: "Chose CE endpoint default on Jul 17",
          content: "Default CE endpoint is 127.0.0.1:9190",
          tags: ["ce"],
          eventDate: "2026-07-17",
        },
      ],
      sourceKrefs: ["kref://memory/1?r=2"],
      spacePath: "personal",
    });

    expect(call).toHaveBeenCalledWith(
      "kumiho_memory_reflect",
      {
        session_id: "personal:user-x:20260717:001",
        response: "Summarized the CE rollout plan.",
        captures: [
          {
            type: "decision",
            title: "Chose CE endpoint default on Jul 17",
            content: "Default CE endpoint is 127.0.0.1:9190",
            tags: ["ce"],
            space_hint: undefined,
            event_date: "2026-07-17",
          },
        ],
        source_krefs: ["kref://memory/1?r=2"],
        space_path: "personal",
        discover_edges: undefined,
      },
      120_000,
    );
    expect(result.captures_stored).toBe(1);
    expect(result.stored_krefs).toEqual(["kref://capture/1?r=1"]);
  });
});

describe("isUnknownToolError", () => {
  it("treats a cloud 404 as unknown-tool even when the body has no MCP phrase", () => {
    expect(isUnknownToolError(new KumihoApiError("Kumiho API kumiho_memory_engage failed: 404 <html>Not Found</html>", "API_ERROR", 404))).toBe(true);
  });

  it("does not treat other statuses or messages as unknown-tool", () => {
    expect(isUnknownToolError(new KumihoApiError("Kumiho API kumiho_memory_engage failed: 500 internal", "API_ERROR", 500))).toBe(false);
    expect(isUnknownToolError(new Error("connection refused"))).toBe(false);
  });

  it("matches the MCP unknown-tool phrases", () => {
    expect(isUnknownToolError(new Error("Unknown tool: kumiho_memory_reflect"))).toBe(true);
    expect(isUnknownToolError(new Error("unsupported tool"))).toBe(true);
  });
});


describe("KumihoClient insight capability and packets", () => {
  const ref = "kref://CognitiveMemory/old.experience?r=2";
  const packet = { schema_version: 1, sources: [{ kref: ref, summary: "Past failure", item_markers: { grounding_stale: true } }], source_krefs: [ref], snapshot_fingerprint: "unchanged", review_brief: { candidates: [] } };

  it("passes opt-in parameters and preserves intact packet provenance independently of ordinary sources", async () => {
    const call = vi.fn().mockResolvedValue({ results: [], source_krefs: [], synthesis_request: packet, insight_brief: { candidates: [] }, learned_source_status: { status: "partial" } });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    const result = await client.memoryEngage({ query: "Apply the old lesson?", includeInsights: true, includeLearnedSources: true, currentContext: "Shared state is now isolated", goals: ["Lower latency"] });
    expect(call).toHaveBeenCalledWith("kumiho_memory_engage", expect.objectContaining({ include_insights: true, include_learned_sources: true, current_context: "Shared state is now isolated", goals: ["Lower latency"] }));
    expect(result.synthesisRequest).toEqual(packet);
    expect(result.sourceKrefs).toEqual([]);
    expect(result.learnedSourceStatus).toEqual({ status: "partial" });
  });

  it("skips optional arguments when a discovered legacy schema lacks them", async () => {
    const call = vi.fn().mockResolvedValue({ results: [] });
    const transport = { ...makeTransport(call), getDiscoveredTools: () => [{ name: "kumiho_memory_engage", inputSchema: { properties: { query: { type: "string" } } } }] };
    const client = new KumihoClient(transport, "CognitiveMemory");
    expect(client.supportsInsightOptions()).toBe(false);
    const result = await client.memoryEngage({ query: "decision", includeInsights: true, includeLearnedSources: true });
    expect(call.mock.calls[0][1]).not.toHaveProperty("include_insights");
    expect(result.insightNotice).toContain("ordinary recall only");
    expect(call).toHaveBeenCalledOnce();
  });

  it("retries a precise new-argument rejection once and remembers the old HTTP capability", async () => {
    const call = vi.fn().mockRejectedValueOnce(new Error("unexpected keyword argument 'include_insights'")).mockResolvedValue({ results: [] });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    await client.memoryEngage({ query: "decision", includeInsights: true });
    await client.memoryEngage({ query: "next decision", includeInsights: true });
    expect(call).toHaveBeenCalledTimes(3);
    expect(call.mock.calls[0][1]).toHaveProperty("include_insights", true);
    expect(call.mock.calls[1][1]).not.toHaveProperty("include_insights");
    expect(call.mock.calls[2][1]).not.toHaveProperty("include_insights");
    expect(client.supportsInsightOptions()).toBe(false);
  });

  it.each(["401 Unauthorized", "request timed out", "invalid goals value", "Unknown tool: kumiho_memory_engage"])("does not retry unrelated errors: %s", async (message) => {
    const call = vi.fn().mockRejectedValue(new Error(message));
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    await expect(client.memoryEngage({ query: "q", includeInsights: true })).rejects.toThrow(message);
    expect(call).toHaveBeenCalledOnce();
  });

  it("rejects learned-source IO without insight before contacting the backend", async () => {
    const call = vi.fn();
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    await expect(client.memoryEngage({ query: "q", includeLearnedSources: true })).rejects.toThrow("requires includeInsights");
    expect(call).not.toHaveBeenCalled();
  });

  it("omits oversized packets intact and keeps ordinary recall usable", async () => {
    const call = vi.fn().mockResolvedValue({ results: [{ kref: ref, summary: "ordinary" }], synthesis_request: { ...packet, instructions: "x".repeat(64001) }, learned_source_status: { detail: "x".repeat(8001) } });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    const result = await client.memoryEngage({ query: "q", includeInsights: true });
    expect(result.synthesisRequest).toBeUndefined();
    expect(result.learnedSourceStatus).toBeUndefined();
    expect(result.results[0].summary).toBe("ordinary");
    expect(result.insightNotice).toContain("Oversized");
  });

  it("rejects packet citation mismatches without repairing evidence", async () => {
    const call = vi.fn().mockResolvedValue({ results: [], synthesis_request: { ...packet, source_krefs: ["kref://other/item?r=1"] } });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    const result = await client.memoryEngage({ query: "q", includeInsights: true });
    expect(result.synthesisRequest).toBeUndefined();
    expect(result.insightNotice).toContain("No usable synthesis");
  });
});
