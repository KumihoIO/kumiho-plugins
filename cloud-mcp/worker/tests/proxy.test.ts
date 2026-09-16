import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from '../src/index';
import { handlePreflight } from '../src/cors';

const env = { ORIGIN_URL: 'https://origin.kumiho.cloud:8443', ENVIRONMENT: 'production' };
const ctx = {} as ExecutionContext;

test('review assets preserve video ranges without forwarding credentials or reaching MCP', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => { throw new Error('Origin must not be contacted'); }) as typeof fetch;
  let seen: Request | undefined;
  const configured = { ...env, REVIEW_ASSETS: { fetch: async (request: Request) => {
    seen = request;
    return new Response('video', { status: 206, headers: { 'Content-Type': 'video/mp4' } });
  } } as unknown as Fetcher };
  const url = 'https://mcp.kumiho.cloud/review/0123456789abcdef0123456789abcdef/demo.mp4';
  try {
    const result = await worker.fetch(new Request(url + '?private=discard', {
      headers: { Authorization: 'Bearer test-only', Cookie: 'private=test', Range: 'bytes=0-4' },
    }), configured, ctx);
    assert.equal(result.status, 206);
    assert.equal(result.headers.get('X-Robots-Tag'), 'noindex, nofollow');
    assert.equal(seen!.url, url);
    assert.equal(seen!.headers.get('Range'), 'bytes=0-4');
    assert.equal(seen!.headers.get('Authorization'), null);
    assert.equal(seen!.headers.get('Cookie'), null);
    assert.equal((await worker.fetch(new Request(url, { method: 'POST', body: '{}' }), configured, ctx)).status, 405);
    assert.equal((await worker.fetch(new Request(url), env, ctx)).status, 404);
    assert.equal((await worker.fetch(new Request(url.replace('demo.mp4', 'secret.json')), configured, ctx)).status, 404);
  } finally { globalThis.fetch = originalFetch; }
});

test('domain verification serves only the configured public token without contacting the origin', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => { calls++; throw new Error('Origin must not be contacted'); }) as typeof fetch;
  try {
    const url = 'https://mcp.kumiho.cloud/.well-known/openai-apps-challenge';
    const configured = { ...env, OPENAI_APPS_CHALLENGE: 'test-public-domain-proof' };
    const result = await worker.fetch(new Request(url), configured, ctx);
    assert.equal(result.status, 200);
    assert.equal(await result.text(), configured.OPENAI_APPS_CHALLENGE);
    assert.equal(result.headers.get('Cache-Control'), 'no-store');
    assert.match(result.headers.get('Content-Type')!, /^text\/plain/);
    const head = await worker.fetch(new Request(url, { method: 'HEAD' }), configured, ctx);
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
    const missing = await worker.fetch(new Request(url), env, ctx);
    assert.equal(missing.status, 404);
    assert.equal(await missing.text(), '');
    const post = await worker.fetch(new Request(url, { method: 'POST' }), configured, ctx);
    assert.equal(post.status, 405);
    assert.equal(post.headers.get('Allow'), 'GET, HEAD');
    assert.equal(calls, 0);
  } finally { globalThis.fetch = originalFetch; }
});

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
