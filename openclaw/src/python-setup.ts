/**
 * Python auto-detection for local (MCP stdio) mode.
 *
 * When the user hasn't explicitly configured `local.pythonPath`, this module
 * probes known virtualenv locations in priority order and verifies that
 * `kumiho.mcp_server` is importable.  Falls back to system "python" with a
 * warning so the error message from McpBridge is still clear.
 *
 * Detection result is cached per-process (module-level singleton) so the
 * `spawnSync` probes run at most once per OpenClaw gateway lifetime.
 */

import { existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const IS_WIN = platform() === "win32";
const BIN    = IS_WIN ? "Scripts" : "bin";
const EXT    = IS_WIN ? ".exe"    : "";

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
  const home         = homedir();
  const localAppData = IS_WIN ? (process.env.LOCALAPPDATA ?? "") : "";

  return [
    // 1. Kumiho-managed venv (created by `npm run setup` / `npx kumiho-setup`)
    {
      python: join(home, ".kumiho", "venv", BIN, `python${EXT}`),
      absolute: true,
    },
    // 2. kumiho-claude integration venv (Windows, used by Claude Code plugin)
    ...(IS_WIN
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
    // 3. System Python (PATH lookup — fast fail if not found)
    { python: "python3", absolute: false },
    { python: "python",  absolute: false },
  ];
}

// ---------------------------------------------------------------------------
// Detection helpers
// ---------------------------------------------------------------------------

function hasKumihoMcp(candidate: Candidate): boolean {
  // Skip absolute paths that clearly don't exist — avoids the spawnSync call
  if (candidate.absolute && !existsSync(candidate.python)) {
    return false;
  }

  const result = spawnSync(
    candidate.python,
    ["-c", "import kumiho.mcp_server; print('ok')"],
    { encoding: "utf8", timeout: 5_000 }
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
    if (hasKumihoMcp(candidate)) {
      logger?.info(
        `[kumiho] Auto-detected kumiho.mcp_server at: ${candidate.python}`
      );
      _cached = { pythonPath: candidate.python, command: "kumiho.mcp_server" };
      return _cached;
    }
  }

  logger?.warn(
    "[kumiho] kumiho.mcp_server not found in any known Python environment. " +
    "Run 'npx kumiho-setup' (or 'npm run setup' in the plugin dir) to install it, " +
    "or set local.pythonPath in your openclaw.json plugin config."
  );

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
  const isDefault =
    configured.pythonPath === "python" && configured.command === "kumiho-mcp";

  if (!isDefault) {
    // User explicitly configured — trust it unconditionally
    return { pythonPath: configured.pythonPath, command: configured.command };
  }

  return detectPython(logger);
}
