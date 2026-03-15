export const GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/";

export type SupportedLlmProvider = "openai" | "openai-codex" | "anthropic" | "gemini";
export type NormalizedLlmProvider = "openai" | "anthropic" | "gemini";

export function normalizeConfiguredLlmProvider(
  provider?: string,
): NormalizedLlmProvider | "" {
  if (!provider) return "";
  if (provider === "anthropic") return "anthropic";
  if (provider === "gemini") return "gemini";
  if (provider === "openai" || provider === "openai-codex") return "openai";
  return "";
}
