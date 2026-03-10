import { describe, it, expect, vi, beforeEach } from "vitest";
import { consolidateSession, createHookState, type HookState } from "../hooks.js";
import type { KumihoClient } from "../client.js";
import type { PIIRedactor } from "../privacy.js";
import type { ArtifactManager } from "../artifacts.js";
import type { ResolvedConfig, ChatMessage } from "../types.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

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

function makeMessages(pairs: Array<{ user: string; assistant: string }>): ChatMessage[] {
  const msgs: ChatMessage[] = [];
  for (const p of pairs) {
    msgs.push({ role: "user", content: p.user, timestamp: "2026-03-10T00:00:00Z" });
    msgs.push({ role: "assistant", content: p.assistant, timestamp: "2026-03-10T00:00:01Z" });
  }
  return msgs;
}

function makeClient(messages: ChatMessage[]): KumihoClient {
  return {
    chatGet: vi.fn().mockResolvedValue({ messages, session_id: "s1", message_count: messages.length, ttl_remaining: 3600 }),
    memoryStore: vi.fn().mockResolvedValue({ item_kref: "kref://x", revision_kref: "kref://r", space_path: "p", summary: "s" }),
    chatClear: vi.fn().mockResolvedValue(undefined),
  } as unknown as KumihoClient;
}

function makeArtifacts(): ArtifactManager {
  return {
    saveConversation: vi.fn().mockResolvedValue({ type: "conversation", storage: "local", location: "/tmp/artifact.json", hash: "abc123" }),
  } as unknown as ArtifactManager;
}

function makeRedactor(): PIIRedactor {
  return {
    redact: vi.fn().mockImplementation((text: string) => ({ text, entities: [] })),
    anonymizeSummary: vi.fn().mockImplementation((text: string) => text),
    reset: vi.fn(),
  } as unknown as PIIRedactor;
}

// ---------------------------------------------------------------------------
// consolidateSession
// ---------------------------------------------------------------------------

describe("consolidateSession", () => {
  let state: HookState;

  beforeEach(() => {
    state = createHookState();
    state.sessionId = "test-session-001";
    state.messageCount = 5;
  });

  it("returns false when sessionId is null", async () => {
    state.sessionId = null;
    const result = await consolidateSession(makeClient([]), baseConfig, state, makeRedactor(), makeArtifacts());
    expect(result).toBe(false);
  });

  it("returns false when there are no messages", async () => {
    const client = makeClient([]);
    const result = await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());
    expect(result).toBe(false);
    expect(client.memoryStore).not.toHaveBeenCalled();
  });

  it("returns true and calls memoryStore when messages exist", async () => {
    const messages = makeMessages([{ user: "Hello", assistant: "Hi there" }]);
    const client = makeClient(messages);
    const artifacts = makeArtifacts();

    const result = await consolidateSession(client, baseConfig, state, makeRedactor(), artifacts);

    expect(result).toBe(true);
    expect(client.memoryStore).toHaveBeenCalledOnce();
    expect(client.chatClear).toHaveBeenCalledWith("test-session-001");
  });

  it("splits messages by role — userText contains only user messages", async () => {
    const messages = makeMessages([
      { user: "Question one", assistant: "Answer one" },
      { user: "Question two", assistant: "Answer two" },
    ]);
    const client = makeClient(messages);

    await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.userText).toContain("Question one");
    expect(call.userText).toContain("Question two");
    expect(call.userText).not.toContain("Answer one");
    expect(call.userText).not.toContain("Answer two");
  });

  it("splits messages by role — assistantText contains only assistant messages", async () => {
    const messages = makeMessages([
      { user: "Question one", assistant: "Answer one" },
      { user: "Question two", assistant: "Answer two" },
    ]);
    const client = makeClient(messages);

    await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.assistantText).toContain("Answer one");
    expect(call.assistantText).toContain("Answer two");
    expect(call.assistantText).not.toContain("Question one");
    expect(call.assistantText).not.toContain("Question two");
  });

  it("sends summary text for both fields when uploadSummariesOnly is true", async () => {
    const config: ResolvedConfig = {
      ...baseConfig,
      privacy: { ...baseConfig.privacy, uploadSummariesOnly: true },
    };
    const messages = makeMessages([{ user: "Secret question", assistant: "Secret answer" }]);
    const client = makeClient(messages);

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    // uploadSummariesOnly → userText gets the summary, assistantText is empty
    expect(call.assistantText).toBe("");
    expect(call.userText).not.toContain("Secret question");
    expect(call.userText).not.toContain("Secret answer");
    // userText should be a summary string referencing the session
    expect(call.userText).toMatch(/Consolidated/i);
  });

  it("sends raw text when uploadSummariesOnly is false", async () => {
    const config: ResolvedConfig = {
      ...baseConfig,
      privacy: { ...baseConfig.privacy, uploadSummariesOnly: false },
    };
    const messages = makeMessages([{ user: "Raw user text", assistant: "Raw assistant text" }]);
    const client = makeClient(messages);

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.userText).toContain("Raw user text");
    expect(call.assistantText).toContain("Raw assistant text");
  });

  it("resets messageCount and creates a new sessionId after consolidation", async () => {
    const messages = makeMessages([{ user: "u", assistant: "a" }]);
    const client = makeClient(messages);

    await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    expect(state.messageCount).toBe(0);
    // New session ID should be different from old one
    expect(state.sessionId).not.toBe("test-session-001");
    expect(typeof state.sessionId).toBe("string");
  });

  it("stores summary with required tags", async () => {
    const messages = makeMessages([{ user: "u", assistant: "a" }]);
    const client = makeClient(messages);

    await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.tags).toContain("consolidated");
    expect(call.tags).toContain("summary");
  });

  it("stores the artifact location in memoryStore metadata", async () => {
    const messages = makeMessages([{ user: "u", assistant: "a" }]);
    const client = makeClient(messages);
    const artifacts = makeArtifacts();

    await consolidateSession(client, baseConfig, state, makeRedactor(), artifacts);

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.artifactLocation).toBe("/tmp/artifact.json");
  });

  it("returns false and does not throw when client.chatGet rejects", async () => {
    const client = {
      chatGet: vi.fn().mockRejectedValue(new Error("Redis down")),
    } as unknown as KumihoClient;

    const result = await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());
    expect(result).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// autoRecall — spacePaths scoping
// ---------------------------------------------------------------------------

import { autoRecall } from "../hooks.js";
import type { ChannelInfo } from "../types.js";

describe("autoRecall — spacePaths scoping", () => {
  function makeRecallClient(memoryRetrieveFn = vi.fn().mockResolvedValue([])) {
    return {
      chatAdd: vi.fn().mockResolvedValue(undefined),
      memoryRetrieve: memoryRetrieveFn,
    } as unknown as KumihoClient;
  }

  const recallConfig: ResolvedConfig = {
    ...baseConfig,
    topK: 5,
    searchThreshold: 0.0,
  };

  it("passes no spacePaths for personal_dm — searches whole project", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = makeRecallClient(memoryRetrieve);
    const state = createHookState();
    const channel: ChannelInfo = { channelType: "personal_dm", channelId: "tg:123" };

    await autoRecall(client, recallConfig, state, "what did we work on?", channel);

    expect(memoryRetrieve).toHaveBeenCalledOnce();
    const [params] = memoryRetrieve.mock.calls[0] as [{ spacePaths?: string[] }];
    expect(params.spacePaths).toBeUndefined();
  });

  it("restricts spacePaths for team_channel — isolates team space", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = makeRecallClient(memoryRetrieve);
    const state = createHookState();
    const channel: ChannelInfo = { channelType: "team_channel", channelId: "slack:C123", teamSlug: "eng" };

    await autoRecall(client, recallConfig, state, "deploy status?", channel);

    const [params] = memoryRetrieve.mock.calls[0] as [{ spacePaths?: string[] }];
    expect(params.spacePaths).toBeDefined();
    expect(params.spacePaths![0]).toContain("eng");
  });

  it("restricts spacePaths for group_dm — isolates group space", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = makeRecallClient(memoryRetrieve);
    const state = createHookState();
    const channel: ChannelInfo = { channelType: "group_dm", channelId: "tg:group:456", groupId: "456" };

    await autoRecall(client, recallConfig, state, "hey", channel);

    const [params] = memoryRetrieve.mock.calls[0] as [{ spacePaths?: string[] }];
    expect(params.spacePaths).toBeDefined();
    expect(params.spacePaths![0]).toContain("456");
  });

  it("passes no spacePaths when channel is undefined", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = makeRecallClient(memoryRetrieve);
    const state = createHookState();

    await autoRecall(client, recallConfig, state, "hello");

    const [params] = memoryRetrieve.mock.calls[0] as [{ spacePaths?: string[] }];
    expect(params.spacePaths).toBeUndefined();
  });
});
