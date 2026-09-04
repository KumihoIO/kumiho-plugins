import { readFileSync, realpathSync } from "node:fs";
import { URL } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { resolvePythonPath } from "../python-setup.js";

const pythonSetupSource = readFileSync(
  new URL("../python-setup.ts", import.meta.url),
  "utf8",
);

describe("resolvePythonPath", () => {
  it("uses the configured venv python to launch the MCP module when only pythonPath is overridden", () => {
    const logger = { info: vi.fn(), warn: vi.fn() };
    const configuredPython =
      process.platform === "win32" ? process.execPath : "/home/test/.kumiho/venv/bin/python";
    const expectedPython =
      process.platform === "win32" ? realpathSync(configuredPython) : configuredPython;

    const resolved = resolvePythonPath(
      {
        pythonPath: configuredPython,
        command: "kumiho-mcp",
      },
      logger,
    );

    expect(resolved).toEqual({
      pythonPath: expectedPython,
      command: "kumiho.mcp_server",
    });
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining(`${expectedPython} -m kumiho.mcp_server`),
    );
  });

  it("keeps an explicitly configured command unchanged", () => {
    const resolved = resolvePythonPath({
      pythonPath: "/home/test/.kumiho/venv/bin/python",
      command: "custom-kumiho-wrapper",
    });

    expect(resolved).toEqual({
      pythonPath: "/home/test/.kumiho/venv/bin/python",
      command: "custom-kumiho-wrapper",
    });
  });
});

describe("Windows Python launch safety contract", () => {
  it("only probes absolute native PE executables and never uses a bare fallback", () => {
    expect(pythonSetupSource).toContain("function verifiedWindowsPython(path: string)");
    expect(pythonSetupSource).toContain("function trustedAccountHome(): string");
    expect(pythonSetupSource).toContain("const home = userInfo().homedir");
    expect(pythonSetupSource).not.toContain("process.env.LOCALAPPDATA");
    expect(pythonSetupSource).toContain("export function verifiedPythonForLaunch(path: string)");
    expect(pythonSetupSource).toContain('path.toLowerCase().endsWith(".exe")');
    expect(pythonSetupSource).toContain("isWindowsAppExecutionAlias(path)");
    expect(pythonSetupSource).toContain("peOffset > Math.min(size - 4, 4 * 1024 * 1024)");
    expect(pythonSetupSource).toContain(
      "signature.equals(Buffer.from([0x50, 0x45, 0x00, 0x00]))",
    );
    expect(pythonSetupSource).toContain("...(!IS_WIN");
    expect(pythonSetupSource).toContain("if (IS_WIN) {\n    throw new Error(");
    expect(pythonSetupSource).toContain(
      "IS_WIN && !hasExplicitPythonPath && hasExplicitCommand && commandNeedsPython",
    );
    expect(pythonSetupSource).toContain("windowsHide: true");
  });
});
