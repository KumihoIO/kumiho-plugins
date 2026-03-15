import { describe, expect, it, vi } from "vitest";

import { KumihoClient, type Transport } from "../client.js";

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
