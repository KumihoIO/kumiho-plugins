import { readFileSync } from "node:fs";
import { URL } from "node:url";

import { describe, expect, it } from "vitest";

const setupSource = readFileSync(
  new URL("../../scripts/setup.mjs", import.meta.url),
  "utf8",
);

describe("setup provisioning process contract", () => {
  it("keeps every provisioning child finite and tears down its full process tree", () => {
    expect(setupSource).toContain(
      "{ inherit = false, timeoutMs = PROVISION_PIP_TIMEOUT_MS } = {}",
    );
    expect(setupSource).toContain("deadline = setTimeout(() => {");
    expect(setupSource).toContain("detached: !IS_WIN");
    expect(setupSource).toContain("process.kill(-pid");
    expect(setupSource).toContain('const taskkillArgs = ["/PID", String(pid), "/T"]');
    expect(setupSource).toContain('taskkillArgs.push("/F")');
    expect(setupSource).toContain("PROVISION_HARD_KILL_GRACE_MS");
    expect(setupSource).toContain("child.unref()");
  });

  it("uses short probe bounds and a longer, still finite pip bound", () => {
    expect(setupSource).toContain(
      '{ timeoutMs: PROVISION_PROBE_TIMEOUT_MS },\n  );\n  if (pipCheck.status',
    );
    expect(setupSource).toContain(
      '{ inherit: true, timeoutMs: PROVISION_VENV_TIMEOUT_MS },',
    );
    expect(
      setupSource.match(/timeoutMs: PROVISION_PIP_TIMEOUT_MS/g)?.length,
    ).toBeGreaterThanOrEqual(3);
  });

  it("commits lock heartbeats through compatibility aliases before canonical", () => {
    const compatRefresh = setupSource.indexOf(
      "for (const { lock, snapshot } of compatSnapshots)",
    );
    const compatRecheck = setupSource.indexOf(
      "provisionCompatLocks.some((lock) => stableOwned(lock) === null)",
    );
    const canonicalCommit = setupSource.indexOf(
      "const canonicalCommit = stableOwned(PROVISION_LOCK)",
    );
    expect(compatRefresh).toBeGreaterThan(0);
    expect(compatRecheck).toBeGreaterThan(compatRefresh);
    expect(canonicalCommit).toBeGreaterThan(compatRecheck);
    expect(setupSource).toContain(
      "assertProvisionLockOwned();\n    renameSync(VENV_DIR, backup)",
    );
    expect(setupSource).toContain(
      "if (!refreshProvisionLock()) {\n    return Promise.resolve",
    );
  });

  it("never executes a relative Windows Python launcher or App Execution Alias", () => {
    expect(setupSource).toContain("function findWindowsPythonLauncher()");
    expect(setupSource).toContain('resolve(entry, "py.exe")');
    expect(setupSource).toContain("isWindowsAppExecutionAlias(candidate)");
    expect(setupSource).toContain("!hasWindowsPeHeader(executable)");
    expect(setupSource).not.toContain('{ cmd: "py", args: ["-3"] }');
  });

  it("lets site.py identify a healthy shared virtualenv", () => {
    const start = setupSource.indexOf("function inspectPython(");
    const end = setupSource.indexOf("function findBasePython()", start);
    const probe = setupSource.slice(start, end);
    expect(probe).toContain('...args, "-I", "-c", code, expectedVenv');
    expect(probe).not.toContain('"-S"');
  });

  it("preserves remote CE TLS schemes and rejects plaintext remote targets", () => {
    expect(setupSource).toContain(
      'const CE_TLS_SCHEMES = new Set(["https:", "grpcs:"])',
    );
    expect(setupSource).toContain(
      "!isLoopbackEndpointHost(url.hostname) && (!hadScheme || !tls)",
    );
    expect(setupSource).toContain('return tls ? `${url.protocol}//${authority}` : authority');
  });
});
