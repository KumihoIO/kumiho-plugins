/**
 * @kumiho/openclaw-kumiho
 *
 * Long-term cognitive memory for OpenClaw agents, powered by Kumiho Cloud.
 *
 * Two operating modes:
 *   - "local"  (default): Spawns kumiho-mcp Python process via stdio.
 *     Uses the full kumiho-memory SDK directly — no HTTP deployment needed.
 *   - "cloud": HTTPS calls to Kumiho Cloud API for production / multi-device.
 *
 * Privacy-first design in both modes:
 *   - Raw conversations, voice recordings, and media stay on your device
 *   - Only structured summaries and metadata reach Kumiho Cloud / Neo4j
 *   - PII is redacted before upload
 *
 * @see https://kumiho.io
 * @see https://docs.kumiho.cloud
 */

import { homedir } from "node:os";
import { join } from "node:path";
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { execFile } from "node:child_process";

// ---------------------------------------------------------------------------
// Preferences loader — reads ~/.kumiho/preferences.json written by kumiho-setup
// ---------------------------------------------------------------------------

interface KumihoPreferences {
  dreamState?: {
    schedule?: string;
    // API keys must NOT be stored in preferences.json — use the host gateway's
    // model provider config (hostLlmApiKey) or the plugin's llm.apiKey field.
    model?: { provider?: string; model?: string };
  };
  consolidation?: {
    model?: { provider?: string; model?: string };
  };
}

function loadPreferences(): KumihoPreferences {
  try {
    const path = join(homedir(), ".kumiho", "preferences.json");
    if (existsSync(path)) {
      const raw = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
      // Strip any API key fields that may have been manually added — keys must
      // come from the plugin config or the host gateway, not preferences.json.
      for (const section of ["dreamState", "consolidation"] as const) {
        const s = raw[section] as Record<string, unknown> | undefined;
        if (s?.["model"] && typeof s["model"] === "object") {
          const m = s["model"] as Record<string, unknown>;
          delete m["apiKey"];
          delete m["api_key"];
        }
      }
      return raw as KumihoPreferences;
    }
  } catch { /* ignore */ }
  return {};
}

// ---------------------------------------------------------------------------
// Auth loader — reads ~/.kumiho/kumiho_authentication.json written by kumiho-setup
// ---------------------------------------------------------------------------

interface KumihoAuthentication {
  api_token?: string;
  api_token_expires_at?: number;
  id_token?: string;
  refresh_token?: string;
  expires_at?: number;
  control_plane_token?: string;
  cp_expires_at?: number;
  email?: string;
  api_key?: string;
  project_id?: string | null;
}

/**
 * Load the long-lived api_token from ~/.kumiho/kumiho_authentication.json.
 * Falls back to id_token if api_token is missing or expired.
 * Returns empty string if nothing usable is found.
 */
function loadAuthToken(): string {
  try {
    const path = join(homedir(), ".kumiho", "kumiho_authentication.json");
    if (!existsSync(path)) return "";
    const auth = JSON.parse(readFileSync(path, "utf8")) as KumihoAuthentication;

    // Prefer api_token (long-lived service token with tenant_id claim)
    if (auth.api_token) {
      const now = Math.floor(Date.now() / 1000);
      if (!auth.api_token_expires_at || auth.api_token_expires_at > now) {
        return auth.api_token;
      }
    }

    // Fall back to id_token (short-lived Firebase token)
    if (auth.id_token) {
      const now = Math.floor(Date.now() / 1000);
      if (!auth.expires_at || auth.expires_at > now) {
        return auth.id_token;
      }
    }

    return "";
  } catch { /* ignore */ }
  return "";
}

const _AUTH_PATH = join(homedir(), ".kumiho", "kumiho_authentication.json");

// ---------------------------------------------------------------------------
// OpenClaw auth-profile loader — reads ~/.openclaw/agents/main/agent/auth-profiles.json
// ---------------------------------------------------------------------------

interface OpenClawAuthProfile {
  type?: string;
  provider?: string;
  token?: string;
  access?: string;
  refresh?: string;
  expires?: number;
  mode?: string;
  [key: string]: unknown;
}

interface OpenClawAuthProfiles {
  version?: number;
  profiles?: Record<string, OpenClawAuthProfile>;
  lastGood?: Record<string, string>;
  usageStats?: Record<string, { lastUsed?: number; errorCount?: number }>;
  auth?: {
    profiles?: Record<string, OpenClawAuthProfile>;
    lastGood?: Record<string, string>;
    usageStats?: Record<string, { lastUsed?: number; errorCount?: number }>;
  };
}

type InheritedLlmConfig = { provider: "anthropic" | "openai"; apiKey: string };

function getPreferredLlmProvider(
  raw: Pick<KumihoPluginConfig, "consolidationModel" | "dreamStateModel" | "llm">,
): string | undefined {
  const preferred = (
    raw.consolidationModel?.provider ||
    raw.dreamStateModel?.provider ||
    raw.llm?.provider
  );
  return normalizeConfiguredLlmProvider(preferred) || preferred;
}

function extractCredentialString(value: unknown, depth = 0): string | null {
  if (!value || typeof value !== "object" || depth > 2) return null;
  const record = value as Record<string, unknown>;
  for (const field of ["apiKey", "api_key", "key", "token"] as const) {
    const candidate = record[field];
    if (typeof candidate === "string" && candidate.trim()) return candidate;
  }
  for (const nested of Object.values(record)) {
    const found = extractCredentialString(nested, depth + 1);
    if (found) return found;
  }
  return null;
}

function normalizeDirectProvider(
  provider?: string,
  mode?: string,
): "anthropic" | "openai" | null {
  if (provider === "anthropic") return "anthropic";
  if (provider === "openai") return "openai";
  if (provider === "openai-codex" && (mode === "token" || mode === "oauth")) return "openai";
  return null;
}

function extractOpenClawProfileCredential(
  profile: OpenClawAuthProfile,
  provider: "anthropic" | "openai" | null,
): string | null {
  if (!provider) return null;

  const authMode =
    typeof profile.mode === "string"
      ? profile.mode
      : typeof profile.type === "string"
        ? profile.type
        : "";

  // Best-effort support for OpenClaw's cached OpenAI Codex OAuth access token.
  // We only use the current access token if it is still valid; refresh logic
  // stays with OpenClaw because this plugin does not own the OAuth client.
  if (provider === "openai" && authMode === "oauth") {
    const access = typeof profile.access === "string" ? profile.access.trim() : "";
    const expires = typeof profile.expires === "number" ? profile.expires : 0;
    if (!access) return null;
    if (expires && expires <= Date.now() + 60_000) return null;
    return access;
  }

  return extractCredentialString(profile);
}

/**
 * Load the best LLM API key from OpenClaw's auth-profiles.json.
 * Uses `lastGood` to pick the preferred profile per provider.
 * If `preferredProvider` is given, tries that provider first.
 * Falls back to whichever profile was used most recently.
 */
function loadOpenClawAuthProfile(
  preferredProvider?: string,
): InheritedLlmConfig | null {
  try {
    const path = join(homedir(), ".openclaw", "agents", "main", "agent", "auth-profiles.json");
    if (!existsSync(path)) return null;
    const data = JSON.parse(readFileSync(path, "utf8")) as OpenClawAuthProfiles;
    const profiles = data.auth?.profiles ?? data.profiles ?? {};
    const lastGood = data.auth?.lastGood ?? data.lastGood ?? {};
    const stats = data.auth?.usageStats ?? data.usageStats ?? {};

    const candidates = Object.entries(profiles)
      .map(([profileKey, profile]) => {
        const rawProvider =
          typeof profile.provider === "string"
            ? profile.provider
            : profileKey.split(":")[0];
        const authMode =
          typeof profile.mode === "string"
            ? profile.mode
            : typeof profile.type === "string"
              ? profile.type
              : undefined;
        const provider = normalizeDirectProvider(rawProvider, authMode);
        const apiKey = extractOpenClawProfileCredential(profile, provider);
        return {
          profileKey,
          provider,
          apiKey,
          lastUsed: stats[profileKey]?.lastUsed ?? 0,
          errorCount: stats[profileKey]?.errorCount ?? 0,
        };
      })
      .filter((entry): entry is {
        profileKey: string;
        provider: "anthropic" | "openai";
        apiKey: string;
        lastUsed: number;
        errorCount: number;
      } => {
        return !!entry.provider && typeof entry.apiKey === "string" && entry.apiKey.length > 0;
      });

    // Try the preferred provider first via lastGood when present.
    if (preferredProvider) {
      const preferredProfileKey = lastGood[preferredProvider];
      if (preferredProfileKey) {
        const preferred = candidates.find((entry) => entry.profileKey === preferredProfileKey);
        if (preferred) {
          return { provider: preferred.provider, apiKey: preferred.apiKey };
        }
      }

      const preferred = candidates
        .filter((entry) => entry.provider === preferredProvider)
        .sort((a, b) => b.lastUsed - a.lastUsed || a.errorCount - b.errorCount)[0];
      if (preferred) {
        return { provider: preferred.provider, apiKey: preferred.apiKey };
      }
    }

    // Try any lastGood entry, preferring the one with the highest lastUsed.
    const rankedLastGood = Object.values(lastGood)
      .map((profileKey) => candidates.find((entry) => entry.profileKey === profileKey))
      .filter((entry): entry is typeof candidates[number] => !!entry)
      .sort((a, b) => b.lastUsed - a.lastUsed);
    if (rankedLastGood[0]) {
      return {
        provider: rankedLastGood[0].provider,
        apiKey: rankedLastGood[0].apiKey,
      };
    }

    // Last resort: pick the healthiest usable direct-call profile.
    const best = [...candidates]
      .sort((a, b) => a.errorCount - b.errorCount || b.lastUsed - a.lastUsed)[0];
    if (best) {
      return { provider: best.provider, apiKey: best.apiKey };
    }
  } catch { /* ignore */ }
  return null;
}

function loadHostLlmFromPluginApi(
  api: PluginAPI,
  preferredProvider?: string,
): InheritedLlmConfig | null {
  const runtime = api as unknown as {
    config?: {
      models?: {
        providers?: Record<string, unknown>;
      };
    };
  };
  const providers = runtime.config?.models?.providers;
  if (!providers || typeof providers !== "object") return null;

  const candidates = Object.entries(providers)
    .map(([name, value]) => {
      const record =
        value && typeof value === "object"
          ? value as Record<string, unknown>
          : {};
      const authMode =
        typeof record.mode === "string"
          ? record.mode
          : typeof record.type === "string"
            ? record.type
            : undefined;
      const provider = normalizeDirectProvider(
        typeof record.provider === "string" ? record.provider : name,
        authMode,
      );
      return {
        provider,
        apiKey: extractOpenClawProfileCredential(record as OpenClawAuthProfile, provider),
      };
    })
    .filter((entry): entry is InheritedLlmConfig => {
      return (
        !!entry.provider &&
        typeof entry.apiKey === "string" &&
        entry.apiKey.length > 0
      );
    });

  if (preferredProvider) {
    const preferred = candidates.find((entry) => entry.provider === preferredProvider);
    if (preferred) return preferred;
  }

  return candidates[0] ?? null;
}

/**
 * Refresh a Firebase ID token using the stored refresh_token.
 * Writes the updated id_token + expires_at back to the auth file.
 * Returns the new token, or null on failure.
 */
async function refreshFirebaseToken(
  auth: KumihoAuthentication,
  authPath: string,
): Promise<string | null> {
  if (!auth.refresh_token || !auth.api_key) return null;
  try {
    const url = `https://securetoken.googleapis.com/v1/token?key=${auth.api_key}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `grant_type=refresh_token&refresh_token=${encodeURIComponent(auth.refresh_token)}`,
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      id_token?: string;
      refresh_token?: string;
      expires_in?: string;
    };
    if (!data.id_token) return null;
    const expiresIn = parseInt(data.expires_in ?? "3600", 10);
    const updated: KumihoAuthentication = {
      ...auth,
      id_token: data.id_token,
      refresh_token: data.refresh_token ?? auth.refresh_token,
      expires_at: Math.floor(Date.now() / 1000) + expiresIn,
    };
    writeFileSync(authPath, JSON.stringify(updated, null, 2), "utf8");
    return data.id_token;
  } catch {
    return null;
  }
}

/**
 * Return a fresh auth token, refreshing the Firebase ID token via the
 * securetoken endpoint if the stored id_token is expired.
 */
async function getAuthToken(): Promise<string> {
  try {
    if (!existsSync(_AUTH_PATH)) return "";
    const auth = JSON.parse(readFileSync(_AUTH_PATH, "utf8")) as KumihoAuthentication;
    const now = Math.floor(Date.now() / 1000);

    if (auth.api_token) {
      if (!auth.api_token_expires_at || auth.api_token_expires_at > now) {
        return auth.api_token;
      }
    }

    if (auth.id_token) {
      if (!auth.expires_at || auth.expires_at > now) {
        return auth.id_token;
      }
      const refreshed = await refreshFirebaseToken(auth, _AUTH_PATH);
      if (refreshed) return refreshed;
    }
  } catch { /* ignore */ }
  return "";
}

import { KumihoClient, KumihoApiError, createTransport, McpTransport, type Transport } from "./client.js";
import { McpBridgeError, type McpToolDefinition } from "./mcp-bridge.js";
import { PIIRedactor } from "./privacy.js";
import { ArtifactManager } from "./artifacts.js";
import {
  autoRecall,
  autoCapture,
  consolidateSession,
  prefetchMemories,
  recordUserTurn,
  createHookState,
  type HookState,
} from "./hooks.js";
import {
  TOOL_SCHEMAS,
  TOOL_HANDLERS,
  type ToolContext,
} from "./tools.js";
import { normalizeConfiguredLlmProvider } from "./llm.js";
import { generateSessionId } from "./session.js";
import { ensureUserIdentity } from "./identity.js";
import type {
  KumihoPluginConfig,
  ResolvedConfig,
  ChannelInfo,
  DreamStateStats,
} from "./types.js";

// ---------------------------------------------------------------------------
// Re-exports for consumers
// ---------------------------------------------------------------------------

export { KumihoClient, KumihoApiError, createTransport, type Transport } from "./client.js";
export { McpBridge, McpBridgeError, type McpToolDefinition } from "./mcp-bridge.js";
export { PIIRedactor } from "./privacy.js";
export { ArtifactManager } from "./artifacts.js";
export { generateSessionId, getMemorySpace, inferChannelType } from "./session.js";
export { ensureUserIdentity, type UserIdentity } from "./identity.js";
export { TOOL_SCHEMAS, TOOL_HANDLERS } from "./tools.js";
export type {
  KumihoPluginConfig,
  ResolvedConfig,
  KumihoLocalConfig,
  MemoryEntry,
  MemoryType,
  MemoryScope,
  ChatMessage,
  ChannelInfo,
  ArtifactPointer,
  DreamStateStats,
  CreativeKind,
  CreativeCaptureParams,
  CreativeCaptureResult,
  CreativeItem,
} from "./types.js";

// ---------------------------------------------------------------------------
// Config resolution
// ---------------------------------------------------------------------------

function resolveConfig(
  raw: KumihoPluginConfig,
  inheritedHostLlm?: InheritedLlmConfig | null,
): ResolvedConfig {
  const mode = raw.mode ?? "local";

  const apiKey =
    raw.apiKey || process.env.KUMIHO_API_TOKEN || process.env.KUMIHO_API_KEY || loadAuthToken();

  // Resolve the LLM key for Dream State / consolidation from the OpenClaw
  // host runtime when available, otherwise from auth-profiles.json.
  let hostLlmApiKey = "";
  let hostLlmProvider = "";
  const preferredProvider = getPreferredLlmProvider(raw);
  const inheritedLlm = inheritedHostLlm ?? loadOpenClawAuthProfile(preferredProvider);
  if (inheritedLlm) {
    hostLlmApiKey = inheritedLlm.apiKey;
    hostLlmProvider = inheritedLlm.provider;
  }

  // Cloud mode requires an API key. Local mode does not (Python SDK handles auth).
  if (mode === "cloud" && !apiKey) {
    throw new Error(
      "Kumiho API key is required for cloud mode. " +
        "Set apiKey in plugin config or KUMIHO_API_TOKEN env var, " +
        'or switch to mode: "local" to use the Python SDK directly.',
    );
  }

  const endpoint =
    raw.endpoint || process.env.KUMIHO_ENDPOINT || "https://api.kumiho.cloud";

  // BFF defaults to the Kumiho Cloud API endpoint in all modes
  const bffEndpoint =
    raw.bffEndpoint ||
    process.env.KUMIHO_BFF_ENDPOINT ||
    endpoint;

  // Fall back to ~/.kumiho/preferences.json written by kumiho-setup
  const prefs = loadPreferences();

  return {
    mode,
    apiKey,
    endpoint,
    bffEndpoint,
    project: raw.project || "CognitiveMemory",
    userId: raw.userId || "default",
    autoCapture: raw.autoCapture ?? true,
    autoRecall: raw.autoRecall ?? true,
    localSummarization: raw.localSummarization ?? true,
    consolidationThreshold: raw.consolidationThreshold ?? 20,
    idleConsolidationTimeout: raw.idleConsolidationTimeout ?? 300,
    sessionTtl: raw.sessionTtl ?? 3600,
    topK: raw.topK ?? 5,
    searchThreshold: raw.searchThreshold ?? 0.3,
    artifactDir:
      raw.artifactDir || process.env.KUMIHO_MEMORY_ARTIFACT_ROOT || join(homedir(), ".kumiho", "artifacts"),
    piiRedaction: raw.piiRedaction ?? true,
    dreamStateSchedule: raw.dreamStateSchedule ?? prefs.dreamState?.schedule ?? "",
    dreamStateModel: raw.dreamStateModel ?? (prefs.dreamState?.model as import("./types.js").KumihoLLMConfig | undefined) ?? {},
    consolidationModel: raw.consolidationModel ?? (prefs.consolidation?.model as import("./types.js").KumihoLLMConfig | undefined) ?? {},
    llm: raw.llm ?? {},
    hostLlmApiKey,
    hostLlmProvider,
    privacy: {
      uploadSummariesOnly: raw.privacy?.uploadSummariesOnly ?? true,
      localArtifacts: raw.privacy?.localArtifacts ?? true,
      storeTranscriptions: raw.privacy?.storeTranscriptions ?? true,
    },
    local: {
      pythonPath: raw.local?.pythonPath ?? "python",
      command: raw.local?.command ?? "kumiho-mcp",
      timeout: raw.local?.timeout ?? 30_000,
      args: raw.local?.args,
      env: raw.local?.env,
      cwd: raw.local?.cwd,
    },
  };
}

// ---------------------------------------------------------------------------
// Plugin registration
//
// OpenClaw plugin lifecycle:
//   1. Discovery: scans package.json for openclaw.extensions
//   2. Validation: config schema validated against openclaw.plugin.json
//   3. Initialization: register() called with plugin API
//   4. Runtime: hooks fire on agent lifecycle events
//   5. Shutdown: service stop() called
// ---------------------------------------------------------------------------

// Use the real OpenClaw plugin API type from the SDK.
// This ensures we never drift from the actual interface.
import type { OpenClawPluginApi as PluginAPI } from "openclaw/plugin-sdk";

// ---------------------------------------------------------------------------
// Plugin state (singleton per gateway lifecycle)
// ---------------------------------------------------------------------------

let transport: Transport | null = null;
let client: KumihoClient | null = null;
let config: ResolvedConfig | null = null;
let redactor: PIIRedactor | null = null;
let artifacts: ArtifactManager | null = null;
let hookState: HookState | null = null;
let startPromise: Promise<void> | null = null;
let runtimeStatusLogged = false;

// Idle consolidation timer — armed after each agent_end, cancelled when the
// user sends another message. Fires consolidation when the session goes quiet.
let idleTimer: ReturnType<typeof setTimeout> | null = null;

function clearIdleTimer() {
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

function clearDreamStateTimer() {
  if (dreamStateTimer !== null) {
    clearTimeout(dreamStateTimer);
    dreamStateTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Dream State scheduler
// Parses a cron expression (wizard presets only) and sets a recursive
// setTimeout so Dream State fires on schedule without any OS-level cron.
// ---------------------------------------------------------------------------

let dreamStateTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Compute milliseconds until the next run for a cron expression.
 * Handles the patterns produced by kumiho-setup:
 *   "0 H * * *"    — daily at hour H
 *   "0 H * * W"    — weekly on weekday W (0=Sun) at hour H
 *   "0 * /N * * *"  — every N hours (interval pattern)
 * Returns -1 for "off" / unrecognised / disabled.
 */
function msUntilNextCron(cron: string): number {
  if (!cron || cron === "off") return -1;

  const now = new Date();
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return -1;

  const [, hourPart, , , weekdayPart] = parts;

  // Every N hours: "0 */N * * *"
  const everyHour = hourPart.match(/^\*\/(\d+)$/);
  if (everyHour) {
    const interval = parseInt(everyHour[1], 10);
    if (!interval) return -1;
    const intervalMs = interval * 60 * 60 * 1000;
    const currentHour = now.getHours();
    const nextHour = Math.ceil((currentHour + 1) / interval) * interval;
    const next = new Date(now);
    next.setMinutes(0, 0, 0);
    if (nextHour >= 24) {
      next.setDate(next.getDate() + 1);
      next.setHours(0);
    } else {
      next.setHours(nextHour);
    }
    const ms = next.getTime() - now.getTime();
    return ms > 0 ? ms : intervalMs;
  }

  const hour = parseInt(hourPart, 10);
  if (isNaN(hour)) return -1;

  // Weekly: "0 H * * W"
  if (weekdayPart !== "*") {
    const targetDay = parseInt(weekdayPart, 10);
    if (isNaN(targetDay)) return -1;
    const next = new Date(now);
    next.setHours(hour, 0, 0, 0);
    const daysUntil = (targetDay - now.getDay() + 7) % 7 || 7;
    next.setDate(next.getDate() + daysUntil);
    const ms = next.getTime() - now.getTime();
    return ms > 0 ? ms : 7 * 24 * 60 * 60 * 1000;
  }

  // Daily: "0 H * * *"
  const next = new Date(now);
  next.setHours(hour, 0, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}

// ---------------------------------------------------------------------------
// LLM key resolution for Dream State / consolidation
// ---------------------------------------------------------------------------

/**
 * Resolve the LLM model config for Dream State, falling back through:
 *   1. Explicit `dreamStateModel.apiKey` (from plugin config or preferences)
 *   2. General `llm.apiKey` on the plugin
 *   3. Inherited host/runtime LLM credentials
 *
 * Step 3 is the normal zero-config path when the plugin can inherit the same
 * LLM credentials OpenClaw or the deployment runtime already uses.
 */
function resolveDreamStateModelConfig(
  cfg: ResolvedConfig,
): { provider?: string; model?: string; apiKey?: string } | undefined {
  const dm = cfg.dreamStateModel ?? {};
  const explicitProvider = normalizeConfiguredLlmProvider(dm.provider || cfg.llm.provider);
  const explicitApiKey = dm.apiKey || cfg.llm.apiKey;
  if (
    explicitProvider &&
    !explicitApiKey &&
    cfg.hostLlmProvider &&
    explicitProvider !== cfg.hostLlmProvider
  ) {
    return undefined;
  }

  const provider = explicitProvider || cfg.hostLlmProvider;
  const model = dm.model || cfg.llm.model;
  const apiKey = explicitApiKey || cfg.hostLlmApiKey;
  if (!provider || !apiKey) return undefined;

  return {
    provider,
    model,
    apiKey,
  };
}

function resolveSessionLlmConfig(
  cfg: ResolvedConfig,
): { provider?: string; model?: string; apiKey?: string } | undefined {
  const cm = cfg.consolidationModel ?? {};
  const explicitProvider = normalizeConfiguredLlmProvider(cm.provider || cfg.llm.provider);
  const explicitApiKey = cm.apiKey || cfg.llm.apiKey;
  if (
    explicitProvider &&
    !explicitApiKey &&
    cfg.hostLlmProvider &&
    explicitProvider !== cfg.hostLlmProvider
  ) {
    return undefined;
  }

  const provider = explicitProvider || cfg.hostLlmProvider;
  const model = cm.model || cfg.llm.model;
  const apiKey = explicitApiKey || cfg.hostLlmApiKey;
  if (!provider || !apiKey) return undefined;

  return {
    provider,
    model,
    apiKey,
  };
}

function buildLlmEnv(
  modelConfig?: { provider?: string; model?: string; apiKey?: string },
): Record<string, string> {
  if (!modelConfig?.provider || !modelConfig.apiKey) {
    return {};
  }

  const env: Record<string, string> = {
    KUMIHO_LLM_PROVIDER: modelConfig.provider,
    KUMIHO_LLM_API_KEY: modelConfig.apiKey,
  };

  if (modelConfig.model) {
    env.KUMIHO_LLM_MODEL = modelConfig.model;
    if (/codex/i.test(modelConfig.model)) {
      env.KUMIHO_LLM_LIGHT_MODEL = modelConfig.model;
    }
  }

  if (modelConfig.provider === "openai") {
    env.OPENAI_API_KEY = modelConfig.apiKey;
  } else if (modelConfig.provider === "anthropic") {
    env.ANTHROPIC_API_KEY = modelConfig.apiKey;
  }

  return env;
}

let baseLocalEnv: Record<string, string> = {};
let resolveLatestHostLlm: (() => InheritedLlmConfig | null) | null = null;

function envMapsEqual(
  left: Record<string, string> | undefined,
  right: Record<string, string> | undefined,
): boolean {
  const leftEntries = Object.entries(left ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right ?? {}).sort(([a], [b]) => a.localeCompare(b));
  if (leftEntries.length !== rightEntries.length) return false;
  return leftEntries.every(([key, value], index) => {
    const [otherKey, otherValue] = rightEntries[index] ?? [];
    return key === otherKey && value === otherValue;
  });
}

function buildManagedLocalEnv(cfg: ResolvedConfig): Record<string, string> {
  if (cfg.mode !== "local") return {};

  const env = buildLlmEnv(resolveSessionLlmConfig(cfg));
  if (cfg.artifactDir) {
    env.KUMIHO_MEMORY_ARTIFACT_ROOT = cfg.artifactDir;
  }
  return env;
}

function syncLocalProcessEnv(cfg: ResolvedConfig): boolean {
  if (cfg.mode !== "local") return false;

  const desiredEnv = {
    ...buildManagedLocalEnv(cfg),
    ...baseLocalEnv,
  };

  const currentEnv = cfg.local.env ?? (cfg.local.env = {});
  if (envMapsEqual(currentEnv, desiredEnv)) return false;

  for (const key of Object.keys(currentEnv)) {
    delete currentEnv[key];
  }
  for (const [key, value] of Object.entries(desiredEnv)) {
    currentEnv[key] = value;
  }

  return true;
}

function refreshResolvedHostLlmConfig(
  target: ResolvedConfig | null | undefined,
): { credentialsChanged: boolean; envChanged: boolean } {
  if (!target) {
    return { credentialsChanged: false, envChanged: false };
  }

  const latest = resolveLatestHostLlm?.() ?? null;
  const nextProvider = latest?.provider ?? "";
  const nextApiKey = latest?.apiKey ?? "";
  const credentialsChanged =
    target.hostLlmProvider !== nextProvider ||
    target.hostLlmApiKey !== nextApiKey;

  target.hostLlmProvider = nextProvider;
  target.hostLlmApiKey = nextApiKey;

  return {
    credentialsChanged,
    envChanged: syncLocalProcessEnv(target),
  };
}

/**
 * Run Dream State via the standalone Python CLI as a fallback.
 * Spawns `python -m kumiho_memory dream` using the resolved Python path.
 */
function execDreamStateCli(
  cfg: ResolvedConfig,
  logger: { info: (m: string) => void; warn: (m: string) => void },
): Promise<DreamStateStats | null> {
  return new Promise((resolve) => {
    const pythonPath = cfg.local.pythonPath;
    const args = ["-m", "kumiho_memory", "dream", "--project", cfg.project];

    logger.info(`Kumiho Dream State fallback: ${pythonPath} ${args.join(" ")}`);

    // Inject model config as env vars for the CLI subprocess
    const env = { ...process.env };
    Object.assign(env, buildLlmEnv(resolveDreamStateModelConfig(cfg)));

    execFile(pythonPath, args, { timeout: 300_000, env }, (err, stdout, stderr) => {
      if (err) {
        logger.warn(`Dream State CLI failed: ${err.message}`);
        if (stderr) logger.warn(`  stderr: ${stderr.trim()}`);
        resolve(null);
        return;
      }
      try {
        resolve(JSON.parse(stdout) as DreamStateStats);
      } catch {
        logger.info(`Dream State CLI output: ${stdout.trim()}`);
        resolve(null);
      }
    });
  });
}

function scheduleDreamState(
  kumihoClient: KumihoClient,
  cfg: ResolvedConfig,
  logger: { info: (m: string) => void; warn: (m: string) => void },
) {
  const ms = msUntilNextCron(cfg.dreamStateSchedule);
  if (ms < 0) return; // schedule disabled

  const nextRun = new Date(Date.now() + ms);
  logger.info(
    `Kumiho Dream State scheduled: ${cfg.dreamStateSchedule} — next run at ${nextRun.toLocaleString()}`,
  );

  dreamStateTimer = setTimeout(async () => {
    dreamStateTimer = null;
    try {
      const modelConfig = resolveDreamStateModelConfig(cfg);
      const stats = await kumihoClient.triggerDreamState(modelConfig);
      logger.info(
        `Kumiho Dream State complete — ${stats.events_processed} events, ` +
          `${stats.edges_created} edges, ${stats.deprecated} deprecated`,
      );
    } catch (err) {
      logger.warn(
        `Kumiho Dream State MCP call failed: ${(err as Error).message} — trying standalone CLI...`,
      );
      // Fallback: run dream state via the standalone Python CLI
      const stats = await execDreamStateCli(cfg, logger);
      if (stats) {
        logger.info(
          `Kumiho Dream State (CLI) complete — ${stats.events_processed} events, ` +
            `${stats.edges_created} edges, ${stats.deprecated} deprecated`,
        );
      }
    }
    // Reschedule for next occurrence
    scheduleDreamState(kumihoClient, cfg, logger);
  }, ms);
}

function ensureInitialized(): {
  transport: Transport;
  client: KumihoClient;
  config: ResolvedConfig;
  redactor: PIIRedactor;
  artifacts: ArtifactManager;
  hookState: HookState;
} {
  if (!transport || !client || !config || !redactor || !artifacts || !hookState) {
    throw new Error("Kumiho plugin not initialized. Check your configuration.");
  }
  return { transport, client, config, redactor, artifacts, hookState };
}

async function ensureRuntimeStarted(
  logger: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void },
): Promise<ReturnType<typeof ensureInitialized>> {
  const state = ensureInitialized();
  let didStart = false;

  if (state.config.mode === "local") {
    const { envChanged } = refreshResolvedHostLlmConfig(state.config);
    let isRunning = await state.transport.ping().catch(() => false);

    if (envChanged && isRunning) {
      logger.info("Restarting kumiho-mcp subprocess to apply refreshed local credentials");
      await state.transport.close?.();
      runtimeStatusLogged = false;
      isRunning = false;
    }

    if (!isRunning) {
      if (startPromise === null) {
        startPromise = (async () => {
          if (state.transport instanceof McpTransport) {
            try {
              const authToken = await getAuthToken();
              state.transport.addEnv({ KUMIHO_AUTH_TOKEN: authToken });
            } catch (err) {
              logger.warn(`Could not inject KUMIHO_AUTH_TOKEN: ${(err as Error).message}`);
            }

            if (state.config.local.env && Object.keys(state.config.local.env).length > 0) {
              state.transport.addEnv(state.config.local.env);
            }
          }

          logger.info(
            `Starting kumiho-mcp subprocess (${state.config.local.command})...`,
          );
          await state.transport.start?.();
          logger.info("kumiho-mcp subprocess started and MCP handshake complete");
        })().finally(() => {
          startPromise = null;
        });
      }

      await startPromise;
      didStart = true;
    }
  }

  if (!state.hookState.sessionId) {
    state.hookState.sessionId = await generateSessionId(state.config.userId);
  }

  if (!runtimeStatusLogged || didStart) {
    const healthy = await state.client.ping();
    if (healthy) {
      logger.info(
        state.config.mode === "local"
          ? "kumiho-mcp local bridge connected"
          : "Kumiho Cloud connection verified",
      );
    } else {
      logger.warn(
        state.config.mode === "local"
          ? "kumiho-mcp process not responding"
          : "Kumiho Cloud unreachable - memories will be queued for later sync",
      );
    }
    runtimeStatusLogged = true;
  }

  if (dreamStateTimer === null) {
    scheduleDreamState(state.client, state.config, logger);
  }

  return state;
}

// ---------------------------------------------------------------------------
// Plugin export
// ---------------------------------------------------------------------------

export default {
  id: "openclaw-kumiho",
  name: "Kumiho Cognitive Memory",

  register(api: PluginAPI) {
    // -----------------------------------------------------------------------
    // 1. Resolve configuration
    // -----------------------------------------------------------------------

    const rawConfig = (api.pluginConfig ?? {}) as KumihoPluginConfig;
    const preferredHostLlmProvider = getPreferredLlmProvider(rawConfig);
    const resolveInheritedHostLlm = () =>
      loadHostLlmFromPluginApi(api, preferredHostLlmProvider) ||
      loadOpenClawAuthProfile(preferredHostLlmProvider);
    resolveLatestHostLlm = resolveInheritedHostLlm;
    const inheritedHostLlm = resolveInheritedHostLlm();

    try {
      config = resolveConfig(rawConfig, inheritedHostLlm);
    } catch (err) {
      api.logger.error(`Kumiho: ${(err as Error).message}`);
      return;
    }

    baseLocalEnv = { ...(config.local.env ?? {}) };
    syncLocalProcessEnv(config);

    const refreshResolvedHostLlm = (target: ResolvedConfig | null | undefined = config) => {
      refreshResolvedHostLlmConfig(target);
    };

    if (
      config.localSummarization &&
      !config.consolidationModel.apiKey &&
      !config.llm.apiKey &&
      !config.hostLlmApiKey
    ) {
      api.logger.warn(
        "Kumiho: no host LLM credentials were resolved from " +
          "api.config.models.providers or " +
          "~/.openclaw/agents/main/agent/auth-profiles.json. " +
          "Session consolidation will fall back to the static " +
          "\"Consolidated N messages...\" summary until the plugin can access " +
          "the OpenClaw host LLM configuration.",
      );
    }

    const dreamProviderHint =
      normalizeConfiguredLlmProvider(config.dreamStateModel.provider || config.llm.provider);
    if (
      dreamProviderHint &&
      !config.dreamStateModel.apiKey &&
      !config.llm.apiKey &&
      config.hostLlmProvider &&
      dreamProviderHint !== config.hostLlmProvider
    ) {
      api.logger.warn(
        `Kumiho: dreamStateModel/llm provider "${dreamProviderHint}" does not match ` +
          `the available host credential provider "${config.hostLlmProvider}". ` +
          "Dream State will stay disabled unless OpenClaw exposes a matching host " +
          "provider or you configure a matching model credential explicitly.",
      );
    }

    // Create transport (HTTP for cloud, MCP stdio for local)
    transport = createTransport(config, api.logger);
    client = new KumihoClient(transport, config.project);
    redactor = new PIIRedactor();
    artifacts = new ArtifactManager(config.artifactDir);
    hookState = createHookState();

    api.logger.info(
      `Kumiho memory initialized (mode: ${config.mode}, project: ${config.project}, ` +
        `autoRecall: ${config.autoRecall}, autoCapture: ${config.autoCapture})`,
    );

    // -----------------------------------------------------------------------
    // 2. Register agent tools
    // -----------------------------------------------------------------------

    const toolCtx: ToolContext = {
      client,
      config,
      get currentSessionId() {
        return hookState?.sessionId ?? null;
      },
      logger: api.logger,
      getToken: () => getAuthToken(),
    };

    // Custom TypeScript tool names (these have value-add logic and take priority)
    const customToolNames = new Set(Object.keys(TOOL_HANDLERS));

    api.registerGatewayMethod("kumiho.tools.list", ({ respond }) => {
      // Custom TypeScript tools (memory convenience wrappers)
      const customTools = Object.entries(TOOL_SCHEMAS).map(([name, schema]) => ({
        name,
        ...schema,
      }));

      // MCP-discovered tools from the Python backend (available after service start)
      const mcpTools: Array<{ name: string; description?: string; parameters?: unknown }> =
        (client?.getDiscoveredTools() ?? [])
          .filter((t: McpToolDefinition) => !customToolNames.has(t.name))
          .map((t: McpToolDefinition) => ({
            name: t.name,
            description: t.description,
            parameters: t.inputSchema,
          }));

      respond(true, { tools: [...customTools, ...mcpTools] });
    });

    for (const [name, handler] of Object.entries(TOOL_HANDLERS)) {
      api.registerGatewayMethod(
        `kumiho.tool.${name}`,
        async ({ respond, params }) => {
          try {
            await ensureRuntimeStarted(api.logger);
            const result = await handler(toolCtx, params ?? {});
            respond(true, { result });
          } catch (err) {
            const msg =
              err instanceof KumihoApiError
                ? `Kumiho API error (${err.code}): ${err.message}`
                : err instanceof McpBridgeError
                  ? `Kumiho MCP error (${err.code}): ${err.message}`
                  : `Tool error: ${(err as Error).message}`;
            api.logger.error(msg);
            respond(false, { error: msg });
          }
        },
      );

      // Also register as an LLM-visible tool so the agent can invoke it
      const schema = TOOL_SCHEMAS[name as keyof typeof TOOL_SCHEMAS];
      if (schema) {
        api.registerTool({
          name,
          label: schema.description,
          description: schema.description,
          parameters: schema.parameters,
          async execute(_toolCallId: string, params: Record<string, unknown>) {
            await ensureRuntimeStarted(api.logger);
            const result = await handler(toolCtx, params ?? {});
            const text = typeof result === "string" ? result : JSON.stringify(result);
            return { content: [{ type: "text" as const, text }], details: result };
          },
        });
      }
    }

    // -----------------------------------------------------------------------
    // 3. Register CLI commands
    // -----------------------------------------------------------------------

    api.registerCli(
      ({ program }) => {
        program
          .command("search")
          .description("Search Kumiho long-term memory")
          .argument("<query>", "Natural language search query")
          .option("--scope <scope>", "Scope: session, long-term, all")
          .option("--limit <n>", "Max results")
          .action(async (...args: unknown[]) => {
            const query = args[0] as string;
            const opts = args[1] as Record<string, string>;
            const state = await ensureRuntimeStarted(api.logger);
            const result = await TOOL_HANDLERS.memory_search(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              {
                query,
                scope: opts.scope,
                limit: opts.limit ? parseInt(opts.limit) : undefined,
              },
            );
            console.log(result);
          });

        program
          .command("stats")
          .description("Show Kumiho memory statistics")
          .action(async () => {
            const state = await ensureRuntimeStarted(api.logger);
            const healthy = await state.client.ping();
            const sessionInfo = state.hookState.sessionId
              ? await state.client.chatGet(state.hookState.sessionId, 1).catch(() => null)
              : null;

            console.log(`Kumiho Memory Status`);
            console.log(`  Mode: ${state.config.mode}`);
            console.log(`  Backend: ${healthy ? "connected" : "unreachable"}`);
            console.log(`  Project: ${state.config.project}`);
            console.log(`  User: ${state.config.userId}`);
            console.log(`  Session: ${state.hookState.sessionId ?? "none"}`);
            if (sessionInfo) {
              console.log(`  Messages: ${sessionInfo.message_count}`);
              console.log(`  TTL: ${sessionInfo.ttl_remaining}s`);
            }
            console.log(`  Auto-Recall: ${state.config.autoRecall}`);
            console.log(`  Auto-Capture: ${state.config.autoCapture}`);
            console.log(`  PII Redaction: ${state.config.piiRedaction}`);
            console.log(`  Artifact Dir: ${state.config.artifactDir}`);
          });

        program
          .command("consolidate")
          .description("Consolidate current session into long-term memory")
          .action(async () => {
            const state = await ensureRuntimeStarted(api.logger);
            const result = await TOOL_HANDLERS.memory_consolidate(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              {},
            );
            console.log(result);
          });

        program
          .command("dream")
          .description("Trigger Dream State memory maintenance")
          .action(async () => {
            const state = await ensureRuntimeStarted(api.logger);
            const result = await TOOL_HANDLERS.memory_dream(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              {},
            );
            console.log(result);
          });

        program
          .command("capture")
          .description("Capture last response as a creative output")
          .argument("<title>", "Title for the creative artifact")
          .argument("<project>", "Project space slug")
          .option("--kind <kind>", "Creative kind (document|code|design|plan|analysis|other)")
          .action(async (...args: unknown[]) => {
            const title = args[0] as string;
            const project = args[1] as string;
            const opts = args[2] as Record<string, string>;
            const state = await ensureRuntimeStarted(api.logger);
            const content =
              state.hookState.lastAssistantResponse?.trim() ||
              `Content captured via CLI on ${new Date().toISOString().slice(0, 10)}`;
            const result = await TOOL_HANDLERS.creative_capture(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              { title, content, project, kind: opts.kind ?? "document" },
            );
            console.log(result);
          });

        program
          .command("project")
          .description("List creative outputs for a project")
          .argument("<project>", "Project space slug")
          .option("--query <query>", "Search query")
          .option("--kind <kind>", "Filter by kind")
          .action(async (...args: unknown[]) => {
            const project = args[0] as string;
            const opts = args[1] as Record<string, string>;
            const state = await ensureRuntimeStarted(api.logger);
            const result = await TOOL_HANDLERS.creative_recall(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              { space: project, query: opts.query, kind: opts.kind },
            );
            console.log(result);
          });
      },
      { commands: ["search", "stats", "consolidate", "dream", "capture", "project"] },
    );

    // -----------------------------------------------------------------------
    // 4. Register auto-reply commands
    // -----------------------------------------------------------------------

    api.registerCommand({
      name: "memory",
      description: "Kumiho memory commands: search, stats, consolidate",
      requireAuth: true,
      acceptsArgs: true,
      handler: (ctx) => {
        const args = ctx.args?.trim() ?? "";

        if (args === "stats" || args === "") {
          const state = ensureInitialized();
          return {
            text:
              `Kumiho Memory (${state.config.mode} mode)\n` +
              `Project: ${state.config.project}\n` +
              `Session: ${state.hookState.sessionId ?? "none"}\n` +
              `Messages: ${state.hookState.messageCount}\n` +
              `Auto-Recall: ${state.config.autoRecall}\n` +
              `Auto-Capture: ${state.config.autoCapture}`,
          };
        }

        return {
          text: `Unknown memory command: ${args}. Try: stats, search <query>, consolidate`,
        };
      },
    });

    api.registerCommand({
      name: "capture",
      description:
        "Capture a creative output into the Kumiho graph. " +
        "Usage: /capture <title> | <project> [| <kind>]\n" +
        "Example: /capture Blog Draft | my-blog | document\n" +
        "Kinds: document, code, design, plan, analysis, other",
      requireAuth: true,
      acceptsArgs: true,
      handler: (ctx) => {
        const args = ctx.args?.trim() ?? "";
        if (!args) {
          return {
            text:
              "Usage: /capture <title> | <project> [| <kind>]\n" +
              "Example: /capture Blog Draft | my-blog | document\n" +
              "Available kinds: document, code, design, plan, analysis, other",
          };
        }

        const parts = args.split("|").map((s) => s.trim());
        const title = parts[0] ?? "";
        const project = parts[1] ?? "";
        const kind = parts[2] ?? "document";

        if (!title || !project) {
          return {
            text:
              "Both title and project are required.\n" +
              "Usage: /capture <title> | <project> [| <kind>]",
          };
        }

        // Use last assistant response as content, fall back to a placeholder
        void ensureRuntimeStarted(api.logger)
          .then((state) =>
            TOOL_HANDLERS.creative_capture(
              {
                client: state.client,
                config: state.config,
                currentSessionId: state.hookState.sessionId,
                logger: api.logger,
              },
              {
                title,
                content:
                  state.hookState.lastAssistantResponse?.trim() ||
                  `Content captured via /capture on ${new Date().toISOString().slice(0, 10)}`,
                project,
                kind,
              },
            ),
          )
          .then((result) => api.logger.info(`/capture: ${result}`))
          .catch((err) =>
            api.logger.error(`/capture failed: ${(err as Error).message}`),
          );

        return {
          text:
            `Capture queued: "${title}" → ${project} (${kind})\n` +
            `Processing in background. Use creative_recall to see it once done.`,
        };
      },
    });

    // -----------------------------------------------------------------------
    // 5. Register background service (manages process lifecycle)
    // -----------------------------------------------------------------------

    api.registerService({
      id: "kumiho-memory",
      async start(_ctx) {
        try {
          await ensureRuntimeStarted(api.logger);
        } catch (err) {
          api.logger.error(
            `Failed to start kumiho-mcp: ${(err as Error).message}. ` +
              `Run 'npx kumiho-setup' to install the Python backend, ` +
              `or set local.pythonPath in your openclaw.json config. ` +
              `Manual install: pip install "kumiho[mcp]" "kumiho-memory[all]"`,
          );
          return;
        }

        const state = ensureInitialized();

        // Register MCP-discovered tools as pass-through gateway methods
        const discovered = state.client.getDiscoveredTools();
        let passthroughCount = 0;
        for (const tool of discovered) {
          if (customToolNames.has(tool.name)) continue;

          const toolName = tool.name;
          api.registerGatewayMethod(
            `kumiho.tool.${toolName}`,
            async ({ respond, params }) => {
              try {
                await ensureRuntimeStarted(api.logger);
                const result = await state.client.callTool(toolName, params ?? {});
                respond(true, { result });
              } catch (err) {
                const msg =
                  err instanceof McpBridgeError
                    ? `MCP error (${err.code}): ${err.message}`
                    : `Tool error: ${(err as Error).message}`;
                api.logger.error(msg);
                respond(false, { error: msg });
              }
            },
          );
          passthroughCount++;
        }

        if (passthroughCount > 0) {
          api.logger.info(
            `Registered ${passthroughCount} MCP pass-through tools ` +
              `(${customToolNames.size} custom TypeScript handlers preserved)`,
          );
        }
      },
      async stop(_ctx) {
        startPromise = null;
        runtimeStatusLogged = false;
        resolveLatestHostLlm = null;
        baseLocalEnv = {};
        clearDreamStateTimer();
        clearIdleTimer();
        // In local mode, gracefully shut down the Python process
        if (transport?.close) {
          api.logger.info("Shutting down kumiho-mcp subprocess...");
          await transport.close();
        }
        api.logger.info("Kumiho memory service stopped");
      },
    });

    // -----------------------------------------------------------------------
    // 6. Register gateway methods for hook integration
    // -----------------------------------------------------------------------

    api.registerGatewayMethod(
      "kumiho.hooks.before_agent",
      async ({ respond, params }) => {
        if (!config?.autoRecall) {
          respond(true, { contextInjection: "" });
          return;
        }

        try {
          const state = await ensureRuntimeStarted(api.logger);

          // Use senderId from OpenClaw's hook event if provided; fall back to config.userId.
          // This scopes memory correctly per-user without any onboarding ceremony.
          const senderId = (params?.senderId as string | undefined) ?? state.config.userId;
          const userMessage = (params?.message as string | undefined) ?? state.hookState.lastUserMessage ?? "";

          // Re-scope session to this sender if it changed (e.g. new user in group channel)
          if (senderId && senderId !== state.config.userId) {
            state.hookState.sessionId = null; // will be regenerated with senderId
            state.config = { ...state.config, userId: senderId };
          }

          // On first message from this sender, silently bootstrap their identity profile
          // in Kumiho so it's available across all Kumiho-enabled platforms.
          if (senderId && !state.hookState.identityStoredFor.has(senderId)) {
            state.hookState.identityStoredFor.add(senderId);
            void ensureUserIdentity(state.client, state.config, senderId, {
              displayName: params?.displayName as string | undefined,
              platform: params?.platform as string | undefined,
              timezone: params?.timezone as string | undefined,
              locale: params?.locale as string | undefined,
            });
          }

          const recallResult = await autoRecall(
            state.client,
            state.config,
            state.hookState,
            userMessage,
          );
          respond(true, {
            contextInjection: recallResult.contextInjection,
            memoriesFound: recallResult.memories.length,
          });
        } catch (err) {
          api.logger.error(`Auto-recall failed: ${(err as Error).message}`);
          respond(true, { contextInjection: "" });
        }
      },
    );

    api.registerGatewayMethod(
      "kumiho.hooks.after_agent",
      async ({ respond, params }) => {
        if (!config?.autoCapture) {
          respond(true, { captured: false });
          return;
        }

        try {
          const state = await ensureRuntimeStarted(api.logger);
          refreshResolvedHostLlm(state.config);
          const assistantResponse = (params?.response as string | undefined) ?? state.hookState.lastAssistantResponse ?? "";
          const captureResult = await autoCapture(
            state.client,
            state.config,
            state.hookState,
            state.redactor,
            state.artifacts,
            assistantResponse,
          );
          respond(true, captureResult);
        } catch (err) {
          api.logger.error(`Auto-capture failed: ${(err as Error).message}`);
          respond(true, { captured: false });
        }
      },
    );

    // -----------------------------------------------------------------------
    // 7. Wire OpenClaw lifecycle events for automatic auto-recall / auto-capture
    // -----------------------------------------------------------------------

    api.on("before_prompt_build", async (event, _ctx) => {
      // User is active — cancel any pending idle consolidation
      clearIdleTimer();
      if (!config?.autoRecall) return;
      try {
        const state = await ensureRuntimeStarted(api.logger);
        const messages = (event.messages as Array<{ role: string; content: string }>) ?? [];
        const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
        const raw = lastUserMsg?.content;
        const message = typeof raw === "string"
          ? raw
          : Array.isArray(raw)
            ? (raw as Array<{ type: string; text?: string }>).filter((b) => b.type === "text").map((b) => b.text).join("\n")
            : state.hookState.lastUserMessage ?? "";

        let recallResult: { contextInjection: string };

        if (state.hookState.prefetchedRecall) {
          // Stale-while-revalidate: use prefetched result instantly (0ms latency),
          // then kick off a fresh background prefetch for the next turn.
          recallResult = state.hookState.prefetchedRecall;
          state.hookState.prefetchedRecall = null;
          // Store the user message and update state — prefetchMemories is read-only
          // and does not do this, so without this call every turn after the first
          // would silently drop the user message from the session buffer.
          await recordUserTurn(state.client, state.config, state.hookState, message);
          void prefetchMemories(state.client, state.config, message)
            .then((r) => { state.hookState.prefetchedRecall = r; })
            .catch(() => {});
        } else {
          // Cold start (first turn ever): wait for recall, 1500ms fallback.
          recallResult = await Promise.race([
            autoRecall(state.client, state.config, state.hookState, message),
            new Promise<{ contextInjection: string }>((resolve) =>
              setTimeout(() => resolve({ contextInjection: "" }), 1500),
            ),
          ]);
        }

        if (recallResult.contextInjection) {
          return { prependContext: recallResult.contextInjection };
        }
      } catch (err) {
        api.logger.error(`Auto-recall hook failed: ${(err as Error).message}`);
      }
    });

    api.on("agent_end", async (event, _ctx) => {
      if (!config?.autoCapture) return;
      try {
        const state = await ensureRuntimeStarted(api.logger);
        refreshResolvedHostLlm(state.config);
        const messages = (event.messages as Array<{ role: string; content: string }>) ?? [];
        const lastAssistantMsg = [...messages].reverse().find((m) => m.role === "assistant");
        const raw = lastAssistantMsg?.content;
        let response: string;
        if (typeof raw === "string") {
          response = raw;
        } else if (Array.isArray(raw)) {
          const blocks = raw as Array<{ type: string; text?: string; name?: string }>;
          const textParts = blocks.filter((b) => b.type === "text").map((b) => b.text ?? "");
          const toolParts = blocks.filter((b) => b.type === "tool_use").map((b) => `[tool: ${b.name}]`);
          response = textParts.join("\n").trim();
          if (!response && toolParts.length > 0) {
            response = `(Tool-only turn: ${toolParts.join(", ")})`;
          }
        } else {
          response = "";
        }
        await autoCapture(
          state.client,
          state.config,
          state.hookState,
          state.redactor,
          state.artifacts,
          response,
        );

        // Prefetch memories for the next turn while the user reads this response.
        // Result is stored in prefetchedRecall and consumed instantly by before_prompt_build.
        if (config?.autoRecall && state.hookState.lastUserMessage) {
          const queryForNext = state.hookState.lastUserMessage;
          void prefetchMemories(state.client, state.config, queryForNext)
            .then((r) => { state.hookState.prefetchedRecall = r; })
            .catch(() => {});
        }

        // Arm idle consolidation timer. If the user goes quiet for
        // idleConsolidationTimeout seconds, flush working memory to the graph.
        const idleMs = (config?.idleConsolidationTimeout ?? 0) * 1000;
        if (idleMs > 0 && state.hookState.messageCount > 0) {
          clearIdleTimer();
          // Capture references at arm-time so a second register() call
          // doesn't replace them with a fresh (empty) hookState.
          const capturedState = state;
          idleTimer = setTimeout(() => {
            idleTimer = null;
            if (capturedState.hookState.messageCount === 0) return;
            refreshResolvedHostLlm(capturedState.config);
            void consolidateSession(
              capturedState.client,
              capturedState.config,
              capturedState.hookState,
              capturedState.redactor,
              capturedState.artifacts,
            ).then((ok) => {
              if (ok) api.logger.info("Kumiho: idle consolidation complete");
            }).catch((err) => {
              api.logger.error(`Kumiho: idle consolidation failed: ${(err as Error).message}`);
            });
          }, idleMs);
        }
      } catch (err) {
        api.logger.error(`Auto-capture hook failed: ${(err as Error).message}`);
      }
    });

    // -----------------------------------------------------------------------
    // 8. Pre-compaction flush — persist working memory before context shrinks
    //
    // OpenClaw fires this hook just before the session context is compacted
    // (either via /compact or auto-compaction). We flush the Kumiho session
    // buffer to the graph so memories are not lost when the context window
    // is reduced. This is equivalent to what the host's memoryFlush agentic
    // turn does, but at the Kumiho graph layer.
    // -----------------------------------------------------------------------

    api.on("before_compaction", async (_event, _ctx) => {
      // Cancel any pending idle consolidation — we're doing a full flush now.
      clearIdleTimer();
      try {
        const state = await ensureRuntimeStarted(api.logger);
        if (state.hookState.messageCount === 0) return;
        refreshResolvedHostLlm(state.config);
        const ok = await consolidateSession(
          state.client,
          state.config,
          state.hookState,
          state.redactor,
          state.artifacts,
        );
        if (ok) {
          api.logger.info("Kumiho: pre-compaction memory flush complete");
        }
      } catch (err) {
        api.logger.error(`Kumiho: pre-compaction flush failed: ${(err as Error).message}`);
      }
    });
  },
};

// ---------------------------------------------------------------------------
// Standalone API (for programmatic use outside OpenClaw plugin system)
// ---------------------------------------------------------------------------

/**
 * Create a standalone Kumiho memory instance.
 *
 * Supports both modes:
 *
 * ```typescript
 * // Local mode (default) — uses kumiho-mcp Python subprocess
 * const memory = await createKumihoMemory({ mode: "local" });
 * await memory.start();
 *
 * // Cloud mode — uses Kumiho Cloud HTTPS API
 * const memory = await createKumihoMemory({
 *   mode: "cloud",
 *   apiKey: "kh_live_...",
 * });
 *
 * // Both modes share the same API
 * const recalled = await memory.recall("user preferences");
 * await memory.store("User prefers dark mode", { type: "fact" });
 *
 * // Clean up (important in local mode to stop the Python process)
 * await memory.close();
 * ```
 */
export function createKumihoMemory(rawConfig: KumihoPluginConfig = {}) {
  const cfg = resolveConfig(rawConfig);
  const tp = createTransport(cfg);
  const kumihoClient = new KumihoClient(tp, cfg.project);
  const piiRedactor = new PIIRedactor();
  const artifactMgr = new ArtifactManager(cfg.artifactDir);
  const state = createHookState();

  return {
    client: kumihoClient,
    config: cfg,

    /**
     * Start the memory system.
     * In local mode this spawns the kumiho-mcp Python process.
     * In cloud mode this is a no-op (HTTPS is stateless).
     */
    async start() {
      await kumihoClient.start();
    },

    /**
     * Shut down the memory system.
     * In local mode this stops the kumiho-mcp Python process.
     */
    async close() {
      await kumihoClient.close();
    },

    /** Search long-term memory. */
    async recall(query: string, limit?: number) {
      return kumihoClient.memoryRetrieve({
        query,
        limit: limit ?? cfg.topK,
      });
    },

    /** Store a fact/decision/summary in long-term memory. */
    async store(
      content: string,
      opts?: {
        type?: "fact" | "decision" | "summary";
        title?: string;
        topics?: string[];
        spaceHint?: string;
      },
    ) {
      let summary = content;
      if (cfg.piiRedaction) {
        const redacted = piiRedactor.redact(content);
        summary = piiRedactor.anonymizeSummary(redacted.text);
      }

      return kumihoClient.memoryStore({
        type: opts?.type ?? "fact",
        title: opts?.title ?? summary.slice(0, 60),
        summary,
        topics: opts?.topics,
        spaceHint: opts?.spaceHint,
      });
    },

    /** Add a message to working memory. */
    async addMessage(
      sessionId: string,
      role: "user" | "assistant",
      content: string,
    ) {
      return kumihoClient.chatAdd(sessionId, role, content);
    },

    /** Get messages from working memory. */
    async getMessages(sessionId: string, limit?: number) {
      return kumihoClient.chatGet(sessionId, limit);
    },

    /** Generate a session ID. */
    async newSession(userId?: string, context?: string) {
      return generateSessionId(userId ?? cfg.userId, context);
    },

    /** Auto-recall hook. */
    async autoRecallHook(userMessage: string, channel?: ChannelInfo) {
      return autoRecall(kumihoClient, cfg, state, userMessage, channel);
    },

    /** Auto-capture hook. */
    async autoCaptureHook(assistantResponse: string, channel?: ChannelInfo) {
      return autoCapture(
        kumihoClient,
        cfg,
        state,
        piiRedactor,
        artifactMgr,
        assistantResponse,
        channel,
      );
    },

    /** Trigger Dream State consolidation. */
    async dream() {
      return kumihoClient.triggerDreamState(resolveDreamStateModelConfig(cfg));
    },

    /** Store tool execution result. */
    async storeExecution(params: Parameters<typeof kumihoClient.storeToolExecution>[0]) {
      return kumihoClient.storeToolExecution(params);
    },

    /** Check backend connectivity. */
    async ping() {
      return kumihoClient.ping();
    },
  };
}
