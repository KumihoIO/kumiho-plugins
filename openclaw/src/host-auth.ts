import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { normalizeConfiguredLlmProvider } from "./llm.js";

export interface OpenClawAuthProfile {
  type?: string;
  provider?: string;
  token?: string;
  access?: string;
  refresh?: string;
  expires?: number;
  mode?: string;
  [key: string]: unknown;
}

export interface OpenClawAuthProfiles {
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

export interface OpenClawModelsConfig {
  providers?: Record<string, { baseUrl?: string; api?: string; [key: string]: unknown }>;
}

export type HostLlmProvider = "anthropic" | "openai";
export type HostAuthSelectionSource =
  | "preferred_last_good"
  | "preferred_last_used"
  | "last_good"
  | "last_used";
export type HostAuthCredentialStatus = "available" | "missing_credential" | "expired_oauth_token";
export type HostAuthDirectCallReason =
  | "direct"
  | "host_only_openai_oauth"
  | "host_only_chatgpt_backend";

interface HostAuthCandidate {
  profileKey: string;
  rawProvider: string;
  normalizedProvider: HostLlmProvider;
  authMode: string;
  credential: string;
  credentialStatus: HostAuthCredentialStatus;
  lastUsed: number;
  errorCount: number;
}

export interface HostAuthDetection {
  profileKey: string;
  rawProvider: string;
  normalizedProvider: HostLlmProvider;
  authMode: string;
  credential: string;
  credentialStatus: HostAuthCredentialStatus;
  directCallCapable: boolean;
  directCallReason: HostAuthDirectCallReason;
  selectedBy: HostAuthSelectionSource;
  lastUsed: number;
  errorCount: number;
  modelProviderKey?: string;
  modelBaseUrl?: string;
  modelApi?: string;
}

export interface DirectHostLlmConfig {
  provider: HostLlmProvider;
  apiKey: string;
  detection: HostAuthDetection;
}

export interface HostAuthPaths {
  authProfilesPath?: string;
  modelsPath?: string;
  nowMs?: number;
}

export const DEFAULT_OPENCLAW_AUTH_PROFILES_PATH = join(
  homedir(),
  ".openclaw",
  "agents",
  "main",
  "agent",
  "auth-profiles.json",
);

export const DEFAULT_OPENCLAW_MODELS_PATH = join(
  homedir(),
  ".openclaw",
  "agents",
  "main",
  "agent",
  "models.json",
);

function readJsonIfExists<T>(path: string): T | null {
  try {
    if (!existsSync(path)) return null;
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return null;
  }
}

function extractCredentialString(value: unknown, depth = 0): string {
  if (!value || typeof value !== "object" || depth > 2) return "";
  const record = value as Record<string, unknown>;
  for (const field of ["apiKey", "api_key", "key", "token"] as const) {
    const candidate = record[field];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  for (const nested of Object.values(record)) {
    const found = extractCredentialString(nested, depth + 1);
    if (found) return found;
  }
  return "";
}

function getProfileAuthMode(profile: OpenClawAuthProfile): string {
  if (typeof profile.mode === "string") return profile.mode;
  if (typeof profile.type === "string") return profile.type;
  return "";
}

export function normalizeHostAuthProvider(
  provider?: string,
  authMode?: string,
): HostLlmProvider | null {
  if (provider === "anthropic") return "anthropic";
  if (provider === "openai") return "openai";
  if (provider === "openai-codex" && (authMode === "token" || authMode === "oauth")) {
    return "openai";
  }
  return null;
}

function normalizePreferredHostProvider(preferredProvider?: string): HostLlmProvider | "" {
  const normalized = normalizeConfiguredLlmProvider(preferredProvider);
  if (normalized === "anthropic" || normalized === "openai") return normalized;
  return "";
}

function extractProfileCredential(
  profile: OpenClawAuthProfile,
  normalizedProvider: HostLlmProvider,
  nowMs: number,
): { credential: string; status: HostAuthCredentialStatus } {
  const authMode = getProfileAuthMode(profile);
  if (normalizedProvider === "openai" && authMode === "oauth") {
    const access = typeof profile.access === "string" ? profile.access.trim() : "";
    const expires = typeof profile.expires === "number" ? profile.expires : 0;
    if (!access) {
      return { credential: "", status: "missing_credential" };
    }
    if (expires && expires <= nowMs + 60_000) {
      return { credential: "", status: "expired_oauth_token" };
    }
    return { credential: access, status: "available" };
  }

  const credential = extractCredentialString(profile);
  return {
    credential,
    status: credential ? "available" : "missing_credential",
  };
}

function normalizeLastGood(
  lastGood: Record<string, string>,
  profiles: Record<string, OpenClawAuthProfile>,
): Record<HostLlmProvider, Set<string>> {
  const normalized: Record<HostLlmProvider, Set<string>> = {
    anthropic: new Set<string>(),
    openai: new Set<string>(),
  };

  for (const [providerKey, profileKey] of Object.entries(lastGood)) {
    const profile = profiles[profileKey];
    const authMode = profile ? getProfileAuthMode(profile) : "";
    const rawProvider =
      (profile && typeof profile.provider === "string" ? profile.provider : "") ||
      providerKey;
    const normalizedProvider = normalizeHostAuthProvider(rawProvider, authMode);
    if (normalizedProvider) {
      normalized[normalizedProvider].add(profileKey);
    }
  }

  return normalized;
}

function findModelProvider(
  models: OpenClawModelsConfig | null,
  candidate: HostAuthCandidate,
): { key: string; baseUrl?: string; api?: string } | null {
  const providers = models?.providers;
  if (!providers || typeof providers !== "object") return null;

  const keys = new Set<string>([
    candidate.rawProvider,
    candidate.profileKey.split(":")[0] ?? "",
    candidate.normalizedProvider,
  ]);

  for (const key of keys) {
    const entry = providers[key];
    if (entry && typeof entry === "object") {
      return {
        key,
        baseUrl: typeof entry.baseUrl === "string" ? entry.baseUrl : undefined,
        api: typeof entry.api === "string" ? entry.api : undefined,
      };
    }
  }

  return null;
}

function classifyDirectCallCapability(
  candidate: HostAuthCandidate,
  models: OpenClawModelsConfig | null,
): Pick<HostAuthDetection, "directCallCapable" | "directCallReason" | "modelProviderKey" | "modelBaseUrl" | "modelApi"> {
  const modelProvider = findModelProvider(models, candidate);
  const baseUrl = modelProvider?.baseUrl?.toLowerCase() ?? "";
  const api = modelProvider?.api?.toLowerCase() ?? "";

  if (candidate.normalizedProvider === "openai" && candidate.authMode === "oauth") {
    if (baseUrl.includes("chatgpt.com/backend-api") || api === "openai-codex-responses") {
      return {
        directCallCapable: false,
        directCallReason: "host_only_chatgpt_backend",
        modelProviderKey: modelProvider?.key,
        modelBaseUrl: modelProvider?.baseUrl,
        modelApi: modelProvider?.api,
      };
    }

    return {
      directCallCapable: false,
      directCallReason: "host_only_openai_oauth",
      modelProviderKey: modelProvider?.key,
      modelBaseUrl: modelProvider?.baseUrl,
      modelApi: modelProvider?.api,
    };
  }

  return {
    directCallCapable: true,
    directCallReason: "direct",
    modelProviderKey: modelProvider?.key,
    modelBaseUrl: modelProvider?.baseUrl,
    modelApi: modelProvider?.api,
  };
}

function rankCandidates(candidates: HostAuthCandidate[]): HostAuthCandidate[] {
  return [...candidates].sort((left, right) => {
    return right.lastUsed - left.lastUsed || left.errorCount - right.errorCount;
  });
}

export function selectOpenClawHostAuth(
  data: OpenClawAuthProfiles,
  models: OpenClawModelsConfig | null = null,
  preferredProvider?: string,
  nowMs = Date.now(),
): HostAuthDetection | null {
  const profiles = data.auth?.profiles ?? data.profiles ?? {};
  const lastGood = data.auth?.lastGood ?? data.lastGood ?? {};
  const usageStats = data.auth?.usageStats ?? data.usageStats ?? {};
  const preferred = normalizePreferredHostProvider(preferredProvider);

  const candidates = Object.entries(profiles)
    .map(([profileKey, profile]): HostAuthCandidate | null => {
      const authMode = getProfileAuthMode(profile);
      const rawProvider =
        typeof profile.provider === "string"
          ? profile.provider
          : profileKey.split(":")[0] ?? "";
      const normalizedProvider = normalizeHostAuthProvider(rawProvider, authMode);
      if (!normalizedProvider) return null;
      const credentialInfo = extractProfileCredential(profile, normalizedProvider, nowMs);
      return {
        profileKey,
        rawProvider,
        normalizedProvider,
        authMode,
        credential: credentialInfo.credential,
        credentialStatus: credentialInfo.status,
        lastUsed: usageStats[profileKey]?.lastUsed ?? 0,
        errorCount: usageStats[profileKey]?.errorCount ?? 0,
      };
    })
    .filter((candidate): candidate is HostAuthCandidate => candidate !== null);

  if (!candidates.length) return null;

  const normalizedLastGood = normalizeLastGood(lastGood, profiles);
  let selected: HostAuthCandidate | undefined;
  let selectedBy: HostAuthSelectionSource = "last_used";

  if (preferred) {
    const preferredLastGood = rankCandidates(
      candidates.filter(
        (candidate) =>
          candidate.normalizedProvider === preferred &&
          normalizedLastGood[preferred].has(candidate.profileKey),
      ),
    )[0];
    if (preferredLastGood) {
      selected = preferredLastGood;
      selectedBy = "preferred_last_good";
    }

    if (!selected) {
      const preferredLastUsed = rankCandidates(
        candidates.filter((candidate) => candidate.normalizedProvider === preferred),
      )[0];
      if (preferredLastUsed) {
        selected = preferredLastUsed;
        selectedBy = "preferred_last_used";
      }
    }
  }

  if (!selected) {
    const anyLastGood = rankCandidates(
      candidates.filter(
        (candidate) =>
          normalizedLastGood.anthropic.has(candidate.profileKey) ||
          normalizedLastGood.openai.has(candidate.profileKey),
      ),
    )[0];
    if (anyLastGood) {
      selected = anyLastGood;
      selectedBy = "last_good";
    }
  }

  if (!selected) {
    selected = rankCandidates(candidates)[0];
    selectedBy = "last_used";
  }

  if (!selected) return null;

  const capability = classifyDirectCallCapability(selected, models);
  return {
    profileKey: selected.profileKey,
    rawProvider: selected.rawProvider,
    normalizedProvider: selected.normalizedProvider,
    authMode: selected.authMode,
    credential: selected.credential,
    credentialStatus: selected.credentialStatus,
    selectedBy,
    lastUsed: selected.lastUsed,
    errorCount: selected.errorCount,
    ...capability,
  };
}

export function detectOpenClawHostAuth(
  preferredProvider?: string,
  paths: HostAuthPaths = {},
): HostAuthDetection | null {
  const authProfilesPath = paths.authProfilesPath ?? DEFAULT_OPENCLAW_AUTH_PROFILES_PATH;
  const modelsPath = paths.modelsPath ?? DEFAULT_OPENCLAW_MODELS_PATH;
  const nowMs = paths.nowMs ?? Date.now();

  const authProfiles = readJsonIfExists<OpenClawAuthProfiles>(authProfilesPath);
  if (!authProfiles) return null;
  const models = readJsonIfExists<OpenClawModelsConfig>(modelsPath);

  return selectOpenClawHostAuth(authProfiles, models, preferredProvider, nowMs);
}

export function resolveOpenClawDirectHostLlm(
  preferredProvider?: string,
  paths: HostAuthPaths = {},
): DirectHostLlmConfig | null {
  const detection = detectOpenClawHostAuth(preferredProvider, paths);
  if (!detection) return null;
  if (!detection.directCallCapable) return null;
  if (detection.credentialStatus !== "available") return null;

  return {
    provider: detection.normalizedProvider,
    apiKey: detection.credential,
    detection,
  };
}
