import { describe, expect, it, vi } from "vitest";

import { resolvePythonPath } from "../python-setup.js";

describe("resolvePythonPath", () => {
  it("uses the configured venv python to launch the MCP module when only pythonPath is overridden", () => {
    const logger = { info: vi.fn(), warn: vi.fn() };

    const resolved = resolvePythonPath(
      {
        pythonPath: "/home/test/.kumiho/venv/bin/python",
        command: "kumiho-mcp",
      },
      logger,
    );

    expect(resolved).toEqual({
      pythonPath: "/home/test/.kumiho/venv/bin/python",
      command: "kumiho.mcp_server",
    });
    expect(logger.info).toHaveBeenCalledWith(
      expect.stringContaining("/home/test/.kumiho/venv/bin/python -m kumiho.mcp_server"),
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
