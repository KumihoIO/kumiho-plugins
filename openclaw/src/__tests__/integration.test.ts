/**
 * Live integration tests using the local kumiho-mcp Python subprocess.
 *
 * These tests run only when:
 *   1. ~/.kumiho/kumiho_authentication.json exists and contains a valid token
 *   2. The `kumiho-mcp` command is available on PATH
 *
 * They are automatically skipped in CI or on machines without auth/the
 * Python package installed.
 *
 * Each test suite cleans up after itself (chatClear, memoryDeprecate) to
 * avoid accumulating noise in the graph.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { homedir, platform } from "node:os";
import { KumihoClient, McpTransport } from "../client.js";
import { consolidateSession, createHookState } from "../hooks.js";
import { PIIRedactor } from "../privacy.js";
import { ArtifactManager } from "../artifacts.js";
import type { ResolvedConfig } from "../types.js";

// ---------------------------------------------------------------------------
// Skip guards
// ---------------------------------------------------------------------------

const AUTH_PATH = join(homedir(), ".kumiho", "kumiho_authentication.json");
const hasAuth = existsSync(AUTH_PATH);
const hasLlmKey = Boolean(
  process.env.KUMIHO_LLM_API_KEY ||
  process.env.OPENAI_API_KEY ||
  process.env.ANTHROPIC_API_KEY,
);

function commandOnPath(command: string): boolean {
  const pathSeparator = platform() === "win32" ? ";" : ":";
  const pathEntries = (process.env.PATH ?? "").split(pathSeparator).filter(Boolean);
  const candidates =
    platform() === "win32"
      ? [command, `${command}.cmd`, `${command}.exe`, `${command}.bat`]
      : [command];

  return pathEntries.some((entry) =>
    candidates.some((candidate) => existsSync(join(entry, candidate))),
  );
}

// Pre-check once at module load (fast, no subprocess)
const mcpAvailable = (() => {
  return commandOnPath("kumiho-mcp");
})();

const canRunLive = hasAuth && mcpAvailable;

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const RUN_ID = Date.now().toString(36);
const TEST_PROJECT = "CognitiveMemory";

function makeLocalConfig(): ResolvedConfig {
  return {
    mode: "local",
    apiKey: "",
    endpoint: "https://api.kumiho.cloud",
    bffEndpoint: "https://api.kumiho.cloud",
    project: TEST_PROJECT,
    userId: `integration-test-${RUN_ID}`,
    autoCapture: false,
    autoRecall: false,
    localSummarization: false,
    consolidationThreshold: 20,
    idleConsolidationTimeout: 0,
    sessionTtl: 3600,
    topK: 5,
    searchThreshold: 0.3,
    artifactDir: join(homedir(), ".kumiho", "artifacts"),
    piiRedaction: false,
    dreamStateSchedule: "off",
    dreamStateModel: {},
    consolidationModel: {},
    llm: {},
    hostLlmApiKey: "",
    hostLlmProvider: "",
    privacy: { uploadSummariesOnly: true, localArtifacts: true, storeTranscriptions: true },
    ce: { enabled: false, endpoint: "127.0.0.1:9190", redisUrl: "redis://127.0.0.1:6379" },
  local: { pythonPath: "python", command: "kumiho-mcp", timeout: 30000 },
  };
}

async function makeClient(): Promise<KumihoClient> {
  const config = makeLocalConfig();
  const transport = new McpTransport(config);
  await transport.start();
  const client = new KumihoClient(transport, TEST_PROJECT);
  return client;
}

// ---------------------------------------------------------------------------
// Connectivity
// ---------------------------------------------------------------------------

describe.skipIf(!canRunLive)("Live API — connectivity", () => {
  let client: KumihoClient;

  beforeAll(async () => {
    client = await makeClient();
  });

  afterAll(async () => {
    await client.close();
  });

  it("ping() returns true", async () => {
    const result = await client.ping();
    expect(result).toBe(true);
  }, 15000);
});

// ---------------------------------------------------------------------------
// Working memory — chatAdd / chatGet / chatClear
// ---------------------------------------------------------------------------

describe.skipIf(!canRunLive)("Live API — working memory", () => {
  let client: KumihoClient;
  const testSession = `integration-wm-${RUN_ID}`;

  beforeAll(async () => {
    client = await makeClient();
  });

  afterAll(async () => {
    try {
      await client.chatClear(testSession);
    } catch { /* ignore cleanup errors */ }
    await client.close();
  });

  it("chatAdd stores messages without error", async () => {
    await expect(
      client.chatAdd(testSession, "user", "Integration test user message"),
    ).resolves.not.toThrow();

    await expect(
      client.chatAdd(testSession, "assistant", "Integration test assistant response"),
    ).resolves.not.toThrow();
  }, 15000);

  it("chatGet returns the stored messages with correct roles", async () => {
    await client.chatAdd(testSession, "user", `User turn ${RUN_ID}`);
    await client.chatAdd(testSession, "assistant", `Assistant turn ${RUN_ID}`);

    const state = await client.chatGet(testSession, 50);

    expect(state.message_count).toBeGreaterThanOrEqual(2);
    const roles = state.messages.map((m) => m.role);
    expect(roles).toContain("user");
    expect(roles).toContain("assistant");

    const userMsg = state.messages.find((m) => m.content.includes(`User turn ${RUN_ID}`));
    expect(userMsg).toBeDefined();
  }, 15000);

  it("chatClear empties the session buffer", async () => {
    // Ensure session has content
    await client.chatAdd(testSession, "user", "message to clear");
    const before = await client.chatGet(testSession, 10);
    expect(before.message_count).toBeGreaterThan(0);

    await client.chatClear(testSession);

    const after = await client.chatGet(testSession, 10);
    expect(after.message_count).toBe(0);
  }, 15000);
});

// ---------------------------------------------------------------------------
// Long-term memory — memoryStore
// ---------------------------------------------------------------------------

describe.skipIf(!canRunLive)("Live API — long-term memory", () => {
  let client: KumihoClient;
  let storedRevisionKref: string | null = null;

  beforeAll(async () => {
    client = await makeClient();
  });

  afterAll(async () => {
    if (storedRevisionKref) {
      try {
        await client.memoryDeprecate(storedRevisionKref);
      } catch { /* ignore cleanup errors */ }
    }
    await client.close();
  });

  it("memoryStore returns item_kref and revision_kref", async () => {
    const result = await client.memoryStore({
      type: "fact",
      title: `Integration test fact ${RUN_ID}`,
      summary: `This is an automated integration test memory created at run ${RUN_ID}. Safe to deprecate.`,
      userText: `Integration test run ${RUN_ID}`,
      assistantText: `Automated test memory for run ${RUN_ID}. Safe to deprecate.`,
      tags: ["test", "integration", "automated"],
      spaceHint: "personal",
    });

    expect(result.item_kref).toBeTruthy();
    expect(result.item_kref).toMatch(/^kref:\/\//);
    expect(result.revision_kref).toBeTruthy();
    expect(result.revision_kref).toMatch(/^kref:\/\//);

    storedRevisionKref = result.revision_kref;
  }, 30000);
});

// ---------------------------------------------------------------------------
// End-to-end — full consolidation flow
// ---------------------------------------------------------------------------

describe.skipIf(!canRunLive || !hasLlmKey)("Live API — consolidation end-to-end", () => {
  let client: KumihoClient;
  const config: ResolvedConfig = makeLocalConfig();

  beforeAll(async () => {
    client = await makeClient();
  });

  afterAll(async () => {
    await client.close();
  });

  it("consolidates session: clears working memory and stores summary", async () => {
    const testSession = `integration-consolidation-${RUN_ID}`;

    // Populate the session with a small conversation
    await client.chatAdd(testSession, "user", "What is the capital of France?");
    await client.chatAdd(testSession, "assistant", "The capital of France is Paris.");
    await client.chatAdd(testSession, "user", "What about Germany?");
    await client.chatAdd(testSession, "assistant", "The capital of Germany is Berlin.");

    const before = await client.chatGet(testSession, 10);
    expect(before.message_count).toBe(4);

    const hookState = createHookState();
    hookState.sessionId = testSession;
    hookState.messageCount = 4;

    const redactor = new PIIRedactor();
    // Use real artifact manager pointed at temp dir
    const artifacts = new ArtifactManager(join(homedir(), ".kumiho", "artifacts", "test"));

    const ok = await consolidateSession(client, config, hookState, redactor, artifacts);

    expect(ok).toBe(true);

    // Session should be cleared
    const after = await client.chatGet(testSession, 10);
    expect(after.message_count).toBe(0);

    // State should have a new session ID
    expect(hookState.sessionId).not.toBe(testSession);
    expect(hookState.messageCount).toBe(0);
  }, 60000);
});
