/**
 * CORS for an MCP endpoint.
 *
 * Claude.ai calls this origin from the browser, so the preflight has to allow
 * exactly the headers the streamable-HTTP transport sends and expose
 * `mcp-session-id` back — without it a browser client cannot continue a
 * session past the first request.
 */

import { ALLOWED_ORIGINS, ALLOWED_REQUEST_HEADERS, EXPOSED_RESPONSE_HEADERS } from './types';

function isAllowedOrigin(origin: string | null): origin is string {
  if (!origin) return false;
  if ((ALLOWED_ORIGINS as readonly string[]).includes(origin)) return true;
  // Kumiho's own subdomains, and localhost for `mcp-inspector` during dev.
  return (
    origin.endsWith('.kumiho.io') ||
    origin.endsWith('.kumiho.cloud') ||
    origin.startsWith('http://localhost:') ||
    origin.startsWith('http://127.0.0.1:')
  );
}

function corsHeaders(origin: string): Headers {
  return new Headers({
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': ALLOWED_REQUEST_HEADERS.join(', '),
    'Access-Control-Expose-Headers': EXPOSED_RESPONSE_HEADERS.join(', '),
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  });
}

/** Answer a preflight, or return null when this is not one. */
export function handlePreflight(request: Request): Response | null {
  if (request.method !== 'OPTIONS') return null;

  const origin = request.headers.get('Origin');
  if (!isAllowedOrigin(origin)) {
    // No CORS headers: the browser refuses, which is the intended answer.
    return new Response(null, { status: 204 });
  }
  return new Response(null, { status: 204, headers: corsHeaders(origin) });
}

/** Copy the CORS headers onto a real response. */
export function withCors(response: Response, request: Request): Response {
  const origin = request.headers.get('Origin');
  if (!isAllowedOrigin(origin)) return response;

  const next = new Response(response.body, response);
  corsHeaders(origin).forEach((value, key) => next.headers.set(key, value));
  return next;
}

/**
 * Nothing here is cacheable: every response is either tenant data or an auth
 * challenge. Set it at the edge as well as at the origin so a misconfigured
 * intermediary cannot decide otherwise.
 */
export function withNoStore(response: Response): Response {
  const next = new Response(response.body, response);
  next.headers.set('Cache-Control', 'no-store');
  next.headers.set('X-Robots-Tag', 'noindex');
  return next;
}
