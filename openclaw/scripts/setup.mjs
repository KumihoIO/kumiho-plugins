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
 *  6. Authenticate with Kumiho Cloud
 *  7. Configure Dream State schedule
 *  8. Choose LLM model for Dream State   (cost-aware, lightweight recommended)
 *  9. Choose LLM model for Consolidation (cost-aware, smarter recommended)
 * 10. Write ~/.kumiho/preferences.json
 * 11. Print openclaw.json config hint
 */

import { existsSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import readline from "node:readline";

const IS_WIN = platform() === "win32";
const VENV_DIR = join(homedir(), ".kumiho", "venv");
const BIN = IS_WIN ? "Scripts" : "bin";
const EXT = IS_WIN ? ".exe" : "";
const VENV_PYTHON = join(VENV_DIR, BIN, `python${EXT}`);
const PREFS_PATH = join(homedir(), ".kumiho", "preferences.json");

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
    label: "gpt-4o-mini (OpenAI)",
    note: "cheapest — ~$0.018/month",
    provider: "openai", model: "gpt-4o-mini",
  },
  {
    label: "claude-sonnet-4-6 (Anthropic)",
    note: "~$0.39/month",
    provider: "anthropic", model: "claude-sonnet-4-6",
  },
  {
    label: "gpt-4o (OpenAI)",
    note: "~$0.30/month",
    provider: "openai", model: "gpt-4o",
  },
  { label: "Use agent default", note: "no extra API key needed", provider: null, model: null },
];

const CONSOLIDATION_MODELS = [
  {
    label: "claude-sonnet-4-6 (Anthropic)",
    note: "recommended — richest summaries",
    provider: "anthropic", model: "claude-sonnet-4-6",
    recommended: true,
  },
  {
    label: "claude-haiku-4-5 (Anthropic)",
    note: "budget — ~$0.18/month",
    provider: "anthropic", model: "claude-haiku-4-5-20251001",
  },
  {
    label: "gpt-4o (OpenAI)",
    note: "~$0.30/month",
    provider: "openai", model: "gpt-4o",
  },
  {
    label: "gpt-4o-mini (OpenAI)",
    note: "~$0.018/month — minimal quality",
    provider: "openai", model: "gpt-4o-mini",
  },
  { label: "Use agent default", note: "no extra API key needed", provider: null, model: null },
];

// ---------------------------------------------------------------------------
// Cost info display
// ---------------------------------------------------------------------------

function showDreamStateCostTable() {
  console.log();
  console.log(`  ${c.bold}Dream State${c.reset} classifies and enriches existing memories.`);
  console.log(`  It processes structured data — a smaller model is more than enough.`);
  console.log();
  console.log(`  ${c.dim}Estimated cost (one nightly run, average memory graph):${c.reset}`);
  console.log();
  console.log(`  ${c.dim}Model           Input       Output      Per night   Per month${c.reset}`);
  console.log(`  ${c.dim}─────────────── ─────────── ─────────── ─────────── ─────────${c.reset}`);
  console.log(`  Haiku           $0.25/1M    $1.25/1M    ~$0.001     ${c.green}~$0.03${c.reset}`);
  console.log(`  Sonnet          $3/1M       $15/1M      ~$0.011     ~$0.33`);
  console.log(`  GPT-4o-mini     $0.15/1M    $0.60/1M    ~$0.0006    ${c.green}~$0.018${c.reset}`);
  console.log();
}

function showConsolidationCostTable() {
  console.log();
  console.log(`  ${c.bold}Consolidation${c.reset} summarizes conversations into lasting long-term memories.`);
  console.log(`  A smarter model produces richer, more useful context for future sessions.`);
  console.log();
  console.log(`  ${c.dim}Estimated cost (average user: ~4-6 sessions/night):${c.reset}`);
  console.log();
  console.log(`  ${c.dim}Model           Per session   Per night   Per month${c.reset}`);
  console.log(`  ${c.dim}─────────────── ───────────── ─────────── ─────────${c.reset}`);
  console.log(`  Haiku           ~$0.001       ~$0.006     ${c.green}~$0.18${c.reset}`);
  console.log(`  Sonnet          ~$0.013       ~$0.078     ~$0.39`);
  console.log(`  GPT-4o-mini     ~$0.0006      ~$0.004     ${c.green}~$0.12${c.reset}`);
  console.log(`  GPT-4o          ~$0.008       ~$0.048     ~$0.29`);
  console.log();
  console.log(`  ${c.dim}Combined monthly (Haiku + Haiku): ~$0.21 — essentially noise.${c.reset}`);
  console.log(`  ${c.dim}Combined monthly (Sonnet + Sonnet): ~$0.72${c.reset}`);
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

// 6. Authenticate -------------------------------------------------------------
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

// 7. Dream State schedule -----------------------------------------------------
console.log();
console.log(`${c.bold}Step 7 / 9  —  Dream State Schedule${c.reset}`);

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

// 8. Dream State LLM model ---------------------------------------------------
console.log();
console.log(`${c.bold}Step 8 / 9  —  LLM Model for Dream State${c.reset}`);
showDreamStateCostTable();

const dreamModelChoice = await selectOption(
  rl,
  "Which model should Dream State use?",
  DREAM_MODELS,
);

if (dreamModelChoice.provider) {
  ok(`Dream State model: ${dreamModelChoice.model} (${dreamModelChoice.provider})`);
} else {
  ok("Dream State: using agent default model");
}

// 9. Consolidation LLM model -------------------------------------------------
console.log();
console.log(`${c.bold}Step 9 / 9  —  LLM Model for Consolidation${c.reset}`);
showConsolidationCostTable();

const consolidationModelChoice = await selectOption(
  rl,
  "Which model should Consolidation use?",
  CONSOLIDATION_MODELS,
);

if (consolidationModelChoice.provider) {
  ok(`Consolidation model: ${consolidationModelChoice.model} (${consolidationModelChoice.provider})`);
} else {
  ok("Consolidation: using agent default model");
}

rl.close();

// 10. Write preferences.json --------------------------------------------------
const prefs = {
  ...existingPrefs,
  dreamState: {
    schedule: finalCron ?? "off",
    scheduleKey: scheduleChoice.key,
    timezone: tz,
    ...(dreamModelChoice.provider
      ? { model: { provider: dreamModelChoice.provider, model: dreamModelChoice.model } }
      : {}),
  },
  consolidation: {
    ...(consolidationModelChoice.provider
      ? { model: { provider: consolidationModelChoice.provider, model: consolidationModelChoice.model } }
      : {}),
  },
};

mkdirSync(join(homedir(), ".kumiho"), { recursive: true });
writeFileSync(PREFS_PATH, JSON.stringify(prefs, null, 2), "utf8");
ok(`Preferences saved to ~/.kumiho/preferences.json`);

// 11. Print config hint -------------------------------------------------------
const configPython = VENV_PYTHON.replace(/\\/g, "\\\\");

console.log();
console.log(`${c.bold}${c.green}Setup complete!${c.reset}`);
console.log();
console.log("Preferences are auto-loaded from ~/.kumiho/preferences.json.");
console.log("To override any setting explicitly, add it to your openclaw.json:");
console.log();

const dreamModelJson = dreamModelChoice.provider
  ? `\n          "dreamStateModel": { "provider": "${dreamModelChoice.provider}", "model": "${dreamModelChoice.model}" },`
  : "";
const consolidationModelJson = consolidationModelChoice.provider
  ? `\n          "consolidationModel": { "provider": "${consolidationModelChoice.provider}", "model": "${consolidationModelChoice.model}" },`
  : "";
const scheduleJson = finalCron && scheduleChoice.key !== "off"
  ? `\n          "dreamStateSchedule": "${finalCron}",`
  : "";

console.log(`  ${c.cyan}"openclaw-kumiho"${c.reset}: {`);
console.log(`    ${c.cyan}"enabled"${c.reset}: true,`);
console.log(`    ${c.cyan}"config"${c.reset}: {`);
console.log(`      ${c.dim}// Mode + auth${c.reset}`);
console.log(`      ${c.cyan}"mode"${c.reset}: "local",`);
console.log(`      ${c.cyan}"userId"${c.reset}: "your-user-id",${scheduleJson}${dreamModelJson}${consolidationModelJson}`);
console.log();
console.log(`      ${c.dim}// Optional: override Python path${c.reset}`);
console.log(`      ${c.cyan}"local"${c.reset}: {`);
console.log(`        ${c.cyan}"pythonPath"${c.reset}: "${configPython}"`);
console.log(`      }`);
console.log(`    }`);
console.log(`  }`);
console.log();

// 12. Dream State standalone cron instructions ---------------------------------
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
