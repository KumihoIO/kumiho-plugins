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
            # Return existing token — prefer id_token (JWT) over api_token
            try:
                creds = json.loads(CRED_PATH.read_text(encoding="utf-8"))
                return creds.get("id_token") or creds.get("api_token")
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
                # id_token is the Firebase JWT the MCP server expects;
                # api_token may be a non-JWT key that the MCP server rejects.
                return creds.get("id_token") or creds.get("api_token")
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
        warn("Dashboard API keys (e.g. kh_live_...) are NOT accepted by the MCP server.")
        warn("The MCP server requires a Firebase ID token or Control Plane JWT.")
        warn("Use 'CLI login' instead to obtain the correct token automatically.")
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


MANAGED_COMMENT = "# Kumiho Memory MCP Server (added by kumiho-setup)"

# Full block: written when no [mcp] section exists yet.
MCP_CONFIG_BLOCK = """\
# Kumiho Memory MCP Server (added by kumiho-setup)
[mcp]
enabled = true
deferred_loading = false

[[mcp.servers]]
name = "kumiho_memory"
transport = "stdio"
command = "{python_path}"
args = ["-m", "kumiho.mcp_server"]
tool_timeout_secs = 30

[mcp.servers.env]
KUMIHO_AUTH_TOKEN = "${{KUMIHO_AUTH_TOKEN}}"
"""

# Server-only block: written when [mcp] already exists to avoid duplicate key.
MCP_SERVER_BLOCK = """\
# Kumiho Memory MCP Server (added by kumiho-setup)
[[mcp.servers]]
name = "kumiho_memory"
transport = "stdio"
command = "{python_path}"
args = ["-m", "kumiho.mcp_server"]
tool_timeout_secs = 30

[mcp.servers.env]
KUMIHO_AUTH_TOKEN = "${{KUMIHO_AUTH_TOKEN}}"
"""

# Sections owned by our managed block — used to find where the block ends.
# [mcp] is included because we may have written it as part of the full block.
_OWN_SECTIONS = frozenset({
    "[mcp]", "[[mcp.servers]]", "[mcp.servers.env]",
    "[mcp_servers.kumiho_memory]", "[mcp_servers.kumiho_memory.env]",
})


def _strip_managed_block(config_text: str) -> str:
    """Return config_text with our managed block removed (for external-section detection)."""
    lines = config_text.splitlines()
    result: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == MANAGED_COMMENT or lines[i].strip() == "[mcp_servers.kumiho_memory]":
            # Skip forward until we hit a non-owned top-level section or EOF
            i += 1
            while i < len(lines):
                stripped = lines[i].strip()
                if stripped.startswith("[") and stripped not in _OWN_SECTIONS:
                    break
                i += 1
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _external_mcp_section_exists(config_text: str) -> bool:
    """Return True if [mcp] exists in the config outside our managed block."""
    import re
    stripped = _strip_managed_block(config_text)
    return bool(re.search(r"^\s*\[mcp\]\s*$", stripped, re.MULTILINE))


def _patch_external_mcp_section(config_text: str) -> tuple[str, list[str]]:
    """Fix conflicts in an existing [mcp] section before we insert [[mcp.servers]].

    Handles:
    - ``servers = [...]`` inline key → removed (conflicts with ``[[mcp.servers]]``)
    - ``enabled = false`` → changed to ``enabled = true``
    - ``enabled`` key missing → ``enabled = true`` inserted after ``[mcp]``

    Returns ``(patched_text, list_of_human_readable_changes)``.
    Only operates on the external [mcp] section; our managed block is left untouched.
    """
    import re

    lines = config_text.splitlines()
    result: list[str] = []
    changes: list[str] = []
    in_our_block = False
    in_mcp_section = False
    mcp_header_idx: int | None = None  # index in result[] where [mcp] line lives
    found_enabled = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── Track our managed block so we never mutate it ──────────────────
        if stripped == MANAGED_COMMENT:
            in_our_block = True
        if in_our_block:
            result.append(line)
            i += 1
            # Leave our block when we hit the next non-owned top-level section
            if in_our_block and stripped != MANAGED_COMMENT and stripped.startswith("[") and stripped not in _OWN_SECTIONS:
                in_our_block = False
            continue

        # ── Entering [mcp] section ──────────────────────────────────────────
        if stripped == "[mcp]":
            in_mcp_section = True
            mcp_header_idx = len(result)
            found_enabled = False
            result.append(line)
            i += 1
            continue

        # ── Leaving [mcp] section ───────────────────────────────────────────
        if in_mcp_section and stripped.startswith("["):
            if not found_enabled and mcp_header_idx is not None:
                result.insert(mcp_header_idx + 1, "enabled = true")
                changes.append("added 'enabled = true' to [mcp]")
            in_mcp_section = False
            result.append(line)
            i += 1
            continue

        # ── Mutations inside [mcp] ──────────────────────────────────────────
        if in_mcp_section:
            # Fix disabled flag
            if re.match(r"^\s*enabled\s*=\s*false\s*$", line, re.IGNORECASE):
                result.append(re.sub(r"(?i)false", "true", line, count=1))
                changes.append("set 'enabled = true' in [mcp] (was false)")
                found_enabled = True
                i += 1
                continue
            if re.match(r"^\s*enabled\s*=", line):
                found_enabled = True

            # Remove inline servers key (single-line or multi-line)
            if re.match(r"^\s*servers\s*=\s*\[", line):
                if "]" in line:
                    # single-line: servers = [...]
                    changes.append("removed inline 'servers = [...]' from [mcp]")
                    i += 1
                else:
                    # multi-line: servers = [\n  ...\n]
                    i += 1
                    while i < len(lines) and "]" not in lines[i]:
                        i += 1
                    if i < len(lines):
                        i += 1  # consume closing ]
                    changes.append("removed multi-line 'servers = [...]' from [mcp]")
                continue

        result.append(line)
        i += 1

    # Handle [mcp] as the very last section with no trailing header
    if in_mcp_section and not found_enabled and mcp_header_idx is not None:
        result.insert(mcp_header_idx + 1, "enabled = true")
        changes.append("added 'enabled = true' to [mcp]")

    return "\n".join(result), changes


def upsert_kumiho_mcp_config(config_text: str, block: str) -> tuple[str, bool]:
    """Insert or replace the managed kumiho_memory MCP block.

    Detects both the new ``[[mcp.servers]]`` format and the old
    ``[mcp_servers.kumiho_memory]`` format so re-running after a manual
    edit or a previous install doesn't create duplicates.
    """
    lines = config_text.splitlines()
    start = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        # New format: our comment marker
        if stripped == MANAGED_COMMENT:
            start = index
            break
        # Old format: legacy section header
        if stripped == "[mcp_servers.kumiho_memory]":
            start = index
            break
        # New format without our comment: [[mcp.servers]] followed by name = "kumiho_memory"
        if stripped == "[[mcp.servers]]":
            # Look ahead up to 5 lines for the name key
            for lookahead in range(index + 1, min(index + 6, len(lines))):
                if lines[lookahead].strip().startswith("name") and "kumiho_memory" in lines[lookahead]:
                    start = index
                    break
            if start is not None:
                break

    if start is None:
        text = config_text.rstrip()
        if text:
            text += "\n\n"
        return text + block.strip("\n") + "\n", False

    # Find end of block: first top-level section not owned by us
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped not in _OWN_SECTIONS:
            end = index
            break

    updated_lines = lines[:start]
    # Strip trailing blank lines before our block, then add exactly one blank separator
    while updated_lines and not updated_lines[-1].strip():
        updated_lines.pop()
    if updated_lines:
        updated_lines.append("")
        updated_lines.append("")
    updated_lines.extend(block.strip("\n").splitlines())
    if end < len(lines) and lines[end].strip():
        updated_lines.append("")
    updated_lines.extend(lines[end:])

    return "\n".join(updated_lines).rstrip() + "\n", True


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

    # Check if already configured (detect both new and old formats)
    config_text = ZEROCLAW_CONFIG.read_text(encoding="utf-8")
    has_existing_config = (
        MANAGED_COMMENT in config_text
        or "[mcp_servers.kumiho_memory]" in config_text
        or ('[[mcp.servers]]' in config_text and 'name = "kumiho_memory"' in config_text)
    )
    if has_existing_config:
        ok("MCP server already configured in config.toml")
        if not ask_yes_no("Overwrite existing kumiho_memory config?", default_yes=False):
            return

    python_path = str(venv_python).replace("\\", "/")
    # Use server-only block if [mcp] already exists externally to avoid duplicate key error.
    if _external_mcp_section_exists(config_text):
        config_text, patch_changes = _patch_external_mcp_section(config_text)
        for change in patch_changes:
            ok(f"Patched config.toml: {change}")
        block = MCP_SERVER_BLOCK.format(python_path=python_path)
    else:
        block = MCP_CONFIG_BLOCK.format(python_path=python_path)

    if ask_yes_no(f"Add kumiho_memory MCP server to {ZEROCLAW_CONFIG}?"):
        updated_text, replaced = upsert_kumiho_mcp_config(config_text, block)
        ZEROCLAW_CONFIG.write_text(updated_text, encoding="utf-8")
        if replaced:
            ok(f"MCP server config updated in {ZEROCLAW_CONFIG}")
        else:
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
    """Write KUMIHO_AUTH_TOKEN to .env file for ZeroClaw.

    The MCP server requires a Firebase ID token (JWT with 3 dot-separated
    parts).  This function resolves the best available JWT in priority order:

    1. ``id_token`` in ``~/.kumiho/kumiho_authentication.json`` — written by
       ``kumiho-cli login`` / ``kumiho.auth_cli login``.
    2. The *passed* token, if it is itself a valid JWT.
    3. ``api_token`` in credentials, if it is a valid JWT (some auth flows
       store the Firebase token under this key).

    If none of those yield a JWT the function warns and skips writing so we
    never silently write a plain API key that the MCP server will reject.
    """
    # --- resolve the best JWT from credentials + passed token ---------------
    jwt_token: str | None = None

    if CRED_PATH.exists():
        try:
            creds = json.loads(CRED_PATH.read_text(encoding="utf-8"))
        except Exception:
            creds = {}

        # Prefer id_token (Firebase JWT written by CLI login)
        for key in ("id_token", "api_token"):
            candidate = creds.get(key, "")
            if candidate and decode_jwt_payload(candidate) is not None:
                jwt_token = candidate
                break

    # Fall back to the token passed in (e.g. user pasted a raw JWT)
    if not jwt_token and token and decode_jwt_payload(token) is not None:
        jwt_token = token

    if not jwt_token:
        fail("No valid JWT found — KUMIHO_AUTH_TOKEN not written to .env")
        warn("API keys (kh_live_...) are rejected by the MCP server.")
        warn("Run the setup wizard again and choose 'CLI login', or run:")
        warn("  python -m kumiho.auth_cli login")
        warn(f"Then copy the 'id_token' value from {CRED_PATH} into")
        warn(f"  {ZEROCLAW_DIR / '.env'}  as  KUMIHO_AUTH_TOKEN=<token>")
        return

    # --- write the JWT to env files -----------------------------------------
    env_path = KUMIHO_DIR / ".env"
    env_path.write_text(f"KUMIHO_AUTH_TOKEN={jwt_token}\n", encoding="utf-8")
    ok(f"JWT written to {env_path}")

    if ZEROCLAW_DIR.exists():
        zc_env = ZEROCLAW_DIR / ".env"
        zc_env.write_text(
            f"# Kumiho auth token (added by kumiho-setup)\n"
            f"KUMIHO_AUTH_TOKEN={jwt_token}\n",
            encoding="utf-8",
        )
        ok(f"JWT written to {zc_env}")


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
