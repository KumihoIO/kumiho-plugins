// src/index.ts
import { homedir as homedir3 } from "os";
import { join as join3 } from "path";
import { readFileSync, existsSync as existsSync2 } from "fs";
import { execFile } from "child_process";

// src/mcp-bridge.ts
import { spawn } from "child_process";
import { EventEmitter } from "events";
import { createInterface } from "readline";

// src/python-setup.ts
import { existsSync } from "fs";
import { homedir, platform } from "os";
import { join } from "path";
import { spawnSync } from "child_process";
var IS_WIN = platform() === "win32";
var BIN = IS_WIN ? "Scripts" : "bin";
var EXT = IS_WIN ? ".exe" : "";
function buildCandidates() {
  const home = homedir();
  const localAppData = IS_WIN ? process.env.LOCALAPPDATA ?? "" : "";
  return [
    // 1. Kumiho-managed venv (created by `npm run setup` / `npx kumiho-setup`)
    {
      python: join(home, ".kumiho", "venv", BIN, `python${EXT}`),
      absolute: true
    },
    // 2. kumiho-claude integration venv (Windows, used by Claude Code plugin)
    ...IS_WIN ? [
      {
        python: join(
          localAppData,
          "kumiho-claude",
          "venv",
          "Scripts",
          "python.exe"
        ),
        absolute: true
      }
    ] : [],
    // 3. System Python (PATH lookup — fast fail if not found)
    { python: "python3", absolute: false },
    { python: "python", absolute: false }
  ];
}
function hasKumihoMcp(candidate) {
  if (candidate.absolute && !existsSync(candidate.python)) {
    return false;
  }
  const result = spawnSync(
    candidate.python,
    ["-c", "import kumiho.mcp_server; print('ok')"],
    { encoding: "utf8", timeout: 5e3 }
  );
  return result.status === 0 && result.stdout.includes("ok");
}
var _cached = null;
function detectPython(logger, fresh = false) {
  if (_cached && !fresh) return _cached;
  for (const candidate of buildCandidates()) {
    if (hasKumihoMcp(candidate)) {
      logger?.info(
        `[kumiho] Auto-detected kumiho.mcp_server at: ${candidate.python}`
      );
      _cached = { pythonPath: candidate.python, command: "kumiho.mcp_server" };
      return _cached;
    }
  }
  logger?.warn(
    "[kumiho] kumiho.mcp_server not found in any known Python environment. Run 'npx kumiho-setup' (or 'npm run setup' in the plugin dir) to install it, or set local.pythonPath in your openclaw.json plugin config."
  );
  _cached = { pythonPath: "python", command: "kumiho-mcp" };
  return _cached;
}
function resolvePythonPath(configured, logger) {
  const isDefault = configured.pythonPath === "python" && configured.command === "kumiho-mcp";
  if (!isDefault) {
    return { pythonPath: configured.pythonPath, command: configured.command };
  }
  return detectPython(logger);
}

// src/mcp-bridge.ts
var McpBridgeError = class extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
    this.name = "McpBridgeError";
  }
};
var McpBridge = class extends EventEmitter {
  proc = null;
  reader = null;
  nextId = 1;
  pending = /* @__PURE__ */ new Map();
  initialized = false;
  serverCapabilities = {};
  availableTools = [];
  pythonPath;
  command;
  args;
  childEnv;
  cwd;
  timeout;
  log;
  constructor(opts = {}) {
    super();
    this.pythonPath = opts.pythonPath ?? "python";
    this.command = opts.command ?? "kumiho-mcp";
    this.args = opts.args ?? [];
    this.childEnv = opts.env ?? {};
    this.cwd = opts.cwd;
    this.timeout = opts.timeout ?? 3e4;
    this.log = opts.logger ?? {
      info: () => {
      },
      warn: () => {
      },
      error: () => {
      }
    };
  }
  // -----------------------------------------------------------------------
  // Lifecycle
  // -----------------------------------------------------------------------
  /**
   * Spawn the Python MCP server and perform the initialization handshake.
   */
  async start() {
    if (this.proc) {
      throw new McpBridgeError("Bridge already started", "ALREADY_STARTED");
    }
    const resolved = resolvePythonPath(
      { pythonPath: this.pythonPath, command: this.command },
      this.log
    );
    const effectivePythonPath = resolved.pythonPath;
    const effectiveCommand = resolved.command;
    const hasPathSep = effectiveCommand.includes("/") || effectiveCommand.includes("\\");
    const isScript = effectiveCommand.endsWith(".py");
    const isModule = !isScript && !hasPathSep && effectiveCommand.includes(".");
    const spawnCmd = isScript || isModule ? effectivePythonPath : effectiveCommand;
    const spawnArgs = isScript ? [effectiveCommand, ...this.args] : isModule ? ["-m", effectiveCommand, ...this.args] : this.args;
    this.log.info(
      `Spawning MCP server: ${spawnCmd} ${spawnArgs.join(" ")}`
    );
    this.proc = spawn(spawnCmd, spawnArgs, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...this.childEnv },
      cwd: this.cwd,
      // Prevent the child from inheriting the parent's signal handlers
      detached: false
    });
    this.proc.stderr?.on("data", (chunk) => {
      const line = chunk.toString().trim();
      if (line) this.log.warn(`[mcp-stderr] ${line}`);
    });
    this.proc.on("exit", (code, signal) => {
      this.log.error(
        `MCP server exited (code=${code}, signal=${signal})`
      );
      this.rejectAllPending(
        new McpBridgeError(
          `MCP server exited unexpectedly (code=${code})`,
          "PROCESS_EXIT"
        )
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
          "SPAWN_ERROR"
        )
      );
      this.proc = null;
      this.emit("error", err);
    });
    this.reader = createInterface({ input: this.proc.stdout });
    this.reader.on("line", (line) => this.handleLine(line));
    await this.initialize();
  }
  /**
   * Graceful shutdown: send a shutdown notification and kill the process.
   */
  async close() {
    if (!this.proc) return;
    try {
      this.sendNotification("notifications/cancelled", {
        reason: "Plugin shutting down"
      });
    } catch {
    }
    this.reader?.close();
    this.reader = null;
    const proc = this.proc;
    this.proc = null;
    this.initialized = false;
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        proc.kill("SIGKILL");
        resolve();
      }, 3e3);
      proc.on("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      proc.kill("SIGTERM");
    });
    this.rejectAllPending(
      new McpBridgeError("Bridge closed", "BRIDGE_CLOSED")
    );
  }
  get isRunning() {
    return this.proc !== null && this.initialized;
  }
  get capabilities() {
    return this.serverCapabilities;
  }
  get tools() {
    return this.availableTools;
  }
  // -----------------------------------------------------------------------
  // MCP handshake
  // -----------------------------------------------------------------------
  async initialize() {
    const initResult = await this.sendRequest("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {
        roots: { listChanged: false }
      },
      clientInfo: {
        name: "@kumiho/openclaw-kumiho",
        version: "0.2.2"
      }
    });
    this.serverCapabilities = initResult.capabilities;
    this.log.info(
      `MCP handshake OK: ${initResult.serverInfo.name} v${initResult.serverInfo.version}`
    );
    this.sendNotification("notifications/initialized", {});
    const toolsResult = await this.sendRequest("tools/list", {});
    this.availableTools = toolsResult.tools;
    this.log.info(
      `MCP tools discovered: ${this.availableTools.map((t) => t.name).join(", ")}`
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
  async callTool(toolName, args) {
    if (!this.initialized) {
      throw new McpBridgeError(
        "Bridge not initialized. Call start() first.",
        "NOT_INITIALIZED"
      );
    }
    const result = await this.sendRequest("tools/call", {
      name: toolName,
      arguments: args
    });
    if (result.isError) {
      const errorText = result.content?.[0]?.text ?? "Unknown MCP tool error";
      throw new McpBridgeError(
        `MCP tool '${toolName}' failed: ${errorText}`,
        "TOOL_ERROR"
      );
    }
    const text = result.content?.[0]?.text ?? "{}";
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  // -----------------------------------------------------------------------
  // JSON-RPC transport
  // -----------------------------------------------------------------------
  sendRequest(method, params) {
    return new Promise((resolve, reject) => {
      if (!this.proc?.stdin?.writable) {
        reject(
          new McpBridgeError("MCP server stdin not writable", "NOT_WRITABLE")
        );
        return;
      }
      const id = this.nextId++;
      const request = {
        jsonrpc: "2.0",
        id,
        method,
        params
      };
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new McpBridgeError(
            `MCP request '${method}' timed out after ${this.timeout}ms`,
            "TIMEOUT"
          )
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
              "WRITE_ERROR"
            )
          );
        }
      });
    });
  }
  sendNotification(method, params) {
    if (!this.proc?.stdin?.writable) return;
    const notification = {
      jsonrpc: "2.0",
      method,
      params
    };
    this.proc.stdin.write(JSON.stringify(notification) + "\n");
  }
  handleLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      this.log.warn(`[mcp-stdout] Non-JSON: ${trimmed}`);
      return;
    }
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
          "RPC_ERROR"
        )
      );
    } else {
      pending.resolve(msg.result);
    }
  }
  rejectAllPending(error) {
    for (const [_id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
};

// src/client.ts
var KumihoApiError = class extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
    this.name = "KumihoApiError";
  }
};
var HttpTransport = class {
  baseUrl;
  apiKey;
  timeout;
  constructor(config2) {
    this.baseUrl = config2.endpoint.replace(/\/+$/, "");
    this.apiKey = config2.apiKey;
    this.timeout = 3e4;
  }
  async call(tool, params) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/mcp/tools`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ tool, arguments: params }),
        signal: controller.signal
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        const code = res.status === 401 ? "UNAUTHORIZED" : res.status === 429 ? "RATE_LIMIT" : "API_ERROR";
        throw new KumihoApiError(
          `Kumiho API ${tool} failed: ${res.status} ${body}`,
          code,
          res.status
        );
      }
      return await res.json();
    } catch (err) {
      if (err instanceof KumihoApiError) throw err;
      if (err.name === "AbortError") {
        throw new KumihoApiError("Kumiho API timeout", "TIMEOUT", 0);
      }
      throw new KumihoApiError(
        `Kumiho API network error: ${err.message}`,
        "NETWORK_ERROR",
        0
      );
    } finally {
      clearTimeout(timer);
    }
  }
  async ping() {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5e3);
      try {
        const res = await fetch(`${this.baseUrl}/health`, {
          headers: { Authorization: `Bearer ${this.apiKey}` },
          signal: controller.signal
        });
        return res.ok;
      } finally {
        clearTimeout(timer);
      }
    } catch {
      return false;
    }
  }
};
var McpTransport = class {
  bridge;
  constructor(config2, logger) {
    this.bridge = new McpBridge({
      pythonPath: config2.local.pythonPath,
      command: config2.local.command,
      args: config2.local.args,
      env: config2.local.env,
      cwd: config2.local.cwd,
      timeout: config2.local.timeout,
      logger
    });
  }
  async start() {
    await this.bridge.start();
  }
  async close() {
    await this.bridge.close();
  }
  async call(tool, params) {
    return this.bridge.callTool(tool, params);
  }
  async ping() {
    return this.bridge.isRunning;
  }
  /** Expose discovered tools from the Python MCP server. */
  get discoveredTools() {
    return this.bridge.tools;
  }
  getDiscoveredTools() {
    return this.bridge.tools;
  }
};
function createTransport(config2, logger) {
  if (config2.mode === "local") {
    return new McpTransport(config2, logger);
  }
  return new HttpTransport(config2);
}
var KumihoClient = class {
  transport;
  project;
  constructor(transport2, project) {
    this.transport = transport2;
    this.project = project;
  }
  /** Start the underlying transport (needed for local/MCP mode). */
  async start() {
    await this.transport.start?.();
  }
  /** Close the underlying transport. */
  async close() {
    await this.transport.close?.();
  }
  // -----------------------------------------------------------------------
  // Generic tool invocation (pass-through to any MCP tool)
  // -----------------------------------------------------------------------
  /** Invoke any MCP tool by name. Used for pass-through asset management tools. */
  async callTool(name, params) {
    return this.transport.call(name, params);
  }
  /** Return tool definitions discovered from the MCP backend (after start). */
  getDiscoveredTools() {
    return this.transport.getDiscoveredTools?.() ?? [];
  }
  // -----------------------------------------------------------------------
  // Working memory (Redis-backed short-term buffer)
  // -----------------------------------------------------------------------
  async chatAdd(sessionId, role, content, metadata) {
    await this.transport.call("kumiho_chat_add", {
      project: this.project,
      session_id: sessionId,
      role,
      message: content,
      metadata
    });
  }
  async chatGet(sessionId, limit = 20) {
    return this.transport.call("kumiho_chat_get", {
      project: this.project,
      session_id: sessionId,
      limit
    });
  }
  async chatClear(sessionId) {
    await this.transport.call("kumiho_chat_clear", {
      project: this.project,
      session_id: sessionId
    });
  }
  // -----------------------------------------------------------------------
  // Long-term memory storage
  // -----------------------------------------------------------------------
  async memoryStore(params) {
    return this.transport.call("kumiho_memory_store", {
      project: this.project,
      space_hint: params.spaceHint,
      user_text: params.userText,
      assistant_text: params.assistantText,
      type: params.type,
      title: params.title,
      summary: params.summary,
      topics: params.topics,
      artifact_location: params.artifactLocation,
      metadata: params.metadata,
      tags: params.tags,
      bundle_name: params.bundleName,
      source_revision_krefs: params.sourceRevisionKrefs
    });
  }
  // -----------------------------------------------------------------------
  // Long-term memory retrieval
  // -----------------------------------------------------------------------
  async memoryRetrieve(params) {
    const raw = await this.transport.call("kumiho_memory_retrieve", {
      project: this.project,
      query: params.query,
      limit: params.limit,
      space_paths: params.spacePaths,
      memory_types: params.memoryTypes
    });
    if (raw.revisions) return raw.revisions;
    return (raw.revision_krefs ?? []).map((kref, i) => ({
      kref,
      type: "summary",
      title: "",
      summary: "",
      topics: [],
      space: raw.spaces_used[i]
    }));
  }
  // -----------------------------------------------------------------------
  // Revision details
  // -----------------------------------------------------------------------
  async getRevision(kref) {
    return this.transport.call("kumiho_get_revision", { kref });
  }
  async getRevisions(krefs) {
    return Promise.all(krefs.map((k) => this.getRevision(k)));
  }
  // -----------------------------------------------------------------------
  // Memory management
  // -----------------------------------------------------------------------
  async memoryDelete(kref) {
    await this.transport.call("kumiho_memory_delete", {
      project: this.project,
      kref
    });
  }
  async memoryDeprecate(kref) {
    await this.transport.call("kumiho_memory_deprecate", {
      project: this.project,
      kref,
      deprecated: true
    });
  }
  // -----------------------------------------------------------------------
  // Tool execution memory
  // -----------------------------------------------------------------------
  async storeToolExecution(params) {
    const isError = ["failed", "error", "blocked"].includes(params.status) || params.exitCode != null && params.exitCode !== 0;
    return this.memoryStore({
      type: isError ? "error" : "action",
      title: `${isError ? "Failed" : "Completed"}: ${params.task}`,
      summary: isError ? `Task "${params.task}" failed (exit ${params.exitCode ?? "N/A"}): ${params.stderr?.slice(0, 200) ?? "unknown error"}` : `Successfully executed: ${params.task}`,
      topics: params.topics ?? [],
      spaceHint: params.spaceHint,
      tags: [isError ? "error" : "action", params.status, "published"],
      metadata: {
        task: params.task,
        status: params.status,
        exit_code: params.exitCode,
        duration_ms: params.durationMs,
        tools: params.tools,
        open_questions: params.openQuestions
      }
    });
  }
  // -----------------------------------------------------------------------
  // Creative Memory (BFF async pipeline)
  // -----------------------------------------------------------------------
  /**
   * Fire a creative capture job to the kumiho-FastAPI BFF.
   *
   * Returns immediately with a job ID — the BFF runs the 7-step graph
   * pipeline (ensureSpace → createItem → createRevision → createArtifact →
   * createEdge → memoryStore → discoverEdges) as a BackgroundTask so the
   * agent turn is never blocked.
   *
   * @param params   Creative capture request
   * @param bffUrl   BFF base URL (e.g. "https://api.kumiho.cloud" or "http://localhost:8000")
   * @param apiKey   Bearer token forwarded to the BFF
   */
  async creativeEnqueue(params, bffUrl, apiKey) {
    const url = `${bffUrl.replace(/\/+$/, "")}/api/v1/apps/creative/capture`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1e4);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          title: params.title,
          content: params.content,
          kind: params.kind,
          project: params.project,
          tags: params.tags ?? [],
          source_memory_kref: params.sourceMemoryKref,
          metadata: params.metadata ?? {}
        }),
        signal: controller.signal
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new KumihoApiError(
          `Creative capture BFF error: ${res.status} ${body}`,
          "BFF_ERROR",
          res.status
        );
      }
      return await res.json();
    } catch (err) {
      if (err instanceof KumihoApiError) throw err;
      if (err.name === "AbortError") {
        throw new KumihoApiError("Creative capture BFF timeout", "TIMEOUT", 0);
      }
      throw new KumihoApiError(
        `Creative capture BFF network error: ${err.message}`,
        "NETWORK_ERROR",
        0
      );
    } finally {
      clearTimeout(timer);
    }
  }
  // -----------------------------------------------------------------------
  // Dream State
  // -----------------------------------------------------------------------
  async triggerDreamState(modelConfig) {
    const params = { project: this.project };
    if (modelConfig?.provider) params.provider = modelConfig.provider;
    if (modelConfig?.model) params.model = modelConfig.model;
    if (modelConfig?.apiKey) params.api_key = modelConfig.apiKey;
    return this.transport.call("kumiho_memory_dream_state", params);
  }
  // -----------------------------------------------------------------------
  // Health check
  // -----------------------------------------------------------------------
  async ping() {
    return this.transport.ping();
  }
};

// src/privacy.ts
var PII_PATTERNS = [
  {
    type: "email",
    regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g
  },
  {
    type: "phone",
    regex: /(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}/g
  },
  {
    type: "ssn",
    regex: /\b\d{3}-\d{2}-\d{4}\b/g
  },
  {
    type: "credit_card",
    regex: /\b(?:\d{4}[-\s]?){3}\d{4}\b/g
  },
  {
    type: "ip_address",
    regex: /\b(?:\d{1,3}\.){3}\d{1,3}\b/g
  }
];
var PIIRedactor = class {
  counters = /* @__PURE__ */ new Map();
  /** Reset placeholder counters (useful between sessions). */
  reset() {
    this.counters.clear();
  }
  nextPlaceholder(type) {
    const count = (this.counters.get(type) ?? 0) + 1;
    this.counters.set(type, count);
    return `${type.toUpperCase()}_${String(count).padStart(3, "0")}`;
  }
  /**
   * Detect and redact PII from text.
   * Returns the sanitized text and a list of redacted entities.
   */
  redact(text) {
    const entities = [];
    let result = text;
    for (const pattern of PII_PATTERNS) {
      result = result.replace(pattern.regex, (_match) => {
        const placeholder = this.nextPlaceholder(pattern.type);
        entities.push({
          type: pattern.type,
          placeholder,
          original: "[REDACTED]"
        });
        return `[${placeholder}]`;
      });
    }
    return { text: result, entities };
  }
  /**
   * Replace remaining PII placeholders with generic descriptors
   * for human-readable summaries.
   */
  anonymizeSummary(summary) {
    return summary.replace(/\[EMAIL_\d+\]/g, "[email]").replace(/\[PHONE_\d+\]/g, "[phone]").replace(/\[SSN_\d+\]/g, "[ssn]").replace(/\[CREDIT_CARD_\d+\]/g, "[card]").replace(/\[IP_ADDRESS_\d+\]/g, "[ip]");
  }
};

// src/artifacts.ts
import { createHash } from "crypto";
import { mkdir, writeFile, stat, readFile } from "fs/promises";
import { homedir as homedir2 } from "os";
import { join as join2 } from "path";
var DEFAULT_ARTIFACT_ROOT = join2(homedir2(), ".kumiho", "artifacts");
async function ensureDir(dir) {
  await mkdir(dir, { recursive: true });
}
function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}
var ArtifactManager = class {
  root;
  constructor(artifactDir) {
    this.root = artifactDir ?? DEFAULT_ARTIFACT_ROOT;
  }
  /**
   * Save a conversation transcript as a local Markdown file.
   * Returns an artifact pointer for Kumiho metadata.
   */
  async saveConversation(project, sessionId, messages, summary) {
    const space = "sessions";
    const dir = join2(this.root, project, space);
    await ensureDir(dir);
    const filename = `${sessionId.replace(/:/g, "-")}.md`;
    const filePath = join2(dir, filename);
    const lines = [
      `# Session: ${sessionId}`,
      "",
      `**Messages**: ${messages.length}`,
      `**Created**: ${(/* @__PURE__ */ new Date()).toISOString()}`,
      "",
      "---",
      ""
    ];
    if (summary) {
      lines.push("## Summary", "", summary, "", "---", "");
    }
    lines.push("## Conversation", "");
    for (const msg of messages) {
      const role = msg.role === "user" ? "User" : "Assistant";
      lines.push(`### ${role} (${msg.timestamp})`, "", msg.content, "");
    }
    const content = lines.join("\n");
    await writeFile(filePath, content, "utf-8");
    const hash = sha256(content);
    const fileStats = await stat(filePath);
    return {
      type: "chat_transcript",
      storage: "local",
      location: `file://${filePath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        message_count: messages.length,
        session_id: sessionId
      }
    };
  }
  /**
   * Save a tool execution log as a local file.
   */
  async saveExecutionLog(project, task, params) {
    const dir = join2(this.root, project, "executions");
    await ensureDir(dir);
    const timestamp = (/* @__PURE__ */ new Date()).toISOString().replace(/[:.]/g, "-");
    const filename = `exec-${timestamp}.md`;
    const filePath = join2(dir, filename);
    const lines = [
      `# Execution: ${task}`,
      "",
      `**Status**: ${params.status}`,
      `**Exit Code**: ${params.exitCode ?? "N/A"}`,
      `**Duration**: ${params.durationMs != null ? `${params.durationMs}ms` : "N/A"}`,
      `**Timestamp**: ${(/* @__PURE__ */ new Date()).toISOString()}`,
      ""
    ];
    if (params.stdout) {
      lines.push("## stdout", "", "```", params.stdout, "```", "");
    }
    if (params.stderr) {
      lines.push("## stderr", "", "```", params.stderr, "```", "");
    }
    const content = lines.join("\n");
    await writeFile(filePath, content, "utf-8");
    const hash = sha256(content);
    const fileStats = await stat(filePath);
    return {
      type: "execution_log",
      storage: "local",
      location: `file://${filePath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        task,
        status: params.status,
        exit_code: params.exitCode
      }
    };
  }
  /**
   * Copy an attachment into the artifact directory and return a pointer.
   */
  async saveAttachment(project, sourcePath, mimeType, description) {
    const dir = join2(this.root, project, "attachments");
    await ensureDir(dir);
    const sourceContent = await readFile(sourcePath);
    const hash = sha256(sourceContent);
    const ext = sourcePath.split(".").pop() ?? "bin";
    const filename = `${hash.slice(0, 16)}.${ext}`;
    const destPath = join2(dir, filename);
    await writeFile(destPath, sourceContent);
    const fileStats = await stat(destPath);
    return {
      type: mimeType.startsWith("image/") ? "image" : mimeType.startsWith("audio/") ? "voice_recording" : mimeType.startsWith("video/") ? "video" : "document",
      storage: "local",
      location: `file://${destPath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        mime_type: mimeType,
        original_path: sourcePath,
        description
      }
    };
  }
  /**
   * Get the local artifact directory path for a project.
   */
  getProjectDir(project) {
    return join2(this.root, project);
  }
};

// src/session.ts
async function userHash(canonicalId) {
  const encoded = new TextEncoder().encode(canonicalId);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  const hex = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex.slice(0, 10);
}
function utcDate() {
  const d = /* @__PURE__ */ new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}
var sequenceCounters = /* @__PURE__ */ new Map();
function nextSequence(userId, date) {
  const key = `${userId}:${date}`;
  const seq = (sequenceCounters.get(key) ?? 0) + 1;
  sequenceCounters.set(key, seq);
  return String(seq).padStart(3, "0");
}
async function generateSessionId(userId, context = "personal", newSession = false) {
  const hash = await userHash(userId);
  const date = utcDate();
  const key = `${context}:user-${hash}:${date}`;
  if (!newSession) {
    const existing = sequenceCounters.get(`${userId}:${date}`);
    if (existing) {
      return `${key}:${String(existing).padStart(3, "0")}`;
    }
  }
  const seq = nextSequence(userId, date);
  return `${key}:${seq}`;
}
function getMemorySpace(channel, project = "CognitiveMemory") {
  return channelTypeToSpace(channel.channelType, project, {
    teamSlug: channel.teamSlug,
    groupId: channel.groupId
  });
}
function channelTypeToSpace(channelType, project, opts) {
  switch (channelType) {
    case "team_channel":
      return `${project}/work/${opts?.teamSlug ?? "default"}`;
    case "group_dm":
      return `${project}/groups/${opts?.groupId ?? "default"}`;
    case "personal_dm":
    default:
      return `${project}/personal`;
  }
}
function inferChannelType(_platform, isGroup, isWorkspace) {
  if (isWorkspace) return "team_channel";
  if (isGroup) return "group_dm";
  return "personal_dm";
}
function buildChannelMetadata(channel) {
  return {
    channel: {
      platform: channel.platform,
      platform_user_id: channel.platformUserId,
      thread_id: channel.threadId,
      device: channel.device,
      timestamp: (/* @__PURE__ */ new Date()).toISOString()
    }
  };
}

// src/hooks.ts
function createHookState() {
  return {
    sessionId: null,
    lastUserMessage: null,
    lastAssistantResponse: null,
    messageCount: 0,
    recalledMemories: [],
    identityStoredFor: /* @__PURE__ */ new Set(),
    prefetchedRecall: null
  };
}
async function prefetchMemories(client2, config2, query) {
  const memories = await client2.memoryRetrieve({ query, limit: config2.topK });
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config2.searchThreshold
  );
  return { contextInjection: formatRecalledMemories(relevant), memories: relevant };
}
function formatRecalledMemories(memories) {
  if (memories.length === 0) return "";
  const cognitiveMemories = [];
  const projectMemories = [];
  for (const mem of memories) {
    const space = mem.space ?? "";
    const segments = space.split("/").filter(Boolean);
    const isProject = segments.length >= 2 && !["personal", "users", "session", "work"].includes(segments[segments.length - 1]);
    if (isProject) {
      projectMemories.push(mem);
    } else {
      cognitiveMemories.push(mem);
    }
  }
  const sections = [];
  if (cognitiveMemories.length > 0) {
    const lines = [
      "<kumiho_memory>",
      "Relevant long-term memories for this conversation:",
      ""
    ];
    for (const mem of cognitiveMemories) {
      lines.push(`- [${mem.type}] ${mem.title}: ${mem.summary}`);
      if (mem.topics?.length) lines.push(`  Topics: ${mem.topics.join(", ")}`);
      lines.push(`  Kref: ${mem.kref}`);
    }
    lines.push("", "</kumiho_memory>");
    sections.push(lines.join("\n"));
  }
  if (projectMemories.length > 0) {
    const lines = [
      "<kumiho_project>",
      "Creative project items relevant to this conversation:",
      "Use `sourceMemoryKref` from these krefs when capturing new outputs derived from this work.",
      ""
    ];
    for (const mem of projectMemories) {
      lines.push(`- [${mem.type}] ${mem.title}: ${mem.summary}`);
      if (mem.topics?.length) lines.push(`  Tags: ${mem.topics.join(", ")}`);
      lines.push(`  Kref: ${mem.kref}`);
    }
    lines.push("", "</kumiho_project>");
    sections.push(lines.join("\n"));
  }
  return sections.join("\n\n");
}
async function autoRecall(client2, config2, state, userMessage, channel) {
  if (!state.sessionId) {
    state.sessionId = await generateSessionId(config2.userId);
  }
  state.lastUserMessage = userMessage;
  state.messageCount++;
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  const spacePaths = channel ? [getMemorySpace(channel, config2.project)] : void 0;
  const [, memories] = await Promise.all([
    client2.chatAdd(state.sessionId, "user", userMessage, {
      ...channelMeta,
      timestamp: (/* @__PURE__ */ new Date()).toISOString()
    }),
    client2.memoryRetrieve({
      query: userMessage,
      limit: config2.topK,
      spacePaths
    })
  ]);
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config2.searchThreshold
  );
  state.recalledMemories = relevant;
  return {
    contextInjection: formatRecalledMemories(relevant),
    memories: relevant,
    sessionId: state.sessionId
  };
}
async function autoCapture(client2, config2, state, redactor2, artifacts2, assistantResponse, channel) {
  if (!state.sessionId || !state.lastUserMessage) {
    return { captured: false, consolidated: false, messageCount: 0 };
  }
  state.lastAssistantResponse = assistantResponse;
  state.messageCount++;
  await client2.chatAdd(state.sessionId, "assistant", assistantResponse, {
    timestamp: (/* @__PURE__ */ new Date()).toISOString()
  });
  let consolidated = false;
  if (state.messageCount >= config2.consolidationThreshold) {
    consolidated = await consolidateSession(
      client2,
      config2,
      state,
      redactor2,
      artifacts2,
      channel
    );
  }
  return {
    captured: true,
    consolidated,
    messageCount: state.messageCount
  };
}
async function consolidateSession(client2, config2, state, redactor2, artifacts2, channel) {
  if (!state.sessionId) return false;
  try {
    const working = await client2.chatGet(state.sessionId, 500);
    if (working.messages.length === 0) return false;
    const artifact = await artifacts2.saveConversation(
      config2.project,
      state.sessionId,
      working.messages
    );
    const conversationText = working.messages.map((m) => `${m.role}: ${m.content}`).join("\n");
    let summaryText = `Consolidated ${working.message_count} messages from session ${state.sessionId}`;
    if (config2.piiRedaction) {
      const redacted = redactor2.redact(summaryText);
      summaryText = redactor2.anonymizeSummary(redacted.text);
    }
    const spaceHint = channel ? getMemorySpace(channel, config2.project).replace(`${config2.project}/`, "") : "personal";
    await client2.memoryStore({
      type: "summary",
      title: `Session consolidation: ${state.sessionId}`,
      summary: summaryText,
      userText: config2.privacy.uploadSummariesOnly ? void 0 : conversationText,
      artifactLocation: artifact.location,
      spaceHint,
      tags: ["consolidated", "summary"],
      metadata: {
        session_id: state.sessionId,
        message_count: working.message_count,
        channels_used: channel ? [channel.platform] : [],
        artifact_hash: artifact.hash
      }
    });
    await client2.chatClear(state.sessionId);
    state.sessionId = await generateSessionId(config2.userId, "personal", true);
    state.messageCount = 0;
    redactor2.reset();
    return true;
  } catch (err) {
    return false;
  }
}

// src/creative.ts
function coerceKind(raw) {
  const KINDS = [
    "document",
    "code",
    "design",
    "plan",
    "analysis",
    "other"
  ];
  if (typeof raw === "string" && KINDS.includes(raw)) {
    return raw;
  }
  return "other";
}
async function creativeCaptureHandler(ctx, params) {
  const title = (params.title ?? "").trim();
  const content = (params.content ?? "").trim();
  const project = (params.project ?? "").trim();
  if (!title) return "creative_capture: `title` is required.";
  if (!content) return "creative_capture: `content` is required.";
  if (!project) return "creative_capture: `project` (space slug) is required.";
  const bffUrl = ctx.config.bffEndpoint;
  if (!bffUrl) {
    return "Creative capture is not configured: `bffEndpoint` is missing.\nSet `bffEndpoint` in the Kumiho plugin config to point at your kumiho-FastAPI instance.";
  }
  const captureParams = {
    title,
    content,
    kind: coerceKind(params.kind),
    project,
    tags: Array.isArray(params.tags) ? params.tags : [],
    sourceMemoryKref: params.sourceMemoryKref,
    metadata: params.metadata ?? {}
  };
  try {
    const result = await ctx.client.creativeEnqueue(
      captureParams,
      bffUrl,
      ctx.config.apiKey
    );
    ctx.logger.info(
      `Creative capture queued: "${title}" \u2192 ${project} (job: ${result.jobId})`
    );
    return [
      `Creative capture queued.`,
      ``,
      `Job ID : ${result.jobId}`,
      `Title  : "${title}"`,
      `Kind   : ${captureParams.kind}`,
      `Project: ${project}`,
      params.sourceMemoryKref ? `Linked to memory kref: ${params.sourceMemoryKref}` : "",
      ``,
      `The content is being processed and linked to your memory graph in the background.`,
      `Use the job ID to poll status: GET /api/v1/apps/creative/jobs/${result.jobId}`
    ].filter((l) => l !== void 0).join("\n");
  } catch (err) {
    const msg = err.message;
    ctx.logger.error(`creative_capture failed: ${msg}`);
    return `Creative capture failed: ${msg}`;
  }
}
async function projectRecallHandler(ctx, params) {
  const project = (params.project ?? "").trim();
  if (!project) return "project_recall: `project` (space slug) is required.";
  const kind = coerceKind(params.kind ?? "other");
  const query = params.query?.trim() || [project, params.kind ? kind : ""].filter(Boolean).join(" ");
  const limit = params.limit ?? ctx.config.topK;
  const spacePaths = [`${ctx.config.project}/${project}`];
  try {
    const results = await ctx.client.memoryRetrieve({
      query,
      limit,
      spacePaths
    });
    if (results.length === 0) {
      return `No creative items found in project "${project}". Capture something first using creative_capture.`;
    }
    const lines = [`## Project: ${project}`, ""];
    for (const item of results) {
      lines.push(`### ${item.title || "(untitled)"}`);
      if (item.summary) lines.push(item.summary);
      if (item.topics?.length) lines.push(`Tags: ${item.topics.join(", ")}`);
      if (item.score != null) lines.push(`Relevance: ${item.score.toFixed(2)}`);
      lines.push(`Kref: ${item.kref}`);
      lines.push("");
    }
    return lines.join("\n");
  } catch (err) {
    const msg = err.message;
    ctx.logger.error(`project_recall failed: ${msg}`);
    return `Project recall failed: ${msg}`;
  }
}

// src/tools.ts
var TOOL_SCHEMAS = {
  memory_search: {
    description: "Search Kumiho long-term memory using a natural language query. Returns relevant memories across all channels the user has interacted on.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Natural language search query"
        },
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope to search (default: all)"
        },
        limit: {
          type: "number",
          description: "Max results to return (default: 5)"
        },
        spacePath: {
          type: "string",
          description: "Restrict search to a specific space path (e.g. CognitiveMemory/work/team-alpha)"
        }
      },
      required: ["query"]
    }
  },
  memory_store: {
    description: "Explicitly store a fact, decision, or summary in Kumiho long-term memory. Use this when the user asks you to remember something specific.",
    parameters: {
      type: "object",
      properties: {
        content: {
          type: "string",
          description: "The information to remember"
        },
        type: {
          type: "string",
          enum: ["fact", "decision", "summary"],
          description: "Memory type (default: fact)"
        },
        title: {
          type: "string",
          description: "Short title for the memory"
        },
        topics: {
          type: "array",
          items: { type: "string" },
          description: "Topic tags for categorization"
        },
        spaceHint: {
          type: "string",
          description: "Space path hint (e.g. personal/preferences)"
        }
      },
      required: ["content"]
    }
  },
  memory_get: {
    description: "Retrieve a specific memory by its kref identifier.",
    parameters: {
      type: "object",
      properties: {
        kref: {
          type: "string",
          description: "The kref identifier of the memory to retrieve"
        }
      },
      required: ["kref"]
    }
  },
  memory_list: {
    description: "List stored memories. Returns recent memories from Kumiho long-term storage.",
    parameters: {
      type: "object",
      properties: {
        scope: {
          type: "string",
          enum: ["session", "long-term", "all"],
          description: "Memory scope (default: all)"
        },
        limit: {
          type: "number",
          description: "Max memories to list (default: 10)"
        },
        spacePath: {
          type: "string",
          description: "Filter by space path"
        }
      }
    }
  },
  memory_forget: {
    description: "Delete or deprecate a memory. Can target by kref or by search query.",
    parameters: {
      type: "object",
      properties: {
        kref: {
          type: "string",
          description: "Kref of the memory to forget"
        },
        query: {
          type: "string",
          description: "Search query to find memories to forget"
        }
      }
    }
  },
  memory_consolidate: {
    description: "Trigger consolidation of the current session's working memory into long-term storage. Summarizes the conversation, redacts PII, and stores a structured summary in Kumiho Cloud.",
    parameters: {
      type: "object",
      properties: {
        sessionId: {
          type: "string",
          description: "Session ID to consolidate (default: current session)"
        }
      }
    }
  },
  memory_dream: {
    description: "Trigger a Dream State consolidation cycle. Reviews recent memories, deprecates stale ones, adds tags, and creates relationship edges.",
    parameters: {
      type: "object",
      properties: {}
    }
  },
  creative_capture: {
    description: "Capture a creative output (document, code, plan, etc.) into the Kumiho graph. The artifact is stored asynchronously \u2014 this tool returns immediately with a job ID. Pass sourceMemoryKref with the kref of the recalled memory that produced this output so the graph links the creative artifact back to its cognitive origin (DERIVED_FROM edge).",
    parameters: {
      type: "object",
      properties: {
        title: {
          type: "string",
          description: "Title or name of the creative artifact"
        },
        content: {
          type: "string",
          description: "The content body to capture (text, code, markdown, etc.)"
        },
        kind: {
          type: "string",
          enum: ["document", "code", "design", "plan", "analysis", "other"],
          description: "Kind of creative output (default: document)"
        },
        project: {
          type: "string",
          description: "Project space slug (e.g. 'blog-post-jan25', 'api-refactor'). Groups outputs from the same project together in the graph."
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "Optional topic tags for discovery"
        },
        sourceMemoryKref: {
          type: "string",
          description: "Kref of the recalled memory that inspired or drove this output. Creates a DERIVED_FROM edge so the artifact can be traced to its cognitive origin."
        },
        metadata: {
          type: "object",
          description: "Extra metadata key-value pairs stored on the revision"
        }
      },
      required: ["title", "content", "project"]
    }
  },
  project_recall: {
    description: "Search and list creative outputs stored in a project space. Use this before continuing work on a project to recall previous outputs, decisions, and artifacts. Returns krefs you can pass as sourceMemoryKref when capturing new outputs derived from existing work.",
    parameters: {
      type: "object",
      properties: {
        project: {
          type: "string",
          description: "Project space slug to search within"
        },
        query: {
          type: "string",
          description: "Natural language search query (optional \u2014 omit to list all)"
        },
        kind: {
          type: "string",
          enum: ["document", "code", "design", "plan", "analysis", "other"],
          description: "Filter by creative kind"
        },
        limit: {
          type: "number",
          description: "Max results to return (default: topK from config)"
        }
      },
      required: ["project"]
    }
  }
};
function formatMemoryEntry(entry) {
  const parts = [`[${entry.type}] ${entry.title || "(untitled)"}`];
  if (entry.summary) parts.push(entry.summary);
  if (entry.topics?.length) parts.push(`Topics: ${entry.topics.join(", ")}`);
  if (entry.kref) parts.push(`Kref: ${entry.kref}`);
  if (entry.score != null) parts.push(`Score: ${entry.score.toFixed(2)}`);
  return parts.join("\n");
}
async function handleMemorySearch(ctx, params) {
  const limit = params.limit ?? ctx.config.topK;
  const spacePaths = params.spacePath ? [params.spacePath] : void 0;
  const results = await ctx.client.memoryRetrieve({
    query: params.query,
    limit,
    spacePaths
  });
  if ((params.scope === "session" || params.scope === "all") && ctx.currentSessionId) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const sessionSection = working.messages.filter(
        (m) => m.content.toLowerCase().includes(params.query.toLowerCase())
      ).map((m) => `[session] ${m.role}: ${m.content}`).join("\n");
      if (sessionSection) {
        const longTermSection = results.length > 0 ? results.map(formatMemoryEntry).join("\n\n") : "No long-term memories found.";
        return `## Long-term Memories

${longTermSection}

## Session Memories

${sessionSection}`;
      }
    }
  }
  if (results.length === 0) {
    return "No memories found matching your query.";
  }
  return results.map(formatMemoryEntry).join("\n\n");
}
async function handleMemoryStore(ctx, params) {
  const type = params.type ?? "fact";
  const title = params.title ?? params.content.slice(0, 60) + (params.content.length > 60 ? "..." : "");
  const result = await ctx.client.memoryStore({
    type,
    title,
    summary: params.content,
    topics: params.topics,
    spaceHint: params.spaceHint,
    tags: [type, "user-stored"]
  });
  ctx.logger.info(`Stored memory: ${result.item_kref}`);
  return `Memory stored successfully.
Kref: ${result.item_kref}
Space: ${result.space_path}`;
}
async function handleMemoryGet(ctx, params) {
  const entry = await ctx.client.getRevision(params.kref);
  return formatMemoryEntry(entry);
}
async function handleMemoryList(ctx, params) {
  const limit = params.limit ?? 10;
  const sections = [];
  if (params.scope !== "session") {
    const results = await ctx.client.memoryRetrieve({
      query: "*",
      limit,
      spacePaths: params.spacePath ? [params.spacePath] : void 0
    });
    if (results.length > 0) {
      sections.push(
        "## Long-term Memories\n\n" + results.map(formatMemoryEntry).join("\n\n")
      );
    } else {
      sections.push("## Long-term Memories\n\nNo memories stored yet.");
    }
  }
  if ((params.scope === "session" || params.scope === "all" || !params.scope) && ctx.currentSessionId) {
    const working = await ctx.client.chatGet(ctx.currentSessionId, limit);
    if (working.messages.length > 0) {
      const msgs = working.messages.map((m) => `- **${m.role}** (${m.timestamp}): ${m.content.slice(0, 100)}`).join("\n");
      sections.push(
        `## Session Memories (${working.message_count} messages, TTL: ${working.ttl_remaining}s)

${msgs}`
      );
    }
  }
  return sections.join("\n\n") || "No memories found.";
}
async function handleMemoryForget(ctx, params) {
  if (params.kref) {
    await ctx.client.memoryDeprecate(params.kref);
    return `Memory deprecated: ${params.kref}`;
  }
  if (params.query) {
    const results = await ctx.client.memoryRetrieve({
      query: params.query,
      limit: 5
    });
    if (results.length === 0) {
      return "No memories found matching your query.";
    }
    const deprecated = [];
    for (const entry of results) {
      await ctx.client.memoryDeprecate(entry.kref);
      deprecated.push(entry.kref);
    }
    return `Deprecated ${deprecated.length} memories:
${deprecated.join("\n")}`;
  }
  return "Please provide either a kref or a query to identify memories to forget.";
}
async function handleMemoryConsolidate(ctx, params) {
  const sessionId = params.sessionId ?? ctx.currentSessionId;
  if (!sessionId) {
    return "No active session to consolidate.";
  }
  const working = await ctx.client.chatGet(sessionId, 200);
  if (working.messages.length === 0) {
    return "Session is empty, nothing to consolidate.";
  }
  const conversationText = working.messages.map((m) => `${m.role}: ${m.content}`).join("\n");
  const result = await ctx.client.memoryStore({
    type: "summary",
    title: `Session consolidation: ${sessionId}`,
    summary: `Consolidated ${working.message_count} messages from session ${sessionId}`,
    userText: conversationText,
    tags: ["consolidated", "summary"],
    metadata: {
      session_id: sessionId,
      message_count: working.message_count
    }
  });
  await ctx.client.chatClear(sessionId);
  ctx.logger.info(`Consolidated session ${sessionId}: ${result.item_kref}`);
  return `Session consolidated successfully.
Messages: ${working.message_count}
Kref: ${result.item_kref}
Space: ${result.space_path}`;
}
async function handleMemoryDream(ctx) {
  const dm = ctx.config.dreamStateModel;
  const modelConfig = dm?.provider || dm?.model || dm?.apiKey ? dm : void 0;
  const stats = await ctx.client.triggerDreamState(modelConfig);
  const lines = [
    "Dream State consolidation complete.",
    "",
    `Events processed: ${stats.events_processed}`,
    `Revisions assessed: ${stats.revisions_assessed}`,
    `Deprecated: ${stats.deprecated}`,
    `Tags added: ${stats.tags_added}`,
    `Metadata updated: ${stats.metadata_updated}`,
    `Edges created: ${stats.edges_created}`,
    `Duration: ${stats.duration_ms}ms`
  ];
  if (stats.errors.length > 0) {
    lines.push("", `Errors: ${stats.errors.length}`, ...stats.errors.map((e) => `  - ${e}`));
  }
  return lines.join("\n");
}
var TOOL_HANDLERS = {
  memory_search: (ctx, p) => handleMemorySearch(ctx, p),
  memory_store: (ctx, p) => handleMemoryStore(ctx, p),
  memory_get: (ctx, p) => handleMemoryGet(ctx, p),
  memory_list: (ctx, p) => handleMemoryList(ctx, p),
  memory_forget: (ctx, p) => handleMemoryForget(ctx, p),
  memory_consolidate: (ctx, p) => handleMemoryConsolidate(ctx, p),
  memory_dream: (ctx) => handleMemoryDream(ctx),
  creative_capture: (ctx, p) => creativeCaptureHandler(ctx, p),
  project_recall: (ctx, p) => projectRecallHandler(ctx, p)
};

// src/identity.ts
async function ensureUserIdentity(client2, config2, senderId, identity) {
  if (!identity.displayName && !identity.platform) return;
  try {
    const existing = await client2.memoryRetrieve({
      query: `agent.instructions identity ${senderId}`,
      limit: 1,
      spacePaths: [`${config2.project}/users`]
    });
    const hasProfile = existing.some(
      (m) => m.metadata?.userId === senderId
    );
    if (hasProfile) return;
    const name = identity.displayName ?? senderId;
    const platform2 = identity.platform ?? "OpenClaw";
    const date = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
    const lines = [
      `## User Identity`,
      ``,
      `- **Name**: ${name}`,
      `- **Platform**: ${platform2}`,
      `- **User ID**: ${senderId}`
    ];
    if (identity.timezone) lines.push(`- **Timezone**: ${identity.timezone}`);
    if (identity.locale) lines.push(`- **Locale**: ${identity.locale}`);
    lines.push(``, `First seen via ${platform2} on ${date}.`);
    lines.push(
      ``,
      `## Dual-Memory Agent Instructions`,
      ``,
      `This user's agent operates with two parallel memory layers:`,
      ``,
      `**Cognitive Memory** (automatic):`,
      `- Long-term memory is recalled before each turn and injected as \`<kumiho_memory>\` context`,
      `- After each turn, responses are buffered in Redis working memory`,
      `- Sessions auto-consolidate when they reach the threshold`,
      ``,
      `**Creative Memory** (explicit tools):`,
      `- Use \`creative_capture\` to permanently store any creative output (document, code, plan, design, analysis)`,
      `- Always pass \`sourceMemoryKref\` \u2014 the kref from the recalled memory that inspired this output`,
      `  This creates a DERIVED_FROM edge in the graph linking the artifact to its cognitive origin`,
      `- Use \`project_recall\` before starting or resuming project work to load past outputs`,
      `  The returned krefs can be passed as \`sourceMemoryKref\` for new captures`,
      ``,
      `**Creative capture workflow**:`,
      `1. Call \`project_recall\` with the project slug to load existing context and krefs`,
      `2. Do the creative work`,
      `3. Call \`creative_capture\` with the output, passing the relevant kref as \`sourceMemoryKref\``,
      `4. The graph pipeline runs in the background \u2014 the agent turn is never blocked`
    );
    const userSlug = senderId.replace(/[^a-zA-Z0-9]/g, "-").toLowerCase().slice(0, 40);
    await client2.memoryStore({
      spaceHint: `users/${userSlug}`,
      userText: `New user connected via ${platform2}: ${name} (ID: ${senderId})`,
      assistantText: lines.join("\n"),
      type: "fact",
      title: `Identity Profile: ${name}`,
      summary: `${name} uses Kumiho via ${platform2} (ID: ${senderId}${identity.timezone ? `, tz: ${identity.timezone}` : ""})`,
      topics: ["identity", "user-profile", "agent.instructions"],
      tags: ["agent.instructions", "user-profile", "latest"],
      metadata: {
        userId: senderId,
        displayName: identity.displayName ?? "",
        platform: identity.platform ?? "openclaw",
        timezone: identity.timezone ?? "",
        locale: identity.locale ?? "",
        firstSeen: date
      }
    });
  } catch {
  }
}

// src/index.ts
function loadPreferences() {
  try {
    const path = join3(homedir3(), ".kumiho", "preferences.json");
    if (existsSync2(path)) {
      return JSON.parse(readFileSync(path, "utf8"));
    }
  } catch {
  }
  return {};
}
function resolveConfig(raw) {
  const mode = raw.mode ?? "local";
  const apiKey = raw.apiKey || process.env.KUMIHO_API_TOKEN || process.env.KUMIHO_API_KEY || "";
  if (mode === "cloud" && !apiKey) {
    throw new Error(
      'Kumiho API key is required for cloud mode. Set apiKey in plugin config or KUMIHO_API_TOKEN env var, or switch to mode: "local" to use the Python SDK directly.'
    );
  }
  const endpoint = raw.endpoint || process.env.KUMIHO_ENDPOINT || "https://api.kumiho.cloud";
  const bffEndpoint = raw.bffEndpoint || process.env.KUMIHO_BFF_ENDPOINT || (mode === "cloud" ? endpoint : "http://localhost:8000");
  const prefs = loadPreferences();
  return {
    mode,
    apiKey,
    endpoint,
    bffEndpoint,
    project: raw.project || "CognitiveMemory",
    userId: raw.userId || "default",
    autoCapture: raw.autoCapture ?? true,
    autoRecall: raw.autoRecall ?? true,
    localSummarization: raw.localSummarization ?? true,
    consolidationThreshold: raw.consolidationThreshold ?? 20,
    idleConsolidationTimeout: raw.idleConsolidationTimeout ?? 300,
    sessionTtl: raw.sessionTtl ?? 3600,
    topK: raw.topK ?? 5,
    searchThreshold: raw.searchThreshold ?? 0.3,
    artifactDir: raw.artifactDir || process.env.KUMIHO_MEMORY_ARTIFACT_ROOT || join3(homedir3(), ".kumiho", "artifacts"),
    piiRedaction: raw.piiRedaction ?? true,
    dreamStateSchedule: raw.dreamStateSchedule ?? prefs.dreamState?.schedule ?? "",
    dreamStateModel: raw.dreamStateModel ?? prefs.dreamState?.model ?? {},
    consolidationModel: raw.consolidationModel ?? prefs.consolidation?.model ?? {},
    llm: raw.llm ?? {},
    privacy: {
      uploadSummariesOnly: raw.privacy?.uploadSummariesOnly ?? true,
      localArtifacts: raw.privacy?.localArtifacts ?? true,
      storeTranscriptions: raw.privacy?.storeTranscriptions ?? true
    },
    local: {
      pythonPath: raw.local?.pythonPath ?? "python",
      command: raw.local?.command ?? "kumiho-mcp",
      timeout: raw.local?.timeout ?? 3e4,
      args: raw.local?.args,
      env: raw.local?.env,
      cwd: raw.local?.cwd
    }
  };
}
var transport = null;
var client = null;
var config = null;
var redactor = null;
var artifacts = null;
var hookState = null;
var idleTimer = null;
function clearIdleTimer() {
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}
var dreamStateTimer = null;
function msUntilNextCron(cron) {
  if (!cron || cron === "off") return -1;
  const now = /* @__PURE__ */ new Date();
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return -1;
  const [, hourPart, , , weekdayPart] = parts;
  const everyHour = hourPart.match(/^\*\/(\d+)$/);
  if (everyHour) {
    const interval = parseInt(everyHour[1], 10);
    if (!interval) return -1;
    const intervalMs = interval * 60 * 60 * 1e3;
    const currentHour = now.getHours();
    const nextHour = Math.ceil((currentHour + 1) / interval) * interval;
    const next2 = new Date(now);
    next2.setMinutes(0, 0, 0);
    if (nextHour >= 24) {
      next2.setDate(next2.getDate() + 1);
      next2.setHours(0);
    } else {
      next2.setHours(nextHour);
    }
    const ms = next2.getTime() - now.getTime();
    return ms > 0 ? ms : intervalMs;
  }
  const hour = parseInt(hourPart, 10);
  if (isNaN(hour)) return -1;
  if (weekdayPart !== "*") {
    const targetDay = parseInt(weekdayPart, 10);
    if (isNaN(targetDay)) return -1;
    const next2 = new Date(now);
    next2.setHours(hour, 0, 0, 0);
    const daysUntil = (targetDay - now.getDay() + 7) % 7 || 7;
    next2.setDate(next2.getDate() + daysUntil);
    const ms = next2.getTime() - now.getTime();
    return ms > 0 ? ms : 7 * 24 * 60 * 60 * 1e3;
  }
  const next = new Date(now);
  next.setHours(hour, 0, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}
function execDreamStateCli(cfg, logger) {
  return new Promise((resolve) => {
    const pythonPath = cfg.local.pythonPath;
    const args = ["-m", "kumiho_memory", "dream", "--project", cfg.project];
    logger.info(`Kumiho Dream State fallback: ${pythonPath} ${args.join(" ")}`);
    const env = { ...process.env };
    const dm = cfg.dreamStateModel;
    if (dm?.provider) env.KUMIHO_LLM_PROVIDER = dm.provider;
    if (dm?.model) env.KUMIHO_LLM_MODEL = dm.model;
    if (dm?.apiKey) env.KUMIHO_LLM_API_KEY = dm.apiKey;
    execFile(pythonPath, args, { timeout: 3e5, env }, (err, stdout, stderr) => {
      if (err) {
        logger.warn(`Dream State CLI failed: ${err.message}`);
        if (stderr) logger.warn(`  stderr: ${stderr.trim()}`);
        resolve(null);
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        logger.info(`Dream State CLI output: ${stdout.trim()}`);
        resolve(null);
      }
    });
  });
}
function scheduleDreamState(kumihoClient, cfg, logger) {
  const ms = msUntilNextCron(cfg.dreamStateSchedule);
  if (ms < 0) return;
  const nextRun = new Date(Date.now() + ms);
  logger.info(
    `Kumiho Dream State scheduled: ${cfg.dreamStateSchedule} \u2014 next run at ${nextRun.toLocaleString()}`
  );
  dreamStateTimer = setTimeout(async () => {
    dreamStateTimer = null;
    try {
      const dm = cfg.dreamStateModel;
      const modelConfig = dm?.provider || dm?.model || dm?.apiKey ? dm : void 0;
      const stats = await kumihoClient.triggerDreamState(modelConfig);
      logger.info(
        `Kumiho Dream State complete \u2014 ${stats.events_processed} events, ${stats.edges_created} edges, ${stats.deprecated} deprecated`
      );
    } catch (err) {
      logger.warn(
        `Kumiho Dream State MCP call failed: ${err.message} \u2014 trying standalone CLI...`
      );
      const stats = await execDreamStateCli(cfg, logger);
      if (stats) {
        logger.info(
          `Kumiho Dream State (CLI) complete \u2014 ${stats.events_processed} events, ${stats.edges_created} edges, ${stats.deprecated} deprecated`
        );
      }
    }
    scheduleDreamState(kumihoClient, cfg, logger);
  }, ms);
}
function ensureInitialized() {
  if (!client || !config || !redactor || !artifacts || !hookState) {
    throw new Error("Kumiho plugin not initialized. Check your configuration.");
  }
  return { client, config, redactor, artifacts, hookState };
}
var index_default = {
  id: "openclaw-kumiho",
  name: "Kumiho Cognitive Memory",
  register(api) {
    const rawConfig = api.config?.plugins?.entries?.["openclaw-kumiho"]?.config ?? {};
    try {
      config = resolveConfig(rawConfig);
    } catch (err) {
      api.logger.error(`Kumiho: ${err.message}`);
      return;
    }
    transport = createTransport(config, api.logger);
    client = new KumihoClient(transport, config.project);
    redactor = new PIIRedactor();
    artifacts = new ArtifactManager(config.artifactDir);
    hookState = createHookState();
    api.logger.info(
      `Kumiho memory initialized (mode: ${config.mode}, project: ${config.project}, autoRecall: ${config.autoRecall}, autoCapture: ${config.autoCapture})`
    );
    const toolCtx = {
      client,
      config,
      get currentSessionId() {
        return hookState?.sessionId ?? null;
      },
      logger: api.logger
    };
    const customToolNames = new Set(Object.keys(TOOL_HANDLERS));
    api.registerGatewayMethod("kumiho.tools.list", ({ respond }) => {
      const customTools = Object.entries(TOOL_SCHEMAS).map(([name, schema]) => ({
        name,
        ...schema
      }));
      const mcpTools = (client?.getDiscoveredTools() ?? []).filter((t) => !customToolNames.has(t.name)).map((t) => ({
        name: t.name,
        description: t.description,
        parameters: t.inputSchema
      }));
      respond(true, { tools: [...customTools, ...mcpTools] });
    });
    for (const [name, handler] of Object.entries(TOOL_HANDLERS)) {
      api.registerGatewayMethod(
        `kumiho.tool.${name}`,
        async ({ respond, params }) => {
          try {
            const result = await handler(toolCtx, params ?? {});
            respond(true, { result });
          } catch (err) {
            const msg = err instanceof KumihoApiError ? `Kumiho API error (${err.code}): ${err.message}` : err instanceof McpBridgeError ? `Kumiho MCP error (${err.code}): ${err.message}` : `Tool error: ${err.message}`;
            api.logger.error(msg);
            respond(false, { error: msg });
          }
        }
      );
    }
    api.registerCli(
      ({ program }) => {
        program.command("search").description("Search Kumiho long-term memory").argument("<query>", "Natural language search query").option("--scope <scope>", "Scope: session, long-term, all").option("--limit <n>", "Max results").action(async (...args) => {
          const query = args[0];
          const opts = args[1];
          const state = ensureInitialized();
          const result = await TOOL_HANDLERS.memory_search(
            {
              client: state.client,
              config: state.config,
              currentSessionId: state.hookState.sessionId,
              logger: api.logger
            },
            {
              query,
              scope: opts.scope,
              limit: opts.limit ? parseInt(opts.limit) : void 0
            }
          );
          console.log(result);
        });
        program.command("stats").description("Show Kumiho memory statistics").action(async () => {
          const state = ensureInitialized();
          const healthy = await state.client.ping();
          const sessionInfo = state.hookState.sessionId ? await state.client.chatGet(state.hookState.sessionId, 1).catch(() => null) : null;
          console.log(`Kumiho Memory Status`);
          console.log(`  Mode: ${state.config.mode}`);
          console.log(`  Backend: ${healthy ? "connected" : "unreachable"}`);
          console.log(`  Project: ${state.config.project}`);
          console.log(`  User: ${state.config.userId}`);
          console.log(`  Session: ${state.hookState.sessionId ?? "none"}`);
          if (sessionInfo) {
            console.log(`  Messages: ${sessionInfo.message_count}`);
            console.log(`  TTL: ${sessionInfo.ttl_remaining}s`);
          }
          console.log(`  Auto-Recall: ${state.config.autoRecall}`);
          console.log(`  Auto-Capture: ${state.config.autoCapture}`);
          console.log(`  PII Redaction: ${state.config.piiRedaction}`);
          console.log(`  Artifact Dir: ${state.config.artifactDir}`);
        });
        program.command("consolidate").description("Consolidate current session into long-term memory").action(async () => {
          const state = ensureInitialized();
          const result = await TOOL_HANDLERS.memory_consolidate(
            {
              client: state.client,
              config: state.config,
              currentSessionId: state.hookState.sessionId,
              logger: api.logger
            },
            {}
          );
          console.log(result);
        });
        program.command("dream").description("Trigger Dream State memory maintenance").action(async () => {
          const state = ensureInitialized();
          const result = await TOOL_HANDLERS.memory_dream(
            {
              client: state.client,
              config: state.config,
              currentSessionId: state.hookState.sessionId,
              logger: api.logger
            },
            {}
          );
          console.log(result);
        });
        program.command("capture").description("Capture last response as a creative output").argument("<title>", "Title for the creative artifact").argument("<project>", "Project space slug").option("--kind <kind>", "Creative kind (document|code|design|plan|analysis|other)").action(async (...args) => {
          const title = args[0];
          const project = args[1];
          const opts = args[2];
          const state = ensureInitialized();
          const content = state.hookState.lastAssistantResponse?.trim() || `Content captured via CLI on ${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}`;
          const result = await TOOL_HANDLERS.creative_capture(
            {
              client: state.client,
              config: state.config,
              currentSessionId: state.hookState.sessionId,
              logger: api.logger
            },
            { title, content, project, kind: opts.kind ?? "document" }
          );
          console.log(result);
        });
        program.command("project").description("List creative outputs for a project").argument("<project>", "Project space slug").option("--query <query>", "Search query").option("--kind <kind>", "Filter by kind").action(async (...args) => {
          const project = args[0];
          const opts = args[1];
          const state = ensureInitialized();
          const result = await TOOL_HANDLERS.project_recall(
            {
              client: state.client,
              config: state.config,
              currentSessionId: state.hookState.sessionId,
              logger: api.logger
            },
            { project, query: opts.query, kind: opts.kind }
          );
          console.log(result);
        });
      },
      { commands: ["search", "stats", "consolidate", "dream", "capture", "project"] }
    );
    api.registerCommand({
      name: "memory",
      description: "Kumiho memory commands: search, stats, consolidate",
      requireAuth: true,
      acceptsArgs: true,
      handler: (ctx) => {
        const args = ctx.args?.trim() ?? "";
        if (args === "stats" || args === "") {
          const state = ensureInitialized();
          return {
            text: `Kumiho Memory (${state.config.mode} mode)
Project: ${state.config.project}
Session: ${state.hookState.sessionId ?? "none"}
Messages: ${state.hookState.messageCount}
Auto-Recall: ${state.config.autoRecall}
Auto-Capture: ${state.config.autoCapture}`
          };
        }
        return {
          text: `Unknown memory command: ${args}. Try: stats, search <query>, consolidate`
        };
      }
    });
    api.registerCommand({
      name: "capture",
      description: "Capture a creative output into the Kumiho graph. Usage: /capture <title> | <project> [| <kind>]\nExample: /capture Blog Draft | my-blog | document\nKinds: document, code, design, plan, analysis, other",
      requireAuth: true,
      acceptsArgs: true,
      handler: (ctx) => {
        const args = ctx.args?.trim() ?? "";
        if (!args) {
          return {
            text: "Usage: /capture <title> | <project> [| <kind>]\nExample: /capture Blog Draft | my-blog | document\nAvailable kinds: document, code, design, plan, analysis, other"
          };
        }
        const parts = args.split("|").map((s) => s.trim());
        const title = parts[0] ?? "";
        const project = parts[1] ?? "";
        const kind = parts[2] ?? "document";
        if (!title || !project) {
          return {
            text: "Both title and project are required.\nUsage: /capture <title> | <project> [| <kind>]"
          };
        }
        const state = ensureInitialized();
        const content = state.hookState.lastAssistantResponse?.trim() || `Content captured via /capture on ${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}`;
        void TOOL_HANDLERS.creative_capture(
          {
            client: state.client,
            config: state.config,
            currentSessionId: state.hookState.sessionId,
            logger: api.logger
          },
          { title, content, project, kind }
        ).then((result) => api.logger.info(`/capture: ${result}`)).catch(
          (err) => api.logger.error(`/capture failed: ${err.message}`)
        );
        return {
          text: `Capture queued: "${title}" \u2192 ${project} (${kind})
Processing in background. Use project_recall to see it once done.`
        };
      }
    });
    api.registerService({
      id: "kumiho-memory",
      async start() {
        if (!client || !config || !transport) return;
        if (transport.start) {
          api.logger.info(
            `Starting kumiho-mcp subprocess (${config.local.command})...`
          );
          try {
            await transport.start();
            api.logger.info("kumiho-mcp subprocess started and MCP handshake complete");
          } catch (err) {
            api.logger.error(
              `Failed to start kumiho-mcp: ${err.message}. Run 'npx kumiho-setup' to install the Python backend, or set local.pythonPath in your openclaw.json config. Manual install: pip install "kumiho[mcp]" "kumiho-memory[all]"`
            );
            return;
          }
        }
        const discovered = client.getDiscoveredTools();
        let passthroughCount = 0;
        for (const tool of discovered) {
          if (customToolNames.has(tool.name)) continue;
          const toolName = tool.name;
          api.registerGatewayMethod(
            `kumiho.tool.${toolName}`,
            async ({ respond, params }) => {
              try {
                const result = await client.callTool(toolName, params ?? {});
                respond(true, { result });
              } catch (err) {
                const msg = err instanceof McpBridgeError ? `MCP error (${err.code}): ${err.message}` : `Tool error: ${err.message}`;
                api.logger.error(msg);
                respond(false, { error: msg });
              }
            }
          );
          passthroughCount++;
        }
        if (passthroughCount > 0) {
          api.logger.info(
            `Registered ${passthroughCount} MCP pass-through tools (${customToolNames.size} custom TypeScript handlers preserved)`
          );
        }
        hookState.sessionId = await generateSessionId(config.userId);
        const healthy = await client.ping();
        if (healthy) {
          api.logger.info(
            config.mode === "local" ? "kumiho-mcp local bridge connected" : "Kumiho Cloud connection verified"
          );
        } else {
          api.logger.warn(
            config.mode === "local" ? "kumiho-mcp process not responding" : "Kumiho Cloud unreachable - memories will be queued for later sync"
          );
        }
        scheduleDreamState(client, config, api.logger);
      },
      async stop() {
        if (dreamStateTimer) {
          clearTimeout(dreamStateTimer);
          dreamStateTimer = null;
        }
        if (transport?.close) {
          api.logger.info("Shutting down kumiho-mcp subprocess...");
          await transport.close();
        }
        api.logger.info("Kumiho memory service stopped");
      }
    });
    api.registerGatewayMethod(
      "kumiho.hooks.before_agent",
      async ({ respond, params }) => {
        if (!config?.autoRecall) {
          respond(true, { contextInjection: "" });
          return;
        }
        try {
          const state = ensureInitialized();
          const senderId = params?.senderId ?? state.config.userId;
          const userMessage = params?.message ?? state.hookState.lastUserMessage ?? "";
          if (senderId && senderId !== state.config.userId) {
            state.hookState.sessionId = null;
            state.config = { ...state.config, userId: senderId };
          }
          if (senderId && !state.hookState.identityStoredFor.has(senderId)) {
            state.hookState.identityStoredFor.add(senderId);
            void ensureUserIdentity(state.client, state.config, senderId, {
              displayName: params?.displayName,
              platform: params?.platform,
              timezone: params?.timezone,
              locale: params?.locale
            });
          }
          const recallResult = await autoRecall(
            state.client,
            state.config,
            state.hookState,
            userMessage
          );
          respond(true, {
            contextInjection: recallResult.contextInjection,
            memoriesFound: recallResult.memories.length
          });
        } catch (err) {
          api.logger.error(`Auto-recall failed: ${err.message}`);
          respond(true, { contextInjection: "" });
        }
      }
    );
    api.registerGatewayMethod(
      "kumiho.hooks.after_agent",
      async ({ respond, params }) => {
        if (!config?.autoCapture) {
          respond(true, { captured: false });
          return;
        }
        try {
          const state = ensureInitialized();
          const assistantResponse = params?.response ?? state.hookState.lastAssistantResponse ?? "";
          const captureResult = await autoCapture(
            state.client,
            state.config,
            state.hookState,
            state.redactor,
            state.artifacts,
            assistantResponse
          );
          respond(true, captureResult);
        } catch (err) {
          api.logger.error(`Auto-capture failed: ${err.message}`);
          respond(true, { captured: false });
        }
      }
    );
    api.on("before_prompt_build", async (ctx) => {
      clearIdleTimer();
      if (!config?.autoRecall) return;
      try {
        const state = ensureInitialized();
        const messages = ctx["messages"] ?? [];
        const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
        const raw = lastUserMsg?.content;
        const message = typeof raw === "string" ? raw : Array.isArray(raw) ? raw.filter((b) => b.type === "text").map((b) => b.text).join("\n") : state.hookState.lastUserMessage ?? "";
        const senderId = ctx["senderId"];
        if (senderId && senderId !== state.config.userId) {
          state.hookState.sessionId = null;
          state.config = { ...state.config, userId: senderId };
        }
        if (senderId && !state.hookState.identityStoredFor.has(senderId)) {
          state.hookState.identityStoredFor.add(senderId);
          void ensureUserIdentity(state.client, state.config, senderId, {
            displayName: ctx["displayName"],
            platform: ctx["platform"],
            timezone: ctx["timezone"],
            locale: ctx["locale"]
          });
        }
        let recallResult;
        if (state.hookState.prefetchedRecall) {
          recallResult = state.hookState.prefetchedRecall;
          state.hookState.prefetchedRecall = null;
          void prefetchMemories(state.client, state.config, message).then((r) => {
            state.hookState.prefetchedRecall = r;
          }).catch(() => {
          });
        } else {
          recallResult = await Promise.race([
            autoRecall(state.client, state.config, state.hookState, message),
            new Promise(
              (resolve) => setTimeout(() => resolve({ contextInjection: "" }), 1500)
            )
          ]);
        }
        if (recallResult.contextInjection) {
          const inject = ctx["inject"];
          if (inject) {
            inject(recallResult.contextInjection);
          } else if (api.runtime?.injectContext) {
            api.runtime.injectContext(recallResult.contextInjection);
          }
        }
      } catch (err) {
        api.logger.error(`Auto-recall hook failed: ${err.message}`);
      }
    });
    api.on("agent_end", async (ctx) => {
      if (!config?.autoCapture) return;
      try {
        const state = ensureInitialized();
        const messages = ctx["messages"] ?? [];
        const lastAssistantMsg = [...messages].reverse().find((m) => m.role === "assistant");
        const raw = lastAssistantMsg?.content;
        const response = typeof raw === "string" ? raw : Array.isArray(raw) ? raw.filter((b) => b.type === "text").map((b) => b.text).join("\n") : "";
        await autoCapture(
          state.client,
          state.config,
          state.hookState,
          state.redactor,
          state.artifacts,
          response
        );
        if (config?.autoRecall && state.hookState.lastUserMessage) {
          const queryForNext = state.hookState.lastUserMessage;
          void prefetchMemories(state.client, state.config, queryForNext).then((r) => {
            state.hookState.prefetchedRecall = r;
          }).catch(() => {
          });
        }
        const idleMs = (config?.idleConsolidationTimeout ?? 0) * 1e3;
        if (idleMs > 0 && state.hookState.messageCount > 0) {
          clearIdleTimer();
          idleTimer = setTimeout(() => {
            idleTimer = null;
            const s = hookState;
            if (!s || s.messageCount === 0) return;
            void consolidateSession(
              state.client,
              state.config,
              s,
              state.redactor,
              state.artifacts
            ).then((ok) => {
              if (ok) api.logger.info("Kumiho: idle consolidation complete");
            }).catch(() => {
            });
          }, idleMs);
        }
      } catch (err) {
        api.logger.error(`Auto-capture hook failed: ${err.message}`);
      }
    });
  }
};
function createKumihoMemory(rawConfig = {}) {
  const cfg = resolveConfig(rawConfig);
  const tp = createTransport(cfg);
  const kumihoClient = new KumihoClient(tp, cfg.project);
  const piiRedactor = new PIIRedactor();
  const artifactMgr = new ArtifactManager(cfg.artifactDir);
  const state = createHookState();
  return {
    client: kumihoClient,
    config: cfg,
    /**
     * Start the memory system.
     * In local mode this spawns the kumiho-mcp Python process.
     * In cloud mode this is a no-op (HTTPS is stateless).
     */
    async start() {
      await kumihoClient.start();
    },
    /**
     * Shut down the memory system.
     * In local mode this stops the kumiho-mcp Python process.
     */
    async close() {
      await kumihoClient.close();
    },
    /** Search long-term memory. */
    async recall(query, limit) {
      return kumihoClient.memoryRetrieve({
        query,
        limit: limit ?? cfg.topK
      });
    },
    /** Store a fact/decision/summary in long-term memory. */
    async store(content, opts) {
      let summary = content;
      if (cfg.piiRedaction) {
        const redacted = piiRedactor.redact(content);
        summary = piiRedactor.anonymizeSummary(redacted.text);
      }
      return kumihoClient.memoryStore({
        type: opts?.type ?? "fact",
        title: opts?.title ?? summary.slice(0, 60),
        summary,
        topics: opts?.topics,
        spaceHint: opts?.spaceHint
      });
    },
    /** Add a message to working memory. */
    async addMessage(sessionId, role, content) {
      return kumihoClient.chatAdd(sessionId, role, content);
    },
    /** Get messages from working memory. */
    async getMessages(sessionId, limit) {
      return kumihoClient.chatGet(sessionId, limit);
    },
    /** Generate a session ID. */
    async newSession(userId, context) {
      return generateSessionId(userId ?? cfg.userId, context);
    },
    /** Auto-recall hook. */
    async autoRecallHook(userMessage, channel) {
      return autoRecall(kumihoClient, cfg, state, userMessage, channel);
    },
    /** Auto-capture hook. */
    async autoCaptureHook(assistantResponse, channel) {
      return autoCapture(
        kumihoClient,
        cfg,
        state,
        piiRedactor,
        artifactMgr,
        assistantResponse,
        channel
      );
    },
    /** Trigger Dream State consolidation. */
    async dream() {
      return kumihoClient.triggerDreamState();
    },
    /** Store tool execution result. */
    async storeExecution(params) {
      return kumihoClient.storeToolExecution(params);
    },
    /** Check backend connectivity. */
    async ping() {
      return kumihoClient.ping();
    }
  };
}
export {
  ArtifactManager,
  KumihoApiError,
  KumihoClient,
  McpBridge,
  McpBridgeError,
  PIIRedactor,
  TOOL_HANDLERS,
  TOOL_SCHEMAS,
  createKumihoMemory,
  createTransport,
  index_default as default,
  ensureUserIdentity,
  generateSessionId,
  getMemorySpace,
  inferChannelType
};
