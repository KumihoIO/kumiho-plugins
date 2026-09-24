import { createHash } from "node:crypto";

const MAX_EVENT_JSON = 16 * 1024;
const MAX_RAW_PROMPT_CHARS = 262144;
const MAX_PATHS = 8;
const MAX_PATH_LENGTH = 1024;
const MAX_ROWS = 128;
const SAFE_KREF = /^kref:\/\/[^\s]{3,512}$/;
const SAFE_SESSION = /^[A-Za-z0-9._:-]{1,256}$/;
const PRIVATE_RE = /\b(?:this is|this's|it's|keep (?:this|it|that)|going|stay|strictly)\s+off[\s-]?(?:the[\s-]+)?record\b|(?:^|[.!?\n]\s*)off[\s-]?(?:the[\s-]+)?record\s*(?:[:,;!—-]|\.?\s*$)|(?:이건|이거는|이 얘기는|이 내용은|이 대화는|지금부터|여기부터)\s*오프\s*더\s*레코드|오프\s*더\s*레코드(?:인데|야|예요|이야|입니다|니까|지만|라서)|오프\s*더\s*레코드로\s*(?:해|하자|할게|부탁|얘기|말|가자|진행)|(?:^|[.!?\n]\s*)오프\s*더\s*레코드\s*(?:[:,]|\.?\s*$)|do not (?:remember|recall)|don't (?:remember|recall)|기억하지\s*(?:마|말)|(?:이건|이거는|이 얘기는|이 내용은|지금부터|여기부터)\s*비공개(?:야|예요|이야|입니다|로\s*해\s*줘|로)?\s*(?:[.,!~]|$)|비공개로\s*(?:해\s*줘|하자|할게|얘기|말할게|부탁)/i;
const NO_WRITE_PATTERNS = [
  /\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect)\s+(?:to|in)\s+memor(?:y|ies)\b/i,
  /\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect|change|modify|update)(?:\s+or\s+(?:save|store|write|record|capture|reflect|change|modify|update))?\s+(?:any\s+)?memor(?:y|ies)\b/i,
  /\b(?:do not|don't|never)\s+(?:save|store|write|record|capture|reflect)(?:\s+(?:this|that|it|anything)(?:\s+(?:to|in)\s+memor(?:y|ies))?)?(?=\s*(?:[.;,!?]|$))/i,
  /\b(?:only|just)\s+recall\b[^.\n]{0,40}\bno\s+saving\b/i,
  /(?:메모리|기억)(?:를|에)?\s*(?:(?:저장|변경|수정|기록)하지|쓰지)\s*(?:마|말)/i,
  /(?:이건|이거는?|이 내용은?|이 얘기는?)\s*(?:저장|기록)하지\s*(?:마|말)/i,
];
const SECRET_RE = /(?:\b(?:password|passwd|secret|credential|token|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|private[_-]?key)\b\s*[:=]\s*\S+|(?:비밀번호|암호|토큰|비밀키|API키)\s*[:=]\s*\S+|\bbearer\s+[A-Za-z0-9._~+/-]{8,}|\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{12,})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|https?:\/\/[^\s/:@]+:[^\s/@]+@|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)/i;

const COMMON_STRING_LIMITS = {
  session_id: 256,
  turn_id: 256,
  transcript_path: 4096,
  cwd: 4096,
  tool_name: 256,
};

function markDegraded(output) {
  output.event_degraded = true;
}

function copyCommon(event, output) {
  for (const [key, limit] of Object.entries(COMMON_STRING_LIMITS)) {
    if (!Object.hasOwn(event, key)) continue;
    if (typeof event[key] !== "string" || event[key].length > limit) {
      markDegraded(output);
      continue;
    }
    output[key] = event[key];
  }
}

function safeQuery(prompt) {
  const fence = String.fromCharCode(96);
  const triple = fence.repeat(3);
  return prompt
    .replace(new RegExp(triple + "[\\s\\S]*?" + triple, "g"), " ")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 600);
}

function copyBooleanIfPresent(source, target, key, output) {
  if (!Object.hasOwn(source, key)) return;
  if (typeof source[key] !== "boolean") {
    markDegraded(output);
    return;
  }
  target[key] = source[key];
}

function copyStringIfPresent(source, target, key, output, limit = 256) {
  if (!Object.hasOwn(source, key)) return;
  if (typeof source[key] !== "string" || source[key].length > limit) {
    markDegraded(output);
    return;
  }
  target[key] = source[key];
}

function sanitizeRow(row, output) {
  if (!row || typeof row !== "object" || Array.isArray(row)) {
    markDegraded(output);
    return null;
  }
  const clean = {};
  copyStringIfPresent(row, clean, "revision_kref", output, 512);
  if (clean.revision_kref && !SAFE_KREF.test(clean.revision_kref)) {
    delete clean.revision_kref;
    markDegraded(output);
  }
  for (const key of ["success", "queued", "isError"]) {
    copyBooleanIfPresent(row, clean, key, output);
  }
  for (const key of ["error", "errors", "backend_error"]) {
    if (Object.hasOwn(row, key)) clean[key] = Boolean(row[key]);
  }
  return clean;
}

function sanitizeStoreResult(value, output) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    markDegraded(output);
    return null;
  }
  return sanitizeRow(value, output);
}

function parseToolResult(envelope, output) {
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) {
    markDegraded(output);
    return null;
  }
  const resultCandidates = [];
  let outerError = false;
  if (Object.hasOwn(envelope, "isError")) {
    if (typeof envelope.isError !== "boolean") markDegraded(output);
    else outerError ||= envelope.isError;
  }
  if (Object.hasOwn(envelope, "is_error")) {
    if (typeof envelope.is_error !== "boolean") markDegraded(output);
    else outerError ||= envelope.is_error;
  }
  if (envelope.structuredContent && typeof envelope.structuredContent === "object" &&
      !Array.isArray(envelope.structuredContent)) {
    resultCandidates.push(envelope.structuredContent);
  }
  if (Array.isArray(envelope.content)) {
    for (const block of envelope.content) {
      if (!block || block.type !== "text" || typeof block.text !== "string") continue;
      try {
        const parsed = JSON.parse(block.text);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          resultCandidates.push(parsed);
        }
      } catch {
        // Content that is not JSON cannot prove a receipt.
      }
    }
  }
  const wrapperOnly = Object.hasOwn(envelope, "content") ||
    Object.hasOwn(envelope, "structuredContent") ||
    Object.hasOwn(envelope, "isError") ||
    Object.hasOwn(envelope, "is_error");
  if (!wrapperOnly) resultCandidates.push(envelope);
  if (!resultCandidates.length) {
    markDegraded(output);
    return null;
  }
  const canonical = JSON.stringify(resultCandidates[0]);
  if (resultCandidates.some((candidate) => JSON.stringify(candidate) !== canonical)) {
    markDegraded(output);
    return null;
  }
  const result = resultCandidates[0];
  const clean = {};
  if (Object.hasOwn(envelope, "isError") || Object.hasOwn(envelope, "is_error") ||
      Object.hasOwn(result, "isError")) {
    if (Object.hasOwn(result, "isError") && typeof result.isError !== "boolean") {
      markDegraded(output);
    }
    clean.isError = outerError || result.isError === true;
  }
  for (const key of ["buffered", "created_bucket", "success", "queued"]) {
    copyBooleanIfPresent(result, clean, key, output);
  }
  for (const key of ["error", "errors", "backend_error"]) {
    if (Object.hasOwn(result, key)) clean[key] = Boolean(result[key]);
  }
  if (Object.hasOwn(result, "captures_stored")) {
    if (Number.isSafeInteger(result.captures_stored) && result.captures_stored >= 0) {
      clean.captures_stored = result.captures_stored;
    } else markDegraded(output);
  }
  copyStringIfPresent(result, clean, "session_id", output, 256);
  if (typeof clean.session_id === "string" && !SAFE_SESSION.test(clean.session_id)) {
    delete clean.session_id;
    markDegraded(output);
  }
  copyStringIfPresent(result, clean, "session_id_source", output, 64);
  if (typeof clean.session_id_source === "string" && clean.session_id_source !== "codex-thread-meta") {
    delete clean.session_id_source;
    markDegraded(output);
  }
  if (Object.hasOwn(result, "stored_krefs")) {
    if (!Array.isArray(result.stored_krefs) || result.stored_krefs.length > MAX_ROWS) {
      markDegraded(output);
    } else if (result.stored_krefs.every((ref) => typeof ref === "string" && SAFE_KREF.test(ref))) {
      clean.stored_krefs = result.stored_krefs.slice();
    } else markDegraded(output);
  }
  if (Object.hasOwn(result, "capture_results")) {
    if (!Array.isArray(result.capture_results) || result.capture_results.length > MAX_ROWS) {
      markDegraded(output);
    } else {
      const rows = result.capture_results.map((row) => sanitizeRow(row, output));
      if (rows.every(Boolean)) clean.capture_results = rows;
    }
  }
  if (Object.hasOwn(result, "store_result")) {
    clean.store_result = sanitizeStoreResult(result.store_result, output);
  }
  return clean;
}

function sanitizePaths(event, output) {
  const name = output.tool_name;
  if (name !== "apply_patch") return;
  const input = event.tool_input;
  const patch = input && typeof input === "object" && !Array.isArray(input)
    ? (typeof input.command === "string" ? input.command : null)
    : null;
  if (patch === null) {
    markDegraded(output);
    return;
  }
  const paths = [];
  const pathRe = /^\*\*\* (?:(?:Update|Delete|Add) File|Move to): (.+?)\s*$/gm;
  for (const match of patch.matchAll(pathRe)) {
    const path = match[1].trim();
    if (!path || path.length > MAX_PATH_LENGTH || /[\r\n\0]/.test(path)) {
      markDegraded(output);
      return;
    }
    if (!paths.includes(path)) paths.push(path);
    if (paths.length > MAX_PATHS) {
      markDegraded(output);
      return;
    }
  }
  if (!paths.length) {
    markDegraded(output);
    return;
  }
  output.edit_paths = paths;
}

function sanitizePostTool(event, output) {
  const name = output.tool_name || "";
  if (!/(?:^|__)kumiho_memory_(?:reflect|consolidate)$/.test(name)) return;
  const args = event.tool_input;
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    markDegraded(output);
    return;
  }
  const cleanArgs = {};
  copyStringIfPresent(args, cleanArgs, "session_id", output, 256);
  if (Object.hasOwn(args, "captures")) {
    cleanArgs.captures_present = true;
    if (!Array.isArray(args.captures)) markDegraded(output);
    else {
      cleanArgs.captures_count = args.captures.length;
      if (args.captures.length > MAX_ROWS) markDegraded(output);
    }
  } else cleanArgs.captures_present = false;
  cleanArgs.summary_present = Object.hasOwn(args, "summary");
  output.tool_input = cleanArgs;
  if (!Object.hasOwn(event, "tool_response")) {
    markDegraded(output);
    return;
  }
  output.tool_result = parseToolResult(event.tool_response, output);
}

export function sanitizeCodexLifecycleEvent(event) {
  const clean = { _kumiho_filtered: true };
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    clean.hook_event_name = "";
    markDegraded(clean);
    return clean;
  }
  copyCommon(event, clean);
  const kind = event.hook_event_name;
  if (typeof kind !== "string" || kind.length > 64) {
    clean.hook_event_name = "";
    markDegraded(clean);
    return clean;
  }
  clean.hook_event_name = kind;
  if (kind === "UserPromptSubmit") {
    if (typeof event.prompt !== "string") {
      markDegraded(clean);
      clean.private = true;
      clean.privacy_filtered = true;
    } else if (event.prompt.length > MAX_RAW_PROMPT_CHARS) {
      clean.private = true;
      clean.privacy_filtered = true;
      markDegraded(clean);
    } else {
      const unsafe = SECRET_RE.test(event.prompt);
      const isPrivate = PRIVATE_RE.test(event.prompt) || process.env.KUMIHO_MEMORY_OFF === "1";
      clean.private = unsafe || isPrivate;
      clean.privacy_filtered = unsafe || isPrivate;
      if (!unsafe && !isPrivate) {
        clean.no_write = NO_WRITE_PATTERNS.some((pattern) => pattern.test(event.prompt));
        const continuation = /^Kumiho memory follow-up:/i.test(event.prompt);
        const query = safeQuery(event.prompt);
        if (query.length >= 3) clean.safe_query = query;
        else clean.privacy_filtered = true;
        if (continuation) {
          clean.prompt_hash = createHash("sha256").update(event.prompt, "utf8").digest("hex");
        }
      }
    }
  } else if (kind === "PreToolUse") {
    sanitizePaths(event, clean);
  } else if (kind === "PostToolUse") {
    sanitizePostTool(event, clean);
  } else if (kind === "Stop") {
    if (Object.hasOwn(event, "stop_hook_active")) {
      if (typeof event.stop_hook_active === "boolean") clean.stop_hook_active = event.stop_hook_active;
      else markDegraded(clean);
    }
  } else {
    markDegraded(clean);
  }
  if (JSON.stringify(clean).length > MAX_EVENT_JSON) {
    return { _kumiho_filtered: true, hook_event_name: kind, event_degraded: true };
  }
  return clean;
}
