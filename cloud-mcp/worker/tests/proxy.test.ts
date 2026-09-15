import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from '../src/index';
import { handlePreflight } from '../src/cors';

const env = { ORIGIN_URL: 'https://origin.kumiho.cloud:8443', ENVIRONMENT: 'production' };
const ctx = {} as ExecutionContext;

test('ChatGPT CORS exposes the OAuth challenge; unrelated origins do not', () => {
  for (const origin of ['https://chatgpt.com', 'https://chat.openai.com', 'https://claude.ai']) {
    const result = handlePreflight(new Request('https://mcp.kumiho.cloud/mcp', {
      method: 'OPTIONS', headers: { Origin: origin },
    }))!;
    assert.equal(result.headers.get('Access-Control-Allow-Origin'), origin);
    assert.match(result.headers.get('Access-Control-Expose-Headers')!, /www-authenticate/);
  }
  const denied = handlePreflight(new Request('https://mcp.kumiho.cloud/mcp', {
    method: 'OPTIONS', headers: { Origin: 'https://chatgpt.com.evil.test' },
  }))!;
  assert.equal(denied.headers.get('Access-Control-Allow-Origin'), null);
});

test('proxy preserves port, streaming body and challenge without forwarding an attacker host', async () => {
  const originalFetch = globalThis.fetch;
  let forwarded: Request | undefined;
  globalThis.fetch = (async (request: Request) => {
    forwarded = request;
    return new Response('event: message\ndata: {}\n\n', { status: 401,
      headers: { 'WWW-Authenticate': 'Bearer resource_metadata="https://mcp.kumiho.cloud/.well-known/oauth-protected-resource"' },
    });
  }) as typeof fetch;
  try {
    const result = await worker.fetch(new Request('https://mcp.kumiho.cloud//attacker.example/mcp?q=1', {
      method: 'POST', body: '{}', headers: { Authorization: 'Bearer test-only', Origin: 'https://chatgpt.com' },
    }), env, ctx);
    assert.equal(new URL(forwarded!.url).host, 'origin.kumiho.cloud:8443');
    assert.equal(new URL(forwarded!.url).pathname, '//attacker.example/mcp');
    assert.equal(await forwarded!.text(), '{}');
    assert.equal(forwarded!.headers.get('Authorization'), 'Bearer test-only');
    assert.equal(result.status, 401);
    assert.match(result.headers.get('WWW-Authenticate')!, /resource_metadata/);
    assert.equal(result.headers.get('Cache-Control'), 'no-store');
    assert.match(await result.text(), /event: message/);
  } finally { globalThis.fetch = originalFetch; }
});

test('healthz checks the origin and never reports edge-only success', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response('unavailable', { status: 503 })) as typeof fetch;
  try {
    const result = await worker.fetch(new Request('https://mcp.kumiho.cloud/healthz'), env, ctx);
    assert.equal(result.status, 503);
    const edge = await worker.fetch(new Request('https://mcp.kumiho.cloud/edge-health'), env, ctx);
    assert.equal(edge.status, 200);
  } finally { globalThis.fetch = originalFetch; }
});
