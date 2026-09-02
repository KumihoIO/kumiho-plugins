/**
 * Kumiho MCP edge worker — `mcp.kumiho.cloud`.
 *
 * A thin, never-caching proxy in front of the App Runner origin. Its whole job
 * is CORS for claude.ai, a per-IP brake, and streaming the body through
 * untouched: MCP responses are Server-Sent Events, so buffering them here would
 * break the transport.
 */

import { handlePreflight, withCors, withNoStore } from './cors';
import {
  checkRateLimit,
  getClientIP,
  rateLimitHeaders,
  rateLimitResponse,
  type RateLimitConfig,
} from './rateLimit';
import type { Env } from './types';

const DEFAULTS: RateLimitConfig = { maxRequests: 600, windowMs: 60_000 };

function config(env: Env): RateLimitConfig {
  return {
    maxRequests: Number.parseInt(env.RATE_LIMIT_REQUESTS || '', 10) || DEFAULTS.maxRequests,
    windowMs: Number.parseInt(env.RATE_LIMIT_WINDOW_MS || '', 10) || DEFAULTS.windowMs,
  };
}

async function forward(request: Request, env: Env, url: URL): Promise<Response> {
  const origin = new URL(url.pathname + url.search, env.ORIGIN_URL);

  const headers = new Headers(request.headers);
  headers.set('X-Forwarded-For', request.headers.get('cf-connecting-ip') || 'unknown');
  headers.set('X-Forwarded-Proto', 'https');
  headers.set('X-Forwarded-Host', url.hostname);
  headers.set('X-Real-IP', request.headers.get('cf-connecting-ip') || 'unknown');

  const forwarded = new Request(origin.toString(), {
    method: request.method,
    headers,
    // Streamed, not buffered: MCP POSTs answer with an SSE stream.
    body: request.body,
    redirect: 'manual',
  });

  try {
    return await fetch(forwarded);
  } catch (error) {
    console.error('origin fetch failed', error);
    return new Response(
      JSON.stringify({
        error: 'bad_gateway',
        error_description: 'Unable to reach the Kumiho MCP origin.',
      }),
      { status: 502, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const started = Date.now();
    try {
      const url = new URL(request.url);

      const preflight = handlePreflight(request);
      if (preflight) return preflight;

      if (url.pathname === '/healthz' || url.pathname === '/edge-health') {
        return withCors(
          new Response(
            JSON.stringify({
              status: 'healthy',
              service: 'kumiho-mcp-edge',
              colo: (request.cf?.colo as string) || 'unknown',
              version: env.VERSION || 'dev',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          ),
          request
        );
      }

      const limits = config(env);
      const verdict = checkRateLimit(getClientIP(request), limits);
      if (!verdict.allowed) {
        return withCors(rateLimitResponse(verdict), request);
      }

      const response = withNoStore(await forward(request, env, url));
      rateLimitHeaders(verdict, limits).forEach((value, key) =>
        response.headers.set(key, value)
      );
      response.headers.set('X-Response-Time', `${Date.now() - started}ms`);
      return withCors(response, request);
    } catch (error) {
      console.error('worker error', error);
      return new Response(
        JSON.stringify({ error: 'internal_error' }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      );
    }
  },
};
