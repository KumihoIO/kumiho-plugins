/**
 * Python auto-detection for local (MCP stdio) mode.
 *
 * When the user hasn't explicitly configured `local.pythonPath`, this module
 * probes known virtualenv locations in priority order and verifies that
 * `kumiho.mcp_server` is importable. POSIX can fall back to system Python;
 * Windows fails closed instead of invoking App Execution Aliases.
 *
 * Detection result is cached per-process (module-level singleton) so the
 * `spawnSync` probes run at most once per OpenClaw gateway lifetime.
 */

import {
  closeSync,
  existsSync,
  fstatSync,
  openSync,
  readSync,
  realpathSync,
  statSync,
} from "node:fs";
import { platform, userInfo } from "node:os";
import { isAbsolute, join } from "node:path";
import { spawnSync } from "node:child_process";

const IS_WIN = platform() === "win32";
const BIN    = IS_WIN ? "Scripts" : "bin";
const EXT    = IS_WIN ? ".exe"    : "";

function trustedAccountHome(): string {
  const home = userInfo().homedir;
  if (typeof home !== "string" || !home.trim() || !isAbsolute(home)) {
    throw new Error("Unable to resolve an absolute home from the OS account record");
  }
  return home;
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface PythonResolution {
  /** Absolute path or bare name ("python3") for the Python executable. */
  pythonPath: string;
  /**
   * Command to pass to McpBridge:
   *   "kumiho.mcp_server"  →  spawned as  `python -m kumiho.mcp_server`
   *   "kumiho-mcp"         →  spawned as  `kumiho-mcp` binary (PATH lookup)
   */
  command: string;
}

// ---------------------------------------------------------------------------
// Candidate list (tried in order)
// ---------------------------------------------------------------------------

interface Candidate {
  python: string;
  /** True for absolute filesystem paths — allows fast existsSync pre-check. */
  absolute: boolean;
}

function buildCandidates(): Candidate[] {
  const home = trustedAccountHome();
  const localAppData = IS_WIN ? join(home, "AppData", "Local") : "";

  return [
    // 1. Kumiho-managed venv (created by `npm run setup` / `npx kumiho-setup`)
    {
      python: join(home, ".kumiho", "venv", BIN, `python${EXT}`),
      absolute: true,
    },
    // 2. kumiho-claude integration venv (Windows, used by Claude Code plugin)
    ...(IS_WIN && localAppData && isAbsolute(localAppData)
      ? [
          {
            python: join(
              localAppData,
              "kumiho-claude",
              "venv",
              "Scripts",
              "python.exe"
            ),
            absolute: true,
          },
        ]
      : []),
    // 3. System Python is safe to probe by name on POSIX. Windows deliberately
    // avoids PATH names because App Execution Aliases and .com shims can open
    // GUI/16-bit compatibility dialogs before Node can observe a failure.
    ...(!IS_WIN
      ? [
          { python: "python3", absolute: false },
          { python: "python", absolute: false },
        ]
      : []),
  ];
}

// ---------------------------------------------------------------------------
// Detection helpers
// ---------------------------------------------------------------------------

function isWindowsAppExecutionAlias(path: string): boolean {
  return path
    .replaceAll("/", "\\")
    .toLowerCase()
    .includes("\\windowsapps\\");
}

function hasWindowsPeHeader(path: string): boolean {
  if (!IS_WIN) return true;
  let fd: number | undefined;
  try {
    fd = openSync(path, "r");
    const size = fstatSync(fd).size;
    if (size < 68) return false;
    const header = Buffer.alloc(64);
    if (readSync(fd, header, 0, 64, 0) !== 64) return false;
    if (header[0] !== 0x4d || header[1] !== 0x5a) return false;
    const peOffset = header.readUInt32LE(0x3c);
    if (peOffset < 64 || peOffset > Math.min(size - 4, 4 * 1024 * 1024)) return false;
    const signature = Buffer.alloc(4);
    return (
      readSync(fd, signature, 0, 4, peOffset) === 4 &&
      signature.equals(Buffer.from([0x50, 0x45, 0x00, 0x00]))
    );
  } catch {
    return false;
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd); } catch { /* best effort */ }
    }
  }
}

function verifiedWindowsPython(path: string): string | null {
  if (
    !isAbsolute(path) || !path.toLowerCase().endsWith(".exe") ||
    isWindowsAppExecutionAlias(path) || !existsSync(path)
  ) return null;
  try {
    const executable = realpathSync(path);
    if (
      !isAbsolute(executable) || !executable.toLowerCase().endsWith(".exe") ||
      isWindowsAppExecutionAlias(executable) || !statSync(executable).isFile() ||
      !hasWindowsPeHeader(executable)
    ) return null;
    return executable;
  } catch {
    return null;
  }
}

/** Revalidate immediately before a Python-backed spawn (cached paths can change). */
export function verifiedPythonForLaunch(path: string): string {
  if (!IS_WIN) return path;
  const executable = verifiedWindowsPython(path);
  if (!executable) {
    throw new Error(
      "Refusing to launch an unverified Windows Python executable. " +
        "Run 'npx kumiho-setup' to repair ~/.kumiho/venv.",
    );
  }
  return executable;
}

function executableForCandidate(candidate: Candidate): string | null {
  // Skip absolute paths that clearly don't exist — avoids the spawnSync call
  if (candidate.absolute && !existsSync(candidate.python)) {
    return null;
  }
  if (IS_WIN) return verifiedWindowsPython(candidate.python);
  return candidate.python;
}

function hasKumihoMcp(executable: string): boolean {
  const result = spawnSync(
    executable,
    ["-c", "import kumiho.mcp_server; print('ok')"],
    { encoding: "utf8", timeout: 5_000, windowsHide: true }
  );

  return result.status === 0 && (result.stdout as string).includes("ok");
}

// ---------------------------------------------------------------------------
// Module-level cache
// ---------------------------------------------------------------------------

let _cached: PythonResolution | null = null;

/**
 * Probe known Python environments and return the first one that has
 * `kumiho.mcp_server` installed.
 *
 * @param logger  Optional logger for info/warn messages.
 * @param fresh   Set to `true` to bypass the cache (testing only).
 */
export function detectPython(
  logger?: { info: (msg: string) => void; warn: (msg: string) => void },
  fresh = false
): PythonResolution {
  if (_cached && !fresh) return _cached;

  for (const candidate of buildCandidates()) {
    const executable = executableForCandidate(candidate);
    if (executable && hasKumihoMcp(executable)) {
      logger?.info(
        `[kumiho] Auto-detected kumiho.mcp_server at: ${executable}`
      );
      _cached = { pythonPath: executable, command: "kumiho.mcp_server" };
      return _cached;
    }
  }

  logger?.warn(
    "[kumiho] kumiho.mcp_server not found in any known Python environment. " +
    "Run 'npx kumiho-setup' (or 'npm run setup' in the plugin dir) to install it, " +
    "or set local.pythonPath in your openclaw.json plugin config."
  );

  if (IS_WIN) {
    throw new Error(
      "No verified native Python was found in ~/.kumiho/venv. " +
        "Run 'npx kumiho-setup' before starting the OpenClaw plugin.",
    );
  }
  _cached = { pythonPath: "python", command: "kumiho-mcp" };
  return _cached;
}

/**
 * Resolve the effective Python executable and MCP command for McpBridge.
 *
 * If the user explicitly set `local.pythonPath` or `local.command` (i.e.
 * either is non-default), their values are used as-is.  Otherwise,
 * `detectPython` probes the environment automatically.
 */
export function resolvePythonPath(
  configured: { pythonPath: string; command: string },
  logger?: { info: (msg: string) => void; warn: (msg: string) => void }
): PythonResolution {
  const hasExplicitPythonPath = configured.pythonPath !== "python";
  const hasExplicitCommand = configured.command !== "kumiho-mcp";
  const commandHasPathSep =
    configured.command.includes("/") || configured.command.includes("\\");
  const commandNeedsPython =
    configured.command.endsWith(".py") ||
    (!commandHasPathSep && configured.command.includes("."));

  let explicitPythonPath = configured.pythonPath;
  if (IS_WIN && hasExplicitPythonPath && (!hasExplicitCommand || commandNeedsPython)) {
    try {
      explicitPythonPath = verifiedPythonForLaunch(configured.pythonPath);
    } catch {
      throw new Error(
        "Configured local.pythonPath must be an absolute, existing native Python .exe " +
          "outside the Windows App Execution Alias directory.",
      );
    }
  }

  if (hasExplicitPythonPath && !hasExplicitCommand) {
    logger?.info(
      `[kumiho] Using configured Python interpreter for MCP module: ` +
      `${explicitPythonPath} -m kumiho.mcp_server`
    );
    return {
      pythonPath: explicitPythonPath,
      command: "kumiho.mcp_server",
    };
  }

  if (IS_WIN && !hasExplicitPythonPath && hasExplicitCommand && commandNeedsPython) {
    const detected = detectPython(logger);
    return { pythonPath: detected.pythonPath, command: configured.command };
  }

  if (hasExplicitPythonPath || hasExplicitCommand) {
    // User explicitly configured both values or explicitly overrode the command.
    return { pythonPath: explicitPythonPath, command: configured.command };
  }

  return detectPython(logger);
}
