import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockState = vi.hoisted(() => {
  interface StoredMessage {
    role: string;
    content: string;
    timestamp: string;
  }

  class FakeMcpTransport {
    started = false;
    startCalls = 0;
    readonly sessions = new Map<string, StoredMessage[]>();
    readonly addEnv = vi.fn((_vars: Record<string, string>) => {});

    async start(): Promise<void> {
      this.started = true;
      this.startCalls++;
    }

    async close(): Promise<void> {
      this.started = false;
    }

    async ping(): Promise<boolean> {
      return this.started;
    }

    async call<T>(tool: string, params: Record<string, unknown>): Promise<T> {
      if (!this.started) {
        throw new Error("Bridge not initialized. Call start() first.");
      }

      switch (tool) {
        case "kumiho_chat_add": {
          const sessionId = String(params.session_id ?? "");
          const role = String(params.role ?? "");
          const message = String(params.message ?? "");
          const existing = this.sessions.get(sessionId) ?? [];
          existing.push({
            role,
            content: message,
            timestamp: new Date().toISOString(),
          });
          this.sessions.set(sessionId, existing);
          return undefined as T;
        }

        case "kumiho_chat_get": {
          const sessionId = String(params.session_id ?? "");
          const messages = this.sessions.get(sessionId) ?? [];
          return {
            session_id: sessionId,
            message_count: messages.length,
            ttl_remaining: 3600,
            messages,
          } as T;
        }

        case "kumiho_chat_clear": {
          const sessionId = String(params.session_id ?? "");
          this.sessions.delete(sessionId);
          return undefined as T;
        }

        case "kumiho_memory_retrieve":
          return {
            item_krefs: [],
            revision_krefs: [],
            spaces_used: [],
            scores: [],
          } as T;

        case "kumiho_memory_store":
          return {
            item_kref: "kref://item/test",
            revision_kref: "kref://revision/test",
            space_path: "CognitiveMemory/personal",
            summary: String(params.summary ?? ""),
          } as T;

        default:
          return {} as T;
      }
    }

    getDiscoveredTools() {
      return [];
    }
  }

  const transports: FakeMcpTransport[] = [];
  const createTransport = vi.fn(() => {
    const transport = new FakeMcpTransport();
    transports.push(transport);
    return transport;
  });

  return {
    FakeMcpTransport,
    createTransport,
    transports,
  };
});

vi.mock("../client.js", async () => {
  const actual = await vi.importActual<typeof import("../client.js")>("../client.js");
  return {
    ...actual,
    createTransport: mockState.createTransport,
    McpTransport: mockState.FakeMcpTransport,
  };
});

interface FakeApi {
  pluginConfig: Record<string, unknown>;
  logger: {
    info: ReturnType<typeof vi.fn>;
    warn: ReturnType<typeof vi.fn>;
    error: ReturnType<typeof vi.fn>;
  };
  events: Map<string, (event: unknown, ctx: unknown) => unknown>;
  service: { start: (_ctx: unknown) => Promise<void>; stop: (_ctx: unknown) => Promise<void> } | null;
  registerGatewayMethod: ReturnType<typeof vi.fn>;
  registerTool: ReturnType<typeof vi.fn>;
  registerCli: ReturnType<typeof vi.fn>;
  registerCommand: ReturnType<typeof vi.fn>;
  registerService: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
}

function makeApi(): FakeApi {
  const events = new Map<string, (event: unknown, ctx: unknown) => unknown>();

  const api: FakeApi = {
    pluginConfig: {
      mode: "local",
      dreamStateSchedule: "off",
      idleConsolidationTimeout: 0,
      autoRecall: true,
      autoCapture: true,
      local: {
        command: "kumiho-mcp",
        pythonPath: "python",
        timeout: 30_000,
      },
    },
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    },
    events,
    service: null,
    registerGatewayMethod: vi.fn(),
    registerTool: vi.fn(),
    registerCli: vi.fn(),
    registerCommand: vi.fn(),
    registerService: vi.fn((service) => {
      api.service = service as FakeApi["service"];
    }),
    on: vi.fn((name: string, handler: (event: unknown, ctx: unknown) => unknown) => {
      events.set(name, handler);
    }),
  };

  return api;
}

describe("OpenClaw prompt hooks", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockState.transports.length = 0;
  });

  afterEach(() => {
    vi.unmock("node:fs");
  });

  it("lazy-starts the MCP bridge when before_prompt_build fires before service start", async () => {
    const { default: plugin } = await import("../index.js");
    const api = makeApi();

    plugin.register(api as never);

    expect(mockState.createTransport).toHaveBeenCalledOnce();
    expect(mockState.transports).toHaveLength(1);
    expect(mockState.transports[0].startCalls).toBe(0);

    const beforePromptBuild = api.events.get("before_prompt_build");
    const agentEnd = api.events.get("agent_end");

    expect(beforePromptBuild).toBeTypeOf("function");
    expect(agentEnd).toBeTypeOf("function");

    await beforePromptBuild?.(
      {
        messages: [{ role: "user", content: "Remember that I prefer Neovim." }],
      },
      {},
    );

    expect(mockState.transports[0].startCalls).toBe(1);

    await agentEnd?.(
      {
        messages: [{ role: "assistant", content: "I will remember your editor preference." }],
      },
      {},
    );

    const storedMessages = [...mockState.transports[0].sessions.values()].flat();
    expect(storedMessages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(
      api.logger.error.mock.calls.some(([message]) =>
        String(message).includes("Bridge not initialized"),
      ),
    ).toBe(false);
  });

  it("uses host LLM credentials from the OpenClaw runtime config when available", async () => {
    const { default: plugin } = await import("../index.js");
    const api = makeApi() as FakeApi & {
      config?: {
        models?: {
          providers?: Record<string, unknown>;
        };
      };
    };
    api.pluginConfig = {
      ...api.pluginConfig,
      localSummarization: true,
      consolidationModel: { provider: "anthropic" },
    };
    api.config = {
      models: {
        providers: {
          anthropic: { apiKey: "host-anthropic-key" },
        },
      },
    };

    plugin.register(api as never);

    const [resolvedConfig] = mockState.createTransport.mock.calls[0] as [Record<string, unknown>];
    expect(resolvedConfig.hostLlmProvider).toBe("anthropic");
    expect(resolvedConfig.hostLlmApiKey).toBe("host-anthropic-key");
    expect(api.logger.warn).not.toHaveBeenCalledWith(
      expect.stringContaining("no host LLM credentials were resolved"),
    );
  });

  it("parses the current auth-profiles schema under auth.profiles", async () => {
    vi.doMock("node:fs", async () => {
      const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
      return {
        ...actual,
        existsSync: vi.fn((path: string) => path.endsWith("auth-profiles.json")),
        readFileSync: vi.fn(() =>
          JSON.stringify({
            auth: {
              profiles: {
                "anthropic:linux-dev": {
                  provider: "anthropic",
                  mode: "token",
                  token: "sk-ant-host-profile",
                },
              },
            },
          }),
        ),
      };
    });

    const { default: plugin } = await import("../index.js");
    const api = makeApi();
    api.pluginConfig = {
      ...api.pluginConfig,
      localSummarization: true,
      consolidationModel: { provider: "anthropic" },
    };

    plugin.register(api as never);

    const [resolvedConfig] = mockState.createTransport.mock.calls[0] as [Record<string, unknown>];
    expect(resolvedConfig.hostLlmProvider).toBe("anthropic");
    expect(resolvedConfig.hostLlmApiKey).toBe("sk-ant-host-profile");
  });

  it("uses an unexpired openai-codex oauth access token as the host OpenAI credential", async () => {
    vi.doMock("node:fs", async () => {
      const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
      return {
        ...actual,
        existsSync: vi.fn((path: string) => path.endsWith("auth-profiles.json")),
        readFileSync: vi.fn(() =>
          JSON.stringify({
            profiles: {
              "openai-codex:default": {
                type: "oauth",
                provider: "openai-codex",
                access: "openai-oauth-access-token",
                refresh: "openai-oauth-refresh-token",
                expires: Date.now() + 60 * 60 * 1000,
              },
            },
            lastGood: {
              "openai-codex": "openai-codex:default",
            },
          }),
        ),
      };
    });

    const { default: plugin } = await import("../index.js");
    const api = makeApi();
    api.pluginConfig = {
      ...api.pluginConfig,
      localSummarization: true,
      llm: { provider: "openai", model: "gpt-5-codex" },
    };

    plugin.register(api as never);

    const [resolvedConfig] = mockState.createTransport.mock.calls[0] as [Record<string, unknown>];
    expect(resolvedConfig.hostLlmProvider).toBe("openai");
    expect(resolvedConfig.hostLlmApiKey).toBe("openai-oauth-access-token");
  });

  it("warns when dreamStateModel requests a different provider than the only host credential", async () => {
    vi.doMock("node:fs", async () => {
      const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
      return {
        ...actual,
        existsSync: vi.fn((path: string) => path.endsWith("auth-profiles.json")),
        readFileSync: vi.fn(() =>
          JSON.stringify({
            profiles: {
              "anthropic:linux-test": {
                type: "token",
                provider: "anthropic",
                token: "sk-ant-live-ish",
              },
            },
            lastGood: {
              anthropic: "anthropic:linux-test",
            },
          }),
        ),
      };
    });

    const { default: plugin } = await import("../index.js");
    const api = makeApi();
    api.pluginConfig = {
      ...api.pluginConfig,
      dreamStateModel: { provider: "openai", model: "gpt-4o-mini" },
    };

    plugin.register(api as never);

    expect(api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("dreamStateModel/llm provider \"openai\" does not match"),
    );
  });

  it("warns when local summarization cannot resolve the host auth profile", async () => {
    vi.doMock("node:fs", async () => {
      const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
      return {
        ...actual,
        existsSync: vi.fn(() => false),
        readFileSync: vi.fn(() => {
          throw new Error("readFileSync should not be called");
        }),
      };
    });

    const { default: plugin } = await import("../index.js");
    const api = makeApi();
    api.pluginConfig = {
      ...api.pluginConfig,
      localSummarization: true,
    };

    plugin.register(api as never);

    expect(api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("no host LLM credentials were resolved"),
    );
  });
});
