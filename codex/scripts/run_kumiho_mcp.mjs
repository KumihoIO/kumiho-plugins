#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import {
  closeSync,
  existsSync,
  fstatSync,
  openSync,
  readFileSync,
  readSync,
  realpathSync,
} from "node:fs";
import { homedir, userInfo } from "node:os";
import { delimiter, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CodexThreadIdBridge } from "./thread_id_bridge.mjs";

const SCRIPT_PATH = fileURLToPath(new URL("./run_kumiho_mcp.py", import.meta.url));
const ONBOARD_PATH = fileURLToPath(new URL("./onboard_kumiho.py", import.meta.url));
const BACKFILL_PATH = fileURLToPath(new URL("./backfill_codex.py", import.meta.url));
const PROBE_NONCE = "kumiho-python-probe-v1";
const PROBE_CODE =
  "import os,platform,sys; " +
  "expected=os.path.normcase(os.path.realpath(sys.argv[1])) if sys.argv[1] else ''; " +
  "prefix=os.path.normcase(os.path.realpath(sys.prefix)); " +
  "base=os.path.normcase(os.path.realpath(sys.base_prefix)); " +
  "venv_ok=(not expected) or (prefix == expected and prefix != base); " +
  `print('${PROBE_NONCE}|' + sys.executable + '|' + platform.python_version() + '|' + ('1' if venv_ok else '0')); ` +
  "raise SystemExit(3 if sys.version_info < (3, 10) else (0 if venv_ok else 4))";

function unquote(value) {
  const text = (value ?? "").trim();
  if (text.length < 2) return text;
  const quoted =
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"));
  return quoted ? text.slice(1, -1).trim() : text;
}

function trustedHome() {
  // os.homedir() follows HOME/USERPROFILE. Project-scoped host settings can
  // overwrite those variables before MCP startup, so obtain the account home
  // from the operating system's user record instead.
  const home = userInfo().homedir;
  if (!home || !isAbsolute(home)) {
    throw new Error("could not resolve the operating-system user profile");
  }
  return home;
}

function isWindowsAppExecutionAlias(path) {
  return path
    .replaceAll("/", "\\")
    .toLowerCase()
    .includes("\\windowsapps\\");
}

function verifiedWindowsExecutable(path) {
  if (process.platform !== "win32") return { executable: path, error: "" };
  if (!isAbsolute(path)) {
    return { executable: "", error: "must be an absolute path on Windows" };
  }
  if (!path.toLowerCase().endsWith(".exe")) {
    return { executable: "", error: "must name a Windows .exe" };
  }
  // Check the lexical path as well as its real target: an App Execution Alias
  // can resolve into a different WindowsApps directory containing a valid PE
  // store stub, and launching either spelling can display a GUI.
  if (isWindowsAppExecutionAlias(path)) {
    return { executable: "", error: "Windows App Execution Alias is not executable here" };
  }
  if (!existsSync(path)) {
    return { executable: "", error: "executable does not exist" };
  }
  let resolved;
  try {
    resolved = realpathSync.native(path);
  } catch {
    return { executable: "", error: "executable path cannot be resolved" };
  }
  if (
    !isAbsolute(resolved) ||
    !resolved.toLowerCase().endsWith(".exe") ||
    isWindowsAppExecutionAlias(resolved) ||
    !hasWindowsPeHeader(resolved)
  ) {
    return { executable: "", error: "not a verified native Windows PE executable" };
  }
  return { executable: resolved, error: "" };
}

function windowsPathExecutables(names, env = process.env) {
  if (process.platform !== "win32") return [];
  const results = [];
  const seen = new Set();
  for (const rawDirectory of (env.PATH ?? "").split(delimiter)) {
    const directory = unquote(rawDirectory);
    // A relative PATH entry lets a project checkout choose which executable
    // Codex launches. Ignore it, including the empty entry that means cwd.
    if (!directory || !isAbsolute(directory)) continue;
    for (const name of names) {
      const checked = verifiedWindowsExecutable(join(directory, name));
      if (checked.error) continue;
      const normalized = checked.executable.toLowerCase();
      if (seen.has(normalized)) continue;
      seen.add(normalized);
      results.push(checked.executable);
    }
  }
  return results;
}

function pythonCandidates() {
  const candidates = [];
  // Kumiho Desktop provisions the shared runtime here. Prefer it before any
  // override or host PATH lookup so Codex and Claude use the same managed
  // installation whenever it exists.
  const accountHome = trustedHome();
  const sharedVenvPython = process.platform === "win32"
    ? join(accountHome, ".kumiho", "venv", "Scripts", "python.exe")
    : join(accountHome, ".kumiho", "venv", "bin", "python");
  if (existsSync(sharedVenvPython)) {
    candidates.push({
      command: sharedVenvPython,
      prefixArgs: [],
      source: "~/.kumiho/venv",
      expectedVenv: join(accountHome, ".kumiho", "venv"),
    });
  }

  const override = unquote(process.env.KUMIHO_PYTHON);
  if (override) {
    const checked = verifiedWindowsExecutable(override);
    candidates.push({
      command: checked.executable || override,
      prefixArgs: [],
      source: "KUMIHO_PYTHON",
      preflightError: checked.error,
    });
  }

  const defaults = process.platform === "win32"
    // Resolve PATH entries ourselves and inspect the actual file before spawn.
    // Bare command lookup can hit Windows Store aliases or malformed files and
    // display a modal GUI/"16-bit application" dialog.
    ? windowsPathExecutables(["py.exe", "python3.exe", "python.exe"]).map(
        (command) => [command, command.toLowerCase().endsWith("\\py.exe") ? ["-3"] : []],
      )
    : [["python3", []], ["python", []], ["py", ["-3"]]];
  candidates.push(...defaults.map(([command, prefixArgs]) => ({
    command,
    prefixArgs,
    source: "PATH",
  })));

  const seen = new Set();
  return candidates.filter(({ command, prefixArgs }) => {
    const key = JSON.stringify([command, ...prefixArgs]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function hasWindowsPeHeader(path) {
  if (process.platform !== "win32") return true;
  let fd;
  try {
    fd = openSync(path, "r");
    const size = fstatSync(fd).size;
    if (size < 68) return false;
    const header = Buffer.alloc(64);
    if (readSync(fd, header, 0, 64, 0) !== 64) return false;
    if (header[0] !== 0x4d || header[1] !== 0x5a) return false;
    const peOffset = header.readUInt32LE(0x3c);
    if (peOffset < 64 || peOffset > Math.min(size - 4, 4 * 1024 * 1024)) {
      return false;
    }
    const signature = Buffer.alloc(4);
    if (readSync(fd, signature, 0, 4, peOffset) !== 4) return false;
    return signature.equals(Buffer.from([0x50, 0x45, 0x00, 0x00]));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd); } catch { /* best effort */ }
    }
  }
}

function probePython(candidate) {
  // A text file or legacy DOS/NE executable named python.exe can make Windows
  // display a modal "16-bit application" dialog before spawn returns. Inspect
  // explicit executable files first and never ask Windows to launch a non-PE.
  if (candidate.preflightError) {
    return {
      ...candidate,
      available: false,
      executable: "",
      version: "",
      tooOld: false,
      error: candidate.preflightError,
    };
  }
  const checked = verifiedWindowsExecutable(candidate.command);
  if (checked.error) {
    return {
      ...candidate,
      available: false,
      executable: "",
      version: "",
      tooOld: false,
      error: checked.error,
    };
  }
  candidate = { ...candidate, command: checked.executable };
  // `-S` is intentionally absent: site.py reads pyvenv.cfg and switches
  // sys.prefix. Disabling it makes a healthy shared venv look like base Python.
  const result = spawnSync(
    candidate.command,
    [
      ...candidate.prefixArgs,
      "-I",
      "-c",
      PROBE_CODE,
      candidate.expectedVenv ?? "",
    ],
    {
      encoding: "utf8",
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 10_000,
      windowsHide: true,
    },
  );
  const output = (result.stdout ?? "").replace(/\r\n/g, "\n").trimEnd();
  const match = output.match(
    new RegExp(`^${PROBE_NONCE}\\|([^|\\n]+)\\|(\\d+(?:\\.\\d+){1,3})\\|([01])$`),
  );
  const executable = match?.[1] ?? "";
  const version = match?.[2] ?? "";
  const venvOk = match?.[3] === "1";
  return {
    ...candidate,
    available: !result.error && result.status === 0 && Boolean(match),
    executable,
    version,
    tooOld: Boolean(match) && result.status === 3,
    error: result.error?.message ?? (Boolean(match) && !venvOk ? "not a valid shared venv" : ""),
  };
}

function findPython({ scanAll = false } = {}) {
  const attempts = [];
  for (const candidate of pythonCandidates()) {
    const attempt = probePython(candidate);
    attempts.push(attempt);
    if (attempt.available && !scanAll) {
      return { attempts, selected: attempt };
    }
  }
  return { attempts, selected: attempts.find(({ available }) => available) };
}

function commandLabel({ command, prefixArgs }) {
  return [command, ...prefixArgs]
    .join(" ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "?");
}

function sessionIdSource(env = process.env) {
  if ((env.CODEX_THREAD_ID ?? "").trim()) return "CODEX_THREAD_ID";
  if ((env.CODEX_SESSION_ID ?? "").trim()) return "CODEX_SESSION_ID";
  return "unavailable";
}

function mcpEnvironment(env = process.env) {
  const childEnv = { ...env };
  // CLAUDE_PLUGIN_DATA is host-owned.  A Codex process launched from a
  // Claude-started shell must derive its own stable plugin-data directory
  // from the Codex cache path instead of reusing Claude's runtime.
  delete childEnv.CLAUDE_PLUGIN_DATA;
  const accountHome = trustedHome();
  childEnv.HOME = accountHome;
  if (process.platform === "win32") childEnv.USERPROFILE = accountHome;
  for (const key of [
    "KUMIHO_CONFIG_DIR",
    "KUMIHO_CLAUDE_HOME",
    "KUMIHO_PLUGIN_SHARED_HOME",
    "KUMIHO_CODEX_CONFIG_ROOT",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_FIREBASE_API_KEY",
    "KUMIHO_FIREBASE_ID_TOKEN",
    "KUMIHO_FIREBASE_PROJECT_ID",
    "KUMIHO_USE_CONTROL_PLANE_TOKEN",
    "KUMIHO_WORKSPACE_ROOT",
    "KUMIHO_ENV_FILE",
    "KUMIHO_AUTO_CONFIGURE",
    "KUMIHO_DISCOVERY_CACHE_FILE",
    "KUMIHO_CLAUDE_DISCOVERY_USER_AGENT",
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "UPSTASH_REDIS_URL",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "KUMIHO_MCP_HOSTED",
    "KUMIHO_HOSTED_LOCAL_REDIS",
    "KUMIHO_LOCAL_REDIS_URL",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_SERVER_USE_TLS",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_REQUIRE_TLS",
  ]) delete childEnv[key];
  childEnv.KUMIHO_CLAUDE_HOST = "codex";
  const codexId =
    (childEnv.CODEX_THREAD_ID ?? "").trim() ||
    (childEnv.CODEX_SESSION_ID ?? "").trim();
  if (codexId) childEnv.KUMIHO_SESSION_ID = codexId;
  else delete childEnv.KUMIHO_SESSION_ID;
  return childEnv;
}

function terminateWindowsTree(pid, { force = false } = {}) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  // Do not trust SystemRoot/windir/PATH from a project-controlled process:
  // timeout cleanup runs with the host's credentials. The standard protected
  // system location is safe; on a relocated Windows install, fall back to the
  // direct child kill instead of executing an environment-selected binary.
  const windowsRoot = "C:\\Windows";
  const system32 = `${windowsRoot}\\System32`;
  const taskkill = `${system32}\\taskkill.exe`;
  if (!existsSync(taskkill)) return false;
  const args = ["/PID", String(pid), "/T"];
  if (force) args.push("/F");
  const result = spawnSync(taskkill, args, {
    encoding: "utf8",
    stdio: "ignore",
    timeout: 5_000,
    windowsHide: true,
    env: { SystemRoot: windowsRoot, WINDIR: windowsRoot, PATH: system32 },
  });
  return !result.error && result.status === 0;
}

// --- Runtime compatibility receipt (kumiho-plugins#96) ----------------------
//
// Answers "which plugin / core / SDK / backend is this session actually using,
// is that combination supported, and do I need to restart?" as a versioned,
// machine-readable receipt. Strictly READ-ONLY and bounded: it never writes to
// the graph, installs or upgrades a package, or mutates host config. It masks
// user-specific paths and never emits a token, credential, or the session-id
// value — only presence and source.

const DOCTOR_RECEIPT_VERSION = "1";
const PLUGIN_MANIFEST_PATH = fileURLToPath(
  new URL("../.codex-plugin/plugin.json", import.meta.url),
);
const PLUGIN_MCP_JSON_PATH = fileURLToPath(new URL("../.mcp.json", import.meta.url));

function maskHome(value) {
  // Replace the user's home prefix with ~ so a receipt is shareable. Best-effort
  // and case-insensitive on Windows; a path outside home is left as-is.
  if (!value) return value;
  let home = "";
  try {
    home = homedir();
  } catch {
    home = "";
  }
  if (!home) return value;
  const norm = (p) => (process.platform === "win32" ? p.toLowerCase() : p);
  if (norm(value).startsWith(norm(home))) return "~" + value.slice(home.length);
  return value;
}

function readJsonFile(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function pluginVersion() {
  const manifest = readJsonFile(PLUGIN_MANIFEST_PATH);
  return {
    name: manifest?.name ?? "kumiho-memory",
    version: manifest?.version ?? "unknown",
    version_source: "packaged manifest (codex/.codex-plugin/plugin.json)",
  };
}

function parseFloor(spec, pkg) {
  // Pull ">=X.Y.Z" for a package name out of the space-separated pip spec.
  const re = new RegExp(`${pkg.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}\\[[^\\]]*\\]?>=([0-9][0-9A-Za-z.\\-]*)`);
  const m = (spec || "").match(re);
  return m ? m[1] : "unknown";
}

function requiredVersions() {
  const mcp = readJsonFile(PLUGIN_MCP_JSON_PATH);
  const spec =
    mcp?.mcpServers?.["kumiho-memory"]?.env?.KUMIHO_CLAUDE_PACKAGE_SPEC ?? "";
  return {
    source: "KUMIHO_CLAUDE_PACKAGE_SPEC in codex/.mcp.json",
    spec: spec || "unknown",
    kumiho: parseFloor(spec, "kumiho"),
    kumiho_memory: parseFloor(spec, "kumiho-memory"),
  };
}

function installedVersions(selected) {
  // Read-only: import the SDK metadata in the SELECTED interpreter. This proves
  // what is ON DISK for that interpreter, NOT what the already-connected MCP
  // process loaded (see active_process below). Absent/unimportable -> null.
  const empty = { kumiho: null, kumiho_memory: null, importable: false };
  if (!selected?.command) return empty;
  const probe =
    "import json;out={}\n" +
    "from importlib.metadata import version,PackageNotFoundError\n" +
    "for n in ('kumiho','kumiho-memory'):\n" +
    "    try: out[n]=version(n)\n" +
    "    except PackageNotFoundError: out[n]=None\n" +
    "import importlib.util as u\n" +
    "out['importable']=bool(u.find_spec('kumiho_memory'))\n" +
    "print('KUMIHO_DOCTOR_PKG '+json.dumps(out))";
  const result = spawnSync(selected.command, [...(selected.prefixArgs ?? []), "-I", "-c", probe], {
    encoding: "utf8",
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 10_000,
    windowsHide: true,
  });
  const line = (result.stdout ?? "")
    .split(/\r?\n/)
    .find((l) => l.startsWith("KUMIHO_DOCTOR_PKG "));
  if (!line) return empty;
  try {
    const data = JSON.parse(line.slice("KUMIHO_DOCTOR_PKG ".length));
    return {
      kumiho: data.kumiho ?? null,
      kumiho_memory: data["kumiho-memory"] ?? null,
      importable: Boolean(data.importable),
    };
  } catch {
    return empty;
  }
}

function backendClass(env = process.env) {
  // Configured class only. "observed" needs a live call the read-only doctor
  // does not make, so it is reported unknown rather than probed.
  const mode = (env.KUMIHO_CLAUDE_MODE ?? "").trim().toLowerCase();
  const endpoint = (env.KUMIHO_CLAUDE_SERVER_ENDPOINT ?? env.KUMIHO_SERVER_ENDPOINT ?? "").trim();
  let configured = "unknown";
  let source = "default (control-plane discovery at runtime)";
  if (mode === "ce") {
    configured = "ce";
    source = "KUMIHO_CLAUDE_MODE=ce";
  } else if (mode === "cloud") {
    configured = "cloud";
    source = "KUMIHO_CLAUDE_MODE=cloud";
  } else if (endpoint) {
    configured = "ce";
    source = "KUMIHO_CLAUDE_SERVER_ENDPOINT set";
  }
  return {
    configured_class: configured,
    source,
    observed_class: "unknown",
    reachable: "unknown",
    note: "read-only doctor does not open a backend connection; observed/reachable require the opt-in live check",
  };
}

function cmpFloor(have, floor) {
  // -1 have<floor, 0 equal-or-satisfies, 1 have>floor; null when uncomparable.
  if (!have || !floor || floor === "unknown") return null;
  const a = String(have).split("-")[0].split(".").map((n) => parseInt(n, 10));
  const b = String(floor).split(".").map((n) => parseInt(n, 10));
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (Number.isNaN(x) || Number.isNaN(y)) return null;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

function computeOutcome({ selected, scripts, packages, required }) {
  // Active-process versions are unknown by construction, so the strongest
  // honest verdict from disk is "possibly_stale" even when disk satisfies the
  // floor: a newer package on disk may not be what the running server loaded.
  if (!selected) {
    return {
      status: "unsupported",
      reason: "no compatible Python 3.10+ interpreter found",
      next_action: "install Python 3.10+ or set KUMIHO_PYTHON, then restart Codex",
    };
  }
  if (!scripts.mcp_present || !scripts.onboard_present) {
    return {
      status: "unsupported",
      reason: "a packaged launcher script is missing",
      next_action: "reinstall the plugin (codex plugin add kumiho-memory@kumiho-plugins)",
    };
  }
  if (!packages.importable || packages.kumiho === null || packages.kumiho_memory === null) {
    return {
      status: "unknown",
      reason: "the SDK is not importable in the selected interpreter yet (first-run provision installs it on demand)",
      next_action: "start the server once (or run --onboard) to provision the venv, then re-run the doctor",
    };
  }
  const belowCore = cmpFloor(packages.kumiho, required.kumiho) === -1;
  const belowMem = cmpFloor(packages.kumiho_memory, required.kumiho_memory) === -1;
  if (belowCore || belowMem) {
    return {
      status: "unsupported",
      reason: `on-disk package below the required floor (kumiho ${packages.kumiho} vs >=${required.kumiho}, kumiho-memory ${packages.kumiho_memory} vs >=${required.kumiho_memory})`,
      next_action: "upgrade the shared venv, then restart the session so the server reloads",
    };
  }
  return {
    status: "possibly_stale",
    reason: "on-disk packages satisfy the required floor, but a fresh probe cannot confirm the ALREADY-CONNECTED MCP process is running them",
    next_action: "if the plugin/core was updated in this session, restart the session so the MCP server reloads; otherwise no action needed",
  };
}

function buildDoctorReceipt() {
  const { attempts, selected } = findPython();
  const scripts = {
    mcp_present: existsSync(SCRIPT_PATH),
    onboard_present: existsSync(ONBOARD_PATH),
  };
  const packages = installedVersions(selected);
  const required = requiredVersions();
  const python = selected
    ? {
        selected: maskHome(commandLabel(selected)),
        executable: maskHome(selected.executable),
        version: selected.version,
      }
    : null;
  return {
    doctor_receipt_version: DOCTOR_RECEIPT_VERSION,
    host: "codex",
    plugin: pluginVersion(),
    runtime: {
      node: process.version,
      platform: process.platform,
      arch: process.arch,
      python,
    },
    packages: {
      observed_source: "on-disk probe of the selected interpreter (read-only)",
      kumiho: packages.kumiho,
      kumiho_memory: packages.kumiho_memory,
      importable: packages.importable,
      note: "on-disk versions; NOT proof of what the running MCP process loaded",
    },
    active_process: {
      observable: false,
      reason:
        "a fresh doctor probe cannot query the already-connected MCP process; the host exposes no active-process version channel",
      kumiho: "unknown",
      kumiho_memory: "unknown",
    },
    required,
    backend: backendClass(),
    session_identity: {
      mechanism: sessionIdSource(),
      present: sessionIdSource() !== "unavailable",
    },
    scripts,
    candidates: attempts.map((a) => ({
      label: maskHome(commandLabel(a)),
      source: a.source,
      available: Boolean(a.available),
      too_old: Boolean(a.tooOld),
    })),
    outcome: computeOutcome({ selected, scripts, packages, required }),
  };
}

function doctorJson() {
  const receipt = buildDoctorReceipt();
  console.log(JSON.stringify(receipt, null, 2));
  process.exitCode =
    receipt.outcome.status === "unsupported" || receipt.outcome.status === "unknown" ? 1 : 0;
}

function doctor() {
  // Stop after the first valid interpreter. Diagnostics must not execute a
  // broken WindowsApps alias after Python has already been found.
  const { attempts, selected } = findPython();
  console.log("Kumiho Memory MCP doctor");
  console.log(`Node: ${process.version} (${process.platform}/${process.arch})`);
  console.log(`Python launcher: ${selected ? commandLabel(selected) : "not found"}`);
  if (selected) {
    console.log(`Python executable: ${selected.executable}`);
    console.log(`Python version: ${selected.version}`);
  }
  console.log(`MCP script: ${SCRIPT_PATH}`);
  console.log(`MCP script exists: ${existsSync(SCRIPT_PATH) ? "yes" : "no"}`);
  console.log(`Onboarding script: ${ONBOARD_PATH}`);
  console.log(`Onboarding script exists: ${existsSync(ONBOARD_PATH) ? "yes" : "no"}`);
  console.log("Session id bridge: MCP _meta thread id -> request context (per call)");
  console.log(`Environment fallback: ${sessionIdSource()}`);
  console.log("Candidates:");
  for (const attempt of attempts) {
    const detail = attempt.available
      ? `ok (${attempt.executable}, Python ${attempt.version})`
      : attempt.tooOld
        ? `incompatible (Python ${attempt.version}; need 3.10+)`
      : `unavailable${attempt.error ? ` (${attempt.error})` : ""}`;
    console.log(`  - ${commandLabel(attempt)} [${attempt.source}]: ${detail}`);
  }
  // Runtime compatibility receipt (#96): plugin / on-disk core+SDK / required
  // floor / backend class / active-process (unknown by construction) / outcome.
  const plugin = pluginVersion();
  const packages = installedVersions(selected);
  const required = requiredVersions();
  const scripts = {
    mcp_present: existsSync(SCRIPT_PATH),
    onboard_present: existsSync(ONBOARD_PATH),
  };
  const backend = backendClass();
  const outcome = computeOutcome({ selected, scripts, packages, required });
  console.log(`Plugin version: ${plugin.version} (${plugin.version_source})`);
  console.log(
    `Installed on disk: kumiho ${packages.kumiho ?? "not installed"}, ` +
    `kumiho-memory ${packages.kumiho_memory ?? "not installed"} ` +
    `(on-disk probe; not proof of the running server)`,
  );
  console.log(`Required floor: kumiho >=${required.kumiho}, kumiho-memory >=${required.kumiho_memory}`);
  console.log(`Active MCP process versions: unknown (a fresh probe cannot query the connected server)`);
  console.log(`Backend class (configured): ${backend.configured_class} [${backend.source}]; observed: unknown`);
  console.log(`Outcome: ${outcome.status} — ${outcome.reason}`);
  console.log(`Next action: ${outcome.next_action}`);
  console.log(`For a machine-readable receipt: --doctor --json`);
  process.exitCode = selected && scripts.mcp_present && scripts.onboard_present ? 0 : 1;
}

function startPython(scriptPath, args, label, { bridgeThreadId = false } = {}) {
  if (!existsSync(scriptPath)) {
    console.error(`[kumiho-codex] ${label} script not found: ${scriptPath}`);
    process.exitCode = 1;
    return;
  }
  const { selected } = findPython();
  if (!selected) {
    console.error(
      "[kumiho-codex] Python 3.10+ was not found. Set KUMIHO_PYTHON to a " +
      "compatible executable, or install Python 3.10+ and restart Codex.",
    );
    process.exitCode = 1;
    return;
  }

  const child = spawn(
    selected.command,
    [...selected.prefixArgs, "-I", scriptPath, ...args],
    {
      env: mcpEnvironment(),
      stdio: bridgeThreadId ? ["pipe", "pipe", "inherit"] : "inherit",
      windowsHide: true,
    },
  );
  let bridge = null;
  if (bridgeThreadId) {
    bridge = new CodexThreadIdBridge();
    // MCP stdio is newline-delimited JSON-RPC. Codex places its stable thread
    // id in each request's `_meta`; carry it into a request-scoped
    // Python ContextVar without mutating process-global environment state.
    process.stdin.pipe(bridge).pipe(child.stdin);
    child.stdout.pipe(process.stdout);
    child.stdin.on("error", (error) => {
      if (error.code !== "EPIPE") {
        console.error(`[kumiho-codex] MCP stdin failed: ${error.message}`);
      }
    });
  }
  const handlers = new Map();
  let requestedSignal = null;
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    const handler = () => {
      try {
        if (process.platform === "win32") {
          requestedSignal = signal;
          // A non-forced taskkill may let the Python root exit before its
          // descendants; after that Windows can no longer resolve the tree by
          // root PID. Kill the complete tree in one operation. If taskkill is
          // unavailable or fails, at least terminate the direct child.
          if (!terminateWindowsTree(child.pid, { force: true })) {
            child.kill("SIGKILL");
          }
        } else {
          // child.killed means only that a signal was sent, not that the child
          // exited. Forward repeated signals so the user can escalate.
          child.kill(signal);
        }
      } catch (error) {
        console.error(
          `[kumiho-codex] Could not forward ${signal} to Python: ${error.message}`,
        );
      }
    };
    handlers.set(signal, handler);
    process.on(signal, handler);
  }
  const removeHandlers = () => {
    for (const [signal, handler] of handlers) process.off(signal, handler);
    if (bridge) {
      process.stdin.unpipe(bridge);
      bridge.unpipe(child.stdin);
    }
  };

  child.once("error", (error) => {
    removeHandlers();
    console.error(`[kumiho-codex] Could not start Python: ${error.message}`);
    process.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    removeHandlers();
    if (signal && !requestedSignal) {
      try {
        process.kill(process.pid, signal);
        return;
      } catch {
        process.exitCode = 1;
        return;
      }
    }
    process.exitCode = code ?? 1;
  });
}

function startMcp(args) {
  startPython(SCRIPT_PATH, args, "MCP launcher", { bridgeThreadId: true });
}

function startOnboarding(args) {
  startPython(ONBOARD_PATH, args, "onboarding");
}

const args = process.argv.slice(2);
if (args[0] === "--doctor" && args.includes("--json")) doctorJson();
else if (args.length === 1 && args[0] === "--doctor") doctor();
else if (args[0] === "--onboard") startOnboarding(args.slice(1));
else if (args[0] === "--backfill") startPython(BACKFILL_PATH, args.slice(1), "backfill");
else startMcp(args);
