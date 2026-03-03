/**
 * MCP stdio transport bridge.
 *
 * Spawns `kumiho-mcp` (Python) as a child process and communicates over
 * stdin/stdout using MCP JSON-RPC 2.0. This lets the OpenClaw plugin use
 * the full kumiho-memory Python SDK without any HTTP deployment.
 *
 * Lifecycle:
 *   1. spawn() → starts the Python process
 *   2. initialize() → MCP handshake (capabilities exchange)
 *   3. call() → tool invocations via JSON-RPC
 *   4. close() → graceful shutdown
 *
 * Protocol reference: https://modelcontextprotocol.io/specification
 */

import { spawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { createInterface, type Interface as ReadlineInterface } from "node:readline";

import { resolvePythonPath } from "./python-setup.js";

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export class McpBridgeError extends Error {
  constructor(
    message: string,
    public readonly code: string,
  ) {
    super(message);
    this.name = "McpBridgeError";
  }
}

// ---------------------------------------------------------------------------
// JSON-RPC 2.0 types
// ---------------------------------------------------------------------------

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// MCP tool call/result types
// ---------------------------------------------------------------------------

export interface McpToolCallParams {
  name: string;
  arguments: Record<string, unknown>;
}

export interface McpToolResult {
  content: Array<{ type: string; text: string }>;
  isError?: boolean;
}

export interface McpServerCapabilities {
  tools?: { listChanged?: boolean };
  resources?: { listChanged?: boolean };
  prompts?: { listChanged?: boolean };
}

export interface McpToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Pending request tracker
// ---------------------------------------------------------------------------

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

// ---------------------------------------------------------------------------
// MCP Bridge
// ---------------------------------------------------------------------------

export interface McpBridgeOptions {
  /** Python executable path. Default: "python" */
  pythonPath?: string;
  /** MCP server module or command. Default: "kumiho-mcp" */
  command?: string;
  /** Extra args passed to the MCP server process. */
  args?: string[];
  /** Environment variables merged into the child process env. */
  env?: Record<string, string>;
  /** Working directory for the child process. */
  cwd?: string;
  /** Request timeout in milliseconds. Default: 30000 */
  timeout?: number;
  /** Logger for bridge-level events. */
  logger?: {
    info: (msg: string) => void;
    warn: (msg: string) => void;
    error: (msg: string) => void;
  };
}

export class McpBridge extends EventEmitter {
  private proc: ChildProcess | null = null;
  private reader: ReadlineInterface | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingRequest>();
  private initialized = false;
  private serverCapabilities: McpServerCapabilities = {};
  private availableTools: McpToolDefinition[] = [];

  private readonly pythonPath: string;
  private readonly command: string;
  private readonly args: string[];
  private readonly childEnv: Record<string, string>;
  private readonly cwd: string | undefined;
  private readonly timeout: number;
  private readonly log: NonNullable<McpBridgeOptions["logger"]>;

  constructor(opts: McpBridgeOptions = {}) {
    super();
    this.pythonPath = opts.pythonPath ?? "python";
    this.command = opts.command ?? "kumiho-mcp";
    this.args = opts.args ?? [];
    this.childEnv = opts.env ?? {};
    this.cwd = opts.cwd;
    this.timeout = opts.timeout ?? 30_000;
    this.log = opts.logger ?? {
      info: () => {},
      warn: () => {},
      error: () => {},
    };
  }

  // -----------------------------------------------------------------------
  // Lifecycle
  // -----------------------------------------------------------------------

  /**
   * Spawn the Python MCP server and perform the initialization handshake.
   */
  async start(): Promise<void> {
    if (this.proc) {
      throw new McpBridgeError("Bridge already started", "ALREADY_STARTED");
    }

    // Auto-detect Python + command when the user hasn't explicitly configured them.
    // resolvePythonPath probes ~/.kumiho/venv and other known locations, caches
    // the result, and falls back gracefully so the spawn error remains readable.
    const resolved = resolvePythonPath(
      { pythonPath: this.pythonPath, command: this.command },
      this.log
    );
    const effectivePythonPath = resolved.pythonPath;
    const effectiveCommand    = resolved.command;

    // Determine how to launch:
    //   .py script  → python <script> [args]
    //   Python module (dot-separated, no path separator) → python -m <module> [args]
    //   binary / full path → <command> [args]
    const hasPathSep = effectiveCommand.includes("/") || effectiveCommand.includes("\\");
    const isScript = effectiveCommand.endsWith(".py");
    const isModule = !isScript && !hasPathSep && effectiveCommand.includes(".");
    const spawnCmd = isScript || isModule ? effectivePythonPath : effectiveCommand;
    const spawnArgs = isScript
      ? [effectiveCommand, ...this.args]
      : isModule
        ? ["-m", effectiveCommand, ...this.args]
        : this.args;

    this.log.info(
      `Spawning MCP server: ${spawnCmd} ${spawnArgs.join(" ")}`,
    );

    this.proc = spawn(spawnCmd, spawnArgs, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...this.childEnv },
      cwd: this.cwd,
      // Prevent the child from inheriting the parent's signal handlers
      detached: false,
    });

    // Stderr → log warnings (the MCP server may emit diagnostics there)
    this.proc.stderr?.on("data", (chunk: Buffer) => {
      const line = chunk.toString().trim();
      if (line) this.log.warn(`[mcp-stderr] ${line}`);
    });

    // Handle unexpected exit
    this.proc.on("exit", (code, signal) => {
      this.log.error(
        `MCP server exited (code=${code}, signal=${signal})`,
      );
      this.rejectAllPending(
        new McpBridgeError(
          `MCP server exited unexpectedly (code=${code})`,
          "PROCESS_EXIT",
        ),
      );
      this.proc = null;
      this.initialized = false;
      this.emit("exit", code, signal);
    });

    this.proc.on("error", (err) => {
      this.log.error(`MCP server spawn error: ${err.message}`);
      this.rejectAllPending(
        new McpBridgeError(
          `Failed to spawn MCP server: ${err.message}`,
          "SPAWN_ERROR",
        ),
      );
      this.proc = null;
      this.emit("error", err);
    });

    // Line-delimited JSON-RPC reader on stdout
    this.reader = createInterface({ input: this.proc.stdout! });
    this.reader.on("line", (line) => this.handleLine(line));

    // MCP initialization handshake
    await this.initialize();
  }

  /**
   * Graceful shutdown: send a shutdown notification and kill the process.
   */
  async close(): Promise<void> {
    if (!this.proc) return;

    try {
      // Send shutdown notification (no response expected)
      this.sendNotification("notifications/cancelled", {
        reason: "Plugin shutting down",
      });
    } catch {
      // Ignore write errors during shutdown
    }

    this.reader?.close();
    this.reader = null;

    // Give the process a moment to exit cleanly, then force-kill
    const proc = this.proc;
    this.proc = null;
    this.initialized = false;

    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        proc.kill("SIGKILL");
        resolve();
      }, 3_000);

      proc.on("exit", () => {
        clearTimeout(timer);
        resolve();
      });

      proc.kill("SIGTERM");
    });

    this.rejectAllPending(
      new McpBridgeError("Bridge closed", "BRIDGE_CLOSED"),
    );
  }

  get isRunning(): boolean {
    return this.proc !== null && this.initialized;
  }

  get capabilities(): McpServerCapabilities {
    return this.serverCapabilities;
  }

  get tools(): McpToolDefinition[] {
    return this.availableTools;
  }

  // -----------------------------------------------------------------------
  // MCP handshake
  // -----------------------------------------------------------------------

  private async initialize(): Promise<void> {
    // Step 1: Send `initialize` request
    const initResult = (await this.sendRequest("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {
        roots: { listChanged: false },
      },
      clientInfo: {
        name: "@kumiho/openclaw-kumiho",
        version: "0.2.2",
      },
    })) as {
      protocolVersion: string;
      capabilities: McpServerCapabilities;
      serverInfo: { name: string; version: string };
    };

    this.serverCapabilities = initResult.capabilities;
    this.log.info(
      `MCP handshake OK: ${initResult.serverInfo.name} v${initResult.serverInfo.version}`,
    );

    // Step 2: Send `initialized` notification
    this.sendNotification("notifications/initialized", {});

    // Step 3: Discover available tools
    const toolsResult = (await this.sendRequest("tools/list", {})) as {
      tools: McpToolDefinition[];
    };
    this.availableTools = toolsResult.tools;
    this.log.info(
      `MCP tools discovered: ${this.availableTools.map((t) => t.name).join(", ")}`,
    );

    this.initialized = true;
  }

  // -----------------------------------------------------------------------
  // Tool invocation (public API)
  // -----------------------------------------------------------------------

  /**
   * Call an MCP tool and return the parsed result.
   *
   * This is the primary method used by KumihoClient in local mode.
   */
  async callTool<T = unknown>(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<T> {
    if (!this.initialized) {
      throw new McpBridgeError(
        "Bridge not initialized. Call start() first.",
        "NOT_INITIALIZED",
      );
    }

    const result = (await this.sendRequest("tools/call", {
      name: toolName,
      arguments: args,
    })) as McpToolResult;

    if (result.isError) {
      const errorText =
        result.content?.[0]?.text ?? "Unknown MCP tool error";
      throw new McpBridgeError(
        `MCP tool '${toolName}' failed: ${errorText}`,
        "TOOL_ERROR",
      );
    }

    // MCP tools return content as an array of text blocks.
    // Parse the first text block as JSON, falling back to raw text.
    const text = result.content?.[0]?.text ?? "{}";
    try {
      return JSON.parse(text) as T;
    } catch {
      return text as unknown as T;
    }
  }

  // -----------------------------------------------------------------------
  // JSON-RPC transport
  // -----------------------------------------------------------------------

  private sendRequest(
    method: string,
    params: Record<string, unknown>,
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (!this.proc?.stdin?.writable) {
        reject(
          new McpBridgeError("MCP server stdin not writable", "NOT_WRITABLE"),
        );
        return;
      }

      const id = this.nextId++;
      const request: JsonRpcRequest = {
        jsonrpc: "2.0",
        id,
        method,
        params,
      };

      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new McpBridgeError(
            `MCP request '${method}' timed out after ${this.timeout}ms`,
            "TIMEOUT",
          ),
        );
      }, this.timeout);

      this.pending.set(id, { resolve, reject, timer });

      const line = JSON.stringify(request) + "\n";
      this.proc.stdin.write(line, (err) => {
        if (err) {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(
            new McpBridgeError(
              `Failed to write to MCP server: ${err.message}`,
              "WRITE_ERROR",
            ),
          );
        }
      });
    });
  }

  private sendNotification(
    method: string,
    params: Record<string, unknown>,
  ): void {
    if (!this.proc?.stdin?.writable) return;

    const notification: JsonRpcNotification = {
      jsonrpc: "2.0",
      method,
      params,
    };
    this.proc.stdin.write(JSON.stringify(notification) + "\n");
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;

    let msg: JsonRpcResponse;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      this.log.warn(`[mcp-stdout] Non-JSON: ${trimmed}`);
      return;
    }

    // Server-initiated notification (no id) — log and emit
    if (!("id" in msg)) {
      this.emit("notification", msg);
      return;
    }

    const pending = this.pending.get(msg.id);
    if (!pending) {
      this.log.warn(`Orphaned MCP response id=${msg.id}`);
      return;
    }

    this.pending.delete(msg.id);
    clearTimeout(pending.timer);

    if (msg.error) {
      pending.reject(
        new McpBridgeError(
          `MCP error ${msg.error.code}: ${msg.error.message}`,
          "RPC_ERROR",
        ),
      );
    } else {
      pending.resolve(msg.result);
    }
  }

  private rejectAllPending(error: Error): void {
    for (const [_id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
