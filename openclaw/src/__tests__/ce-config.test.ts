import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ceChildEnv } from "../client.js";
import { createKumihoMemory } from "../index.js";

// resolveConfig reads these at call time; clear them so ambient developer
// environments (e.g. a real UPSTASH_REDIS_URL) don't leak into assertions.
beforeEach(() => {
  vi.stubEnv("KUMIHO_OPENCLAW_MODE", "");
  vi.stubEnv("KUMIHO_OPENCLAW_SERVER_ENDPOINT", "");
  vi.stubEnv("KUMIHO_LOCAL_SERVER_ENDPOINT", "");
  vi.stubEnv("UPSTASH_REDIS_URL", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("CE config resolution", () => {
  it("defaults to CE disabled with loopback defaults", () => {
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce).toEqual({
      enabled: false,
      endpoint: "127.0.0.1:9190",
      redisUrl: "redis://127.0.0.1:6379",
    });
  });

  it("enables CE via ce.enabled", () => {
    const memory = createKumihoMemory({ mode: "local", ce: { enabled: true } });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("127.0.0.1:9190");
  });

  it("setting ce.endpoint alone enables CE and normalizes scheme URLs", () => {
    const memory = createKumihoMemory({
      mode: "local",
      ce: { endpoint: "http://localhost:9190/" },
    });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("localhost:9190");
  });

  it("enables CE via KUMIHO_OPENCLAW_MODE=ce", () => {
    vi.stubEnv("KUMIHO_OPENCLAW_MODE", "ce");
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce.enabled).toBe(true);
  });

  it("preserves a TLS-bearing remote CE URL from the environment", () => {
    vi.stubEnv("KUMIHO_OPENCLAW_SERVER_ENDPOINT", "grpcs://192.168.1.10:9190");
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("grpcs://192.168.1.10:9190");
  });

  it("rejects plaintext and scheme-less remote CE endpoints", () => {
    expect(() =>
      createKumihoMemory({
        mode: "local",
        ce: { endpoint: "192.168.1.10:9190" },
      }),
    ).toThrow(/require an explicit grpcs:\/\/ or https:\/\//);
    expect(() =>
      createKumihoMemory({
        mode: "local",
        ce: { endpoint: "http://ce.example.test:9190" },
      }),
    ).toThrow(/require an explicit grpcs:\/\/ or https:\/\//);
  });

  it("does NOT enable CE from the SDK-generic KUMIHO_LOCAL_SERVER_ENDPOINT alone", () => {
    // A leftover shell export from another tool's CE setup (e.g. the Claude
    // plugin) must not silently reroute a cloud-backed OpenClaw.
    vi.stubEnv("KUMIHO_LOCAL_SERVER_ENDPOINT", "192.168.1.10:9190");
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce.enabled).toBe(false);
  });

  it("honors KUMIHO_LOCAL_SERVER_ENDPOINT as the endpoint value once CE is enabled", () => {
    vi.stubEnv("KUMIHO_LOCAL_SERVER_ENDPOINT", "https://ce.example.test:9443");
    const memory = createKumihoMemory({ mode: "local", ce: { enabled: true } });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("https://ce.example.test:9443");
  });

  it("explicit ce.enabled: false wins over env opt-in", () => {
    vi.stubEnv("KUMIHO_OPENCLAW_MODE", "ce");
    const memory = createKumihoMemory({ mode: "local", ce: { enabled: false } });
    expect(memory.config.ce.enabled).toBe(false);
  });

  it("honors a custom redisUrl", () => {
    const memory = createKumihoMemory({
      mode: "local",
      ce: { enabled: true, redisUrl: "redis://10.0.0.5:6380" },
    });
    expect(memory.config.ce.redisUrl).toBe("redis://10.0.0.5:6380");
  });

  it("ignores CE in cloud mode — routing applies to the local Python SDK only", () => {
    const memory = createKumihoMemory({
      mode: "cloud",
      apiKey: "kh_test_token",
      ce: { enabled: true },
    });
    expect(memory.config.ce.enabled).toBe(false);
  });
});

describe("CE child credential isolation", () => {
  it("overrides every inherited Cloud/discovery route and hides the shared auth cache", () => {
    const env = ceChildEnv({
      endpoint: "127.0.0.1:9190",
      redisUrl: "redis://127.0.0.1:6379",
    });

    expect(env.KUMIHO_LOCAL_SERVER_ENDPOINT).toBe("127.0.0.1:9190");
    expect(env.UPSTASH_REDIS_URL).toBe("redis://127.0.0.1:6379");
    expect(env.KUMIHO_CONFIG_DIR.replaceAll("\\", "/")).toMatch(
      /\/\.kumiho\/openclaw-ce$/,
    );

    for (const key of [
      "KUMIHO_AUTH_TOKEN",
      "KUMIHO_API_TOKEN",
      "KUMIHO_API_KEY",
      "KUMIHO_TOKEN",
      "KUMIHO_AUTO_CONFIGURE",
      "KUMIHO_FORCE_DISCOVERY_REFRESH",
      "KUMIHO_TENANT_HINT",
      "KUMIHO_FIREBASE_API_KEY",
      "KUMIHO_FIREBASE_ID_TOKEN",
      "KUMIHO_FIREBASE_PROJECT_ID",
      "KUMIHO_USE_CONTROL_PLANE_TOKEN",
      "KUMIHO_MEMORY_PROXY_URL",
      "KUMIHO_MCP_HOSTED",
      "KUMIHO_HOSTED_LOCAL_REDIS",
      "UPSTASH_REDIS_REST_URL",
      "UPSTASH_REDIS_REST_TOKEN",
      "KUMIHO_SERVER_ENDPOINT",
      "KUMIHO_SERVER_ADDRESS",
      "KUMIHO_ENDPOINT",
      "KUMIHO_BFF_ENDPOINT",
      "KUMIHO_SERVER_USE_TLS",
      "KUMIHO_SERVER_AUTHORITY",
      "KUMIHO_SSL_TARGET_OVERRIDE",
      "KUMIHO_SERVER_CA_FILE",
      "KUMIHO_REQUIRE_TLS",
    ]) {
      expect(env[key], key).toBe("");
    }

    expect(env.KUMIHO_DISABLE_AUTO_DISCOVERY).toBe("true");
    expect(env.KUMIHO_NO_INTERACTIVE_LOGIN).toBe("1");
    expect(env.KUMIHO_LOCAL_SERVER_PORT).toBe("");
    expect(env.KUMIHO_CONTROL_PLANE_URL).toBe("http://127.0.0.1:1");
    expect(env.KUMIHO_CONTROL_PLANE_API_URL).toBe("http://127.0.0.1:1");
    expect(env.KUMIHO_DISCOVERY_CACHE_FILE.replaceAll("\\", "/")).toMatch(
      /\/\.kumiho\/openclaw-ce\/discovery-cache\.json$/,
    );
    expect(env.KUMIHO_DISCOVERY_TIMEOUT_SECONDS).toBe("10");
    expect(env.KUMIHO_LOCAL_DISCOVERY_TIMEOUT_SECONDS).toBe("0.5");
    expect(env.KUMIHO_AUTH_TOKEN_GRACE_SECONDS).toBe("300");
  });

  it("routes remote CE only through the TLS bootstrap while retaining isolation", () => {
    const env = ceChildEnv({
      endpoint: "grpcs://ce.example.test:9443",
      redisUrl: "rediss://redis.example.test:6380",
    });

    expect(env.KUMIHO_OPENCLAW_REMOTE_CE_ENDPOINT).toBe(
      "grpcs://ce.example.test:9443",
    );
    expect(env.KUMIHO_LOCAL_SERVER_ENDPOINT).toBe("");
    expect(env.KUMIHO_SERVER_ENDPOINT).toBe("grpcs://ce.example.test:9443");
    expect(env.KUMIHO_SERVER_USE_TLS).toBe("true");
    expect(env.KUMIHO_REQUIRE_TLS).toBe("true");
    expect(env.UPSTASH_REDIS_URL).toBe("rediss://redis.example.test:6380");
    expect(env.KUMIHO_AUTH_TOKEN).toBe("");
    expect(env.KUMIHO_CONFIG_DIR).not.toBe("");
  });
});
