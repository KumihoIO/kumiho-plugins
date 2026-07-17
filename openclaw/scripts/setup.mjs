#!/usr/bin/env node
/**
 * Kumiho Python backend setup.
 *
 * Setup flow:
 *  1. Find Python 3.9+
 *  2. Create ~/.kumiho/venv
 *  3. Ensure pip is available
 *  4. Upgrade pip + install kumiho[mcp] + kumiho-memory[all]
 *  5. Verify kumiho.mcp_server
 *  6. Choose backend (Kumiho Cloud or self-hosted CE), then authenticate
 *     with Kumiho Cloud — skipped for CE, which runs tokenless
 *  7. Configure Dream State schedule
 *  8. Choose LLM model for Dream State   (cost-aware, lightweight recommended)
 *  9. Choose LLM model for Consolidation (cost-aware, smarter recommended)
 * 10. Collect LLM API key(s) for chosen providers
 * 11. Write ~/.kumiho/preferences.json
 * 12. Offer to update openclaw.json
 * 13. Print openclaw.json config hint
 */

import { existsSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createConnection } from "node:net";
import readline from "node:readline";

let detectOpenClawHostAuth;
let buildMemoryPreferences;
let buildOpenClawPluginConfig;
let getConsolidationModelOptions;
let getDreamStateModelOptions;
let getExplicitMemoryProviderOptions;
let getMemoryProviderBaseUrl;
let getSuggestedApiEnvVars;
let requiresExplicitMemoryProvider;

try {
  ({ detectOpenClawHostAuth } = await import(new URL("../dist/host-auth.js", import.meta.url)));
  ({
    buildMemoryPreferences,
    buildOpenClawPluginConfig,
    getConsolidationModelOptions,
    getDreamStateModelOptions,
    getExplicitMemoryProviderOptions,
    getMemoryProviderBaseUrl,
    getSuggestedApiEnvVars,
    requiresExplicitMemoryProvider,
  } = await import(new URL("../dist/setup-support.js", import.meta.url)));
} catch (error) {
  console.error("kumiho-setup requires the built dist files. Run npm run build first.");
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

const IS_WIN = platform() === "win32";
const VENV_DIR = join(homedir(), ".kumiho", "venv");
const BIN = IS_WIN ? "Scripts" : "bin";
const EXT = IS_WIN ? ".exe" : "";
const VENV_PYTHON = join(VENV_DIR, BIN, `python${EXT}`);
const PREFS_PATH = join(homedir(), ".kumiho", "preferences.json");
const OPENCLAW_CONFIG_PATH = join(homedir(), ".openclaw", "openclaw.json");

const c = {
  reset:  "\x1b[0m",
  bold:   "\x1b[1m",
  dim:    "\x1b[2m",
  cyan:   "\x1b[36m",
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  red:    "\x1b[31m",
  blue:   "\x1b[34m",
};

const log  = (msg) => console.log(`${c.cyan}[kumiho-setup]${c.reset} ${msg}`);
const ok   = (msg) => console.log(`${c.green}✓${c.reset} ${msg}`);
const warn = (msg) => console.log(`${c.yellow}⚠${c.reset} ${msg}`);
const die  = (msg) => { console.error(`${c.red}✗ ${msg}${c.reset}`); process.exit(1); };
const hr   = () => console.log(`${c.dim}${"─".repeat(55)}${c.reset}`);

// ---------------------------------------------------------------------------
// Interactive prompt helpers (readline-based, no third-party deps)
// ---------------------------------------------------------------------------

async function selectOption(rl, question, options) {
  console.log();
  console.log(`${c.bold}? ${question}${c.reset}`);
  hr();
  for (let i = 0; i < options.length; i++) {
    const opt = options[i];
    const star = opt.recommended ? `${c.green}★${c.reset}` : " ";
    const note = opt.note ? `  ${c.dim}${opt.note}${c.reset}` : "";
    console.log(`  ${star} ${i + 1}. ${opt.label}${note}`);
  }
  console.log();

  return new Promise((resolve) => {
    const ask = () => {
      rl.question(`  Enter number [1-${options.length}]: `, (answer) => {
        const n = parseInt(answer.trim(), 10);
        if (n >= 1 && n <= options.length) {
          resolve(options[n - 1]);
        } else {
          console.log(`  ${c.yellow}Please enter a number between 1 and ${options.length}.${c.reset}`);
          ask();
        }
      });
    };
    ask();
  });
}

async function askFreeText(rl, prompt, placeholder) {
  return new Promise((resolve) => {
    rl.question(`  ${prompt} [${c.dim}${placeholder}${c.reset}]: `, (answer) => {
      resolve(answer.trim() || placeholder);
    });
  });
}

async function askInput(rl, prompt, placeholder = "") {
  const suffix = placeholder ? ` [${c.dim}${placeholder}${c.reset}]` : "";
  return new Promise((resolve) => {
    rl.question(`  ${prompt}${suffix}: `, (answer) => {
      resolve(answer.trim());
    });
  });
}

async function askYesNo(rl, prompt, defaultYes = true) {
  const suffix = defaultYes ? "[Y/n]" : "[y/N]";
  return new Promise((resolve) => {
    const ask = () => {
      rl.question(`  ${prompt} ${suffix}: `, (answer) => {
        const normalized = answer.trim().toLowerCase();
        if (!normalized) {
          resolve(defaultYes);
          return;
        }
        if (["y", "yes"].includes(normalized)) {
          resolve(true);
          return;
        }
        if (["n", "no"].includes(normalized)) {
          resolve(false);
          return;
        }
        console.log(`  ${c.yellow}Please answer yes or no.${c.reset}`);
        ask();
      });
    };
    ask();
  });
}

async function resolveModelChoice(rl, question, options, customDefaultModel) {
  const choice = await selectOption(rl, question, options);
  if (choice.model !== "__custom__") {
    return choice;
  }

  console.log();
  const customModel = await askFreeText(
    rl,
    `${choice.provider} model id`,
    customDefaultModel,
  );
  return {
    ...choice,
    model: customModel,
    note: `custom — ${customModel}`,
  };
}

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function mergeObjects(base, extra) {
  const result = isPlainObject(base) ? { ...base } : {};
  for (const [key, value] of Object.entries(extra)) {
    if (isPlainObject(value) && isPlainObject(result[key])) {
      result[key] = mergeObjects(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Schedule / model option tables
// ---------------------------------------------------------------------------

const SCHEDULES = [
  { label: "Nightly at 3 AM",     cron: "0 3 * * *",   key: "nightly-3am",     recommended: true },
  { label: "Nightly at midnight", cron: "0 0 * * *",   key: "nightly-midnight" },
  { label: "Every 6 hours",       cron: "0 */6 * * *", key: "every-6h" },
  { label: "Weekly (Sunday 3 AM)",cron: "0 3 * * 0",   key: "weekly-sun-3am" },
  { label: "Custom cron expression", cron: null,        key: "custom" },
  { label: "Skip for now",        cron: null,           key: "off" },
];

// Cost note: average user, nightly single-run estimate
const DREAM_MODELS = [
  {
    label: "claude-haiku-4-5 (Anthropic)",
    note: "recommended — ~$0.03/month",
    provider: "anthropic", model: "claude-haiku-4-5-20251001",
    recommended: true,
  },
  {
    label: "gpt-5-nano (OpenAI)",
    note: "recommended lightweight OpenAI Dream State preset",
    provider: "openai", model: "gpt-5-nano",
  },
  {
    label: "gpt-5-mini (OpenAI)",
    note: "quality upgrade for Dream State",
    provider: "openai", model: "gpt-5-mini",
  },
  {
    label: "claude-sonnet-4-6 (Anthropic)",
    note: "~$0.39/month",
    provider: "anthropic", model: "claude-sonnet-4-6",
  },
  {
    label: "Custom OpenAI model",
    note: "type any OpenAI model id",
    provider: "openai", model: "__custom__",
  },
  {
    label: "Custom Anthropic model",
    note: "type any Anthropic model id",
    provider: "anthropic", model: "__custom__",
  },
  { label: "Use agent default", note: "no extra API key needed", provider: null, model: null },
];

const CONSOLIDATION_MODELS = [
  {
    label: "claude-haiku-4-5 (Anthropic)",
    note: "recommended — lightweight direct summary model",
    provider: "anthropic", model: "claude-haiku-4-5-20251001",
    recommended: true,
  },
  {
    label: "claude-sonnet-4-6 (Anthropic)",
    note: "quality upgrade for richer summaries",
    provider: "anthropic", model: "claude-sonnet-4-6",
  },
  {
    label: "gpt-5-mini (OpenAI)",
    note: "recommended — lightweight OpenAI summary model",
    provider: "openai", model: "gpt-5-mini",
  },
  {
    label: "gpt-5-nano (OpenAI)",
    note: "cheaper OpenAI summary model",
    provider: "openai", model: "gpt-5-nano",
  },
  {
    label: "Custom OpenAI model",
    note: "type any OpenAI model id",
    provider: "openai", model: "__custom__",
  },
  {
    label: "Custom Anthropic model",
    note: "type any Anthropic model id",
    provider: "anthropic", model: "__custom__",
  },
  { label: "Use agent default", note: "no extra API key needed", provider: null, model: null },
];

// ---------------------------------------------------------------------------
// Backend selection (Kumiho Cloud vs self-hosted CE)
// ---------------------------------------------------------------------------

const CE_DEFAULT_ENDPOINT = "127.0.0.1:9190";
const CE_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379";

const BACKENDS = [
  {
    key: "cloud",
    label: "Kumiho Cloud",
    note: "managed backend — sign in with your kumiho.io account",
    recommended: true,
  },
  {
    key: "ce",
    label: "Self-hosted Community Edition (CE)",
    note: "your own kumiho-server CE — tokenless, fully offline, no cloud login",
  },
];

/** Best-effort TCP reachability probe for the CE gRPC endpoint. Never fatal. */
function probeTcp(endpoint, timeoutMs = 1500) {
  const [host, portRaw] = endpoint.split(":");
  const port = parseInt(portRaw, 10);
  if (!host || !port) return Promise.resolve(null); // unparseable — skip probe
  return new Promise((resolve) => {
    let settled = false;
    const done = (up) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(up);
    };
    const socket = createConnection({ host, port });
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
    socket.setTimeout(timeoutMs, () => done(false));
  });
}

// ---------------------------------------------------------------------------
// Cost info display
// ---------------------------------------------------------------------------

function showDreamStateCostTable() {
  console.log();
  console.log(`  ${c.bold}Dream State${c.reset} classifies and enriches existing memories.`);
  console.log(`  It processes structured data — a smaller model is more than enough.`);
  console.log();
  console.log(`  ${c.dim}Preset guidance (one nightly run, average memory graph):${c.reset}`);
  console.log();
  console.log(`  ${c.dim}Recommended presets:${c.reset}`);
  console.log(`  Haiku           cheapest Anthropic option`);
  console.log(`  GPT-5-nano      cheapest OpenAI Dream State preset`);
  console.log(`  GPT-5-mini      quality upgrade for OpenAI Dream State`);
  console.log(`  Custom OpenAI   type any direct OpenAI model id`);
  console.log();
}

function showConsolidationCostTable() {
  console.log();
  console.log(`  ${c.bold}Consolidation${c.reset} summarizes conversations into lasting long-term memories.`);
  console.log(`  A smarter model produces richer, more useful context for future sessions.`);
  console.log();
  console.log(`  ${c.dim}Preset guidance (average user: ~4-6 sessions/night):${c.reset}`);
  console.log();
  console.log(`  ${c.dim}Recommended presets:${c.reset}`);
  console.log(`  Haiku           cheapest Anthropic summary option`);
  console.log(`  Sonnet          richer Anthropic summaries`);
  console.log(`  GPT-5-mini      lightweight OpenAI summary model`);
  console.log(`  GPT-5-nano      cheaper OpenAI summary option`);
  console.log(`  Custom OpenAI   type any direct OpenAI model id`);
  console.log();
}

// ---------------------------------------------------------------------------
// Find a usable base Python (3.9+) on PATH
// ---------------------------------------------------------------------------

function findBasePython() {
  for (const cmd of ["python3", "python"]) {
    const r = spawnSync(cmd, ["--version"], { encoding: "utf8" });
    if (r.status !== 0) continue;

    const ver = (r.stdout || r.stderr).trim();
    const m = ver.match(/Python (\d+)\.(\d+)/);
    if (!m) continue;

    const [, major, minor] = m.map(Number);
    if (major > 3 || (major === 3 && minor >= 9)) {
      return { cmd, ver };
    }
    warn(`Found ${ver} but Python 3.9+ is required — skipping.`);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

log("Setting up Kumiho Python backend...");
log(`Target venv: ${VENV_DIR}`);
console.log();

// 1. Locate base Python -------------------------------------------------------
const base = findBasePython();
if (!base) {
  die(
    "Python 3.9+ not found on PATH.\n\n" +
    "  Windows : https://www.python.org/downloads/\n" +
    "  macOS   : brew install python3\n" +
    "  Linux   : sudo apt install python3 python3-venv\n"
  );
}
ok(`Base Python: ${base.ver}  (${base.cmd})`);

// 2. Create virtualenv --------------------------------------------------------
if (!existsSync(VENV_PYTHON)) {
  log("Creating virtualenv...");
  mkdirSync(join(homedir(), ".kumiho"), { recursive: true });

  const r = spawnSync(base.cmd, ["-m", "venv", VENV_DIR], { stdio: "inherit" });
  if (r.status !== 0) {
    die(
      "Failed to create virtualenv.\n\n" +
      "  Linux fix : sudo apt install python3-venv\n" +
      `  Manual    : ${base.cmd} -m venv ${VENV_DIR}\n`
    );
  }
  ok("Virtualenv created.");
} else {
  ok("Virtualenv already exists — reusing.");
}

// 3. Ensure pip is available (some distros create venvs without it) -----------
{
  const pipCheck = spawnSync(VENV_PYTHON, ["-m", "pip", "--version"], { encoding: "utf8" });
  if (pipCheck.status !== 0) {
    log("pip not found in venv — trying ensurepip...");
    const ensurePip = spawnSync(VENV_PYTHON, ["-m", "ensurepip", "--upgrade"], { stdio: "inherit" });
    if (ensurePip.status !== 0) {
      // ensurepip also missing — download get-pip.py using the base Python
      log("ensurepip unavailable — downloading get-pip.py...");
      const getPip = spawnSync(
        base.cmd,
        [
          "-c",
          [
            "import urllib.request, subprocess, sys, tempfile, os",
            "f = tempfile.mktemp(suffix='.py')",
            "urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', f)",
            "r = subprocess.call([sys.argv[1], f])",
            "os.unlink(f)",
            "sys.exit(r)",
          ].join("; "),
          VENV_PYTHON,
        ],
        { stdio: "inherit" }
      );
      if (getPip.status !== 0) {
        die(
          "Failed to install pip.\n\n" +
          "  Linux fix : sudo apt install python3-pip\n" +
          `  Or run    : curl https://bootstrap.pypa.io/get-pip.py | ${VENV_PYTHON}\n`
        );
      }
      ok("pip installed via get-pip.py.");
    } else {
      ok("pip bootstrapped via ensurepip.");
    }
  }
}

// 4a. Upgrade pip -------------------------------------------------------------
log("Upgrading pip...");
spawnSync(
  VENV_PYTHON,
  ["-m", "pip", "install", "--quiet", "--upgrade", "pip"],
  { stdio: "inherit" }
);

// 4b. Install packages --------------------------------------------------------
const PACKAGES = ["kumiho[mcp]", "kumiho-memory[all]"];
log(`Installing: ${PACKAGES.join("  ")} ...`);
console.log();

const install = spawnSync(
  VENV_PYTHON,
  ["-m", "pip", "install", "--upgrade", ...PACKAGES],
  { stdio: "inherit" }
);
if (install.status !== 0) {
  die("pip install failed. See output above.");
}
console.log();
ok(`Installed: ${PACKAGES.join(", ")}`);

// 5. Verify kumiho.mcp_server -------------------------------------------------
log("Verifying kumiho.mcp_server...");
const verify = spawnSync(
  VENV_PYTHON,
  ["-c", "from kumiho.mcp_server import main; print('ok')"],
  { encoding: "utf8" }
);
if (verify.status !== 0 || !verify.stdout.includes("ok")) {
  die(`Verification failed:\n${verify.stderr || verify.stdout}`);
}
ok("kumiho.mcp_server verified.");

// 6. Choose backend, then authenticate ----------------------------------------
// A temporary readline asks the backend question and is closed BEFORE the
// Python login runs — the auth CLI takes stdin with stdio: "inherit", and the
// main wizard readline is only created after it finishes (see below).
let ceSelection = null; // { endpoint, redisUrl } when CE is chosen
{
  const backendRl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const backendChoice = await selectOption(
    backendRl,
    "Which Kumiho backend should this OpenClaw use?",
    BACKENDS,
  );

  if (backendChoice.key === "ce") {
    console.log();
    console.log(`  CE runs tokenless against your own kumiho-server deployment.`);
    console.log(`  ${c.dim}Deploy it first: https://github.com/kumihoclouds/kumiho-server${c.reset}`);
    console.log();
    const ceEndpoint = await askFreeText(backendRl, "CE gRPC endpoint (host:port)", CE_DEFAULT_ENDPOINT);
    const ceRedisUrl = await askFreeText(backendRl, "CE working-memory Redis URL", CE_DEFAULT_REDIS_URL);
    ceSelection = { endpoint: ceEndpoint, redisUrl: ceRedisUrl };

    const reachable = await probeTcp(ceEndpoint);
    if (reachable === true) {
      ok(`CE endpoint ${ceEndpoint} is reachable.`);
    } else if (reachable === false) {
      warn(
        `CE endpoint ${ceEndpoint} is not reachable right now.\n` +
        `  Setup will continue — start kumiho-server CE before using the plugin.`,
      );
    }
  }
  backendRl.close();
}

if (ceSelection) {
  ok("CE backend selected — Kumiho Cloud login skipped (CE runs tokenless).");
} else {
const checkAuth = spawnSync(
  VENV_PYTHON,
  ["-c", `
import sys, json, time, os
from pathlib import Path

creds_path = Path.home() / ".kumiho" / "kumiho_authentication.json"
if not creds_path.exists():
    print("not_logged_in")
    sys.exit(0)

try:
    data = json.loads(creds_path.read_text())
    expires_at = int(data.get("expires_at", 0))
    refresh_token = data.get("refresh_token", "")
    # Valid if refresh token present (can always refresh, even if id_token expired)
    if refresh_token:
        print("logged_in:" + data.get("email", ""))
    else:
        print("not_logged_in")
except Exception:
    print("not_logged_in")
`],
  { encoding: "utf8" }
);

const authStatus = (checkAuth.stdout ?? "").trim();

if (authStatus.startsWith("logged_in:")) {
  const email = authStatus.split(":")[1];
  ok(`Already authenticated as ${email} — skipping login.`);
} else {
  console.log();
  log("Authenticating with Kumiho Cloud...");
  console.log("  Enter your KumihoClouds account credentials.");
  console.log("  Don't have an account? Sign up at https://kumiho.io");
  console.log();

  const loginResult = spawnSync(
    VENV_PYTHON,
    ["-m", "kumiho.auth_cli", "login"],
    { stdio: "inherit" }
  );

  if (loginResult.status !== 0) {
    warn(
      "Login step failed or was skipped.\n" +
      `  Run manually later:  ${VENV_PYTHON} -m kumiho.auth_cli login`
    );
  } else {
    ok("Authenticated and credentials saved to ~/.kumiho/kumiho_authentication.json");
  }
}
}

// ---------------------------------------------------------------------------
// 7–9. Interactive wizard (Dream State + LLM models)
// After Python auth completes, we take over stdin with readline.
// ---------------------------------------------------------------------------

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

// Load existing preferences if present (for re-run defaults)
let existingPrefs = {};
try {
  if (existsSync(PREFS_PATH)) {
    existingPrefs = JSON.parse(readFileSync(PREFS_PATH, "utf8"));
  }
} catch { /* ignore */ }

console.log();
console.log(`${c.bold}${c.cyan}── Kumiho Configuration Wizard ────────────────────────────${c.reset}`);

const activeHostAuth = detectOpenClawHostAuth();
const forceExplicitMemoryProvider = requiresExplicitMemoryProvider(activeHostAuth);
const totalWizardSteps = forceExplicitMemoryProvider ? 12 : 11;
let wizardStep = 7;

// 7. Dream State schedule -----------------------------------------------------
console.log();
console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  Dream State Schedule${c.reset}`);

const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const scheduleChoice = await selectOption(rl, "When should Dream State run? (memory maintenance)", SCHEDULES);

let finalCron = scheduleChoice.cron;
if (scheduleChoice.key === "custom") {
  finalCron = await askFreeText(rl, "Enter cron expression", "0 3 * * *");
}

if (scheduleChoice.key !== "off") {
  console.log();
  console.log(`  ${c.dim}Timezone: ${tz} (detected)${c.reset}`);
  ok(`Cron job scheduled: kumiho-dream-state (${finalCron} @ ${tz})`);
} else {
  warn("Dream State scheduling skipped — you can configure it later in openclaw.json.");
}

let selectedMemoryProvider = null;
let selectedMemoryBaseUrl = "";
if (forceExplicitMemoryProvider) {
  console.log();
  console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  Memory LLM Provider${c.reset}`);
  console.log();
  console.log(`  OpenClaw is currently using host-only OpenAI OAuth for its own model access.`);
  console.log(`  Kumiho Dream State and session consolidation call providers directly, so they cannot reuse that host auth.`);
  if (activeHostAuth?.profileKey) {
    console.log(`  ${c.dim}Detected profile: ${activeHostAuth.profileKey} (${activeHostAuth.rawProvider}, ${activeHostAuth.authMode || "unknown"})${c.reset}`);
  }
  if (activeHostAuth?.modelBaseUrl || activeHostAuth?.modelApi) {
    const details = [activeHostAuth.modelBaseUrl, activeHostAuth.modelApi].filter(Boolean).join(" / ");
    console.log(`  ${c.dim}Host model path: ${details}${c.reset}`);
  }
  console.log();

  const providerChoice = await selectOption(
    rl,
    "Which direct LLM provider should Kumiho use for Dream State and Consolidation?",
    getExplicitMemoryProviderOptions(),
  );
  selectedMemoryProvider = providerChoice.key;
  selectedMemoryBaseUrl = getMemoryProviderBaseUrl(selectedMemoryProvider);
  ok(`Memory provider: ${selectedMemoryProvider}`);
}

// Dream State LLM model ------------------------------------------------------
console.log();
console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  LLM Model for Dream State${c.reset}`);
if (ceSelection) {
  console.log();
  console.log(`  ${c.dim}CE note: the keyless core memory tools (engage, reflect, recall) need no LLM.${c.reset}`);
  console.log(`  ${c.dim}Dream State and consolidation summaries are the only features that call one —${c.reset}`);
  console.log(`  ${c.dim}pick "Use agent default" to stay fully keyless, or a local endpoint via KUMIHO_LLM_BASE_URL.${c.reset}`);
}
if (selectedMemoryProvider) {
  console.log();
  console.log(`  ${c.bold}Dream State${c.reset} classifies and enriches existing memories.`);
  console.log(`  It processes structured data — a lightweight model is more than enough.`);
  console.log();
} else {
  showDreamStateCostTable();
}

const dreamModelOptions = selectedMemoryProvider
  ? getDreamStateModelOptions(selectedMemoryProvider)
  : DREAM_MODELS;
const defaultDreamModel =
  dreamModelOptions.find((option) => option.recommended)?.model ||
  dreamModelOptions[0]?.model ||
  "gpt-5-nano";
const dreamModelChoice = await resolveModelChoice(
  rl,
  "Which model should Dream State use?",
  dreamModelOptions,
  defaultDreamModel,
);

if (dreamModelChoice.provider) {
  ok(`Dream State model: ${dreamModelChoice.model} (${dreamModelChoice.provider})`);
} else {
  ok("Dream State: using agent default model");
}

// Consolidation LLM model ----------------------------------------------------
console.log();
console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  LLM Model for Consolidation${c.reset}`);
if (selectedMemoryProvider) {
  console.log();
  console.log(`  ${c.bold}Consolidation${c.reset} summarizes conversations into long-term memories.`);
  console.log(`  A lightweight direct model is the safest default; upgrade only if you want richer summaries.`);
  console.log();
} else {
  showConsolidationCostTable();
}

const consolidationModelOptions = selectedMemoryProvider
  ? getConsolidationModelOptions(selectedMemoryProvider)
  : CONSOLIDATION_MODELS;
const defaultConsolidationModel =
  consolidationModelOptions.find((option) => option.recommended)?.model ||
  consolidationModelOptions[0]?.model ||
  "gpt-5-mini";
const consolidationModelChoice = await resolveModelChoice(
  rl,
  "Which model should Consolidation use?",
  consolidationModelOptions,
  defaultConsolidationModel,
);

if (consolidationModelChoice.provider) {
  ok(`Consolidation model: ${consolidationModelChoice.model} (${consolidationModelChoice.provider})`);
} else {
  ok("Consolidation: using agent default model");
}

// Collect LLM API key(s) -----------------------------------------------------
let dreamStateApiKey = "";
let sharedMemoryApiKey = "";

if (forceExplicitMemoryProvider && selectedMemoryProvider) {
  const envVars = getSuggestedApiEnvVars(selectedMemoryProvider);
  const existingEnvVar = envVars.find((name) => typeof process.env[name] === "string" && process.env[name].trim());
  const existingEnv = existingEnvVar ? process.env[existingEnvVar].trim() : "";
  const existingPref =
    existingPrefs?.llm?.provider === selectedMemoryProvider &&
    typeof existingPrefs?.llm?.apiKey === "string"
      ? existingPrefs.llm.apiKey
      : "";

  console.log();
  console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  Memory Provider API Key${c.reset}`);
  console.log();
  console.log(`  Dream State and Consolidation will both use this direct provider credential.`);
  console.log(`  The API key is stored in ~/.kumiho/preferences.json for the local MCP process and Dream State runner.`);
  if (selectedMemoryProvider === "gemini") {
    console.log(`  ${c.dim}Gemini base URL will be set to ${selectedMemoryBaseUrl}.${c.reset}`);
  }
  console.log();

  if (existingPref) {
    const masked = existingPref.slice(0, 8) + "..." + existingPref.slice(-4);
    ok(`${selectedMemoryProvider} API key already saved: ${masked}`);
    sharedMemoryApiKey = existingPref;
  } else {
    if (existingEnv) {
      const masked = existingEnv.slice(0, 8) + "..." + existingEnv.slice(-4);
      console.log(`  ${c.dim}Found ${existingEnvVar} in environment: ${masked}${c.reset}`);
    }

    while (!sharedMemoryApiKey) {
      const key = await askInput(
        rl,
        `${selectedMemoryProvider} API key`,
        existingEnv ? "press Enter to use detected env var" : "required",
      );

      if (key) {
        sharedMemoryApiKey = key;
      } else if (existingEnv) {
        sharedMemoryApiKey = existingEnv;
      } else {
        warn(`A direct ${selectedMemoryProvider} API key is required for Dream State and Consolidation.`);
      }
    }

    ok(`${selectedMemoryProvider} API key saved for Kumiho memory.`);
  }
} else if (scheduleChoice.key !== "off") {
  const provider = dreamModelChoice.provider || "anthropic";
  const envVars = getSuggestedApiEnvVars(provider);
  const existingEnvVar = envVars.find((name) => typeof process.env[name] === "string" && process.env[name].trim());
  const existingEnv = existingEnvVar ? process.env[existingEnvVar].trim() : "";
  const existingPref = existingPrefs?.dreamState?.model?.apiKey;

  console.log();
  console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  LLM API Key for Dream State${c.reset}`);
  console.log();
  console.log(`  Dream State runs standalone via cron — it can't inherit the host's LLM key.`);
  console.log(`  The API key is stored in ~/.kumiho/preferences.json for the cron runner.`);
  console.log(`  ${c.dim}(Consolidation may use the active host credential when available.)${c.reset}`);
  console.log();

  if (existingPref) {
    const masked = existingPref.slice(0, 8) + "..." + existingPref.slice(-4);
    ok(`${provider} API key already saved: ${masked}`);
    dreamStateApiKey = existingPref;
  } else {
    if (existingEnv) {
      const masked = existingEnv.slice(0, 8) + "..." + existingEnv.slice(-4);
      console.log(`  ${c.dim}Found ${existingEnvVar} in environment: ${masked}${c.reset}`);
    }

    const key = await askInput(
      rl,
      `${provider} API key`,
      existingEnv ? "press Enter to use detected env var" : "optional",
    );

    if (key) {
      dreamStateApiKey = key;
      ok(`${provider} API key saved for Dream State.`);
    } else if (existingEnv) {
      dreamStateApiKey = existingEnv;
      ok(`${provider} API key saved from ${existingEnvVar}.`);
    } else {
      warn(`No ${provider} API key provided. Set ${envVars[0]} before running Dream State standalone.`);
    }
  }
} else {
  console.log();
  console.log(`${c.bold}Step ${wizardStep++} / ${totalWizardSteps}  —  LLM API Key for Dream State${c.reset}`);
  console.log();
  console.log(`  ${c.dim}Dream State scheduling is off — skipping API key setup.${c.reset}`);
  if (forceExplicitMemoryProvider) {
    console.log(`  ${c.dim}Explicit provider credentials are still required for session consolidation.${c.reset}`);
  } else {
    console.log(`  ${c.dim}If you enable it later, re-run kumiho-setup or set the env var.${c.reset}`);
  }
}

// Write preferences.json -----------------------------------------------------
const { llm: _unusedSharedLlm, ...existingPrefsWithoutLlm } = existingPrefs ?? {};
const prefs = forceExplicitMemoryProvider && selectedMemoryProvider
  ? buildMemoryPreferences({
      existingPrefs,
      schedule: finalCron ?? "off",
      scheduleKey: scheduleChoice.key,
      timezone: tz,
      provider: selectedMemoryProvider,
      apiKey: sharedMemoryApiKey,
      baseUrl: selectedMemoryBaseUrl || undefined,
      dreamModelChoice,
      consolidationModelChoice,
    })
  : {
      ...existingPrefsWithoutLlm,
      dreamState: {
        ...(existingPrefs?.dreamState ?? {}),
        schedule: finalCron ?? "off",
        scheduleKey: scheduleChoice.key,
        timezone: tz,
        model: {
          ...(dreamModelChoice.provider
            ? { provider: dreamModelChoice.provider, model: dreamModelChoice.model }
            : {}),
          ...(dreamStateApiKey ? { apiKey: dreamStateApiKey } : {}),
        },
      },
      consolidation: {
        ...(existingPrefs?.consolidation ?? {}),
        ...(consolidationModelChoice.provider
          ? {
              model: {
                provider: consolidationModelChoice.provider,
                model: consolidationModelChoice.model,
              },
            }
          : {}),
      },
    };

mkdirSync(join(homedir(), ".kumiho"), { recursive: true });
writeFileSync(PREFS_PATH, JSON.stringify(prefs, null, 2), "utf8");
ok(`Preferences saved to ~/.kumiho/preferences.json`);

// Offer to update openclaw.json ----------------------------------------------
const openClawPluginConfig = buildOpenClawPluginConfig({
  pythonPath: VENV_PYTHON,
  ...(ceSelection ? { ce: ceSelection } : {}),
  ...(finalCron && scheduleChoice.key !== "off" ? { dreamStateSchedule: finalCron } : {}),
  dreamModelChoice,
  consolidationModelChoice,
});

let openClawConfigUpdated = false;
let openClawConfigUpdateError = "";
const openClawConfigStep = wizardStep++;

console.log();
console.log(`${c.bold}Step ${openClawConfigStep} / ${totalWizardSteps}  —  OpenClaw Config${c.reset}`);
console.log();
console.log(`  Config path: ${c.cyan}${OPENCLAW_CONFIG_PATH}${c.reset}`);
console.log(`  kumiho-setup can merge the plugin entry automatically if that file exists.`);
console.log();

if (existsSync(OPENCLAW_CONFIG_PATH)) {
  const shouldUpdate = await askYesNo(
    rl,
    "Update openclaw.json with the openclaw-kumiho plugin config now?",
    true,
  );

  if (shouldUpdate) {
    try {
      const existing = JSON.parse(readFileSync(OPENCLAW_CONFIG_PATH, "utf8"));
      const plugins = isPlainObject(existing.plugins) ? { ...existing.plugins } : {};
      const entries = isPlainObject(plugins.entries) ? { ...plugins.entries } : {};
      const existingEntry =
        isPlainObject(entries["openclaw-kumiho"]) ? entries["openclaw-kumiho"] : {};
      const existingEntryConfig =
        isPlainObject(existingEntry.config) ? existingEntry.config : {};

      entries["openclaw-kumiho"] = {
        ...existingEntry,
        enabled: true,
        config: mergeObjects(existingEntryConfig, openClawPluginConfig),
      };

      plugins.entries = entries;

      const updated = {
        ...existing,
        plugins,
      };

      writeFileSync(OPENCLAW_CONFIG_PATH, JSON.stringify(updated, null, 2), "utf8");
      ok(`Updated ${OPENCLAW_CONFIG_PATH}`);
      openClawConfigUpdated = true;
    } catch (err) {
      openClawConfigUpdateError = err instanceof Error ? err.message : String(err);
      warn(`Could not update openclaw.json automatically: ${openClawConfigUpdateError}`);
    }
  } else {
    warn("Skipped automatic openclaw.json update.");
  }
} else {
  warn(`openclaw.json not found at ${OPENCLAW_CONFIG_PATH}`);
}

rl.close();

// 13. Print config hint -------------------------------------------------------
const configPython = VENV_PYTHON.replace(/\\/g, "\\\\");

console.log();
console.log(`${c.bold}${c.green}Setup complete!${c.reset}`);
console.log();
console.log("Preferences are auto-loaded from ~/.kumiho/preferences.json.");
if (openClawConfigUpdated) {
  console.log(`openclaw.json was updated at ${OPENCLAW_CONFIG_PATH}.`);
  console.log("Review the merged plugin entry below if you want to tweak it further:");
} else {
  console.log("Add or verify this plugin entry in your openclaw.json:");
  console.log(`Path: ${OPENCLAW_CONFIG_PATH}`);
  if (openClawConfigUpdateError) {
    console.log(`Reason automatic update failed: ${openClawConfigUpdateError}`);
  }
}
console.log();

console.log(`Place this under ${c.cyan}plugins.entries${c.reset} in openclaw.json:`);
console.log();
console.log(`  ${c.cyan}"openclaw-kumiho"${c.reset}: {`);
console.log(`    ${c.cyan}"enabled"${c.reset}: true,`);
console.log(`    ${c.cyan}"config"${c.reset}: {`);
const configLines = [
  { text: `      ${c.cyan}"mode"${c.reset}: "local"`, comma: true },
  { text: `      ${c.dim}// Optional: set userId if you want a fixed identity override${c.reset}`, comma: false },
  { text: `      ${c.dim}// "userId": "your-user-id",${c.reset}`, comma: false },
];
if (ceSelection) {
  configLines.push(
    { text: `      ${c.cyan}"ce"${c.reset}: {`, comma: false },
    { text: `        ${c.cyan}"enabled"${c.reset}: true,`, comma: false },
    { text: `        ${c.cyan}"endpoint"${c.reset}: "${ceSelection.endpoint}",`, comma: false },
    { text: `        ${c.cyan}"redisUrl"${c.reset}: "${ceSelection.redisUrl}"`, comma: false },
    { text: `      }`, comma: true },
  );
}
if (forceExplicitMemoryProvider) {
  configLines.push(
    { text: `      ${c.dim}// Direct memory-provider credentials live in ~/.kumiho/preferences.json${c.reset}`, comma: false },
  );
}
if (dreamModelChoice.provider === "openai" || consolidationModelChoice.provider === "openai") {
  configLines.push(
    { text: `      ${c.dim}// OpenClaw OAuth / Codex users: keep provider as "openai" here.${c.reset}`, comma: false },
  );
}
configLines.push(
  { text: `      ${c.cyan}"local"${c.reset}: {`, comma: false },
  { text: `        ${c.cyan}"pythonPath"${c.reset}: "${configPython}",`, comma: false },
  { text: `        ${c.cyan}"command"${c.reset}: "kumiho.mcp_server"`, comma: false },
  { text: `      }`, comma: true },
);
if (finalCron && scheduleChoice.key !== "off") {
  configLines.push({ text: `      ${c.cyan}"dreamStateSchedule"${c.reset}: "${finalCron}"`, comma: true });
}
if (dreamModelChoice.provider) {
  configLines.push(
    {
      text:
        `      ${c.cyan}"dreamStateModel"${c.reset}: { ` +
        `${c.cyan}"provider"${c.reset}: "${dreamModelChoice.provider}", ` +
        `${c.cyan}"model"${c.reset}: "${dreamModelChoice.model}" }`,
      comma: true,
    },
  );
}
if (consolidationModelChoice.provider) {
  configLines.push(
    {
      text:
        `      ${c.cyan}"consolidationModel"${c.reset}: { ` +
        `${c.cyan}"provider"${c.reset}: "${consolidationModelChoice.provider}", ` +
        `${c.cyan}"model"${c.reset}: "${consolidationModelChoice.model}" }`,
      comma: true,
    },
  );
}
for (let i = 0; i < configLines.length; i++) {
  const line = configLines[i];
  const hasTrailingProperty = configLines.slice(i + 1).some((item) => item.comma);
  const suffix = line.comma && hasTrailingProperty ? "," : "";
  console.log(`${line.text}${suffix}`);
}
console.log(`    }`);
console.log(`  }`);
console.log();

// 14. Dream State standalone cron instructions ---------------------------------
if (finalCron && scheduleChoice.key !== "off") {
  const pythonCmd = IS_WIN
    ? VENV_PYTHON.replace(/\\/g, "\\\\")
    : VENV_PYTHON;

  console.log(`${c.bold}${c.cyan}── Dream State Standalone Runner ──────────────────────────${c.reset}`);
  console.log();
  console.log("Dream State can run standalone (no plugin or active session needed).");
  console.log(`The CLI is installed at: ${c.green}${VENV_PYTHON.replace(/python(\.exe)?$/, `kumiho-memory${EXT}`)}${c.reset}`);
  console.log();

  if (IS_WIN) {
    // Windows Task Scheduler
    console.log(`${c.bold}Option A: Windows Task Scheduler${c.reset}`);
    console.log(`  Run this in PowerShell to create a scheduled task:`);
    console.log();
    console.log(`  ${c.dim}$action = New-ScheduledTaskAction -Execute "${VENV_PYTHON}" -Argument "-m kumiho_memory dream"${c.reset}`);
    console.log(`  ${c.dim}$trigger = New-ScheduledTaskTrigger -Daily -At 3AM${c.reset}`);
    console.log(`  ${c.dim}Register-ScheduledTask -TaskName "KumihoDreamState" -Action $action -Trigger $trigger${c.reset}`);
  } else {
    // Unix crontab
    console.log(`${c.bold}Option A: crontab (recommended)${c.reset}`);
    console.log(`  Add this line to your crontab (${c.dim}crontab -e${c.reset}):`);
    console.log();
    console.log(`  ${c.green}${finalCron} ${VENV_PYTHON} -m kumiho_memory dream >> ~/.kumiho/dream-state.log 2>&1${c.reset}`);
  }
  console.log();
  console.log(`${c.bold}Option B: Manual / test run${c.reset}`);
  console.log(`  ${c.green}${pythonCmd} -m kumiho_memory dream${c.reset}`);
  console.log(`  ${c.green}${pythonCmd} -m kumiho_memory dream --dry-run${c.reset}  ${c.dim}# preview without mutations${c.reset}`);
  console.log();
  console.log(`${c.bold}Option C: In-session (plugin loaded)${c.reset}`);
  console.log(`  The OpenClaw plugin also schedules Dream State via setTimeout while active.`);
  console.log(`  The standalone runner above ensures it runs even when no session is open.`);
  console.log();
}
