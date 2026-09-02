#!/usr/bin/env python3
"""Kumiho Memory setup wizard for Claude Code / Claude Desktop.

Interactive setup that:
  1. Finds or creates a Python venv with kumiho packages
  2. Selects a backend — Kumiho Cloud (API token) or self-hosted CE (no token)
  3. Writes credentials / CE config to .env.local, OS env, and Desktop config
  4. Ingests discoverable skills into CognitiveMemory/Skills graph
  5. Verifies the MCP server can connect

Usage:
    python scripts/setup.py                    # interactive (choose backend)
    python scripts/setup.py --token TOKEN -y   # non-interactive cloud
    python scripts/setup.py --ce -y            # non-interactive self-hosted CE
    python scripts/setup.py --ce --ce-endpoint 127.0.0.1:9190 -y
"""

from __future__ import annotations

import argparse
import base64
import getpass
import importlib.util
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import bounded_proc

#: Bounds for the provisioning subprocesses. Onboarding is interactive: a hang
#: here is a user sitting in front of a dead prompt, so every wait is finite
#: (kumiho-plugins#36).
VENV_TIMEOUT_S = 120

#: A cold install of kumiho[mcp] + kumiho-memory[all] is 51 wheels / ~150 MB
#: unpacked. Measured on a fresh machine: 203-245 s, and 211 s even with a fully
#: warm pip HTTP cache and zero downloads -- the cost is unpacking grpcio and
#: protobuf, so neither a fast link nor a warm cache brings it under two minutes.
#: The 120 s this started at was calibrated against `git log`, not against pip,
#: and it aborted onboarding at step 1 of 5 on every fresh machine while
#: discarding the token the user had just pasted. This bound exists only to stop
#: an indefinite hang, so it is set far above the real work.
PIP_TIMEOUT_S = 900

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


def _launcher_state_dir() -> Path:
    """Mirror ``run_kumiho_mcp._state_dir`` (kept in sync deliberately, like
    ``code_capture_pending._state_dir`` -- this wizard must run pre-install and
    cannot import the launcher)."""
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def _plugin_data_dir():
    """Mirror ``run_kumiho_mcp._plugin_data_dir`` (kept in sync deliberately).

    The wizard runs from the model's shell, not from the host, so it is never
    handed CLAUDE_PLUGIN_DATA -- it derives the same path from its own location
    inside the plugin cache instead. Without this the wizard would provision a
    DIFFERENT venv from the one the server and hooks use, which is exactly the
    two-venv bug that made onboarding's 151 MB useless.
    """
    env = (os.getenv("CLAUDE_PLUGIN_DATA", "") or "").strip()
    if env and "${" not in env:
        return Path(env)
    parts = Path(__file__).resolve().parts
    if "cache" in parts:
        i = len(parts) - 1 - parts[::-1].index("cache")
        # Do not require the parent to be literally named "plugins": Cowork
        # lays the cache out under a differently-named root, and demanding the
        # name there silently fell back to the state dir -- the two-venv split
        # again, for exactly the users least able to notice it.
        if len(parts) >= i + 4:
            marketplace, plugin = parts[i + 1], parts[i + 2]
            return Path(*parts[:i]) / "data" / ("%s-%s" % (plugin, marketplace))
    return None


#: THE SAME venv the MCP server and the hooks use -- not a second one.
#:
#: The wizard used to provision ~/.kumiho/venv while run_kumiho_mcp provisioned
#: <state-dir>/venv, so onboarding built and verified 151 MB that the server
#: never opened, and the server's own first start then paid the full cold
#: install again (205-320 s against a 30 s host budget, so the first session
#: could never connect).
#:
#: It now lives under the plugin data dir, because that is the only writable
#: location an exec-form HOOK can name: hook commands substitute
#: ${CLAUDE_PLUGIN_DATA} and nothing else writable, and exec form is the only
#: hook form that bypasses all three shells the host may use.
VENV_DIR = (_plugin_data_dir() or _launcher_state_dir()) / "venv"
BIN = "Scripts" if IS_WIN else "bin"
EXT = ".exe" if IS_WIN else ""
VENV_PYTHON = VENV_DIR / BIN / f"python{EXT}"
CRED_PATH = KUMIHO_DIR / "kumiho_authentication.json"
MCP_JSON = PLUGIN_DIR / ".mcp.json"
ENV_LOCAL = PLUGIN_DIR / ".env.local"
ENV_LOCAL_FALLBACK = KUMIHO_DIR / ".env.local"  # used when plugin dir is read-only
SKILL_MD = PLUGIN_DIR / "skills" / "kumiho-memory" / "SKILL.md"
REFS_DIR = PLUGIN_DIR / "skills" / "kumiho-memory" / "references"
INGEST_SCRIPT = SCRIPT_DIR / "ingest-skills.py"

# Self-hosted Community Edition (CE) defaults — mirror run_kumiho_mcp.py.
DEFAULT_CE_ENDPOINT = "127.0.0.1:9190"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"


# ---------------------------------------------------------------------------
# The launcher, for the parts of provisioning it OWNS
# ---------------------------------------------------------------------------

def _load_launcher():
    """Import ``run_kumiho_mcp`` for the provisioning facts it is the source of.

    Safe pre-install: the launcher is stdlib-only and its ``main()`` is
    ``__main__``-guarded -- the same idiom ``reflex_prefetch_worker`` uses to
    ask it where the venv lives.

    The path helpers above are still mirrored (they are module-level constants
    here, and this file must keep working if it is ever run alone), but the
    marker is deliberately NOT mirrored: its name, location and contents are a
    contract between the launcher and ``reflex_prefetch_worker``, and a wizard
    writing a *slightly* different one is worse than writing none at all.
    """
    path = SCRIPT_DIR / "run_kumiho_mcp.py"
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER = _load_launcher()


def package_spec() -> str:
    """What to install, resolved exactly the way the launcher resolves it.

    Not a second hardcoded package list: the wizard and the launcher share ONE
    venv and ONE marker, so a spec differing by a single token means the
    launcher tears down and reinstalls what onboarding just built.
    """
    raw = (os.getenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "") or "").strip()
    if raw and not LAUNCHER._looks_like_placeholder(raw):
        return raw
    return LAUNCHER.DEFAULT_PACKAGE_SPEC


def marker_path() -> Path:
    """The provisioning marker, named and located by the launcher."""
    return LAUNCHER._state_dir() / LAUNCHER.MARKER_FILE


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


#: The knob claude/.mcp.json reads as ``${KUMIHO_PYTHON:-python}``. Measured
#: against the shipped host: ``${VAR:-default}`` is expanded in an MCP
#: ``command`` field, and the lookup reads ~/.claude/settings.json ``env`` as
#: well as the OS environment -- which is what makes this work for a
#: GUI-launched Claude Desktop, where exported shell variables are invisible.
PYTHON_ENV_KNOB = "KUMIHO_PYTHON"
#: Resolve the settings file the way the host does. Hardcoding ~/.claude means
#: that anyone running with CLAUDE_CONFIG_DIR set gets the override written to a
#: file the host never reads -- silently, since the write itself succeeds.
CLAUDE_SETTINGS = (
    Path((os.getenv("CLAUDE_CONFIG_DIR", "") or "").strip() or (Path.home() / ".claude"))
    .expanduser() / "settings.json"
)


def write_python_knob(base_python: str) -> None:
    """Record the interpreter that actually works on THIS machine.

    ``.mcp.json`` cannot name one literal that exists everywhere: macOS 12.3+
    and Debian/Ubuntu have only ``python3``, Windows only ``python`` -- and on
    Windows ``python3`` is worse than absent, because the WindowsApps alias
    resolves and then exits 127 without running anything. So the manifest ships
    the Windows-correct default and this writes the override where the other
    platforms need it.

    Merges into the user's settings file; never rewrites keys it does not own.
    """
    try:
        resolved = bounded_proc.run(
            [base_python, "-c", "import sys; print(sys.executable)"], timeout=30,
        )
        interpreter = (resolved.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError):
        interpreter = ""
    if not interpreter or not Path(interpreter).exists():
        warn(f"Could not resolve an absolute path for {base_python}; "
             f"skipping the {PYTHON_ENV_KNOB} override")
        return

    settings: dict = {}
    if CLAUDE_SETTINGS.exists():
        try:
            loaded = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
            settings = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            warn(f"{CLAUDE_SETTINGS} is not readable JSON ({exc}); "
                 f"set {PYTHON_ENV_KNOB} there by hand")
            return

    env = settings.get("env")
    if not isinstance(env, dict):
        env = {}
    if env.get(PYTHON_ENV_KNOB) == interpreter:
        ok(f"{PYTHON_ENV_KNOB} already set to {interpreter}")
        return
    env[PYTHON_ENV_KNOB] = interpreter
    settings["env"] = env

    try:
        CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        CLAUDE_SETTINGS.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        warn(f"Could not write {CLAUDE_SETTINGS} ({exc}); "
             f"set {PYTHON_ENV_KNOB}={interpreter} there by hand")
        return
    ok(f"{PYTHON_ENV_KNOB} -> {interpreter}  (in {CLAUDE_SETTINGS})")


def find_python() -> str | None:
    """Find a Python 3.10+ on PATH."""
    import re

    for cmd in ["python3", "python"]:
        try:
            r = bounded_proc.run([cmd, "--version"], timeout=10)
            if r.returncode != 0:
                continue
            ver = (r.stdout or r.stderr).strip()
            m = re.match(r"Python (\d+)\.(\d+)", ver)
            if m and (int(m.group(1)), int(m.group(2))) >= (3, 10):
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def link_windows_bin(venv_dir: Path) -> None:
    """Mirror ``run_kumiho_mcp._link_windows_bin``: give a Windows venv a
    POSIX-shaped ``bin/python`` so one literal hook command works everywhere.
    The junction must live inside the venv it serves -- pointing it at an
    external venv's Scripts makes sys.prefix wrong and site-packages empty."""
    if not IS_WIN:
        return
    bin_dir, scripts = venv_dir / "bin", venv_dir / "Scripts"
    if bin_dir.exists() or not scripts.is_dir():
        return
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(bin_dir), str(scripts)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        warn("Could not create the venv bin junction; hooks may not fire")


#: Mirrors run_kumiho_mcp.PROVISION_LOCK_STALE_S.
PROVISION_LOCK_STALE_S = 1800


def _provision_lock_path() -> Path:
    return VENV_DIR.parent / "provision.lock"


def _wait_for_provisioning(timeout_s: int = 900) -> None:
    """Do not run a second pip against a venv another process is building.

    The launcher hands a cold first run to a detached provisioner and tells the
    user about it -- so the user reaching for /kumiho-onboard while that is
    still running is the EXPECTED sequence, not an edge case. Two pip runs
    against one venv interleave their writes.
    """
    lock = _provision_lock_path()
    waited = 0
    while True:
        try:
            if not lock.exists():
                return
            if (time.time() - lock.stat().st_mtime) > PROVISION_LOCK_STALE_S:
                return  # holder is gone; the launcher breaks it the same way
        except OSError:
            return
        if waited == 0:
            log("Another process is already building the environment; waiting...")
        if waited >= timeout_s:
            warn(f"Still locked after {timeout_s}s. Continuing anyway — if this "
                 f"fails, delete {lock} and retry.")
            return
        time.sleep(5)
        waited += 5


def setup_venv(base_python: str) -> Path:
    """Create or reuse the shared venv and install packages."""
    _wait_for_provisioning()
    lock = _provision_lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        lock = None          # advisory only; never block onboarding on it
    try:
        return _setup_venv_locked(base_python)
    finally:
        if lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def _setup_venv_locked(base_python: str) -> Path:
    if VENV_PYTHON.exists():
        ok(f"Venv exists: {VENV_DIR}")
    else:
        log("Creating venv...")
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = bounded_proc.run(
                [base_python, "-m", "venv", str(VENV_DIR)], timeout=VENV_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            fail(f"venv creation timed out after {VENV_TIMEOUT_S}s")
            sys.exit(1)
        if r.returncode != 0:
            fail(f"venv creation failed: {r.stderr}")
            sys.exit(1)
        ok(f"Created venv: {VENV_DIR}")
    # Unconditionally: a venv from an older version has no junction and nothing
    # else would ever add one, which leaves every hook unstartable.
    link_windows_bin(VENV_DIR)

    # Install/upgrade packages. pip's build-backend children inherit the pipe
    # handles, which is exactly the pipe-holder that used to turn "pip timed
    # out" into an indefinite hang of interactive onboarding (#36).
    #
    # Say how long BEFORE the wait, not after: output is captured and pip runs
    # --quiet, so this is several silent minutes on a fresh machine and an
    # unannounced silence is indistinguishable from a hang.
    log("Installing kumiho packages (first run downloads ~150 MB; "
        "several minutes is normal)...")
    spec = package_spec()
    try:
        r = bounded_proc.run(
            [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "--quiet",
             *shlex.split(spec)],
            timeout=PIP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        fail(f"pip install timed out after {PIP_TIMEOUT_S}s")
        sys.exit(1)
    if r.returncode != 0:
        fail(f"pip install failed: {r.stderr}")
        sys.exit(1)
    ok("kumiho[mcp] and kumiho-memory[all] installed")

    # Verify MCP server is importable
    try:
        r = bounded_proc.run(
            [str(VENV_PYTHON), "-c", "import kumiho.mcp_server"], timeout=10,
        )
    except subprocess.TimeoutExpired:
        fail("kumiho.mcp_server import check timed out")
        sys.exit(1)
    if r.returncode != 0:
        fail("kumiho.mcp_server not importable — check installation")
        sys.exit(1)
    ok("kumiho.mcp_server verified")

    # The provisioning marker, written only now that the install is VERIFIED --
    # never on a pip or import failure, both of which exit above.
    #
    # ``reflex_prefetch_worker._venv_ready`` requires the interpreter AND this
    # file. Without it auto-recall and the reflect/consolidate nudges are dead
    # every single turn, and the only evidence is "skip: venv not provisioned"
    # in reflex.log -- the MCP server keeps starting fine, because it decides by
    # comparing installed versions and consults the marker only for extras
    # identity. That asymmetry is why a wizard that built the venv and never
    # wrote the marker went unnoticed for a full working session
    # (kumiho-plugins#65).
    marker = marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(spec, encoding="utf-8")
        ok(f"Provisioning marker written: {marker}")
    except OSError as exc:
        # Not fatal -- the packages ARE installed and the server will run. Say
        # what is lost, because the symptom otherwise appears nowhere.
        warn(f"Could not write the provisioning marker {marker}: {exc}\n"
             f"      Auto-recall stays off until the MCP server rewrites it.")

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

    # Atomic write — write to a temp file in the same directory then rename.
    # Prevents a 0-byte credential file if the process is interrupted or if
    # an MCP server restart races with the write.
    content = json.dumps(existing, indent=2) + "\n"
    try:
        fd, tmp_path = tempfile.mkstemp(dir=KUMIHO_DIR, prefix=".cred_tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, CRED_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
    except Exception:
        # Fallback to non-atomic write if temp approach fails (e.g. cross-device)
        CRED_PATH.write_text(content, encoding="utf-8")

    # Set restrictive permissions (owner read/write only)
    try:
        os.chmod(CRED_PATH, 0o600)
    except Exception:
        pass

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
# Step 2 (alt): Self-hosted Community Edition (CE)
# ---------------------------------------------------------------------------


def choose_backend(args: argparse.Namespace) -> str:
    """Return 'cloud' or 'ce'. Preserves the cloud default for non-interactive
    runs and whenever a token is supplied, so existing scripts are unaffected."""
    if getattr(args, "ce", False):
        return "ce"
    if args.token:
        return "cloud"
    if AUTO_YES:
        return "cloud"

    choice = ask_choice("Which Kumiho backend?", [
        {
            "label": "Kumiho Cloud (managed)",
            "note": "API token from kumiho.io",
            "value": "cloud",
            "recommended": True,
        },
        {
            "label": "Self-hosted (Community Edition)",
            "note": "local kumiho-server, no token",
            "value": "ce",
        },
    ])
    return choice["value"]


def _normalize_endpoint(raw: str) -> str:
    """Reduce an endpoint to bare host:port, mirroring the launcher's
    _normalize_server_target — strips a scheme (grpc://, https://) and any path
    so both the liveness probe and the persisted value stay well-formed."""
    target = (raw or "").strip()
    if not target:
        return ""
    if "://" in target:
        import urllib.parse

        parsed = urllib.parse.urlparse(target)
        if not parsed.hostname:
            return target
        port = parsed.port
        if port is None:
            scheme = parsed.scheme.lower()
            port = 443 if scheme in {"https", "grpcs"} else (80 if scheme in {"http", "grpc"} else 443)
        return f"{parsed.hostname}:{port}"
    if "/" in target:
        target = target.split("/", 1)[0]
    return target


def _probe_ce(endpoint: str, timeout: float = 2.0) -> bool:
    """Best-effort liveness check against a local CE server's /api/_live."""
    import urllib.request

    target = _normalize_endpoint(endpoint)
    if not target:
        return False
    try:
        with urllib.request.urlopen(f"http://{target}/api/_live", timeout=timeout) as resp:
            return getattr(resp, "status", 200) < 400
    except Exception:
        return False


def _ce_runtime_env(ce: dict) -> dict:
    """Env for direct-SDK subprocesses (ingest/verify): tokenless CE routing."""
    env = {
        "KUMIHO_LOCAL_SERVER_ENDPOINT": ce["endpoint"],
        "KUMIHO_AUTH_TOKEN": "",
        "UPSTASH_REDIS_URL": ce.get("redis_url") or DEFAULT_CE_REDIS_URL,
    }
    if ce.get("llm_base_url"):
        env["KUMIHO_LLM_BASE_URL"] = ce["llm_base_url"]
    return env


def _ce_persist_pairs(ce: dict) -> list[tuple[str, str]]:
    """KEY=VALUE pairs to persist; the launcher derives the rest at startup.
    Non-default values only, to keep configs minimal."""
    pairs = [("KUMIHO_CLAUDE_MODE", "ce")]
    if ce["endpoint"] != DEFAULT_CE_ENDPOINT:
        pairs.append(("KUMIHO_CLAUDE_SERVER_ENDPOINT", ce["endpoint"]))
    if ce.get("redis_url") and ce["redis_url"] != DEFAULT_CE_REDIS_URL:
        pairs.append(("UPSTASH_REDIS_URL", ce["redis_url"]))
    if ce.get("llm_base_url"):
        pairs.append(("KUMIHO_LLM_BASE_URL", ce["llm_base_url"]))
    return pairs


def setup_ce(args: argparse.Namespace) -> dict:
    """Collect CE settings, probe the server, and return a CE config dict."""
    endpoint = (getattr(args, "ce_endpoint", None) or "").strip() or DEFAULT_CE_ENDPOINT
    if not AUTO_YES:
        endpoint = ask("CE server endpoint (host:port)", endpoint).strip() or DEFAULT_CE_ENDPOINT
    endpoint = _normalize_endpoint(endpoint) or DEFAULT_CE_ENDPOINT

    ce = {
        "endpoint": endpoint,
        "redis_url": (getattr(args, "ce_redis_url", None) or "").strip(),
        "llm_base_url": (getattr(args, "ce_llm_base_url", None) or "").strip(),
    }

    if _probe_ce(endpoint):
        ok(f"CE server detected at {endpoint}")
    else:
        warn(f"No CE server answering at {endpoint} yet")
        warn("Start it first — see github.com/KumihoIO/kumiho-server-community")

    if not AUTO_YES and not ce["llm_base_url"]:
        llm = ask("Local LLM base URL for summarization (optional, blank to skip)", "").strip()
        if llm:
            ce["llm_base_url"] = llm

    return ce


def verify_ce(ce: dict) -> None:
    """Confirm the CE server answers on its liveness endpoint."""
    if _probe_ce(ce["endpoint"]):
        ok(f"CE server reachable at {ce['endpoint']}")
    else:
        warn(f"CE server not reachable at {ce['endpoint']} — start kumiho-server CE, "
             "then start a new session")


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


def _try_write_env_to_config(config_path: Path, updates: dict) -> bool:
    """Merge *updates* into the kumiho-memory server's env block. Returns True
    on success (including when already in sync)."""
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
        env = {}
        server["env"] = env
    changed = False
    for k, v in updates.items():
        if env.get(k) != v:
            env[k] = v
            changed = True
    if not changed:
        return True
    try:
        config_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _delete_env_from_config(config_path: Path, keys: list[str]) -> bool:
    """Remove *keys* from the kumiho-memory server's env block. Returns True
    only when a key was actually present and removed (so callers can stop)."""
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
    removed = False
    for k in keys:
        if k in env:
            del env[k]
            removed = True
    if not removed:
        return False
    try:
        config_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _neutralize_env_markers(keys: list[str]) -> None:
    """Clear the other backend's persisted markers so they cannot override the
    backend just configured. Only touches surfaces where a marker is actually
    present, so fresh installs are left clean. (.env.local is fully rewritten by
    each backend's writer, so it needs no cleanup here.)"""
    # OS user env — rewrite empty only when the marker was actually inherited,
    # to avoid planting stray empty vars for users who never used the other mode.
    for k in keys:
        if (os.getenv(k, "") or "").strip():
            _set_os_env_var(k, "")
    # Claude Desktop config — delete the keys if present (no-op otherwise).
    for desktop_path in _claude_desktop_config_paths():
        if _delete_env_from_config(desktop_path, keys):
            break


def _upsert_shell_export(rc_path: Path, key: str, value: str) -> bool:
    """Upsert `export KEY="value"` in a shell rc/env file."""
    marker = f"export {key}="
    new_line = f'export {key}="{value}"\n'
    try:
        existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
        lines = existing.splitlines(keepends=True)
        updated = [new_line if l.startswith(marker) else l for l in lines]
        if not any(l.startswith(marker) for l in lines):
            updated.append(new_line)
        rc_path.write_text("".join(updated), encoding="utf-8")
        return True
    except Exception:
        return False


def _set_os_env_var(key: str, value: str) -> bool:
    """Persist an environment variable at the OS user level.

    Windows:
      - Writes to HKCU\\Environment via winreg (persists across reboots)
      - Broadcasts WM_SETTINGCHANGE so running apps see it immediately

    macOS:
      - Runs `launchctl setenv` to inject into the current launchd user
        session — running Claude Desktop picks it up on next MCP restart
      - Writes to ~/.zshenv for persistence across reboots

    Linux:
      - Runs `systemctl --user set-environment` for the current systemd
        user session (falls back silently if systemd not available)
      - Writes to ~/.config/environment.d/kumiho.conf (systemd env drop-in,
        persists across reboots) and ~/.profile as a portable fallback
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
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            result = ctypes.c_size_t()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                SMTO_ABORTIFHUNG, 5000, ctypes.byref(result),
            )
            return True
        except Exception:
            return False

    elif platform.system() == "Darwin":
        # Inject into running launchd user session (immediate effect)
        try:
            subprocess.run(
                ["launchctl", "setenv", key, value],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        # Persist across reboots via ~/.zshenv (zsh is macOS default shell)
        return _upsert_shell_export(Path.home() / ".zshenv", key, value)

    else:
        # Linux — inject into systemd user session (immediate for new processes)
        try:
            subprocess.run(
                ["systemctl", "--user", "set-environment", f"{key}={value}"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        # Persist via systemd environment drop-in
        env_dir = Path.home() / ".config" / "environment.d"
        env_dir.mkdir(parents=True, exist_ok=True)
        try:
            (env_dir / "kumiho.conf").write_text(f"{key}={value}\n", encoding="utf-8")
        except Exception:
            pass
        # Also write ~/.profile as portable fallback for non-systemd distros
        _upsert_shell_export(Path.home() / ".profile", key, value)
        return True


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

    # Clear any CE markers left by a prior self-hosted onboarding, so the
    # launcher does not blank this token and route to a local CE instead.
    _neutralize_env_markers(["KUMIHO_CLAUDE_MODE", "KUMIHO_CLAUDE_SERVER_ENDPOINT"])

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
    env_content = (
        f"# Kumiho API token (written by setup wizard)\n"
        f"KUMIHO_AUTH_TOKEN={token}\n"
    )
    try:
        ENV_LOCAL.write_text(env_content, encoding="utf-8")
        ok(f"Token written to {ENV_LOCAL.name}")
    except OSError:
        # Plugin dir is read-only (e.g. Cowork) — fall back to ~/.kumiho/.env.local
        warn(f"Plugin dir is read-only — writing .env.local to {ENV_LOCAL_FALLBACK}")
        try:
            KUMIHO_DIR.mkdir(parents=True, exist_ok=True)
            ENV_LOCAL_FALLBACK.write_text(env_content, encoding="utf-8")
            ok(f"Token written to {ENV_LOCAL_FALLBACK}")
        except OSError as e:
            warn(f"Could not write .env.local to fallback location: {e}")


def write_ce_config(ce: dict) -> None:
    """Write CE config to the three surfaces the launcher reads: OS user env,
    Claude Desktop config, and .env.local. No token is involved."""
    pairs = _ce_persist_pairs(ce)

    # 1. OS-level user env vars (inherited by Claude Desktop on next launch)
    for k, v in pairs:
        if _set_os_env_var(k, v):
            ok(f"{k} set as user environment variable (OS level)")
        else:
            warn(f"Could not set OS-level env var {k} — a restart may be needed")

    # 2. Claude Desktop global config (triggers MCP server restart)
    desktop_written = False
    for desktop_path in _claude_desktop_config_paths():
        if _try_write_env_to_config(desktop_path, dict(pairs)):
            ok(f"CE config written to {desktop_path.name} (MCP server will restart)")
            desktop_written = True
            break
    if not desktop_written:
        warn("Claude Desktop config not found — restart Claude Desktop after onboarding")

    # 3. .env.local for Claude Code / run_kumiho_mcp.py
    env_content = "# Kumiho self-hosted CE config (written by setup wizard)\n"
    env_content += "".join(f"{k}={v}\n" for k, v in pairs)
    try:
        ENV_LOCAL.write_text(env_content, encoding="utf-8")
        ok(f"CE config written to {ENV_LOCAL.name}")
    except OSError:
        warn(f"Plugin dir is read-only — writing .env.local to {ENV_LOCAL_FALLBACK}")
        try:
            KUMIHO_DIR.mkdir(parents=True, exist_ok=True)
            ENV_LOCAL_FALLBACK.write_text(env_content, encoding="utf-8")
            ok(f"CE config written to {ENV_LOCAL_FALLBACK}")
        except OSError as e:
            warn(f"Could not write .env.local to fallback location: {e}")


# ---------------------------------------------------------------------------
# Step 4: Ingest skills into the graph
# ---------------------------------------------------------------------------


def run_ingestion(venv_python: Path, token: str | None = None, ce_env: dict | None = None) -> None:
    """Run the ingest-skills.py script to populate CognitiveMemory/Skills.

    Cloud mode authenticates with *token*; CE mode routes tokenlessly via the
    env derived from *ce_env*."""
    if not INGEST_SCRIPT.exists():
        warn(f"Ingestion script not found: {INGEST_SCRIPT}")
        warn("Run: python -m kumiho_memory ingest-skill <SKILL.md>")
        return

    if ce_env is None and not token:
        warn("Skipping skill ingestion (no auth token) — run later after authenticating")
        return

    if not ask_yes_no("Ingest skills into Kumiho graph? (populates CognitiveMemory/Skills)"):
        warn("Skipped — run later: python scripts/ingest-skills.py")
        return

    log("Ingesting skills into the graph...")
    if ce_env is not None:
        env = {**os.environ, **_ce_runtime_env(ce_env)}
        # Drop any inherited cloud endpoint so the tokenless SDK cannot route
        # away from the CE loopback (the launcher pops these; we do too).
        env.pop("KUMIHO_SERVER_ENDPOINT", None)
        env.pop("KUMIHO_SERVER_ADDRESS", None)
    else:
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
    try:
        r = bounded_proc.run(
            [str(venv_python), str(test_script), "--env-file", str(temp_env)],
            timeout=15, env=env,
        )
    except subprocess.TimeoutExpired:
        warn("Connection test timed out — the MCP server may still work")
        return
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
        help="API token (skips interactive auth prompts; selects cloud backend)",
    )
    p.add_argument(
        "--ce",
        action="store_true",
        help="Self-hosted Community Edition backend (no API token required)",
    )
    p.add_argument(
        "--ce-endpoint",
        metavar="HOST:PORT",
        help=f"CE gRPC endpoint (default {DEFAULT_CE_ENDPOINT}); implies --ce",
    )
    p.add_argument(
        "--ce-redis-url",
        metavar="URL",
        help=f"CE working-memory Redis URL (default {DEFAULT_CE_REDIS_URL})",
    )
    p.add_argument(
        "--ce-llm-base-url",
        metavar="URL",
        help="OpenAI-compatible LLM endpoint for CE summarization",
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

    # Bank a token supplied on the command line BEFORE provisioning, which is
    # the long, failure-prone step. `/kumiho-onboard <TOKEN>` used to exit at
    # step 1 of 5 and silently discard the token the user had just pasted, so a
    # retry meant finding it again. cache_token only touches the filesystem, so
    # it is safe this early.
    if args.token:
        cleaned = clean_token(args.token)
        if cleaned and cache_token(cleaned):
            ok("Token cached (kept even if the steps below fail)")
        print()

    # Step 1: Python & venv
    log("Step 1/5: Python environment")
    base_python = find_python()
    if not base_python:
        fail("Python 3.10+ not found on PATH")
        fail("Install Python 3.10+ and try again")
        return 1
    ok(f"Found: {base_python}")
    # Before the long provisioning step: this is what lets the MCP server and
    # the hooks find an interpreter at all on macOS/Linux.
    write_python_knob(base_python)
    venv_python = setup_venv(base_python)
    print()

    # A CE-specific flag implies the CE backend.
    if args.ce_endpoint or args.ce_redis_url or args.ce_llm_base_url:
        args.ce = True

    # Step 2: Backend selection + auth/config
    log("Step 2/5: Backend & authentication")
    backend = choose_backend(args)
    token: str | None = None
    ce: dict | None = None
    if backend == "ce":
        ce = setup_ce(args)
    else:
        token = setup_auth(cli_token=args.token)
        if token:
            os.environ["KUMIHO_AUTH_TOKEN"] = token
    print()

    # Step 3: Persist config (OS env + Desktop config + .env.local)
    log("Step 3/5: MCP server configuration")
    if ce is not None:
        write_ce_config(ce)
    else:
        patch_mcp_json(token)
    print()

    # Step 4: Skill ingestion
    log("Step 4/5: Skill ingestion")
    run_ingestion(venv_python, token=token, ce_env=ce)
    print()

    # Step 5: Verify
    log("Step 5/5: Verify connection")
    if ce is not None:
        verify_ce(ce)
    else:
        verify_connection(venv_python, token)
    print()

    # Summary
    hr()
    print()
    print(f"  {GREEN}{BOLD}Setup complete!{RESET}")
    print()
    if ce is not None:
        print(f"  Self-hosted CE mode configured (endpoint {ce['endpoint']}).")
        print(f"  Start a new session — the plugin bootstraps on first message.")
        print(f"  {DIM}Ensure your kumiho-server CE is running.{RESET}")
    elif token:
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
