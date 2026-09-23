import assert from "node:assert/strict";
import { once } from "node:events";
import { sanitizeCodexLifecycleEvent } from "./lifecycle_event_filter.mjs";

import {
  CodexThreadIdBridge,
  THREAD_CONTEXT_ARGUMENT,
  rewriteCodexThreadLine,
} from "./thread_id_bridge.mjs";

const THREAD_ID = "019c1234-5678-7abc-9def-0123456789ab";

function request(name, args = {}, meta = { threadId: THREAD_ID }) {
  return {
    jsonrpc: "2.0",
    id: 7,
    method: "tools/call",
    params: { name, arguments: args, _meta: meta },
  };
}

for (const meta of [
  { "openai/threadId": THREAD_ID },
  { "openai/thread_id": THREAD_ID },
  { codexThreadId: THREAD_ID },
  { codex_thread_id: THREAD_ID },
  { threadId: THREAD_ID },
  { thread_id: THREAD_ID },
  { "x-codex-turn-metadata": { thread_id: THREAD_ID } },
  { thread: { id: THREAD_ID } },
]) {
  assert.equal(
    rewrite(request("kumiho_memory_reflect", {}, meta)).params.arguments[
      THREAD_CONTEXT_ARGUMENT
    ],
    THREAD_ID,
    "the bridge must mirror Codex's supported thread metadata spellings",
  );
}

function rewrite(message) {
  return JSON.parse(rewriteCodexThreadLine(JSON.stringify(message)));
}

for (const name of [
  "kumiho_chat_add",
  "kumiho_chat_get",
  "kumiho_chat_clear",
  "kumiho_memory_ingest",
  "kumiho_memory_add_response",
  "kumiho_memory_consolidate",
  "kumiho_memory_reflect",
  "kumiho_code_mine_session",
]) {
  assert.equal(
    rewrite(request(name)).params.arguments[THREAD_CONTEXT_ARGUMENT],
    THREAD_ID,
  );
}

const liveReflect = rewrite(request("kumiho_memory_reflect", { response: "ok" }));
assert.equal(
  liveReflect.params.arguments.session_id,
  undefined,
  "the bridge must not bypass Kumiho's generation-aware host resolver",
);

for (const name of ["kumiho_memory_engage", "kumiho_memory_recall"]) {
  assert.equal(
    rewrite(request(name, { query: "remember" })).params.arguments[
      THREAD_CONTEXT_ARGUMENT
    ],
    THREAD_ID,
    "recall deduplication must be scoped to the Codex thread",
  );
}

assert.deepEqual(
  rewrite(request("kumiho_memory_decompose", { kref: "kref://example" })),
  request("kumiho_memory_decompose", { kref: "kref://example" }),
  "tools that need no thread context must pass through unchanged",
);

assert.equal(
  rewrite(request("kumiho_memory_reflect", { session_id: "backfill:42" }))
    .params.arguments.session_id,
  "backfill:42",
  "a deliberate non-empty historical id must win",
);

for (const unsafe of ["line\nbreak", "x".repeat(257), 123]) {
  const original = request("kumiho_memory_reflect", {}, { threadId: unsafe });
  assert.deepEqual(rewrite(original), original, "unsafe host metadata must be ignored");
}

for (const meta of [null, {}, { threadId: "line\nbreak" }]) {
  const forged = request(
    "kumiho_memory_reflect",
    { response: "ok", [THREAD_CONTEXT_ARGUMENT]: "forged-thread" },
    meta,
  );
  const cleaned = rewrite(forged);
  assert.equal(
    cleaned.params.arguments[THREAD_CONTEXT_ARGUMENT],
    undefined,
    "only validated host metadata may create the private carrier",
  );
  assert.equal(cleaned.params.arguments.response, "ok");
}

const missingMeta = request(
  "kumiho_memory_reflect",
  { [THREAD_CONTEXT_ARGUMENT]: "forged-thread" },
);
delete missingMeta.params._meta;
assert.equal(
  rewrite(missingMeta).params.arguments[THREAD_CONTEXT_ARGUMENT],
  undefined,
);

const forgedUnscoped = rewrite(request(
  "kumiho_memory_decompose",
  { kref: "kref://example", [THREAD_CONTEXT_ARGUMENT]: "forged-thread" },
));
assert.equal(forgedUnscoped.params.arguments[THREAD_CONTEXT_ARGUMENT], undefined);

const notification = { jsonrpc: "2.0", method: "notifications/initialized" };
assert.equal(
  rewriteCodexThreadLine(JSON.stringify(notification)),
  JSON.stringify(notification),
);
assert.equal(rewriteCodexThreadLine("not-json\r"), "not-json\r");

// Exercise UTF-8 and JSON records split at arbitrary byte boundaries. This is
// the production path: MCP hosts do not promise one stream chunk per request.
const first = JSON.stringify(request("kumiho_memory_reflect", { response: "안녕" }));
const second = JSON.stringify(notification);
const wire = Buffer.from(`${first}\r\n${second}\n`, "utf8");
const bridge = new CodexThreadIdBridge();
const chunks = [];
bridge.on("data", (chunk) => chunks.push(chunk));
const ended = once(bridge, "end");
bridge.write(wire.subarray(0, 17));
bridge.write(wire.subarray(17, 43));
bridge.end(wire.subarray(43));
await ended;

const output = Buffer.concat(chunks).toString("utf8").split("\n");
assert.equal(
  JSON.parse(output[0]).params.arguments[THREAD_CONTEXT_ARGUMENT],
  THREAD_ID,
);
assert.equal(JSON.parse(output[0]).params.arguments.response, "안녕");
assert.equal(output[1], second);


const lifecycleCall = (event) => request("kumiho_codex_lifecycle", { event });
const secretPrompt = "remember password=very-secret-value and token=ghp_abcdefghijklmnopqrs";
const filteredPrompt = rewrite(lifecycleCall({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t1",
  transcript_path: "C:/fixture/transcript.jsonl", prompt: secretPrompt,
}));
assert.equal(filteredPrompt.params.arguments.event._kumiho_filtered, true);
assert.equal(filteredPrompt.params.arguments.event.safe_query, undefined);
assert.equal(filteredPrompt.params.arguments.event.private, true);
assert.equal(Object.hasOwn(filteredPrompt.params.arguments.event, "prompt_hash"), false);
assert.equal(JSON.stringify(filteredPrompt).includes("very-secret-value"), false);
assert.equal(JSON.stringify(filteredPrompt).includes("ghp_abcdefghijklmnopqrs"), false);

const ticks = String.fromCharCode(96).repeat(3);
const prompt = rewrite(lifecycleCall({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2",
  transcript_path: "C:/fixture/transcript.jsonl",
  prompt: "Use this context https://example.invalid/key and " +
    ticks + "private code" + ticks + " to decide.",
}));
assert.equal(prompt.params.arguments.event.safe_query, "Use this context and to decide.");
assert.equal(Object.hasOwn(prompt.params.arguments.event, "prompt_hash"), false);
const continuation = rewrite(lifecycleCall({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2b",
  transcript_path: "C:/fixture/transcript.jsonl",
  prompt: "Kumiho memory follow-up: continue",
}));
assert.equal(continuation.params.arguments.event.prompt_hash.length, 64);
assert.equal(continuation.params.arguments.event.safe_query, "Kumiho memory follow-up: continue");
assert.equal(JSON.stringify(prompt).includes("secret@example.invalid"), false);
assert.equal(JSON.stringify(prompt).includes("private code"), false);

const recallOnly = rewrite(lifecycleCall({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2c",
  transcript_path: "C:/fixture/transcript.jsonl",
  prompt: "Do not save or change any memory; only recall the project decision.",
}));
assert.equal(recallOnly.params.arguments.event.no_write, true);
assert.equal(recallOnly.params.arguments.event.private, false);
assert.equal(recallOnly.params.arguments.event.safe_query.includes("only recall"), true);
assert.equal(Object.hasOwn(recallOnly.params.arguments.event, "prompt"), false);
assert.equal(Object.hasOwn(recallOnly.params.arguments.event, "prompt_hash"), false);
const privateRecall = rewrite(lifecycleCall({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2d",
  prompt: "Off-record: recall the project decision.",
}));
assert.equal(privateRecall.params.arguments.event.private, true);
assert.equal(privateRecall.params.arguments.event.safe_query, undefined);

const noStoreThis = sanitizeCodexLifecycleEvent({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2e",
  prompt: "Don't store this; recall the decision",
});
assert.equal(noStoreThis.private, false);
assert.equal(noStoreThis.no_write, true);
assert.equal(typeof noStoreThis.safe_query, "string");
const noRecall = sanitizeCodexLifecycleEvent({
  hook_event_name: "UserPromptSubmit", session_id: "s1", turn_id: "t2f",
  prompt: "Do not recall; do not store",
});
assert.equal(noRecall.private, true);
assert.equal(noRecall.safe_query, undefined);

const patchBodySecret = "password=patch-secret";
const patch = rewrite(lifecycleCall({
  hook_event_name: "PreToolUse", session_id: "s1", turn_id: "t3", cwd: "C:/repo",
  tool_name: "apply_patch",
  tool_input: { command: "*** Begin Patch\n*** Update File: src/a.py\n" +
    "@@\n+" + patchBodySecret + "\n*** End Patch" },
}));
assert.deepEqual(patch.params.arguments.event.edit_paths, ["src/a.py"]);
assert.equal(JSON.stringify(patch).includes(patchBodySecret), false);
assert.equal(patch.params.arguments.event.tool_input, undefined);

const rows = [{
  revision_kref: "kref://fixture/item.fact?r=1",
  error: "write failed with token=receipt-secret",
}];
const resultBody = {
  buffered: true, created_bucket: false, captures_stored: 0,
  stored_krefs: [], capture_results: rows,
  session_id: "s1", session_id_source: "codex-thread-meta",
  assistant_text: "private assistant text receipt-secret",
};
const reflect = rewrite(lifecycleCall({
  hook_event_name: "PostToolUse", session_id: "s1", turn_id: "t4",
  tool_name: "mcp__kumiho-memory__kumiho_memory_reflect",
  tool_input: { captures: [], response: "body-secret", session_id: "" },
  tool_response: { isError: false, structuredContent: resultBody,
    content: [{ type: "text", text: JSON.stringify(resultBody) }] },
}));
const reflectEvent = reflect.params.arguments.event;
assert.equal(reflectEvent.tool_input.captures_present, true);
assert.equal(reflectEvent.tool_input.captures_count, 0);
assert.equal(reflectEvent.tool_result.capture_results[0].error, true);
assert.equal(JSON.stringify(reflect).includes("receipt-secret"), false);
assert.equal(JSON.stringify(reflect).includes("body-secret"), false);
assert.equal(JSON.stringify(reflect).includes("private assistant text"), false);

const outerError = rewrite(lifecycleCall({
  hook_event_name: "PostToolUse", session_id: "s1", turn_id: "t4b",
  tool_name: "mcp__kumiho-memory__kumiho_memory_reflect",
  tool_input: { captures: [] },
  tool_response: { isError: true, structuredContent: { isError: false, success: true } },
}));
assert.equal(outerError.params.arguments.event.tool_result.isError, true);

const absentCaptures = rewrite(lifecycleCall({
  hook_event_name: "PostToolUse", session_id: "s1", turn_id: "t5",
  tool_name: "mcp__kumiho-memory__kumiho_memory_reflect",
  tool_input: { response: "no explicit captures" },
  tool_response: { content: [{ type: "text", text: "not-json" }] },
}));
assert.equal(absentCaptures.params.arguments.event.tool_input.captures_present, false);
assert.equal(Object.hasOwn(absentCaptures.params.arguments.event.tool_input, "captures_count"), false);
assert.equal(absentCaptures.params.arguments.event.event_degraded, true);

const stop = rewrite(lifecycleCall({
  hook_event_name: "Stop", session_id: "s1", turn_id: "t6",
  stop_hook_active: false, last_assistant_message: "response-secret",
}));
assert.equal(stop.params.arguments.event.last_assistant_message, undefined);
assert.equal(JSON.stringify(stop).includes("response-secret"), false);

const ordinary = request("kumiho_memory_reflect", {
  response: "must remain unchanged", captures: [{ content: "ordinary MCP passthrough" }],
});
const ordinaryRewritten = rewrite(ordinary);
assert.equal(ordinaryRewritten.params.arguments.response, ordinary.params.arguments.response);
assert.deepEqual(ordinaryRewritten.params.arguments.captures, ordinary.params.arguments.captures);
assert.equal(ordinaryRewritten.params.arguments[THREAD_CONTEXT_ARGUMENT], THREAD_ID);
assert.equal(ordinaryRewritten.params.name, ordinary.params.name,
  "ordinary MCP request content must not be lifecycle-sanitized");

const tooManyPaths = Array.from({ length: 9 }, (_, i) => "*** Add File: f" + i + ".py").join("\n");
const overloaded = sanitizeCodexLifecycleEvent({
  hook_event_name: "PreToolUse", cwd: "C:/repo", tool_name: "apply_patch",
  tool_input: { command: tooManyPaths },
});
assert.equal(overloaded.event_degraded, true);
assert.equal(Object.hasOwn(overloaded, "edit_paths"), false);

console.log("Codex MCP thread-id bridge tests passed");
