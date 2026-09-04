import { StringDecoder } from "node:string_decoder";
import { Transform } from "node:stream";

// Private wire carrier removed by codex_thread_context.py before a Kumiho tool
// handler sees its arguments. It deliberately is NOT public `session_id`:
// Kumiho's host-session resolver owns post-consolidation generation rotation.
export const THREAD_CONTEXT_ARGUMENT = "__kumiho_codex_thread_id";

// Session tools consume the host identity. Engage/recall also receive it so
// their 5-second duplicate guard is scoped per Codex thread rather than per
// long-lived stdio process.
export const THREAD_CONTEXT_TOOLS = new Set([
  "kumiho_chat_add",
  "kumiho_chat_get",
  "kumiho_chat_clear",
  "kumiho_memory_ingest",
  "kumiho_memory_add_response",
  "kumiho_memory_consolidate",
  "kumiho_memory_recall",
  "kumiho_memory_engage",
  "kumiho_memory_reflect",
  "kumiho_code_mine_session",
]);

function normalizedThreadId(value) {
  if (typeof value !== "string") return "";
  const threadId = value.trim();
  if (!threadId || threadId.length > 256) return "";
  for (const char of threadId) {
    const code = char.codePointAt(0);
    if (code < 0x20 || code === 0x7f) return "";
  }
  return threadId;
}

function threadIdFromMeta(meta) {
  if (!meta || typeof meta !== "object" || Array.isArray(meta)) return "";
  const candidates = [
    meta["openai/threadId"],
    meta["openai/thread_id"],
    meta.codexThreadId,
    meta.codex_thread_id,
    meta.threadId,
    meta.thread_id,
    meta["x-codex-turn-metadata"]?.thread_id,
    meta.thread?.id,
  ];
  for (const candidate of candidates) {
    const normalized = normalizedThreadId(candidate);
    if (normalized) return normalized;
  }
  return "";
}

function bridgeOneMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return message;
  }
  if (message.method !== "tools/call") return message;

  const params = message.params;
  if (!params || typeof params !== "object" || Array.isArray(params)) {
    return message;
  }
  const current = params.arguments;
  if (current !== undefined && current !== null &&
      (typeof current !== "object" || Array.isArray(current))) {
    return message;
  }
  const args = { ...(current ?? {}) };
  const hadForgedCarrier = Object.hasOwn(args, THREAD_CONTEXT_ARGUMENT);
  delete args[THREAD_CONTEXT_ARGUMENT];

  // Codex adds this trusted metadata on every MCP tool call. It is per-call,
  // unlike an MCP server environment, so it remains correct when one
  // long-lived server handles multiple threads. The private argument carrier
  // is always stripped first, so a model/caller cannot forge host identity.
  const threadId = threadIdFromMeta(params._meta);
  if (!THREAD_CONTEXT_TOOLS.has(params.name) || !threadId) {
    if (!hadForgedCarrier) return message;
    return { ...message, params: { ...params, arguments: args } };
  }

  return {
    ...message,
    params: {
      ...params,
      // Trusted metadata overwrites only the private carrier. A deliberate
      // public session_id remains untouched and keeps the SDK's argument-wins
      // behavior for historical/backfill operations.
      arguments: { ...args, [THREAD_CONTEXT_ARGUMENT]: threadId },
    },
  };
}

function bridgeMessage(message) {
  if (!Array.isArray(message)) return bridgeOneMessage(message);
  let changed = false;
  const bridged = message.map((entry) => {
    const next = bridgeOneMessage(entry);
    if (next !== entry) changed = true;
    return next;
  });
  return changed ? bridged : message;
}

/** Rewrite one newline-delimited MCP JSON-RPC record, preserving non-calls. */
export function rewriteCodexThreadLine(line) {
  const hadCarriageReturn = line.endsWith("\r");
  const body = hadCarriageReturn ? line.slice(0, -1) : line;
  if (!body.trim()) return line;
  try {
    const parsed = JSON.parse(body);
    const bridged = bridgeMessage(parsed);
    if (bridged === parsed) return line;
    return JSON.stringify(bridged) + (hadCarriageReturn ? "\r" : "");
  } catch {
    // Let the MCP server report malformed JSON exactly as it did before this
    // bridge existed. The launcher must not turn protocol errors into output.
    return line;
  }
}

/** Streaming newline bridge for Codex -> Python MCP stdin. */
export class CodexThreadIdBridge extends Transform {
  constructor(options = {}) {
    super(options);
    this.decoder = new StringDecoder("utf8");
    this.pending = "";
  }

  _transform(chunk, _encoding, callback) {
    try {
      this.pending += this.decoder.write(chunk);
      let newline;
      while ((newline = this.pending.indexOf("\n")) !== -1) {
        const line = this.pending.slice(0, newline);
        this.pending = this.pending.slice(newline + 1);
        this.push(`${rewriteCodexThreadLine(line)}\n`);
      }
      callback();
    } catch (error) {
      callback(error);
    }
  }

  _flush(callback) {
    try {
      this.pending += this.decoder.end();
      if (this.pending) this.push(rewriteCodexThreadLine(this.pending));
      this.pending = "";
      callback();
    } catch (error) {
      callback(error);
    }
  }
}
