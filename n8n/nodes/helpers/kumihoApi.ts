import {
  ApplicationError,
  NodeApiError,
  sleep,
  type IDataObject,
  type IExecuteFunctions,
  type ILoadOptionsFunctions,
  type ITriggerFunctions,
  type JsonObject,
} from 'n8n-workflow';
import { createHash, randomUUID } from 'crypto';

export interface KumihoApiCredentials {
  baseUrl: string;
  serviceToken: string;
  tenantId?: string;
  userToken?: string;
}

type RequestContext = IExecuteFunctions | ITriggerFunctions | ILoadOptionsFunctions;

type KumihoResponseShape = {
  statusCode?: number;
  headers?: Record<string, unknown>;
  header?: Record<string, unknown>;
  body?: unknown;
  request?: { headers?: unknown };
};

type KumihoRequestErrorShape = {
  message?: string;
  statusCode?: number;
  code?: string;
  response?: KumihoResponseShape;
  options?: { headers?: unknown };
  config?: { headers?: unknown };
  request?: { headers?: unknown };
};

const CLIENT_ID = 'n8n-nodes-kumiho';

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '');

const toBase64 = (input: string) => {
  const normalized = input.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
  return Buffer.from(padded, 'base64').toString('utf8');
};

const tryParseJwtPayload = (token: string): Record<string, unknown> | undefined => {
  const parts = token.split('.');
  if (parts.length < 2) return undefined;
  try {
    const json = toBase64(parts[1]);
    const payload = JSON.parse(json);
    if (payload && typeof payload === 'object') return payload as Record<string, unknown>;
  } catch {
    return undefined;
  }
  return undefined;
};

const extractTenantIdFromToken = (token: string): string | undefined => {
  const payload = tryParseJwtPayload(token);
  if (!payload) return undefined;

  const candidates = [
    payload.tenant_id,
    payload.tenantId,
    payload.tid,
    payload.tenant,
    payload.tenant_slug,
    payload.tenantSlug,
  ];

  for (const value of candidates) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }

  return undefined;
};

const getCorrelationId = (context: RequestContext): string => {
  const maybeFn = (context as unknown as { getExecutionId?: () => string | number }).getExecutionId;
  if (typeof maybeFn === 'function') {
    const value = maybeFn.call(context);
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return randomUUID();
};

const isWriteMethod = (method: string) => method !== 'GET';

const sanitizeHeaderToken = (value: string, maxLen: number) => {
  // Keep it conservative: header-safe, URL-safe-ish, no spaces.
  // Some gateways/servers reject characters like ':' in idempotency keys.
  const sanitized = value.replace(/[^a-zA-Z0-9._-]/g, '-').replace(/-+/g, '-').replace(/^-+|-+$/g, '');
  if (!sanitized) return '';
  return sanitized.length <= maxLen ? sanitized : sanitized.slice(0, maxLen);
};

const stableIdempotencyKey = (correlationId: string, options: { method: string; path: string; qs?: unknown; body?: unknown }) => {
  const hash = createHash('sha256')
    .update(JSON.stringify({ method: options.method, path: options.path, qs: options.qs ?? null, body: options.body ?? null }))
    .digest('hex')
    .slice(0, 24);

  const corr = sanitizeHeaderToken(correlationId, 32) || 'corr';
  // Typical infra limits are 64-128 chars; keep this comfortably small.
  return `n8n-${corr}-${hash}`;
};

const parseRetryAfterMs = (value: unknown): number | undefined => {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;

  if (/^\d+$/.test(trimmed)) {
    return Number(trimmed) * 1000;
  }

  const dateMs = Date.parse(trimmed);
  if (!Number.isNaN(dateMs)) {
    return Math.max(dateMs - Date.now(), 0);
  }

  return undefined;
};

const getHeaderCaseInsensitive = (headers: Record<string, unknown> | undefined, name: string): unknown => {
  if (!headers) return undefined;
  const direct = headers[name];
  if (direct !== undefined) return direct;
  const lower = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === lower) return value;
  }
  return undefined;
};

const redactKumihoTokenInHeaders = (headers: unknown) => {
  if (!headers || typeof headers !== 'object') return;
  for (const [key, value] of Object.entries(headers as Record<string, unknown>)) {
    const lower = key.toLowerCase();
    if ((lower === 'x-kumiho-token' || lower === 'authorization') && typeof value === 'string' && value.trim()) {
      (headers as Record<string, unknown>)[key] = 'REDACTED';
    }
  }
};

const tryParseJsonObject = (value: unknown): Record<string, unknown> | undefined => {
  if (!value) return undefined;
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  if (!trimmed || !trimmed.startsWith('{')) return undefined;
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  } catch {
    return undefined;
  }
  return undefined;
};

export const normalizeMetadata = (value: unknown): Record<string, string> => {
  let parsed: unknown = value;

  if (parsed === undefined || parsed === null) return {};

  if (typeof parsed === 'string') {
    const trimmed = parsed.trim();
    if (!trimmed) return {};
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      throw new ApplicationError('Metadata must be a JSON object (dictionary). If providing a string, it must be valid JSON.');
    }
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new ApplicationError('Metadata must be a JSON object (dictionary of string keys).');
  }

  const out: Record<string, string> = {};
  for (const [key, entry] of Object.entries(parsed as Record<string, unknown>)) {
    if (entry === undefined || entry === null) continue;
    out[key] = typeof entry === 'string' ? entry : String(entry);
  }
  return out;
};

export const clampLimit = (value: unknown, defaultLimit = 100): number => {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return defaultLimit;
  const asInt = Math.floor(raw);
  return asInt > 0 ? asInt : defaultLimit;
};

export const applyReturnAllLimit = (value: unknown, returnAll: boolean, limit: number): unknown => {
  if (returnAll) return value;
  if (!Array.isArray(value)) return value;
  return value.slice(0, clampLimit(limit));
};

export const applyReturnAllLimitToArrayProperty = (
  value: unknown,
  returnAll: boolean,
  limit: number,
  propertyName: string,
): unknown => {
  if (returnAll) return value;
  if (!value || typeof value !== 'object') return value;

  const record = value as Record<string, unknown>;
  const entry = record[propertyName];
  if (!Array.isArray(entry)) return value;

  return {
    ...record,
    [propertyName]: entry.slice(0, clampLimit(limit)),
  };
};

const sanitizeErrorForNodeApiError = (error: unknown) => {
  if (!error || typeof error !== 'object') return error;

  const err = error as KumihoRequestErrorShape;

  // request/request-promise style
  redactKumihoTokenInHeaders(err?.options?.headers);
  redactKumihoTokenInHeaders(err?.response?.request?.headers);
  redactKumihoTokenInHeaders(err?.response?.headers);

  // axios/fetch style (defensive)
  redactKumihoTokenInHeaders(err?.config?.headers);
  redactKumihoTokenInHeaders(err?.request?.headers);

  return err;
};

export const getKumihoCredentials = async (context: RequestContext): Promise<KumihoApiCredentials> => {
  const credentials = (await context.getCredentials('kumihoApi')) as KumihoApiCredentials;
  if (!credentials || !credentials.serviceToken) {
    throw new ApplicationError('Missing Kumiho service token');
  }

  return {
    baseUrl: trimTrailingSlash(credentials.baseUrl || 'https://api.kumiho.cloud'),
    serviceToken: credentials.serviceToken,
    tenantId: typeof credentials.tenantId === 'string' ? credentials.tenantId : undefined,
    userToken: typeof credentials.userToken === 'string' ? credentials.userToken : undefined,
  };
};

export const getKumihoRequestHeaders = async (
  context: RequestContext,
  options?: {
    correlationId?: string;
  },
): Promise<Record<string, string>> => {
  const { serviceToken, tenantId: tenantIdFromCreds, userToken } = await getKumihoCredentials(context);

  const correlationId = options?.correlationId ?? getCorrelationId(context);
  const tokenTrimmed = serviceToken.trim();
  const bearerMatch = tokenTrimmed.match(/^Bearer\s+(.+)$/i);
  const token = (bearerMatch?.[1] ?? tokenTrimmed).trim();

  const userTokenTrimmed = (userToken ?? '').trim();
  const userBearerMatch = userTokenTrimmed.match(/^Bearer\s+(.+)$/i);
  const effectiveUserToken = (userBearerMatch?.[1] ?? userTokenTrimmed).trim();

  const tenantId = (typeof tenantIdFromCreds === 'string' && tenantIdFromCreds.trim())
    ? tenantIdFromCreds.trim()
    : extractTenantIdFromToken(token);

  const headers: Record<string, string> = {
    'X-Kumiho-Token': token,
    'x-correlation-id': correlationId,
    'x-client': CLIENT_ID,
    'x-request-time': new Date().toISOString(),
  };

  if (effectiveUserToken) headers.Authorization = `Bearer ${effectiveUserToken}`;
  if (tenantId) headers['x-tenant-id'] = tenantId;

  return headers;
};

export const kumihoRequest = async (
  context: RequestContext,
  options: {
    method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    path: string;
    qs?: Record<string, string | number | boolean | string[] | undefined>;
    body?: unknown;
    correlationId?: string;
    idempotencyKey?: string;
    timeoutMs?: number;
    retryBudgetMs?: number;
    maxAttempts?: number;
  }
): Promise<IDataObject> => {
  const { baseUrl, serviceToken, tenantId: tenantIdFromCreds, userToken } = await getKumihoCredentials(context);

  const correlationId = options.correlationId ?? getCorrelationId(context);
  const tokenTrimmed = serviceToken.trim();
  const bearerMatch = tokenTrimmed.match(/^Bearer\s+(.+)$/i);
  const token = (bearerMatch?.[1] ?? tokenTrimmed).trim();

  const userTokenTrimmed = (userToken ?? '').trim();
  const userBearerMatch = userTokenTrimmed.match(/^Bearer\s+(.+)$/i);
  const effectiveUserToken = (userBearerMatch?.[1] ?? userTokenTrimmed).trim();

  // The n8n credential UI text says "Firebase_uid" in some places/users' mental model.
  // Guardrail: a Firebase ID token is a JWT and should contain 2 dots.
  if (effectiveUserToken && !effectiveUserToken.includes('.')) {
    throw new ApplicationError(
      'User Token must be a Firebase ID token (JWT), not a UID. It should look like three base64url segments separated by dots.',
    );
  }

  const tenantId = (typeof tenantIdFromCreds === 'string' && tenantIdFromCreds.trim())
    ? tenantIdFromCreds.trim()
    : extractTenantIdFromToken(token);
  const idempotencyKey =
    options.idempotencyKey ?? (isWriteMethod(options.method) ? stableIdempotencyKey(correlationId, options) : undefined);

  // Temporary compatibility workaround:
  // The upstream data plane currently throws gRPC INTERNAL when `x-idempotency-key` is supplied
  // for certain create endpoints. Create works reliably without this header.
  // NOTE: Without idempotency, retries rely on server-side uniqueness + client-side GET fallback.
  const disableIdempotencyForRequest =
    options.method === 'POST' && (options.path === '/api/v1/projects' || options.path === '/api/v1/spaces');
  const effectiveIdempotencyKey = disableIdempotencyForRequest ? undefined : idempotencyKey;

  const headers: Record<string, string> = {
    'X-Kumiho-Token': token,
    'x-correlation-id': correlationId,
    'x-client': CLIENT_ID,
    'x-request-time': new Date().toISOString(),
  };
  if (effectiveUserToken) headers.Authorization = `Bearer ${effectiveUserToken}`;
  if (tenantId) headers['x-tenant-id'] = tenantId;
  if (effectiveIdempotencyKey) headers['x-idempotency-key'] = effectiveIdempotencyKey;

  const requestOptions = {
    method: options.method,
    uri: `${baseUrl}${options.path}`,
    headers,
    qs: options.qs,
    body: options.body,
    json: true,
    timeout: options.timeoutMs ?? 60000,
  };

  const requestContextParts = [
    `method=${options.method}`,
    `path=${options.path}`,
    `baseUrl=${baseUrl}`,
    `tenant_header=${tenantId ? 'set' : 'unset'}`,
    `user_token=${effectiveUserToken ? 'set' : 'unset'}`,
    `service_token_jwt=${token.includes('.') ? 'yes' : 'no'}`,
    `idempotency_header=${effectiveIdempotencyKey ? 'set' : 'unset'}`,
  ];

  const startedAt = Date.now();
  const retryBudgetMs = options.retryBudgetMs ?? 60000;
  const maxAttempts = options.maxAttempts ?? 5;
  const baseDelayMs = 1000;
  const maxDelayMs = 16000;

  const toNodeApiError = (error: unknown) => {
    const sanitizedError = sanitizeErrorForNodeApiError(error);
    const err =
      (sanitizedError && typeof sanitizedError === 'object' ? (sanitizedError as KumihoRequestErrorShape) : undefined) ?? {};
    const statusCode: number | undefined = err.statusCode ?? err.response?.statusCode;
    const responseHeaders: Record<string, unknown> | undefined = err.response?.headers ?? err.response?.header;

    const responseBodyRaw = err.response?.body;
    const responseBody = tryParseJsonObject(responseBodyRaw);
    const kumihoErrorRaw = responseBody?.error;
    const kumihoError =
      kumihoErrorRaw && typeof kumihoErrorRaw === 'object' ? (kumihoErrorRaw as Record<string, unknown>) : undefined;

    const serverCorrelationId =
      (typeof responseBody?.correlation_id === 'string' && (responseBody.correlation_id as string)) ||
      (typeof getHeaderCaseInsensitive(responseHeaders, 'x-correlation-id') === 'string'
        ? (getHeaderCaseInsensitive(responseHeaders, 'x-correlation-id') as string)
        : undefined);

    const kumihoCode = typeof kumihoError?.code === 'string' ? (kumihoError.code as string) : undefined;
    const retryable = kumihoError?.retryable === true;
    const retryAfterMs = typeof kumihoError?.retry_after_ms === 'number' ? (kumihoError.retry_after_ms as number) : undefined;

    const message = kumihoCode ? `Kumiho API error: ${kumihoCode}` : 'Kumiho API request failed';
    const descriptionParts = [
      statusCode ? `status=${statusCode}` : undefined,
      kumihoError?.message ? `message=${String(kumihoError.message)}` : undefined,
      serverCorrelationId ? `correlation_id=${serverCorrelationId}` : `correlation_id=${correlationId}`,
      retryable ? `retryable=true` : undefined,
      retryAfterMs ? `retry_after_ms=${retryAfterMs}` : undefined,
      ...requestContextParts,
    ].filter(Boolean);

    const nodeApiErrorCause: JsonObject =
      sanitizedError && typeof sanitizedError === 'object' ? (sanitizedError as unknown as JsonObject) : { message: String(sanitizedError) };

    const nodeError = new NodeApiError(context.getNode(), nodeApiErrorCause, {
      message,
      description: descriptionParts.join(' | '),
      httpCode: statusCode !== undefined ? String(statusCode) : undefined,
    });

    const nodeErrorWithMeta = nodeError as NodeApiError & {
      kumiho?: {
        code?: string;
        retryable: boolean;
        retry_after_ms?: number;
        correlation_id: string;
        status_code?: number;
      };
    };

    nodeErrorWithMeta.kumiho = {
      code: kumihoCode,
      retryable,
      retry_after_ms: retryAfterMs,
      correlation_id: serverCorrelationId ?? correlationId,
      status_code: statusCode,
    };

    return nodeError;
  };

  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (Date.now() - startedAt > retryBudgetMs) {
      throw toNodeApiError({
        message: `Retry budget exceeded (${retryBudgetMs}ms)`,
        statusCode: 408,
        response: {
          statusCode: 408,
          body: {
            error: {
              code: 'client_retry_budget_exceeded',
              message: `Retry budget exceeded (${retryBudgetMs}ms)`,
              retryable: false,
            },
            correlation_id: correlationId,
          },
        },
      });
    }

    try {
      const response = await context.helpers.request(requestOptions);
      if (response && typeof response === 'object') {
        return response as IDataObject;
      }
      return { value: response } as IDataObject;
    } catch (error) {
      lastError = error;

      const err = (error && typeof error === 'object' ? (error as KumihoRequestErrorShape) : undefined) ?? {};

      const statusCode: number | undefined = err.statusCode ?? err.response?.statusCode;
      const responseHeaders: Record<string, unknown> | undefined = err.response?.headers ?? err.response?.header;
      const responseBodyRaw = err.response?.body;
      const responseBody = tryParseJsonObject(responseBodyRaw);
      const responseErrorRaw = responseBody?.error;
      const responseError =
        responseErrorRaw && typeof responseErrorRaw === 'object' ? (responseErrorRaw as Record<string, unknown>) : undefined;

      const errorCode: string | undefined = typeof err.code === 'string' ? err.code : undefined;

      const retryableFromBody = responseError?.retryable === true;
      const retryableByStatus = statusCode === 429 || (typeof statusCode === 'number' && statusCode >= 500);
      const retryableByNetwork =
        !statusCode &&
        !!errorCode &&
        ['ETIMEDOUT', 'ECONNRESET', 'EPIPE', 'ENOTFOUND', 'EAI_AGAIN', 'ECONNABORTED'].includes(errorCode);
      const shouldRetry = retryableFromBody || retryableByStatus || retryableByNetwork;

      if (!shouldRetry || attempt === maxAttempts) {
        throw toNodeApiError(err);
      }

      const retryAfterMsFromBody =
        typeof responseError?.retry_after_ms === 'number' ? (responseError.retry_after_ms as number) : undefined;
      const retryAfterMsFromHeader = parseRetryAfterMs(getHeaderCaseInsensitive(responseHeaders, 'Retry-After'));
      const retryAfterMs = retryAfterMsFromBody ?? retryAfterMsFromHeader;

      const expDelay = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1));
      const jitterFactor = 1 + (Math.random() * 0.4 - 0.2); // ±20%
      const backoffMs = Math.max(0, Math.round(expDelay * jitterFactor));
      const delayMs = retryAfterMs ? Math.max(backoffMs, retryAfterMs) : backoffMs;

      await sleep(delayMs);
    }
  }

  // Should never hit here, but keep TS happy.
  throw toNodeApiError(lastError);
};
