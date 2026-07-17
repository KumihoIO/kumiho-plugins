import { GEMINI_OPENAI_BASE_URL } from "./llm.js";
import type { HostAuthDetection } from "./host-auth.js";

export type ExplicitMemoryProvider = "openai" | "anthropic" | "gemini";

export interface SetupOption<T extends string | null = string | null> {
  label: string;
  note?: string;
  recommended?: boolean;
  provider: T;
  model: string | null;
}

export interface MemoryPreferencesInput {
  existingPrefs: Record<string, unknown>;
  schedule: string;
  scheduleKey: string;
  timezone: string;
  provider?: ExplicitMemoryProvider;
  apiKey?: string;
  baseUrl?: string;
  dreamModelChoice: SetupOption<ExplicitMemoryProvider>;
  consolidationModelChoice: SetupOption<ExplicitMemoryProvider>;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function mergeRecord(base: unknown, extra: Record<string, unknown>): Record<string, unknown> {
  const result = isPlainObject(base) ? { ...base } : {};
  for (const [key, value] of Object.entries(extra)) {
    if (isPlainObject(value) && isPlainObject(result[key])) {
      result[key] = mergeRecord(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

export function requiresExplicitMemoryProvider(activeHostAuth: HostAuthDetection | null): boolean {
  return !!activeHostAuth &&
    activeHostAuth.normalizedProvider === "openai" &&
    !activeHostAuth.directCallCapable;
}

export function getExplicitMemoryProviderOptions(): Array<{
  key: ExplicitMemoryProvider;
  label: string;
  note: string;
  recommended?: boolean;
}> {
  return [
    {
      key: "openai",
      label: "OpenAI API key",
      note: "direct OpenAI API credential for Dream State + consolidation",
      recommended: true,
    },
    {
      key: "anthropic",
      label: "Anthropic API key",
      note: "simple direct-token path for Dream State + consolidation",
    },
    {
      key: "gemini",
      label: "Gemini API key",
      note: "Google Gemini via the OpenAI-compatible endpoint",
    },
  ];
}

export function getMemoryProviderBaseUrl(provider: ExplicitMemoryProvider): string {
  return provider === "gemini" ? GEMINI_OPENAI_BASE_URL : "";
}

export function getSuggestedApiEnvVars(provider: ExplicitMemoryProvider): string[] {
  if (provider === "anthropic") return ["ANTHROPIC_API_KEY", "KUMIHO_LLM_API_KEY"];
  if (provider === "gemini") return ["GEMINI_API_KEY", "GOOGLE_API_KEY", "KUMIHO_LLM_API_KEY"];
  return ["OPENAI_API_KEY", "KUMIHO_LLM_API_KEY"];
}

export function getDreamStateModelOptions(provider: ExplicitMemoryProvider): SetupOption<ExplicitMemoryProvider>[] {
  if (provider === "anthropic") {
    return [
      {
        label: "claude-haiku-4-5 (Anthropic)",
        note: "recommended — low-cost Dream State classifier",
        provider,
        model: "claude-haiku-4-5-20251001",
        recommended: true,
      },
      {
        label: "claude-sonnet-4-6 (Anthropic)",
        note: "quality upgrade for richer Dream State assessment",
        provider,
        model: "claude-sonnet-4-6",
      },
      {
        label: "Custom Anthropic model",
        note: "type any Anthropic model id",
        provider,
        model: "__custom__",
      },
    ];
  }

  if (provider === "gemini") {
    return [
      {
        label: "gemini-2.5-flash-lite (Gemini)",
        note: "recommended — cheapest Gemini Dream State preset",
        provider,
        model: "gemini-2.5-flash-lite",
        recommended: true,
      },
      {
        label: "gemini-2.5-flash (Gemini)",
        note: "quality upgrade for Dream State",
        provider,
        model: "gemini-2.5-flash",
      },
      {
        label: "Custom Gemini model",
        note: "type any Gemini model id",
        provider,
        model: "__custom__",
      },
    ];
  }

  return [
    {
      label: "gpt-5-nano (OpenAI)",
      note: "recommended — cheapest GPT-5 Dream State preset",
      provider,
      model: "gpt-5-nano",
      recommended: true,
    },
    {
      label: "gpt-5-mini (OpenAI)",
      note: "quality upgrade for Dream State",
      provider,
      model: "gpt-5-mini",
    },
    {
      label: "Custom OpenAI model",
      note: "type any OpenAI model id",
      provider,
      model: "__custom__",
    },
  ];
}

export function getConsolidationModelOptions(provider: ExplicitMemoryProvider): SetupOption<ExplicitMemoryProvider>[] {
  if (provider === "anthropic") {
    return [
      {
        label: "claude-haiku-4-5 (Anthropic)",
        note: "recommended — lightweight direct summary model",
        provider,
        model: "claude-haiku-4-5-20251001",
        recommended: true,
      },
      {
        label: "claude-sonnet-4-6 (Anthropic)",
        note: "quality upgrade for richer summaries",
        provider,
        model: "claude-sonnet-4-6",
      },
      {
        label: "Custom Anthropic model",
        note: "type any Anthropic model id",
        provider,
        model: "__custom__",
      },
    ];
  }

  if (provider === "gemini") {
    return [
      {
        label: "gemini-2.5-flash (Gemini)",
        note: "recommended — lightweight direct summary model",
        provider,
        model: "gemini-2.5-flash",
        recommended: true,
      },
      {
        label: "gemini-2.5-flash-lite (Gemini)",
        note: "cheaper Gemini summary preset",
        provider,
        model: "gemini-2.5-flash-lite",
      },
      {
        label: "Custom Gemini model",
        note: "type any Gemini model id",
        provider,
        model: "__custom__",
      },
    ];
  }

  return [
    {
      label: "gpt-5-mini (OpenAI)",
      note: "recommended — lightweight direct summary model",
      provider,
      model: "gpt-5-mini",
      recommended: true,
    },
    {
      label: "gpt-5-nano (OpenAI)",
      note: "cheaper GPT-5 summary preset",
      provider,
      model: "gpt-5-nano",
    },
    {
      label: "Custom OpenAI model",
      note: "type any OpenAI model id",
      provider,
      model: "__custom__",
    },
  ];
}

export function buildMemoryPreferences(input: MemoryPreferencesInput): Record<string, unknown> {
  const llm = input.provider
    ? {
        provider: input.provider,
        ...(input.apiKey ? { apiKey: input.apiKey } : {}),
        ...(input.baseUrl ? { baseUrl: input.baseUrl } : {}),
      }
    : undefined;
  const result = mergeRecord(input.existingPrefs, {});
  const existingDreamState = isPlainObject(result.dreamState) ? result.dreamState : {};
  const existingConsolidation = isPlainObject(result.consolidation) ? result.consolidation : {};

  if (llm) {
    result.llm = llm;
  }

  result.dreamState = {
    ...existingDreamState,
    schedule: input.schedule,
    scheduleKey: input.scheduleKey,
    timezone: input.timezone,
    model: {
      provider: input.dreamModelChoice.provider,
      model: input.dreamModelChoice.model,
    },
  };
  result.consolidation = {
    ...existingConsolidation,
    model: {
      provider: input.consolidationModelChoice.provider,
      model: input.consolidationModelChoice.model,
    },
  };

  return result;
}

export function buildOpenClawPluginConfig(input: {
  pythonPath: string;
  dreamStateSchedule?: string;
  dreamModelChoice?: SetupOption<ExplicitMemoryProvider>;
  consolidationModelChoice?: SetupOption<ExplicitMemoryProvider>;
  /** Self-hosted Community Edition routing chosen in the wizard. */
  ce?: { endpoint: string; redisUrl: string };
}): Record<string, unknown> {
  return {
    mode: "local",
    ...(input.ce
      ? {
          ce: {
            enabled: true,
            endpoint: input.ce.endpoint,
            redisUrl: input.ce.redisUrl,
          },
        }
      : {}),
    ...(input.dreamStateSchedule ? { dreamStateSchedule: input.dreamStateSchedule } : {}),
    ...(input.dreamModelChoice?.provider
      ? {
          dreamStateModel: {
            provider: input.dreamModelChoice.provider,
            model: input.dreamModelChoice.model,
          },
        }
      : {}),
    ...(input.consolidationModelChoice?.provider
      ? {
          consolidationModel: {
            provider: input.consolidationModelChoice.provider,
            model: input.consolidationModelChoice.model,
          },
        }
      : {}),
    local: {
      pythonPath: input.pythonPath,
      command: "kumiho.mcp_server",
    },
  };
}
