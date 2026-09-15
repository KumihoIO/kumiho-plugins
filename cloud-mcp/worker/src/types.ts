/**
 * Cloudflare Worker types for the Kumiho MCP edge.
 */

export interface Env {
  ORIGIN_URL: string;
  ENVIRONMENT: string;

  RATE_LIMIT_REQUESTS?: string;
  RATE_LIMIT_WINDOW_MS?: string;

  VERSION?: string;
}

export interface RateLimitEntry {
  count: number;
  resetAt: number;
}

/**
 * Origins allowed to make browser requests. Claude.ai is the connector host;
 * the rest are Kumiho's own surfaces.
 */
export const ALLOWED_ORIGINS = [
  'https://claude.ai',
  'https://www.claude.ai',
  'https://kumiho.io',
  'https://www.kumiho.io',
  'https://app.kumiho.io',
] as const;

/**
 * Headers the MCP streamable-HTTP transport actually sends. `x-api-key` is the
 * static-headers custom-connector credential; the `mcp-*` ones are transport
 * protocol headers; `last-event-id` is SSE resumption.
 */
export const ALLOWED_REQUEST_HEADERS = [
  'authorization',
  'content-type',
  'x-api-key',
  'mcp-session-id',
  'mcp-protocol-version',
  'last-event-id',
] as const;

/**
 * `mcp-session-id` must be readable by the browser client or it cannot
 * continue a session on the next request.
 */
export const EXPOSED_RESPONSE_HEADERS = ['mcp-session-id', 'www-authenticate'] as const;
