/**
 * Kumiho MCP edge worker — `mcp.kumiho.cloud`.
 *
 * A thin, never-caching proxy in front of the ECS origin. Its whole job
 * is CORS for ChatGPT and Claude, a per-IP brake, and streaming the body through
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
  // Assign paths instead of resolving them: a //host path must never turn
  // this into a proxy to a caller-controlled host carrying their bearer token.
  const origin = new URL(env.ORIGIN_URL);
  if (env.ENVIRONMENT !== 'development' && origin.protocol !== 'https:') {
    throw new Error('Production and staging origins must use HTTPS');
  }
  if (origin.origin === url.origin || origin.username || origin.password) {
    throw new Error('Invalid or recursive MCP origin');
  }
  origin.pathname = url.pathname;
  origin.search = url.search;

  const headers = new Headers(request.headers);
  headers.delete('Host');
  headers.set('X-Forwarded-For', request.headers.get('cf-connecting-ip') || 'unknown');
  headers.set('X-Forwarded-Proto', 'https');
  headers.set('X-Forwarded-Host', url.hostname);
  headers.set('X-Real-IP', request.headers.get('cf-connecting-ip') || 'unknown');

  // Request-to-Request copying keeps the streaming body and method intact.
  const forwarded = new Request(new Request(origin.toString(), request), {
    headers,
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

      if (url.pathname.startsWith('/review/')) {
        if (!/^\/review\/[a-f0-9]{32}\/(?:index\.html|demo\.mp4)$/.test(url.pathname)
          || !env.REVIEW_ASSETS) return new Response(null, { status: 404 });
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          return new Response(null, { status: 405, headers: { Allow: 'GET, HEAD' } });
        }
        // The static binding receives no caller credentials or query string.
        url.search = '';
        const headers = new Headers();
        for (const name of ['Range', 'If-Range']) {
          const value = request.headers.get(name);
          if (value) headers.set(name, value);
        }
        const asset = await env.REVIEW_ASSETS.fetch(new Request(url, {
          method: request.method, headers,
        }));
        const response = new Response(asset.body, asset);
        response.headers.set('X-Robots-Tag', 'noindex, nofollow');
        response.headers.set('Referrer-Policy', 'no-referrer');
        return response;
      }

      if (url.pathname === '/.well-known/openai-apps-challenge') {
        const headers = { 'Content-Type': 'text/plain; charset=utf-8' };
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          return withNoStore(new Response(null, {
            status: 405, headers: { ...headers, Allow: 'GET, HEAD' },
          }));
        }
        const token = env.OPENAI_APPS_CHALLENGE;
        return withNoStore(new Response(request.method === 'HEAD' ? null : token || null, {
          status: token ? 200 : 404, headers,
        }));
      }

      const preflight = handlePreflight(request);
      if (preflight) return preflight;

      if (url.pathname === '/edge-health') {
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
