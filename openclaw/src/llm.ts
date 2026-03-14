export type SupportedLlmProvider = "openai" | "openai-codex" | "anthropic";
export type NormalizedLlmProvider = "openai" | "anthropic";

export function normalizeConfiguredLlmProvider(
  provider?: string,
): NormalizedLlmProvider | "" {
  if (!provider) return "";
  if (provider === "anthropic") return "anthropic";
  if (provider === "openai" || provider === "openai-codex") return "openai";
  return "";
}
