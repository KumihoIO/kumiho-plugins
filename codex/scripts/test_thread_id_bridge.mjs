import assert from "node:assert/strict";
import { once } from "node:events";

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

console.log("Codex MCP thread-id bridge tests passed");
