import { describe, expect, it } from "vitest";

import {
  buildMemoryPreferences,
  buildOpenClawPluginConfig,
  getMemoryProviderBaseUrl,
  requiresExplicitMemoryProvider,
} from "../setup-support.js";

describe("setup-support", () => {
  it("requires an explicit memory provider for host-only OpenAI OAuth", () => {
    expect(requiresExplicitMemoryProvider({
      profileKey: "openai-codex:default",
      rawProvider: "openai-codex",
      normalizedProvider: "openai",
      authMode: "oauth",
      credential: "oauth-access",
      credentialStatus: "available",
      directCallCapable: false,
      directCallReason: "host_only_chatgpt_backend",
      selectedBy: "last_good",
      lastUsed: 1,
      errorCount: 0,
    })).toBe(true);

    expect(requiresExplicitMemoryProvider({
      profileKey: "anthropic:default",
      rawProvider: "anthropic",
      normalizedProvider: "anthropic",
      authMode: "token",
      credential: "sk-ant-live",
      credentialStatus: "available",
      directCallCapable: true,
      directCallReason: "direct",
      selectedBy: "last_used",
      lastUsed: 1,
      errorCount: 0,
    })).toBe(false);
  });

  it("builds shared llm prefs plus per-feature models for explicit providers", () => {
    const prefs = buildMemoryPreferences({
      existingPrefs: {
        dreamState: {
          schedule: "0 0 * * *",
          model: {
            provider: "openai",
            model: "gpt-5-mini",
            apiKey: "stale-dream-key",
          },
        },
      },
      schedule: "0 3 * * *",
      scheduleKey: "nightly-3am",
      timezone: "Asia/Seoul",
      provider: "gemini",
      apiKey: "gemini-direct-key",
      baseUrl: getMemoryProviderBaseUrl("gemini"),
      dreamModelChoice: {
        label: "gemini-2.5-flash-lite",
        provider: "gemini",
        model: "gemini-2.5-flash-lite",
      },
      consolidationModelChoice: {
        label: "gemini-2.5-flash",
        provider: "gemini",
        model: "gemini-2.5-flash",
      },
    });

    expect(prefs).toMatchObject({
      llm: {
        provider: "gemini",
        apiKey: "gemini-direct-key",
        baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
      },
      dreamState: {
        schedule: "0 3 * * *",
        scheduleKey: "nightly-3am",
        timezone: "Asia/Seoul",
        model: {
          provider: "gemini",
          model: "gemini-2.5-flash-lite",
        },
      },
      consolidation: {
        model: {
          provider: "gemini",
          model: "gemini-2.5-flash",
        },
      },
    });
    expect((prefs.dreamState as { model: Record<string, string> }).model).not.toHaveProperty("apiKey");
  });

  it("builds openclaw plugin config without embedding provider secrets", () => {
    const pluginConfig = buildOpenClawPluginConfig({
      pythonPath: "/home/test/.kumiho/venv/bin/python",
      dreamStateSchedule: "0 3 * * *",
      dreamModelChoice: {
        label: "gpt-5-nano",
        provider: "openai",
        model: "gpt-5-nano",
      },
      consolidationModelChoice: {
        label: "gpt-5-mini",
        provider: "openai",
        model: "gpt-5-mini",
      },
    });

    expect(pluginConfig).toMatchObject({
      mode: "local",
      dreamStateSchedule: "0 3 * * *",
      dreamStateModel: {
        provider: "openai",
        model: "gpt-5-nano",
      },
      consolidationModel: {
        provider: "openai",
        model: "gpt-5-mini",
      },
      local: {
        pythonPath: "/home/test/.kumiho/venv/bin/python",
        command: "kumiho.mcp_server",
      },
    });
    expect(pluginConfig).not.toHaveProperty("llm");
  });
});
