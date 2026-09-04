import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

  it("enables CE via KUMIHO_OPENCLAW_SERVER_ENDPOINT and uses it as the endpoint", () => {
    vi.stubEnv("KUMIHO_OPENCLAW_SERVER_ENDPOINT", "192.168.1.10:9190");
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("192.168.1.10:9190");
  });

  it("does NOT enable CE from the SDK-generic KUMIHO_LOCAL_SERVER_ENDPOINT alone", () => {
    // A leftover shell export from another tool's CE setup (e.g. the Claude
    // plugin) must not silently reroute a cloud-backed OpenClaw.
    vi.stubEnv("KUMIHO_LOCAL_SERVER_ENDPOINT", "192.168.1.10:9190");
    const memory = createKumihoMemory({ mode: "local" });
    expect(memory.config.ce.enabled).toBe(false);
  });

  it("honors KUMIHO_LOCAL_SERVER_ENDPOINT as the endpoint value once CE is enabled", () => {
    vi.stubEnv("KUMIHO_LOCAL_SERVER_ENDPOINT", "192.168.1.10:9190");
    const memory = createKumihoMemory({ mode: "local", ce: { enabled: true } });
    expect(memory.config.ce.enabled).toBe(true);
    expect(memory.config.ce.endpoint).toBe("192.168.1.10:9190");
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
