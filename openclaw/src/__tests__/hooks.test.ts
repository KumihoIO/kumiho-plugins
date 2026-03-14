import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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

describe("consolidateSession — backend local consolidation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("prefers the backend consolidator over the legacy plugin summary path", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const client = {
      consolidateSession: vi.fn().mockResolvedValue({ success: true, summary: "Semantic summary" }),
      getDiscoveredTools: vi.fn().mockReturnValue([{ name: "kumiho_memory_consolidate" }]),
      chatGet: vi.fn(),
      memoryStore: vi.fn(),
      chatClear: vi.fn(),
    } as unknown as KumihoClient;
    const artifacts = makeArtifacts();
    const redactor = makeRedactor();
    const state = createHookState();
    state.sessionId = "session-backend-001";
    state.messageCount = 12;

    const result = await consolidateSession(client, baseConfig, state, redactor, artifacts);

    expect(result).toBe(true);
    expect(vi.mocked(client.consolidateSession)).toHaveBeenCalledWith("session-backend-001");
    expect(vi.mocked(client.chatGet)).not.toHaveBeenCalled();
    expect(vi.mocked(client.memoryStore)).not.toHaveBeenCalled();
    expect(vi.mocked(client.chatClear)).not.toHaveBeenCalled();
    expect(artifacts.saveConversation).not.toHaveBeenCalled();
    expect(redactor.reset).toHaveBeenCalledOnce();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(state.sessionId).not.toBe("session-backend-001");
    expect(state.messageCount).toBe(0);
  });

  it("returns false instead of storing a generic fallback when backend consolidation fails", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const client = {
      consolidateSession: vi.fn().mockResolvedValue({
        success: false,
        error: "The api_key client option must be set",
      }),
      getDiscoveredTools: vi.fn().mockReturnValue([{ name: "kumiho_memory_consolidate" }]),
      chatGet: vi.fn(),
      memoryStore: vi.fn(),
      chatClear: vi.fn(),
    } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-backend-002";
    state.messageCount = 9;

    const result = await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    expect(result).toBe(false);
    expect(vi.mocked(client.consolidateSession)).toHaveBeenCalledWith("session-backend-002");
    expect(vi.mocked(client.memoryStore)).not.toHaveBeenCalled();
    expect(vi.mocked(client.chatGet)).not.toHaveBeenCalled();
    expect(vi.mocked(client.chatClear)).not.toHaveBeenCalled();
    expect(state.sessionId).toBe("session-backend-002");
    expect(state.messageCount).toBe(9);
    expect(error).toHaveBeenCalledWith(
      expect.stringContaining("The api_key client option must be set"),
    );
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

// ---------------------------------------------------------------------------
// buildRecallQuery
// ---------------------------------------------------------------------------

import { buildRecallQuery } from "../hooks.js";

describe("buildRecallQuery", () => {
  it("returns the message unchanged when it is long enough and has no prior context", () => {
    const result = buildRecallQuery("what is the LoCoMo benchmark score for kumiho memory", {
      lastUserMessage: null,
      lastAssistantResponse: null,
    });
    expect(result).toContain("LoCoMo");
    expect(result).toContain("benchmark");
  });

  it("appends previous user message when current message is short (<= 6 words)", () => {
    const result = buildRecallQuery("what about that?", {
      lastUserMessage: "we were discussing the locomo benchmark results",
      lastAssistantResponse: null,
    });
    expect(result).toContain("locomo");
    expect(result).toContain("benchmark");
  });

  it("does NOT append previous message when current message is long enough", () => {
    const result = buildRecallQuery("tell me about the locomo plus benchmark architecture and scoring", {
      lastUserMessage: "something completely unrelated about dogs and weather",
      lastAssistantResponse: null,
    });
    expect(result).not.toContain("dogs");
    expect(result).not.toContain("weather");
  });

  it("appends first 20 words of last assistant response", () => {
    const result = buildRecallQuery("yeah", {
      lastUserMessage: null,
      lastAssistantResponse: "The LoCoMo-Plus benchmark measures long-context memory. Kumiho scores 93.3% vs 45.7% for Gemini 2.5 Pro.",
    });
    expect(result).toContain("LoCoMo-Plus");
    expect(result).toContain("benchmark");
  });

  it("deduplicates words across message and context", () => {
    const result = buildRecallQuery("locomo benchmark", {
      lastUserMessage: "locomo benchmark results",
      lastAssistantResponse: "locomo benchmark score is 93.3%",
    });
    const words = result.split(/\s+/);
    const locomo = words.filter(w => w.toLowerCase() === "locomo");
    expect(locomo.length).toBe(1);
  });

  it("caps output at 200 characters", () => {
    const long = "word ".repeat(100);
    const result = buildRecallQuery(long, {
      lastUserMessage: long,
      lastAssistantResponse: long,
    });
    expect(result.length).toBeLessThanOrEqual(200);
  });

  it("autoRecall uses enriched query — short message gets prior context appended", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = {
      chatAdd: vi.fn().mockResolvedValue(undefined),
      memoryRetrieve,
    } as unknown as KumihoClient;
    const state = createHookState();

    // Simulate a prior turn
    state.lastUserMessage = "we were discussing the locomo plus benchmark";
    state.lastAssistantResponse = "Kumiho scores 93.3% on LoCoMo-Plus";

    await autoRecall(client, { ...baseConfig, topK: 5, searchThreshold: 0.0 }, state, "yeah?");

    const [params] = memoryRetrieve.mock.calls[0] as [{ query: string }];
    expect(params.query).toContain("locomo");
  });
});

// ---------------------------------------------------------------------------
// recordUserTurn
// ---------------------------------------------------------------------------

import { recordUserTurn, autoCapture, prefetchMemories } from "../hooks.js";

describe("recordUserTurn", () => {
  it("stores user message via chatAdd with correct role and content", async () => {
    const chatAdd = vi.fn().mockResolvedValue(undefined);
    const client = { chatAdd } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-rut-001";

    await recordUserTurn(client, baseConfig, state, "hello world");

    expect(chatAdd).toHaveBeenCalledOnce();
    const [sid, role, content] = chatAdd.mock.calls[0] as [string, string, string, unknown];
    expect(sid).toBe("session-rut-001");
    expect(role).toBe("user");
    expect(content).toBe("hello world");
  });

  it("sets state.lastUserMessage", async () => {
    const client = { chatAdd: vi.fn().mockResolvedValue(undefined) } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-rut-002";

    await recordUserTurn(client, baseConfig, state, "test message");

    expect(state.lastUserMessage).toBe("test message");
  });

  it("increments state.messageCount", async () => {
    const client = { chatAdd: vi.fn().mockResolvedValue(undefined) } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-rut-003";
    state.messageCount = 3;

    await recordUserTurn(client, baseConfig, state, "msg");

    expect(state.messageCount).toBe(4);
  });

  it("initialises sessionId when null", async () => {
    const client = { chatAdd: vi.fn().mockResolvedValue(undefined) } as unknown as KumihoClient;
    const state = createHookState();
    expect(state.sessionId).toBeNull();

    await recordUserTurn(client, baseConfig, state, "hello");

    expect(state.sessionId).not.toBeNull();
    expect(typeof state.sessionId).toBe("string");
  });

  it("includes channel metadata in chatAdd call when channel provided", async () => {
    const chatAdd = vi.fn().mockResolvedValue(undefined);
    const client = { chatAdd } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-rut-004";
    const channel: ChannelInfo = { platform: "telegram", channelType: "personal_dm" };

    await recordUserTurn(client, baseConfig, state, "msg", channel);

    const [, , , meta] = chatAdd.mock.calls[0] as [string, string, string, Record<string, unknown>];
    expect(meta).toHaveProperty("channel");
    expect((meta.channel as Record<string, unknown>).platform).toBe("telegram");
  });

  it("propagates chatAdd rejection to caller", async () => {
    const client = {
      chatAdd: vi.fn().mockRejectedValue(new Error("Redis down")),
    } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-rut-005";

    await expect(recordUserTurn(client, baseConfig, state, "msg")).rejects.toThrow("Redis down");
  });
});

// ---------------------------------------------------------------------------
// consolidateSession — LLM summarization
// ---------------------------------------------------------------------------

describe("consolidateSession — LLM summarization", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses static fallback when localSummarization is false", async () => {
    const messages = makeMessages([{ user: "Hello", assistant: "Hi there" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-001";

    await consolidateSession(client, baseConfig, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toMatch(/Consolidated/i);
  });

  it("uses static fallback when no LLM provider is configured", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      consolidationModel: {},
      llm: {},
      hostLlmProvider: "",
      hostLlmApiKey: "",
    };
    const messages = makeMessages([{ user: "Hello", assistant: "Hi" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-002";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toMatch(/Consolidated/i);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("no LLM provider/API key was resolved"),
    );
  });

  it("calls Anthropic API and uses LLM response as summary", async () => {
    const llmSummary = "The user asked about TypeScript generics and received an explanation.";
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ content: [{ text: llmSummary }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      hostLlmProvider: "anthropic",
      hostLlmApiKey: "sk-ant-test-key",
    };
    const messages = makeMessages([{ user: "Explain generics", assistant: "Generics are..." }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-003";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.anthropic.com/v1/messages");
    const headers = init.headers as Record<string, string>;
    expect(headers["x-api-key"]).toBe("sk-ant-test-key");
    expect(headers["anthropic-version"]).toBeDefined();

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toBe(llmSummary);
  });

  it("calls OpenAI API and uses LLM response as summary", async () => {
    const llmSummary = "Conversation about Python async/await patterns.";
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        choices: [{ message: { content: llmSummary } }],
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      hostLlmProvider: "openai",
      hostLlmApiKey: "sk-openai-test-key",
    };
    const messages = makeMessages([{ user: "async/await?", assistant: "Use async functions..." }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-004";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.openai.com/v1/chat/completions");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer sk-openai-test-key");

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toBe(llmSummary);
  });

  it("calls OpenAI Responses API for codex models", async () => {
    const llmSummary = "Conversation summary from gpt-5-codex.";
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        output_text: llmSummary,
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      llm: {
        provider: "openai",
        model: "gpt-5-codex",
        apiKey: "oauth-access-token",
      },
    };
    const messages = makeMessages([{ user: "Summarize this thread", assistant: "Here is the thread summary." }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-004b";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.openai.com/v1/responses");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer oauth-access-token");

    const body = JSON.parse(init.body as string) as { model: string; input: string };
    expect(body.model).toBe("gpt-5-codex");
    expect(body.input).toContain("Summarize the following conversation");

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toBe(llmSummary);
  });

  it("falls back to static summary when fetch rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")));

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      hostLlmProvider: "anthropic",
      hostLlmApiKey: "sk-ant-test",
    };
    const messages = makeMessages([{ user: "hi", assistant: "hello" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-005";

    const result = await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    expect(result).toBe(true);
    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toMatch(/Consolidated/i);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("network error"),
    );
  });

  it("falls back to static summary when API returns non-ok status", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 429 }));

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      hostLlmProvider: "anthropic",
      hostLlmApiKey: "sk-ant-test",
    };
    const messages = makeMessages([{ user: "hi", assistant: "hello" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-006";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const call = vi.mocked(client.memoryStore).mock.calls[0][0];
    expect(call.summary).toMatch(/Consolidated/i);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("status 429"),
    );
  });

  it("prefers consolidationModel.apiKey over hostLlmApiKey", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ content: [{ text: "summary" }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      consolidationModel: { provider: "anthropic", apiKey: "explicit-key" },
      hostLlmProvider: "anthropic",
      hostLlmApiKey: "host-key",
    };
    const messages = makeMessages([{ user: "q", assistant: "a" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-007";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["x-api-key"]).toBe("explicit-key");
  });

  it("caps transcript at 8000 chars before sending to LLM", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ content: [{ text: "summary" }] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      hostLlmProvider: "anthropic",
      hostLlmApiKey: "sk-ant-test",
    };
    // Produce messages well over 8000 chars total
    const bigMsg = "x".repeat(3000);
    const messages = makeMessages([
      { user: bigMsg, assistant: bigMsg },
      { user: bigMsg, assistant: bigMsg },
    ]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-008";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { messages: Array<{ content: string }> };
    const promptContent = body.messages[0].content;
    // The conversation transcript embedded in the prompt is capped at 8000 chars
    expect(promptContent.length).toBeLessThan(9000);
  });

  it("uses llm.model as the fallback consolidation model", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        choices: [{ message: { content: "summary" } }],
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const config: ResolvedConfig = {
      ...baseConfig,
      localSummarization: true,
      llm: {
        provider: "openai",
        model: "gpt-4.1-mini",
        apiKey: "sk-openai-test-key",
      },
    };
    const messages = makeMessages([{ user: "Summarize this", assistant: "Done" }]);
    const client = makeClient(messages);
    const state = createHookState();
    state.sessionId = "session-llm-009";

    await consolidateSession(client, config, state, makeRedactor(), makeArtifacts());

    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { model: string };
    expect(body.model).toBe("gpt-4.1-mini");
  });
});

// ---------------------------------------------------------------------------
// autoCapture
// ---------------------------------------------------------------------------

describe("autoCapture", () => {
  it("returns captured:false when sessionId is null", async () => {
    const client = { chatAdd: vi.fn() } as unknown as KumihoClient;
    const state = createHookState();

    const result = await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "response");

    expect(result.captured).toBe(false);
    expect(vi.mocked(client.chatAdd)).not.toHaveBeenCalled();
  });

  it("returns captured:false when lastUserMessage is null", async () => {
    const client = { chatAdd: vi.fn() } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-ac-001";
    state.lastUserMessage = null;

    const result = await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "response");

    expect(result.captured).toBe(false);
  });

  it("returns captured:false when response is empty", async () => {
    const client = { chatAdd: vi.fn() } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-ac-002";
    state.lastUserMessage = "hello";

    const result = await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "   ");

    expect(result.captured).toBe(false);
    expect(vi.mocked(client.chatAdd)).not.toHaveBeenCalled();
  });

  it("stores assistant message via chatAdd", async () => {
    const chatAdd = vi.fn().mockResolvedValue(undefined);
    const client = { chatAdd } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-ac-003";
    state.lastUserMessage = "question";

    await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "the answer");

    expect(chatAdd).toHaveBeenCalledOnce();
    const [, role, content] = chatAdd.mock.calls[0] as [string, string, string];
    expect(role).toBe("assistant");
    expect(content).toBe("the answer");
  });

  it("sets state.lastAssistantResponse", async () => {
    const client = { chatAdd: vi.fn().mockResolvedValue(undefined) } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-ac-004";
    state.lastUserMessage = "question";

    await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "  trimmed response  ");

    expect(state.lastAssistantResponse).toBe("trimmed response");
  });

  it("returns consolidated:false when messageCount is below threshold", async () => {
    const client = { chatAdd: vi.fn().mockResolvedValue(undefined) } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-ac-005";
    state.lastUserMessage = "question";
    state.messageCount = 5; // threshold is 20

    const result = await autoCapture(client, baseConfig, state, makeRedactor(), makeArtifacts(), "response");

    expect(result.captured).toBe(true);
    expect(result.consolidated).toBe(false);
  });

  it("triggers consolidation and returns consolidated:true when messageCount reaches threshold", async () => {
    // After autoCapture increments messageCount to 20, consolidation fires
    const messages = makeMessages([{ user: "q", assistant: "a" }]);
    const fullClient = makeClient(messages);
    // Add chatAdd mock (used for storing assistant message)
    (fullClient as unknown as Record<string, unknown>).chatAdd = vi.fn().mockResolvedValue(undefined);

    const state = createHookState();
    state.sessionId = "session-ac-006";
    state.lastUserMessage = "question";
    state.messageCount = 19; // will become 20 after increment → triggers consolidation

    const result = await autoCapture(fullClient, baseConfig, state, makeRedactor(), makeArtifacts(), "response");

    expect(result.consolidated).toBe(true);
    expect(vi.mocked(fullClient.memoryStore)).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// prefetchMemories
// ---------------------------------------------------------------------------

describe("prefetchMemories", () => {
  const memories: import("../types.js").MemoryEntry[] = [
    { kref: "kref://x/y/z?r=1", type: "fact", title: "Test fact", summary: "Something important", topics: [], score: 0.9 },
    { kref: "kref://x/y/w?r=1", type: "fact", title: "Low score", summary: "Not very relevant", topics: [], score: 0.2 },
  ];

  it("calls memoryRetrieve with query and topK", async () => {
    const memoryRetrieve = vi.fn().mockResolvedValue([]);
    const client = { memoryRetrieve } as unknown as KumihoClient;
    const config = { ...baseConfig, topK: 7 };

    await prefetchMemories(client, config, "test query");

    expect(memoryRetrieve).toHaveBeenCalledOnce();
    const [params] = memoryRetrieve.mock.calls[0] as [{ query: string; limit: number }];
    expect(params.query).toBe("test query");
    expect(params.limit).toBe(7);
  });

  it("filters out memories below searchThreshold", async () => {
    const client = {
      memoryRetrieve: vi.fn().mockResolvedValue(memories),
    } as unknown as KumihoClient;
    const config = { ...baseConfig, searchThreshold: 0.5 };

    const result = await prefetchMemories(client, config, "query");

    // Only the 0.9 score memory passes the 0.5 threshold
    expect(result.memories).toHaveLength(1);
    expect(result.memories[0].kref).toBe("kref://x/y/z?r=1");
  });

  it("returns non-empty contextInjection when memories pass threshold", async () => {
    const client = {
      memoryRetrieve: vi.fn().mockResolvedValue([memories[0]]),
    } as unknown as KumihoClient;

    const result = await prefetchMemories(client, baseConfig, "query");

    expect(result.contextInjection).not.toBe("");
    expect(result.contextInjection).toContain("Test fact");
  });

  it("returns empty contextInjection when no memories pass threshold", async () => {
    const client = {
      memoryRetrieve: vi.fn().mockResolvedValue([memories[1]]), // score 0.2 below 0.5 threshold
    } as unknown as KumihoClient;
    const config = { ...baseConfig, searchThreshold: 0.5 };

    const result = await prefetchMemories(client, config, "query");

    expect(result.contextInjection).toBe("");
    expect(result.memories).toHaveLength(0);
  });

  it("does not call chatAdd — read-only with no state mutations", async () => {
    const chatAdd = vi.fn();
    const client = {
      chatAdd,
      memoryRetrieve: vi.fn().mockResolvedValue([]),
    } as unknown as KumihoClient;
    const state = createHookState();
    state.sessionId = "session-pf-001";
    state.messageCount = 5;

    await prefetchMemories(client, baseConfig, "query");

    expect(chatAdd).not.toHaveBeenCalled();
    expect(state.messageCount).toBe(5); // unchanged
  });
});
