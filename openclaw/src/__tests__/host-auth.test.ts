import { describe, expect, it } from "vitest";

import { selectOpenClawHostAuth } from "../host-auth.js";

describe("selectOpenClawHostAuth", () => {
  it("normalizes lastGood openai-codex entries for preferred OpenAI selection", () => {
    const detection = selectOpenClawHostAuth(
      {
        profiles: {
          "anthropic:dev": {
            provider: "anthropic",
            type: "token",
            token: "sk-ant-live",
          },
          "openai-codex:default": {
            provider: "openai-codex",
            type: "oauth",
            access: "oauth-access",
            expires: Date.now() + 60 * 60 * 1000,
          },
        },
        lastGood: {
          "openai-codex": "openai-codex:default",
        },
        usageStats: {
          "anthropic:dev": { lastUsed: 200, errorCount: 0 },
          "openai-codex:default": { lastUsed: 50, errorCount: 0 },
        },
      },
      {
        providers: {
          "openai-codex": {
            baseUrl: "https://chatgpt.com/backend-api",
            api: "openai-codex-responses",
          },
        },
      },
      "openai",
    );

    expect(detection).toMatchObject({
      profileKey: "openai-codex:default",
      normalizedProvider: "openai",
      selectedBy: "preferred_last_good",
      directCallCapable: false,
      directCallReason: "host_only_chatgpt_backend",
    });
  });

  it("uses lastUsed to prefer the active provider when no preferred provider is requested", () => {
    const detection = selectOpenClawHostAuth({
      profiles: {
        "openai-codex:stale": {
          provider: "openai-codex",
          type: "oauth",
          access: "stale-access",
          expires: Date.now() + 60 * 60 * 1000,
        },
        "anthropic:active": {
          provider: "anthropic",
          type: "token",
          token: "sk-ant-live",
        },
      },
      usageStats: {
        "openai-codex:stale": { lastUsed: 10, errorCount: 0 },
        "anthropic:active": { lastUsed: 500, errorCount: 1 },
      },
    });

    expect(detection).toMatchObject({
      profileKey: "anthropic:active",
      normalizedProvider: "anthropic",
      selectedBy: "last_used",
      directCallCapable: true,
      directCallReason: "direct",
    });
  });

  it("keeps host-only OpenAI OAuth detection even when the cached token is expired", () => {
    const detection = selectOpenClawHostAuth(
      {
        profiles: {
          "openai-codex:expired": {
            provider: "openai-codex",
            type: "oauth",
            access: "expired-access",
            expires: Date.now() - 60 * 1000,
          },
        },
        usageStats: {
          "openai-codex:expired": { lastUsed: 999, errorCount: 0 },
        },
      },
      {
        providers: {
          "openai-codex": {
            baseUrl: "https://chatgpt.com/backend-api",
          },
        },
      },
    );

    expect(detection).toMatchObject({
      profileKey: "openai-codex:expired",
      normalizedProvider: "openai",
      credentialStatus: "expired_oauth_token",
      directCallCapable: false,
      directCallReason: "host_only_chatgpt_backend",
    });
  });
});
