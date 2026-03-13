import { beforeEach, describe, expect, it, vi } from "vitest";

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
});
