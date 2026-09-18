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

describe("KumihoClient memory retrieval — the space of each revision", () => {
  // The kref is authoritative: it names where the revision that came back
  // actually lives. A declared `space`/`metadata.space` is only the writer's
  // intent, and `spaces_used` is the de-duplicated set of spaces the hits came
  // from, not a list aligned with `revision_krefs` — never spaces_used[i].
  type Revision = { metadata?: Record<string, unknown>; fail?: boolean };

  function retrieveTransport(
    retrieve: Record<string, unknown>,
    revisions: Record<string, Revision> = {},
  ) {
    return vi.fn(async (tool: string, params: Record<string, unknown>) => {
      if (tool === "kumiho_memory_retrieve") return retrieve;
      if (tool === "kumiho_get_revision") {
        const kref = String(params.kref);
        const revision = revisions[kref] ?? {};
        if (revision.fail) throw new Error(`revision unavailable: ${kref}`);
        return {
          kref,
          item_kref: kref.split("?")[0],
          created_at: "2026-09-17T09:00:00Z",
          metadata: { type: "decision", title: `title of ${kref}`, ...revision.metadata },
        };
      }
      throw new Error(`unexpected tool ${tool}`);
    });
  }

  async function spacesOf(
    retrieve: Record<string, unknown>,
    revisions: Record<string, Revision> = {},
  ) {
    const call = retrieveTransport(retrieve, revisions);
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");
    const results = await client.memoryRetrieve({ query: "anything" });
    return results.map((entry) => entry.space);
  }

  it("labels multi-space results by their own kref when spaces_used is deduped and shorter", async () => {
    // Hits from [personal, personal, work] report spaces_used [personal, work].
    // Indexing it labeled the second personal hit "work" and the work hit undefined.
    const spaces = await spacesOf({
      revision_krefs: [
        "kref://CognitiveMemory/personal/editor-preference.preference?r=2",
        "kref://CognitiveMemory/personal/timezone.fact?r=1",
        "kref://CognitiveMemory/work/release-cadence.decision?r=5",
      ],
      spaces_used: ["CognitiveMemory/personal", "CognitiveMemory/work"],
      scores: [0.91, 0.84, 0.77],
    });

    expect(spaces).toEqual([
      "CognitiveMemory/personal",
      "CognitiveMemory/personal",
      "CognitiveMemory/work",
    ]);
  });

  it("keeps each result's score aligned while deriving its space", async () => {
    const call = retrieveTransport({
      revision_krefs: [
        "kref://CognitiveMemory/work/a.decision?r=1",
        "kref://CognitiveMemory/personal/b.fact?r=1",
      ],
      spaces_used: ["CognitiveMemory/personal", "CognitiveMemory/work"],
      scores: [0.9, 0.6],
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "anything" });

    expect(results.map(({ kref, space, score }) => ({ kref, space, score }))).toEqual([
      { kref: "kref://CognitiveMemory/work/a.decision?r=1", space: "CognitiveMemory/work", score: 0.9 },
      { kref: "kref://CognitiveMemory/personal/b.fact?r=1", space: "CognitiveMemory/personal", score: 0.6 },
    ]);
  });

  it("keeps every subspace segment of a nested kref", async () => {
    const spaces = await spacesOf({
      revision_krefs: [
        "kref://CognitiveMemory/work/kumiho/plugins/openclaw-release.decision?r=4",
        "kref://CognitiveMemory/blog-post-jan25/draft-outline.summary?r=1",
      ],
      // Deduped order need not follow the results either.
      spaces_used: ["CognitiveMemory/blog-post-jan25", "CognitiveMemory/work/kumiho/plugins"],
    });

    expect(spaces).toEqual([
      "CognitiveMemory/work/kumiho/plugins",
      "CognitiveMemory/blog-post-jan25",
    ]);
  });

  it("drops the query string, including an artifact (?r=N&a=...), and a dotted item name", async () => {
    const spaces = await spacesOf({
      revision_krefs: [
        "kref://CognitiveMemory/personal/editor.preference?r=2&a=notes.md",
        "kref://CognitiveMemory/work/v0.7.1.release-notes.summary?r=12",
        "kref://CognitiveMemory/work/plain.fact",
      ],
      spaces_used: [],
    });

    expect(spaces).toEqual([
      "CognitiveMemory/personal",
      "CognitiveMemory/work",
      "CognitiveMemory/work",
    ]);
  });

  it("takes the kref's space over a metadata space that disagrees with it", async () => {
    // `metadata.space` is the writer's intended space, fixed before stacking
    // chose which item the text landed on, so the revision handed back can
    // live somewhere else. Every memory the SDK writes carries one, so
    // preferring it left the kref path all but unreachable.
    const spaces = await spacesOf(
      {
        revision_krefs: [
          "kref://CognitiveMemory/personal/team-norms.decision?r=3",
          "kref://CognitiveMemory/personal/other.fact?r=1",
        ],
        spaces_used: ["CognitiveMemory/personal"],
      },
      {
        "kref://CognitiveMemory/personal/team-norms.decision?r=3": {
          metadata: { space: "CognitiveMemory/team/eng" },
        },
      },
    );

    expect(spaces).toEqual(["CognitiveMemory/personal", "CognitiveMemory/personal"]);
  });

  it("uses a declared space only when the kref names none", async () => {
    const spaces = await spacesOf(
      {
        revision_krefs: ["kref://memory/item/1?r=3", "kref://memory/item/2?r=1"],
        spaces_used: [],
      },
      { "kref://memory/item/1?r=3": { metadata: { space: "CognitiveMemory/team/eng" } } },
    );

    expect(spaces).toEqual(["CognitiveMemory/team/eng", undefined]);
  });

  it("normalizes every space it sets to the `spaces_used` shape", async () => {
    // One shape everywhere: no leading or trailing slash, no empty segments,
    // project-prefixed — so an entry's space compares directly against
    // `spaces_used` and against a space parsed out of a kref.
    expect(
      await spacesOf(
        { revision_krefs: ["kref://memory/item/1?r=3"], spaces_used: [] },
        { "kref://memory/item/1?r=3": { metadata: { space: "/CognitiveMemory/work/infra/" } } },
      ),
    ).toEqual(["CognitiveMemory/work/infra"]);

    // …including the sole-`spaces_used` last resort.
    expect(
      await spacesOf({ revision_krefs: ["not-a-kref"], spaces_used: ["/CognitiveMemory/x"] }),
    ).toEqual(["CognitiveMemory/x"]);

    // …and on the getRevision failure path.
    expect(
      await spacesOf(
        { revision_krefs: ["not-a-kref"], spaces_used: ["/CognitiveMemory/x/"] },
        { "not-a-kref": { fail: true } },
      ),
    ).toEqual(["CognitiveMemory/x"]);
  });

  it("applies the same precedence to the rich `results` branch", async () => {
    const call = vi.fn().mockResolvedValue({
      results: [
        {
          kref: "kref://CognitiveMemory/work/infra/runbook.decision?r=2",
          metadata: { space: "CognitiveMemory/personal" },
        },
        { kref: "kref://memory/legacy/1?r=1", space: "/CognitiveMemory/work/infra" },
        { kref: "kref://memory/legacy/2?r=1" },
      ],
      spaces_used: ["CognitiveMemory/work/infra"],
      count: 3,
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "runbook" });

    expect(results.map((entry) => entry.space)).toEqual([
      "CognitiveMemory/work/infra", // its own kref, not the space it declares
      "CognitiveMemory/work/infra", // declared, normalized
      "CognitiveMemory/work/infra", // the one space the whole recall used
    ]);
    expect(call).toHaveBeenCalledTimes(1);
  });

  it("leaves a `results` entry unlabelled when two spaces were used and its kref names none", async () => {
    const call = vi.fn().mockResolvedValue({
      results: [{ kref: "kref://memory/legacy/1?r=1" }],
      spaces_used: ["CognitiveMemory/personal", "CognitiveMemory/work"],
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "q" });

    expect(results[0].space).toBeUndefined();
  });

  it("derives the space for the fallback entry when getRevision fails", async () => {
    const call = retrieveTransport(
      {
        revision_krefs: [
          "kref://CognitiveMemory/personal/ok.fact?r=1",
          "kref://CognitiveMemory/work/projects/broken.decision?r=7&a=data",
        ],
        spaces_used: ["CognitiveMemory/work/projects", "CognitiveMemory/personal"],
        scores: [0.8, 0.7],
      },
      { "kref://CognitiveMemory/work/projects/broken.decision?r=7&a=data": { fail: true } },
    );
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const results = await client.memoryRetrieve({ query: "anything" });

    expect(results[0].space).toBe("CognitiveMemory/personal");
    expect(results[1]).toEqual({
      kref: "kref://CognitiveMemory/work/projects/broken.decision?r=7&a=data",
      type: "summary",
      title: "",
      summary: "",
      topics: [],
      space: "CognitiveMemory/work/projects",
      score: 0.7,
    });
  });

  it("leaves the space undefined when spaces_used is empty and the kref names no item", async () => {
    const spaces = await spacesOf({
      revision_krefs: [
        "kref://CognitiveMemory/personal/known.fact?r=1",
        "not-a-kref",
        "kref://CognitiveMemory?r=1",
      ],
      spaces_used: [],
    });

    expect(spaces).toEqual(["CognitiveMemory/personal", undefined, undefined]);
  });

  it("falls back to spaces_used only when it names exactly one space", async () => {
    const krefs = ["not-a-kref", "kref://CognitiveMemory/work/w.fact?r=1"];

    // One space covers every hit, so it is unambiguous for an unparseable kref;
    // a parseable kref still wins over it.
    expect(
      await spacesOf({ revision_krefs: krefs, spaces_used: ["CognitiveMemory/personal"] }),
    ).toEqual(["CognitiveMemory/personal", "CognitiveMemory/work"]);

    // Two spaces cannot be attributed to one hit, so none is guessed.
    expect(
      await spacesOf({
        revision_krefs: krefs,
        spaces_used: ["CognitiveMemory/personal", "CognitiveMemory/work"],
      }),
    ).toEqual([undefined, "CognitiveMemory/work"]);

    // Same rule on the getRevision failure path.
    expect(
      await spacesOf(
        { revision_krefs: ["not-a-kref"], spaces_used: ["CognitiveMemory/personal"] },
        { "not-a-kref": { fail: true } },
      ),
    ).toEqual(["CognitiveMemory/personal"]);
    expect(
      await spacesOf(
        { revision_krefs: ["not-a-kref"], spaces_used: ["CognitiveMemory/personal", "CognitiveMemory/work"] },
        { "not-a-kref": { fail: true } },
      ),
    ).toEqual([undefined]);
  });

  it("does not change the retrieve request", async () => {
    const call = retrieveTransport({ revision_krefs: [], spaces_used: ["CognitiveMemory/personal"] });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    await expect(
      client.memoryRetrieve({ query: "q", limit: 3, spacePaths: ["CognitiveMemory/work"], memoryTypes: ["fact"] }),
    ).resolves.toEqual([]);
    expect(call).toHaveBeenCalledTimes(1);
    expect(call).toHaveBeenCalledWith("kumiho_memory_retrieve", {
      project: "CognitiveMemory",
      query: "q",
      limit: 3,
      space_paths: ["CognitiveMemory/work"],
      memory_types: ["fact"],
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

  it("takes each recalled memory's space from its own kref", async () => {
    // engage carries no `spaces_used`, so before this the space came from
    // whatever the writer declared — the one field that can name a space the
    // stacked revision does not live in.
    const call = vi.fn().mockResolvedValue({
      context: "recalled context",
      results: [
        {
          kref: "kref://CognitiveMemory/work/infra/runbook.decision?r=2",
          type: "decision",
          title: "Runbook",
          metadata: { space: "CognitiveMemory/personal" },
        },
        {
          kref: "kref://memory/legacy/1?r=1",
          type: "fact",
          title: "Legacy",
          space: "/CognitiveMemory/personal",
        },
        { kref: "kref://memory/legacy/2?r=1", type: "fact", title: "Unplaced" },
      ],
      source_krefs: [],
    });
    const client = new KumihoClient(makeTransport(call), "CognitiveMemory");

    const result = await client.memoryEngage({ query: "runbook" });

    expect(result.results.map((entry) => entry.space)).toEqual([
      "CognitiveMemory/work/infra",
      "CognitiveMemory/personal",
      undefined,
    ]);
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
