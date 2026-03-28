#!/usr/bin/env python3
"""Kumiho Memory setup wizard for Claude Code / Claude Desktop.

Interactive setup that:
  1. Finds or creates a Python venv with kumiho packages
  2. Authenticates with Kumiho Cloud (paste API token or use existing)
  3. Writes token to .env.local and credential cache (MCP server reads on start)
  4. Ingests discoverable skills into CognitiveMemory/Skills graph
  5. Verifies the MCP server can connect

Usage:
    python scripts/setup.py                    # interactive
    python scripts/setup.py --token TOKEN -y   # non-interactive (for Claude Code)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

# Ensure stdout can handle Unicode (em dashes, box drawing, etc.)
# even on Windows consoles with legacy codepages like cp949/cp1252.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent  # kumiho-plugins/claude/
IS_WIN = platform.system() == "Windows"
KUMIHO_DIR = Path.home() / ".kumiho"
VENV_DIR = KUMIHO_DIR / "venv"
BIN = "Scripts" if IS_WIN else "bin"
EXT = ".exe" if IS_WIN else ""
VENV_PYTHON = VENV_DIR / BIN / f"python{EXT}"
CRED_PATH = KUMIHO_DIR / "kumiho_authentication.json"
MCP_JSON = PLUGIN_DIR / ".mcp.json"
ENV_LOCAL = PLUGIN_DIR / ".env.local"
SKILL_MD = PLUGIN_DIR / "skills" / "kumiho-memory" / "SKILL.md"
REFS_DIR = PLUGIN_DIR / "skills" / "kumiho-memory" / "references"
INGEST_SCRIPT = SCRIPT_DIR / "ingest-skills.py"

# ---------------------------------------------------------------------------
# Console helpers (same as ZeroClaw setup for consistency)
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


AUTO_YES = False  # Set by --yes flag


def ask(prompt: str, default: str = "") -> str:
    if AUTO_YES and default:
        return default
    suffix = f" [{DIM}{default}{RESET}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer or default


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    if AUTO_YES:
        return default_yes
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
# JWT helpers
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


# ---------------------------------------------------------------------------
# Step 1: Python & venv
# ---------------------------------------------------------------------------


def find_python() -> str | None:
    """Find a Python 3.10+ on PATH."""
    import re

    for cmd in ["python3", "python"]:
        try:
            r = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                continue
            ver = (r.stdout or r.stderr).strip()
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
            return claims.get("email") or claims.get("created_by") or claims.get("sub") or "unknown"
        return "unknown"
    except Exception:
        return None


def cache_token(token: str) -> bool:
    """Merge API token into ~/.kumiho/kumiho_authentication.json, preserving existing credentials."""
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


def setup_auth(cli_token: str | None = None) -> str | None:
    """Authenticate and return the token, or None if skipped.

    If *cli_token* is provided (via ``--token``), skip all interactive
    prompts and use it directly.
    """
    # Fast path: token supplied via CLI — no prompts needed
    if cli_token:
        token = clean_token(cli_token)
        if not token:
            fail("Empty token supplied via --token")
            return None
        claims = decode_jwt_payload(token)
        if claims is None:
            fail("Token doesn't look like a valid JWT (expected 3 dot-separated base64url parts)")
            return None
        if cache_token(token):
            email = (claims.get("email") or claims.get("created_by") or "unknown") if claims else "unknown"
            ok(f"Token cached at {CRED_PATH}")
            if email != "unknown":
                ok(f"Authenticated as {email}")
        else:
            fail("Failed to cache token")
        return token

    # Interactive path
    existing_email = check_existing_auth()
    if existing_email:
        ok(f"Already authenticated as {existing_email}")
        if not ask_yes_no("Re-authenticate with a new token?", default_yes=False):
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
        warn("Authentication skipped — set KUMIHO_AUTH_TOKEN before using the plugin")
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
        email = (claims.get("email") or claims.get("created_by") or "unknown") if claims else "unknown"
        ok(f"Token cached at {CRED_PATH}")
        if email != "unknown":
            ok(f"Authenticated as {email}")
    else:
        fail("Failed to cache token")

    return token


# ---------------------------------------------------------------------------
# Step 3: Patch MCP config with token
# ---------------------------------------------------------------------------


def _claude_desktop_config_paths() -> list[Path]:
    """Return platform-specific Claude Desktop global config paths."""
    paths: list[Path] = []
    if IS_WIN:
        local_appdata = os.getenv("LOCALAPPDATA", "")
        if local_appdata:
            msix_base = Path(local_appdata) / "Packages"
            if msix_base.exists():
                for entry in msix_base.iterdir():
                    if entry.name.startswith("Claude_") and entry.is_dir():
                        paths.append(
                            entry / "LocalCache" / "Roaming" / "Claude"
                            / "claude_desktop_config.json"
                        )
                        break
        appdata = os.getenv("APPDATA", "")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(
            Path.home() / "Library" / "Application Support" / "Claude"
            / "claude_desktop_config.json"
        )
        xdg = os.getenv("XDG_CONFIG_HOME", "")
        paths.append(
            Path(xdg) / "Claude" / "claude_desktop_config.json"
            if xdg else Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
        )
    return paths


def _try_write_token_to_config(config_path: Path, token: str) -> bool:
    """Write token into an MCP config file. Returns True on success."""
    if not config_path.exists():
        return False
    try:
        body = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    servers = body.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    server = None
    for name in ("kumiho-memory", "kumiho"):
        if isinstance(servers.get(name), dict):
            server = servers[name]
            break
    if server is None:
        return False
    env = server.get("env")
    if not isinstance(env, dict):
        return False
    if env.get("KUMIHO_AUTH_TOKEN") == token:
        return True  # already in sync
    env["KUMIHO_AUTH_TOKEN"] = token
    try:
        config_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _set_os_env_var(key: str, value: str) -> bool:
    """Persist an environment variable at the OS user level.

    Windows: writes to HKCU\\Environment via winreg and broadcasts
             WM_SETTINGCHANGE so running apps (including Claude Desktop)
             pick it up without a full reboot.
    macOS/Linux: appends/updates an export line in ~/.zshenv (created if
                 absent).  New Claude Desktop processes will inherit it.
    """
    if IS_WIN:
        try:
            import winreg
            key_handle = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key_handle, key, 0, winreg.REG_SZ, value)
            winreg.CloseKey(key_handle)
            # Broadcast so Explorer and GUI apps see the change immediately
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            ctypes.windll.user32.SendMessageW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment"
            )
            return True
        except Exception:
            return False
    else:
        # Write to ~/.zshenv — sourced by zsh for all session types
        zshenv = Path.home() / ".zshenv"
        marker = f"export {key}="
        try:
            existing = zshenv.read_text(encoding="utf-8") if zshenv.exists() else ""
            lines = existing.splitlines(keepends=True)
            new_line = f'export {key}="{value}"\n'
            updated = [new_line if l.startswith(marker) else l for l in lines]
            if not any(l.startswith(marker) for l in lines):
                updated.append(new_line)
            zshenv.write_text("".join(updated), encoding="utf-8")
            return True
        except Exception:
            return False


def patch_mcp_json(token: str | None) -> None:
    """Write token to all reachable MCP config locations.

    Priority:
      1. OS user-level env var — Claude Desktop inherits it on next launch
         and WM_SETTINGCHANGE notifies running apps on Windows immediately.
      2. Claude Desktop global config — triggers MCP server restart now.
      3. .env.local next to the plugin — picked up by run_kumiho_mcp.py
         for Claude Code sessions.

    We deliberately do NOT write into the plugin .mcp.json (git-tracked).
    """
    if not token:
        return

    # 1. OS-level user env var
    if _set_os_env_var("KUMIHO_AUTH_TOKEN", token):
        ok("KUMIHO_AUTH_TOKEN set as user environment variable (OS level)")
    else:
        warn("Could not set OS-level env var — Claude Desktop may need a restart")

    # 2. Claude Desktop global config (triggers restart)
    desktop_written = False
    for desktop_path in _claude_desktop_config_paths():
        if _try_write_token_to_config(desktop_path, token):
            ok(f"Token written to {desktop_path.name} (MCP server will restart)")
            desktop_written = True
            break
    if not desktop_written:
        warn("Claude Desktop config not found — restart Claude Desktop after onboarding")

    # 3. .env.local for Claude Code / run_kumiho_mcp.py
    try:
        ENV_LOCAL.write_text(
            f"# Kumiho API token (written by setup wizard)\n"
            f"KUMIHO_AUTH_TOKEN={token}\n",
            encoding="utf-8",
        )
        ok(f"Token written to {ENV_LOCAL.name}")
    except OSError:
        pass  # Non-critical if plugin dir is read-only


# ---------------------------------------------------------------------------
# Step 4: Ingest skills into the graph
# ---------------------------------------------------------------------------


def run_ingestion(venv_python: Path, token: str | None) -> None:
    """Run the ingest-skills.py script to populate CognitiveMemory/Skills."""
    if not INGEST_SCRIPT.exists():
        warn(f"Ingestion script not found: {INGEST_SCRIPT}")
        warn("Run: python -m kumiho_memory ingest-skill <SKILL.md>")
        return

    if not token:
        warn("Skipping skill ingestion (no auth token) — run later after authenticating")
        return

    if not ask_yes_no("Ingest skills into Kumiho graph? (populates CognitiveMemory/Skills)"):
        warn("Skipped — run later: python scripts/ingest-skills.py")
        return

    log("Ingesting skills into the graph...")
    env = {**os.environ, "KUMIHO_AUTH_TOKEN": token}
    r = subprocess.run(
        [str(venv_python), str(INGEST_SCRIPT)],
        timeout=60,
        env=env,
    )
    if r.returncode == 0:
        ok("Skills ingested into CognitiveMemory/Skills")
    else:
        fail("Ingestion failed — run manually: python scripts/ingest-skills.py")


# ---------------------------------------------------------------------------
# Step 5: Verify MCP connection
# ---------------------------------------------------------------------------


def verify_connection(venv_python: Path, token: str | None) -> None:
    """Quick self-test of the MCP server."""
    if not token:
        return

    test_script = SCRIPT_DIR / "test_discovery_env.py"
    if not test_script.exists():
        return

    log("Verifying Kumiho Cloud connection...")
    env = {**os.environ, "KUMIHO_AUTH_TOKEN": token}

    # Write a temp env file for the test
    temp_env = PLUGIN_DIR / ".env.local"
    r = subprocess.run(
        [str(venv_python), str(test_script), "--env-file", str(temp_env)],
        capture_output=True, text=True, timeout=15,
        env=env,
    )
    if r.returncode == 0:
        ok("Connection to Kumiho Cloud verified")
    else:
        warn("Connection test inconclusive — the MCP server may still work")
        if r.stderr:
            warn(f"  {r.stderr.strip()[:200]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Kumiho Memory setup wizard for Claude Code / Claude Desktop",
    )
    p.add_argument(
        "--token",
        metavar="TOKEN",
        help="API token (skips interactive auth prompts)",
    )
    p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm all yes/no prompts (non-interactive mode)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global AUTO_YES
    args = parse_args(argv)
    AUTO_YES = args.yes

    print()
    print(f"  {BOLD}Kumiho Memory Setup for Claude{RESET}")
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
    token = setup_auth(cli_token=args.token)
    if token:
        os.environ["KUMIHO_AUTH_TOKEN"] = token
    print()

    # Step 3: Write token to OS env + Desktop config + .env.local
    log("Step 3/5: MCP server configuration")
    patch_mcp_json(token)
    print()

    # Step 4: Skill ingestion
    log("Step 4/5: Skill ingestion")
    run_ingestion(venv_python, token)
    print()

    # Step 5: Verify
    log("Step 5/5: Verify connection")
    verify_connection(venv_python, token)
    print()

    # Summary
    hr()
    print()
    print(f"  {GREEN}{BOLD}Setup complete!{RESET}")
    print()
    if token:
        print(f"  Claude will connect to Kumiho memory automatically.")
        print(f"  Start a new session — the plugin bootstraps on first message.")
    else:
        print(f"  {YELLOW}Remaining:{RESET} Authenticate with one of:")
        print(f"    1. Run this setup again with a token")
        print(f"    2. Use /kumiho-onboard in Claude Code")
        print(f"    3. Set KUMIHO_AUTH_TOKEN environment variable")
    print()
    print(f"  {DIM}Plugin:  {PLUGIN_DIR}{RESET}")
    print(f"  {DIM}Creds:   {CRED_PATH}{RESET}")
    print(f"  {DIM}Venv:    {VENV_DIR}{RESET}")
    print(f"  {DIM}MCP:     {MCP_JSON}{RESET}")
    print()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Setup cancelled.{RESET}")
        sys.exit(1)
