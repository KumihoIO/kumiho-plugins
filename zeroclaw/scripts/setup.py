#!/usr/bin/env python3
"""Kumiho Memory setup wizard for ZeroClaw.

Interactive setup that:
  1. Finds or creates a Python venv with kumiho packages
  2. Authenticates with Kumiho Cloud (paste API token or use existing)
  3. Detects ZeroClaw config.toml and patches MCP server config
  4. Copies the skill into ZeroClaw's skills directory
  5. Runs the skill ingestion script to populate CognitiveMemory/Skills

Usage:
    python scripts/setup.py
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent  # kumiho-plugins/zeroclaw/
IS_WIN = platform.system() == "Windows"
KUMIHO_DIR = Path.home() / ".kumiho"
VENV_DIR = KUMIHO_DIR / "venv"
BIN = "Scripts" if IS_WIN else "bin"
EXT = ".exe" if IS_WIN else ""
VENV_PYTHON = VENV_DIR / BIN / f"python{EXT}"
CRED_PATH = KUMIHO_DIR / "kumiho_authentication.json"
PREFS_PATH = KUMIHO_DIR / "preferences.json"
ZEROCLAW_DIR = Path.home() / ".zeroclaw"
ZEROCLAW_CONFIG = ZEROCLAW_DIR / "config.toml"
ZEROCLAW_SKILLS = ZEROCLAW_DIR / "skills"

# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(msg: str) -> None:
    print(f"{CYAN}[kumiho-setup]{RESET} {msg}")


def ok(msg: str) -> None:
    print(f"  {GREEN}+{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}x{RESET} {msg}")


def hr() -> None:
    print(f"  {DIM}{'─' * 50}{RESET}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{DIM}{default}{RESET}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer or default


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"  {prompt} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not answer:
        return default_yes
    return answer in ("y", "yes")


def ask_secret(prompt: str) -> str:
    """Prompt for sensitive input without echoing."""
    try:
        return getpass.getpass(f"  {prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def ask_choice(question: str, options: list[dict]) -> dict:
    print()
    print(f"  {BOLD}{question}{RESET}")
    hr()
    for i, opt in enumerate(options, 1):
        star = f"{GREEN}*{RESET}" if opt.get("recommended") else " "
        note = f"  {DIM}{opt['note']}{RESET}" if opt.get("note") else ""
        print(f"    {star} {i}. {opt['label']}{note}")
    print()
    while True:
        try:
            raw = input(f"  Enter number [1-{len(options)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1]
        except ValueError:
            pass
        print(f"  {YELLOW}Please enter a number between 1 and {len(options)}.{RESET}")


# ---------------------------------------------------------------------------
# Step 1: Python & venv
# ---------------------------------------------------------------------------


def find_python() -> str | None:
    """Find a Python 3.10+ on PATH."""
    for cmd in ["python3", "python"]:
        try:
            r = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                continue
            ver = (r.stdout or r.stderr).strip()
            import re
            m = re.match(r"Python (\d+)\.(\d+)", ver)
            if m and (int(m.group(1)), int(m.group(2))) >= (3, 10):
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def setup_venv(base_python: str) -> Path:
    """Create or reuse ~/.kumiho/venv and install packages."""
    if VENV_PYTHON.exists():
        ok(f"Venv exists: {VENV_DIR}")
    else:
        log("Creating venv...")
        KUMIHO_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [base_python, "-m", "venv", str(VENV_DIR)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"venv creation failed: {r.stderr}")
            sys.exit(1)
        ok(f"Created venv: {VENV_DIR}")

    # Install/upgrade packages
    log("Installing kumiho packages...")
    r = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "--quiet",
         "kumiho[mcp]>=0.9.16", "kumiho-memory[all]>=0.3.16"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        fail(f"pip install failed: {r.stderr}")
        sys.exit(1)
    ok("kumiho[mcp] and kumiho-memory[all] installed")

    # Verify MCP server is importable
    r = subprocess.run(
        [str(VENV_PYTHON), "-c", "import kumiho.mcp_server"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        fail("kumiho.mcp_server not importable — check installation")
        sys.exit(1)
    ok("kumiho.mcp_server verified")

    return VENV_PYTHON


# ---------------------------------------------------------------------------
# Step 2: Authentication
# ---------------------------------------------------------------------------


def decode_jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        claims = json.loads(decoded.decode("utf-8"))
        return claims if isinstance(claims, dict) else None
    except Exception:
        return None


def clean_token(raw: str) -> str:
    token = raw.strip()
    for q in ('"', "'"):
        if token.startswith(q) and token.endswith(q):
            token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def check_existing_auth() -> str | None:
    """Check for existing credentials. Returns email if found."""
    if not CRED_PATH.exists():
        return None
    try:
        creds = json.loads(CRED_PATH.read_text(encoding="utf-8"))
        token = creds.get("api_token") or creds.get("id_token") or ""
        if not token:
            return None
        claims = decode_jwt_payload(token)
        if claims:
            return claims.get("email") or claims.get("sub") or "unknown"
        return "unknown"
    except Exception:
        return None


def cache_token(token: str) -> bool:
    """Store API token in ~/.kumiho/kumiho_authentication.json."""
    KUMIHO_DIR.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if CRED_PATH.exists():
        try:
            existing = json.loads(CRED_PATH.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    claims = decode_jwt_payload(token)
    expires_at = claims.get("exp") if claims else None

    existing["api_token"] = token
    if isinstance(expires_at, (int, float)):
        existing["api_token_expires_at"] = int(expires_at)
    else:
        existing.pop("api_token_expires_at", None)

    CRED_PATH.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return True


def setup_auth() -> str | None:
    """Authenticate and return the token, or None if skipped."""
    existing_email = check_existing_auth()
    if existing_email:
        ok(f"Already authenticated as {existing_email}")
        if not ask_yes_no("Re-authenticate with a new token?", default_yes=False):
            # Return existing token
            try:
                creds = json.loads(CRED_PATH.read_text(encoding="utf-8"))
                return creds.get("api_token") or creds.get("id_token")
            except Exception:
                return None

    choice = ask_choice("How would you like to authenticate?", [
        {
            "label": "Paste API token",
            "note": "from kumiho.io dashboard > API Keys",
            "value": "token",
            "recommended": True,
        },
        {
            "label": "CLI login (email + password)",
            "note": "uses kumiho-cli login",
            "value": "cli",
        },
        {
            "label": "Skip for now",
            "note": "set KUMIHO_AUTH_TOKEN later",
            "value": "skip",
        },
    ])

    if choice["value"] == "skip":
        warn("Authentication skipped — set KUMIHO_AUTH_TOKEN before using the skill")
        return None

    if choice["value"] == "cli":
        log("Running kumiho-cli login...")
        venv_python = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable
        r = subprocess.run(
            [str(venv_python), "-m", "kumiho.auth_cli", "login"],
            timeout=60,
        )
        if r.returncode == 0:
            ok("Authenticated via CLI login")
            try:
                creds = json.loads(CRED_PATH.read_text(encoding="utf-8"))
                return creds.get("api_token") or creds.get("id_token")
            except Exception:
                return None
        else:
            fail("CLI login failed — try pasting an API token instead")
            return None

    # Token method
    print()
    print(f"  Paste your Kumiho API token below.")
    print(f"  {DIM}Find it at kumiho.io > Dashboard > API Keys{RESET}")
    print(f"  {DIM}Token looks like: eyJ... (three dot-separated parts){RESET}")
    print()
    raw = ask_secret("API token")
    token = clean_token(raw)

    if not token:
        fail("Empty token — skipping authentication")
        return None

    claims = decode_jwt_payload(token)
    if claims is None:
        fail("Token doesn't look like a valid JWT (expected 3 dot-separated base64url parts)")
        if not ask_yes_no("Store it anyway?", default_yes=False):
            return None

    if cache_token(token):
        email = claims.get("email", "unknown") if claims else "unknown"
        ok(f"Token cached at {CRED_PATH}")
        if email != "unknown":
            ok(f"Authenticated as {email}")
    else:
        fail("Failed to cache token")

    return token


# ---------------------------------------------------------------------------
# Step 3: ZeroClaw config.toml
# ---------------------------------------------------------------------------


MCP_CONFIG_BLOCK = """\

# Kumiho Memory MCP Server (added by kumiho-setup)
[mcp_servers.kumiho_memory]
transport = "stdio"
command = "{python_path}"
args = ["-m", "kumiho.mcp_server"]
tool_timeout_secs = 30

[mcp_servers.kumiho_memory.env]
KUMIHO_AUTH_TOKEN = "${{KUMIHO_AUTH_TOKEN}}"
"""


def setup_zeroclaw_config(venv_python: Path) -> None:
    """Detect and optionally patch ZeroClaw's config.toml."""
    if not ZEROCLAW_DIR.exists():
        warn(f"ZeroClaw config dir not found: {ZEROCLAW_DIR}")
        warn("Skipping config.toml patching — add MCP config manually (see config.toml.example)")
        return

    if not ZEROCLAW_CONFIG.exists():
        log(f"Creating {ZEROCLAW_CONFIG}...")
        ZEROCLAW_CONFIG.write_text(
            "# ZeroClaw configuration\n# See: https://github.com/zeroclaw-labs/zeroclaw\n",
            encoding="utf-8",
        )

    # Check if already configured
    config_text = ZEROCLAW_CONFIG.read_text(encoding="utf-8")
    if "kumiho_memory" in config_text:
        ok("MCP server already configured in config.toml")
        if not ask_yes_no("Overwrite existing kumiho_memory config?", default_yes=False):
            return

    python_path = str(venv_python).replace("\\", "/")
    block = MCP_CONFIG_BLOCK.format(python_path=python_path)

    if ask_yes_no(f"Add kumiho_memory MCP server to {ZEROCLAW_CONFIG}?"):
        with open(ZEROCLAW_CONFIG, "a", encoding="utf-8") as f:
            f.write(block)
        ok(f"MCP server config added to {ZEROCLAW_CONFIG}")
    else:
        warn("Skipped — add the config manually from config.toml.example")


# ---------------------------------------------------------------------------
# Step 4: Copy skill into ZeroClaw skills directory
# ---------------------------------------------------------------------------


def setup_skill_copy() -> None:
    """Copy the skill files into ZeroClaw's skills directory."""
    target = ZEROCLAW_SKILLS / "kumiho-memory"

    if target.exists():
        ok(f"Skill already installed: {target}")
        if not ask_yes_no("Overwrite with latest version?", default_yes=True):
            return

    if not ZEROCLAW_SKILLS.exists():
        ZEROCLAW_SKILLS.mkdir(parents=True, exist_ok=True)

    # Copy SKILL.toml and SKILL.md
    target.mkdir(parents=True, exist_ok=True)
    for filename in ("SKILL.toml", "SKILL.md"):
        src = PLUGIN_DIR / filename
        if src.exists():
            shutil.copy2(src, target / filename)

    ok(f"Skill installed: {target}")


# ---------------------------------------------------------------------------
# Step 5: Set KUMIHO_AUTH_TOKEN in environment
# ---------------------------------------------------------------------------


def setup_env_token(token: str | None) -> None:
    """Write KUMIHO_AUTH_TOKEN to .env file for ZeroClaw."""
    if not token:
        return

    # Write to ~/.kumiho/.env (shared)
    env_path = KUMIHO_DIR / ".env"
    env_path.write_text(
        f"KUMIHO_AUTH_TOKEN={token}\n",
        encoding="utf-8",
    )
    ok(f"Token written to {env_path}")

    # Also write to zeroclaw .env if directory exists
    if ZEROCLAW_DIR.exists():
        zc_env = ZEROCLAW_DIR / ".env"
        zc_env.write_text(
            f"# Kumiho API token (added by kumiho-setup)\n"
            f"KUMIHO_AUTH_TOKEN={token}\n",
            encoding="utf-8",
        )
        ok(f"Token written to {zc_env}")


# ---------------------------------------------------------------------------
# Step 6: Run skill ingestion
# ---------------------------------------------------------------------------


def run_ingestion(venv_python: Path) -> None:
    """Run the ingest-skills.py script to populate CognitiveMemory/Skills."""
    ingest_script = SCRIPT_DIR / "ingest-skills.py"
    if not ingest_script.exists():
        warn(f"Ingestion script not found: {ingest_script}")
        return

    if not ask_yes_no("Ingest skills into Kumiho graph? (populates CognitiveMemory/Skills)"):
        warn("Skipped — run later: python scripts/ingest-skills.py")
        return

    log("Ingesting skills into the graph...")
    r = subprocess.run(
        [str(venv_python), str(ingest_script)],
        timeout=60,
        env={**os.environ, "KUMIHO_AUTH_TOKEN": os.getenv("KUMIHO_AUTH_TOKEN", "")},
    )
    if r.returncode == 0:
        ok("Skills ingested into CognitiveMemory/Skills")
    else:
        fail("Ingestion failed — run manually: python scripts/ingest-skills.py")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print()
    print(f"  {BOLD}Kumiho Memory Setup for ZeroClaw{RESET}")
    print(f"  {DIM}Persistent graph-native cognitive memory{RESET}")
    hr()
    print()

    # Step 1: Python & venv
    log("Step 1/5: Python environment")
    base_python = find_python()
    if not base_python:
        fail("Python 3.10+ not found on PATH")
        fail("Install Python 3.10+ and try again")
        return 1
    ok(f"Found: {base_python}")
    venv_python = setup_venv(base_python)
    print()

    # Step 2: Auth
    log("Step 2/5: Authentication")
    token = setup_auth()
    if token:
        # Set in current environment for subsequent steps
        os.environ["KUMIHO_AUTH_TOKEN"] = token
    print()

    # Step 3: ZeroClaw config
    log("Step 3/5: ZeroClaw configuration")
    setup_zeroclaw_config(venv_python)
    print()

    # Step 4: Copy skill
    log("Step 4/5: Install skill")
    setup_skill_copy()
    print()

    # Step 5: Environment token + ingestion
    log("Step 5/5: Finalize")
    setup_env_token(token)
    if token:
        run_ingestion(venv_python)
    else:
        warn("Skipping skill ingestion (no auth token) — run later after authenticating")
    print()

    # Summary
    hr()
    print()
    print(f"  {GREEN}{BOLD}Setup complete!{RESET}")
    print()
    if token:
        print(f"  ZeroClaw will discover kumiho_memory tools via MCP.")
        print(f"  Use {BOLD}tool_search(\"kumiho\"){RESET} in a session to see available tools.")
    else:
        print(f"  {YELLOW}Remaining:{RESET} Set KUMIHO_AUTH_TOKEN and run:")
        print(f"    python scripts/ingest-skills.py")
    print()
    print(f"  {DIM}Config:  {ZEROCLAW_CONFIG}{RESET}")
    print(f"  {DIM}Skill:   {ZEROCLAW_SKILLS / 'kumiho-memory'}{RESET}")
    print(f"  {DIM}Creds:   {CRED_PATH}{RESET}")
    print(f"  {DIM}Venv:    {VENV_DIR}{RESET}")
    print()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Setup cancelled.{RESET}")
        sys.exit(1)
