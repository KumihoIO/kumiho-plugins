import { readFileSync } from "node:fs";
import { URL } from "node:url";

import { describe, expect, it } from "vitest";

const clientSource = readFileSync(new URL("../client.ts", import.meta.url), "utf8");
const bridgeSource = readFileSync(new URL("../mcp-bridge.ts", import.meta.url), "utf8");
const bootstrapSource = readFileSync(
  new URL("../../scripts/run-remote-ce-mcp.py", import.meta.url),
  "utf8",
);

describe("remote CE bootstrap contract", () => {
  it("uses shared-Python detection before replacing the MCP command", () => {
    expect(clientSource).toContain("pythonBootstrapScript: remoteCe");
    expect(bridgeSource).toContain(
      'command: this.pythonBootstrapScript ? "kumiho-mcp" : this.command',
    );
    expect(bridgeSource).toContain(
      "const effectiveCommand = this.pythonBootstrapScript ?? resolved.command",
    );
    expect(bridgeSource).toContain("windowsHide: true");
    expect(bridgeSource).toContain("verifiedPythonForLaunch(effectivePythonPath)");
  });

  it("constructs and pins an explicit tokenless, discovery-free SDK client", () => {
    expect(bootstrapSource).toContain('_TLS_SCHEMES = {"https", "grpcs"}');
    expect(bootstrapSource).toContain('token=""');
    expect(bootstrapSource).toContain("enable_auto_login=False");
    expect(bootstrapSource).toContain("use_discovery=False");
    expect(bootstrapSource).toContain(
      "kumiho.auto_configure_from_discovery = explicit_remote_ce_client",
    );
  });
});
