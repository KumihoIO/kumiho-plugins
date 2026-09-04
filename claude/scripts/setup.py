#!/usr/bin/env python3
"""Kumiho Memory setup wizard for Claude Code / Claude Desktop.

Interactive setup that:
  1. Finds or creates a Python venv with kumiho packages
  2. Selects a backend and asks the SDK to verify Cloud auth, or configures CE
  3. Persists only the CE/backend selection; Cloud credentials remain SDK-owned
  4. Ingests discoverable skills into CognitiveMemory/Skills graph
  5. Verifies the MCP server can connect

Usage:
    python -I scripts/setup.py                    # interactive (choose backend)
    python -I scripts/setup.py -y                 # persisted backend, Cloud if fresh
    python -I scripts/setup.py --token-stdin -y   # legacy one-run verification
    python -I scripts/setup.py --ce -y            # non-interactive self-hosted CE
    python -I scripts/setup.py --ce --ce-endpoint 127.0.0.1:9190 -y
"""

from __future__ import annotations

import argparse
import base64
import getpass
import importlib.util
import ipaddress
import json
import os
import secrets
import shutil
import platform
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
OFFICIAL_CONTROL_PLANE_URL = "https://control.kumiho.cloud"

_SETUP_HOST_UNTRUSTED_ENV = (
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_CONFIG_DIR",
    "KUMIHO_CONFIG_DIR",
    "KUMIHO_CLAUDE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_FIREBASE_API_KEY",
    "KUMIHO_FIREBASE_ID_TOKEN",
    "KUMIHO_FIREBASE_PROJECT_ID",
    "KUMIHO_USE_CONTROL_PLANE_TOKEN",
    "KUMIHO_AUTO_CONFIGURE",
    "KUMIHO_DISCOVERY_CACHE_FILE",
    "KUMIHO_WORKSPACE_ROOT",
    "KUMIHO_ENV_FILE",
    "KUMIHO_CLAUDE_PACKAGE_SPEC",
)
_SETUP_HOST_UNTRUSTED_TRANSPORT_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "AWS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "SSLKEYLOGFILE",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
    "GRPC_PROXY",
    "NO_GRPC_PROXY",
    "GRPC_SSL_CIPHER_SUITES",
)
_SETUP_HOST_UNTRUSTED_BACKEND_ENV = ("KUMIHO_CLAUDE_MODE",)
_SETUP_HOST_UNTRUSTED_DATA_ROUTE_ENV = (
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "UPSTASH_REDIS_URL",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_LOCAL_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "KUMIHO_MCP_HOSTED",
    "KUMIHO_HOSTED_LOCAL_REDIS",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "KUMIHO_LLM_BASE_URL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
    "KUMIHO_MEMORY_CODE_AUTOMINE",
)
_SETUP_PROJECT_LOOPBACK_ROUTE_ENV = frozenset({
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "UPSTASH_REDIS_URL",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_LOCAL_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "UPSTASH_REDIS_REST_URL",
    "KUMIHO_LLM_BASE_URL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
})
_SETUP_TRUSTED_GLOBAL_PATH_ENV = frozenset({
    "KUMIHO_CONFIG_DIR",
    "KUMIHO_CLAUDE_HOME",
    "KUMIHO_SERVER_CA_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "AWS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "SSLKEYLOGFILE",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
})
_SETUP_TRUSTED_PROVISION_TRANSPORT_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "AWS_CA_BUNDLE",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "GRPC_PROXY",
    "NO_GRPC_PROXY", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
})

# Values restored from the user's exact Claude settings file are allowed to
# reach pip.  The live host environment is still treated as untrusted: a
# repository must not be able to redirect onboarding through its own proxy or
# CA bundle.
_SETUP_TRUSTED_TRANSPORT_ENV: dict[str, str] = {}


def _setup_host_launch_isolated() -> bool:
    return (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() in {
        "claude",
        "codex",
    }


def _service_route_is_loopback(raw: object) -> bool:
    if not isinstance(raw, str) or any(char in raw for char in "\r\n\0"):
        return False
    value = raw.strip()
    if not value or "${" in value:
        return False
    try:
        parsed = urllib.parse.urlsplit(
            value if "://" in value else f"//{value}"
        )
        parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _project_route_is_allowed(key: str, raw: object) -> bool:
    """Allow only loopback service routes during host-driven setup."""
    if not isinstance(raw, str) or any(char in raw for char in "\r\n\0"):
        return False
    value = raw.strip()
    if not value or "${" in value:
        return False
    if _service_route_is_loopback(value):
        return key in _SETUP_PROJECT_LOOPBACK_ROUTE_ENV
    return False


def _account_home() -> Path:
    """Return the OS account home without trusting host-injected HOME values."""
    if not _setup_host_launch_isolated():
        return Path.home()
    try:
        if os.name == "nt":
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            result = ctypes.windll.shell32.SHGetFolderPathW(
                None, 0x0028, None, 0, buffer
            )
            home = Path(buffer.value) if result == 0 and buffer.value else None
        else:
            import pwd

            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        home = None
    if home is None or not home.is_absolute():
        raise SystemExit(
            "Could not resolve the operating-system account home; "
            "refusing a host-provided runtime path."
        )
    return home


def _prepare_host_setup_environment() -> None:
    """Pin official Cloud discovery and trust only local setup routes.

    Claude applies repository settings before it invokes this wizard. A
    project may provide an explicit token for the SDK or select a loopback CE
    endpoint, but it cannot redirect Cloud discovery, execute a
    repository-selected virtualenv, or persist a remote CE data route.
    """
    host = (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower()
    if host not in {"claude", "codex"}:
        os.environ["KUMIHO_CONTROL_PLANE_URL"] = OFFICIAL_CONTROL_PLANE_URL
        os.environ.pop("KUMIHO_CONTROL_PLANE_API_URL", None)
        return
    safe_project_routes = {}
    for key in _SETUP_HOST_UNTRUSTED_DATA_ROUTE_ENV:
        value = (os.getenv(key, "") or "").strip()
        if _project_route_is_allowed(key, value):
            safe_project_routes[key] = value
    account_home = _account_home()
    untrusted = frozenset(key.upper() for key in (
        *_SETUP_HOST_UNTRUSTED_ENV,
        *_SETUP_HOST_UNTRUSTED_TRANSPORT_ENV,
        *_SETUP_HOST_UNTRUSTED_BACKEND_ENV,
        *_SETUP_HOST_UNTRUSTED_DATA_ROUTE_ENV,
    ))
    for actual in tuple(os.environ):
        if actual.upper() in untrusted:
            os.environ.pop(actual, None)
    os.environ["HOME"] = str(account_home)
    os.environ["USERPROFILE"] = str(account_home)
    if os.name == "nt":
        drive, tail = os.path.splitdrive(str(account_home))
        os.environ["HOMEDRIVE"] = drive
        os.environ["HOMEPATH"] = tail or "\\"
        os.environ["APPDATA"] = str(account_home / "AppData" / "Roaming")
        os.environ["LOCALAPPDATA"] = str(account_home / "AppData" / "Local")
    os.environ.update(safe_project_routes)
    if host != "claude":
        return

    trusted: dict[str, str] = {}
    settings_root = account_home / ".claude"
    for settings_path in (
        settings_root / "settings.local.json",
        settings_root / "settings.json",
    ):
        try:
            body = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        env = body.get("env") if isinstance(body, dict) else None
        if not isinstance(env, dict):
            continue
        for key in (
            "KUMIHO_CONFIG_DIR",
            "KUMIHO_CLAUDE_HOME",
            "KUMIHO_CLAUDE_PACKAGE_SPEC",
            *_SETUP_HOST_UNTRUSTED_BACKEND_ENV,
            *_SETUP_HOST_UNTRUSTED_DATA_ROUTE_ENV,
            *_SETUP_HOST_UNTRUSTED_TRANSPORT_ENV,
        ):
            raw = env.get(key)
            if key in trusted or not isinstance(raw, str):
                continue
            value = raw.strip()
            if (
                not value
                or "${" in value
                or any(char in value for char in "\r\n\0")
            ):
                continue
            if key in _SETUP_TRUSTED_GLOBAL_PATH_ENV:
                path = Path(value)
                if not path.is_absolute():
                    continue
                value = str(path)
            if key in _SETUP_HOST_UNTRUSTED_BACKEND_ENV and value.lower() not in {
                "cloud", "managed", "ce", "community", "self-hosted", "local",
            }:
                continue
            if (
                key in _SETUP_HOST_UNTRUSTED_DATA_ROUTE_ENV
                and not _project_route_is_allowed(key, value)
            ):
                continue
            trusted[key] = value
            if key in _SETUP_TRUSTED_PROVISION_TRANSPORT_ENV:
                _SETUP_TRUSTED_TRANSPORT_ENV[key] = value
    os.environ.update(trusted)
    os.environ["KUMIHO_CONTROL_PLANE_URL"] = OFFICIAL_CONTROL_PLANE_URL
    os.environ.pop("KUMIHO_CONTROL_PLANE_API_URL", None)


_prepare_host_setup_environment()
_KUMIHO_DIR_OVERRIDE = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
KUMIHO_DIR = (
    (
        Path(_KUMIHO_DIR_OVERRIDE)
        if _setup_host_launch_isolated()
        else Path(_KUMIHO_DIR_OVERRIDE).expanduser()
    )
    if _KUMIHO_DIR_OVERRIDE
    else _account_home() / ".kumiho"
)


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


#: THE SAME venv both Claude and Codex MCP servers use -- not a second one.
#:
#: Host-specific backend choices remain separate. Package installation is
#: shared under ~/.kumiho so installing from one host prepares the other too.
#:
#: Claude's fixed hook path is retained through a plugin-data alias created by
#: the launcher after provisioning.
VENV_DIR = KUMIHO_DIR / "venv"
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
CLOUD_RUNNER = SCRIPT_DIR / "run_kumiho_cloud.py"
CE_RUNNER = SCRIPT_DIR / "run_kumiho_ce.py"

# Self-hosted Community Edition (CE) defaults — mirror run_kumiho_mcp.py.
DEFAULT_CE_ENDPOINT = "127.0.0.1:9190"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"

# Values CE onboarding can persist outside the plugin directory. Cloud
# onboarding must remove all of them together or an old Redis/LLM route can
# silently override the newly selected cloud backend.
_CE_PERSISTED_ENV_KEYS = (
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "UPSTASH_REDIS_URL",
    "KUMIHO_LLM_BASE_URL",
)

# Provisioning executes package/build code that neither needs nor should
# inherit runtime credentials or backend routing. Direct maintenance retains
# legacy package-index/proxy overrides. Host-launched onboarding scrubs them
# because Claude project settings are indistinguishable from ambient exports;
# private index/proxy policy should live in the user's pip configuration.
_PROVISION_ENV_EXACT_SCRUB = frozenset({
    "KUMIHO_AUTH_TOKEN",
    "KUMIHO_LLM_API_KEY",
    "KUMIHO_FIREBASE_API_KEY",
    "KUMIHO_FIREBASE_ID_TOKEN",
    "KUMIHO_FIREBASE_PROJECT_ID",
    "KUMIHO_USE_CONTROL_PLANE_TOKEN",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CODEX_BACKEND",
    "KUMIHO_CODEX_CE_ENDPOINT",
    "KUMIHO_CODEX_CE_REDIS_URL",
    "KUMIHO_CODEX_CE_LLM_BASE_URL",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_ENV_FILE",
    "KUMIHO_DISCOVERY_CACHE_FILE",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_AUTO_CONFIGURE",
    "KUMIHO_REQUIRE_TLS",
    "KUMIHO_SERVER_USE_TLS",
    "UPSTASH_REDIS_URL",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_LOCAL_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "KUMIHO_MCP_HOSTED",
    "KUMIHO_HOSTED_LOCAL_REDIS",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "HF_TOKEN",
})
_PROVISION_ENV_SECRET_SUFFIXES = (
    "_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN", "_SECRET_KEY", "_TOKEN",
)
_HOST_PROVISION_ENV_PREFIXES = ("PIP_", "PIPENV_", "UV_")
_HOST_PROVISION_ENV_EXACT_SCRUB = frozenset({
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
})


def _provision_subprocess_env() -> dict[str, str]:
    """Return a copy of the environment with runtime secrets/routes removed."""
    env = dict(os.environ)
    for key in tuple(env):
        normalized = key.upper()
        if (
            normalized in _PROVISION_ENV_EXACT_SCRUB
            or normalized.endswith(_PROVISION_ENV_SECRET_SUFFIXES)
            or (
                _setup_host_launch_isolated()
                and (
                    normalized in _HOST_PROVISION_ENV_EXACT_SCRUB
                    or normalized.startswith(_HOST_PROVISION_ENV_PREFIXES)
                )
            )
        ):
            env.pop(key, None)
    if _setup_host_launch_isolated():
        # Re-add only proxy/CA values read from the user's exact Claude
        # settings file. Ambient project values were removed above.
        env.update(_SETUP_TRUSTED_TRANSPORT_ENV)
    return env


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
    return LAUNCHER._marker_path()


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


#: Kept for external integrations and older installed manifests. Current
#: Claude plugin installs enter through the host-owned plugin-data venv alias.
PYTHON_ENV_KNOB = "KUMIHO_PYTHON"
#: Resolve the settings file the way the host does. Hardcoding ~/.claude means
#: that anyone running with CLAUDE_CONFIG_DIR set gets the override written to a
#: file the host never reads -- silently, since the write itself succeeds.
CLAUDE_SETTINGS = (
    Path((os.getenv("CLAUDE_CONFIG_DIR", "") or "").strip() or (_account_home() / ".claude"))
    .expanduser() / "settings.json"
)


def _merge_user_global_claude_env(
    updates: dict[str, str] | None = None, *, remove: tuple[str, ...] = ()
) -> bool:
    """Merge trusted routing while honoring settings.local.json precedence."""
    updates = updates or {}
    settings_local = CLAUDE_SETTINGS.with_name("settings.local.json")
    paths = (settings_local, CLAUDE_SETTINGS)
    documents: dict[Path, dict] = {}
    success = True
    for path in paths:
        if not path.exists():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            documents[path] = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            warn(
                f"{path} is not readable JSON ({exc}); "
                "leaving that trusted settings file unchanged"
            )
            success = False

    # Put new values in the highest-priority usable existing file. With no
    # settings yet, create the conventional settings.json. Removal touches
    # both files so a stale settings.local.json cannot pin the old backend.
    target = next((path for path in paths if path in documents), CLAUDE_SETTINGS)
    documents.setdefault(target, {})
    for path, settings in documents.items():
        env = settings.get("env")
        if not isinstance(env, dict):
            env = {}
        changed = False
        for key in remove:
            if key in env:
                env.pop(key, None)
                changed = True
        if path == target:
            for key, value in updates.items():
                if env.get(key) != value:
                    env[key] = value
                    changed = True
        if not changed:
            continue
        settings["env"] = env
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            warn(f"Could not write trusted routing to {path} ({exc})")
            success = False
    return success


def write_python_knob(base_python: str) -> None:
    """Record the interpreter that actually works on THIS machine.

    Current plugin installs use the cross-platform
    ``${CLAUDE_PLUGIN_DATA}/venv/bin/python`` alias. Keep this absolute setting
    for older Claude installs and external integrations without reintroducing a
    PATH-resolved Windows executable.

    Merges into the user's settings file; never rewrites keys it does not own.
    """
    try:
        resolved = bounded_proc.run(
            [base_python, "-I", "-c", "import sys; print(sys.executable)"], timeout=30,
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
    """Find Python 3.10+ without probing Windows App Execution Aliases.

    This setup script is already running under a real interpreter, so its
    absolute ``sys.executable`` is the only fallback needed when the shared
    Desktop/Claude/Codex runtime is absent or broken.
    """
    import re

    candidates = []
    for raw in (getattr(sys, "_base_executable", None), sys.executable):
        if not raw:
            continue
        current = str(Path(raw).resolve())
        try:
            Path(current).relative_to(VENV_DIR.resolve())
            continue
        except ValueError:
            pass
        if current not in candidates:
            candidates.append(current)
    for cmd in candidates:
        if not LAUNCHER._windows_pe_executable(Path(cmd)):
            continue
        try:
            r = bounded_proc.run([cmd, "--version"], timeout=10)
            if r.returncode != 0:
                continue
            ver = (r.stdout or r.stderr).strip()
            m = re.match(r"Python (\d+)\.(\d+)", ver)
            if m and (int(m.group(1)), int(m.group(2))) >= (3, 10):
                return cmd
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _link_posix_pythonw(venv_dir: Path) -> None:
    """Mirror ``run_kumiho_mcp._ensure_hook_interpreter``: hooks name
    ``bin/pythonw`` (Windows' console-less interpreter); on POSIX that is a
    symlink to ``bin/python``."""
    bin_dir = venv_dir / "bin"
    target, link = bin_dir / "python", bin_dir / "pythonw"
    if link.exists() or not target.exists():
        return
    try:
        LAUNCHER._assert_provision_lock_owned()
        os.symlink("python", link)
    except OSError:
        try:
            LAUNCHER._assert_provision_lock_owned()
            shutil.copy2(target, link)
        except OSError:
            warn("Could not create %s; hooks may not fire" % link)


def link_windows_bin(venv_dir: Path) -> None:
    """Mirror ``run_kumiho_mcp._link_windows_bin``: give a Windows venv a
    POSIX-shaped ``bin/`` (``python`` and ``pythonw``) so one literal hook
    command works everywhere. The junction must live inside the venv it
    serves -- pointing it at an external venv's Scripts makes sys.prefix wrong
    and site-packages empty."""
    if not IS_WIN:
        _link_posix_pythonw(venv_dir)
        return
    bin_dir, scripts = venv_dir / "bin", venv_dir / "Scripts"
    if bin_dir.exists() or not scripts.is_dir():
        return
    LAUNCHER._assert_provision_lock_owned()
    result = LAUNCHER._create_windows_junction(bin_dir, scripts)
    if result is None or result.returncode != 0 or not bin_dir.is_dir():
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
        if not LAUNCHER._provision_in_progress():
            return
        if waited == 0:
            log("Another process is already building the environment; waiting...")
        if waited >= timeout_s:
            fail(
                f"The shared runtime is still being provisioned after {timeout_s}s; "
                "retry onboarding after that process exits"
            )
            raise SystemExit(1)
        time.sleep(5)
        waited += 5


def setup_venv(base_python: str) -> Path:
    """Create or reuse the shared venv and install packages."""
    _wait_for_provisioning()
    lock_token = LAUNCHER._acquire_provision_lock()
    if lock_token is None:
        fail(
            "Another Kumiho host is provisioning the shared environment; "
            "wait for it to finish and rerun onboarding"
        )
        raise SystemExit(1)
    try:
        with LAUNCHER._provision_lock_heartbeat(lock_token):
            return _setup_venv_locked(base_python)
    finally:
        LAUNCHER._release_provision_lock(lock_token)


def _setup_venv_locked(base_python: str) -> Path:
    provision_env = _provision_subprocess_env()
    if VENV_DIR.exists() and not LAUNCHER._python_interpreter_works(VENV_PYTHON):
        backup = VENV_DIR.with_name(
            f"{VENV_DIR.name}.broken-{os.getpid()}-{secrets.token_hex(4)}"
        )
        try:
            LAUNCHER._assert_provision_lock_owned()
            VENV_DIR.rename(backup)
        except OSError as exc:
            fail(
                "The existing shared runtime is not executable and could not "
                f"be preserved for repair: {type(exc).__name__}"
            )
            raise SystemExit(1) from None
        warn(f"Preserved the unusable shared runtime at {backup}")
    if VENV_PYTHON.exists():
        ok(f"Venv exists: {VENV_DIR}")
    else:
        log("Creating venv...")
        LAUNCHER._assert_provision_lock_owned()
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = bounded_proc.run(
                [base_python, "-I", "-m", "venv", str(VENV_DIR)], timeout=VENV_TIMEOUT_S,
                env=provision_env,
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
    if not LAUNCHER._python_interpreter_works(VENV_PYTHON):
        fail("The shared runtime is not a valid Python 3.10+ virtual environment")
        raise SystemExit(1)
    link_windows_bin(VENV_DIR)

    spec = package_spec()
    needs_install = LAUNCHER._needs_install(VENV_PYTHON, marker_path(), spec)

    # Install/upgrade packages only when the shared Desktop/Claude/Codex venv
    # does not already satisfy the contract. pip's build-backend children inherit the pipe
    # handles, which is exactly the pipe-holder that used to turn "pip timed
    # out" into an indefinite hang of interactive onboarding (#36).
    #
    # Say how long BEFORE the wait, not after: output is captured and pip runs
    # --quiet, so this is several silent minutes on a fresh machine and an
    # unannounced silence is indistinguishable from a hang.
    if needs_install:
        log("Installing kumiho packages (first run downloads ~150 MB; "
            "several minutes is normal)...")
        try:
            pip_check = bounded_proc.run(
                [str(VENV_PYTHON), "-I", "-m", "pip", "--version"],
                timeout=10,
                env=provision_env,
            )
            if pip_check.returncode != 0:
                ensure_pip = bounded_proc.run(
                    [str(VENV_PYTHON), "-I", "-m", "ensurepip", "--upgrade"],
                    timeout=VENV_TIMEOUT_S,
                    env=provision_env,
                )
                if ensure_pip.returncode != 0:
                    fail(f"pip bootstrap failed: {ensure_pip.stderr}")
                    raise SystemExit(1)
            r = bounded_proc.run(
                [str(VENV_PYTHON), "-I", "-m", "pip", "install", "--upgrade", "--quiet",
                 *shlex.split(spec)],
                timeout=PIP_TIMEOUT_S,
                env=provision_env,
            )
        except subprocess.TimeoutExpired:
            fail(f"pip install timed out after {PIP_TIMEOUT_S}s")
            sys.exit(1)
        if r.returncode != 0:
            fail(f"pip install failed: {r.stderr}")
            sys.exit(1)
        ok("kumiho[mcp] and kumiho-memory[all] installed")
    else:
        ok(f"Shared Kumiho runtime already satisfies {spec}")

    # Verify MCP server is importable
    try:
        r = bounded_proc.run(
            [str(VENV_PYTHON), "-I", "-c", "import kumiho.mcp_server"], timeout=10,
            env=provision_env,
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
        LAUNCHER._assert_provision_lock_owned()
        marker.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHER._write_install_marker(marker, spec)
        ok(f"Provisioning marker written: {marker}")
    except OSError as exc:
        # Not fatal -- the packages ARE installed and the server will run. Say
        # what is lost, because the symptom otherwise appears nowhere.
        warn(f"Could not write the provisioning marker {marker}: {exc}\n"
             f"      Auto-recall stays off until the MCP server rewrites it.")

    # hooks.json can only name ${CLAUDE_PLUGIN_DATA}; point that stable path at
    # the cross-host runtime, preserving any old per-plugin venv as a backup.
    # Keep this last: once the migration lock is released, an older Desktop
    # build can observe that legacy mutex path and begin its own provisioning.
    LAUNCHER._assert_provision_lock_owned()
    LAUNCHER._ensure_plugin_data_venv_alias(VENV_DIR)

    return VENV_PYTHON


# ---------------------------------------------------------------------------
# Step 2: Authentication
# ---------------------------------------------------------------------------


def check_existing_auth() -> str | None:
    """Legacy cache inspector retained for compatibility; active setup skips it."""
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
    """Legacy writer retained for compatibility; active setup never calls it."""
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


def _sdk_cloud_auth_works(token: str | None = None) -> bool:
    """Let the SDK validate its own explicit token or shared login cache."""
    env = dict(os.environ)
    env["KUMIHO_PLUGIN_SHARED_HOME"] = str(KUMIHO_DIR)
    if token is not None:
        env["KUMIHO_AUTH_TOKEN"] = token
    try:
        result = bounded_proc.run(
            [str(VENV_PYTHON), "-I", str(CLOUD_RUNNER), "--auth-check"],
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def setup_auth(cli_token: str | None = None) -> tuple[str | None, bool]:
    """Delegate Cloud authentication entirely to the installed Python SDK.

    An explicit API token is passed through without parsing. Otherwise the SDK
    uses the shared ``~/.kumiho`` credentials maintained by Kumiho Desktop,
    ``kumiho-auth login``, or ``kumiho-cli login``.
    """
    token = cli_token.strip() if cli_token is not None else None
    if token:
        warn(
            "Compatibility --token/--token-stdin input is used only by this "
            "setup process and is not saved. Before starting Claude, configure "
            "a persistent KUMIHO_AUTH_TOKEN or use kumiho-auth login / "
            "kumiho-cli login."
        )
        if _sdk_cloud_auth_works(token):
            ok("Explicit Kumiho API token verified by the Python SDK")
            return token, True
        fail("The Python SDK could not authenticate with the explicit token")
        return token, False

    ambient = (os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip()
    if ambient:
        if _sdk_cloud_auth_works():
            ok("KUMIHO_AUTH_TOKEN verified by the Python SDK")
            return ambient, True
        fail("The Python SDK could not authenticate with KUMIHO_AUTH_TOKEN")
        return ambient, False

    if _sdk_cloud_auth_works():
        ok("Shared ~/.kumiho credentials verified by the Python SDK")
        return None, True

    if AUTO_YES or not sys.stdin.isatty():
        warn(
            "No SDK credential is available. Set KUMIHO_AUTH_TOKEN or run "
            "kumiho-auth login / kumiho-cli login, then rerun onboarding."
        )
        return None, False

    log("No cached SDK credential found; running kumiho-auth login...")
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-I", "-m", "kumiho.auth_cli", "login"],
            timeout=5 * 60,
            env={**os.environ, "KUMIHO_CONFIG_DIR": str(KUMIHO_DIR)},
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    authenticated = bool(
        result is not None
        and result.returncode == 0
        and _sdk_cloud_auth_works()
    )
    if authenticated:
        ok("Shared ~/.kumiho credentials verified by the Python SDK")
    else:
        fail(
            "SDK login failed; set KUMIHO_AUTH_TOKEN or run "
            "kumiho-auth login / kumiho-cli login"
        )
    return None, authenticated


# ---------------------------------------------------------------------------
# Step 2 (alt): Self-hosted Community Edition (CE)
# ---------------------------------------------------------------------------


def choose_backend(args: argparse.Namespace) -> str:
    """Return ``cloud`` or ``ce`` while preserving an existing CE selection.

    ``--yes`` is commonly used by host integrations that cannot answer a
    prompt. In that mode an existing persisted backend is authoritative; a
    fresh install retains the historical Cloud default.
    """
    if getattr(args, "ce", False):
        return "ce"
    if args.token:
        return "cloud"
    if AUTO_YES:
        persisted = (os.getenv("KUMIHO_CLAUDE_MODE", "") or "").strip().lower()
        if persisted in {"ce", "community", "self-hosted", "local"}:
            return "ce"
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
    """Validate a CE endpoint without discarding its transport security."""
    import ipaddress
    target = (raw or "").strip()
    if not target or any(char in target for char in "\r\n\0"):
        return ""
    import urllib.parse

    has_scheme = "://" in target
    parsed = urllib.parse.urlsplit(target if has_scheme else f"//{target}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme and scheme not in {"http", "https", "grpc", "grpcs"}:
        raise ValueError("CE endpoint scheme must be http(s) or grpc(s)")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CE endpoint contains unsupported URL components")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CE endpoint has an invalid port") from exc
    if port is None:
        if not scheme:
            raise ValueError("CE endpoint must include a port")
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    loopback_host = host.rstrip(".").lower()
    loopback = loopback_host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(loopback_host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise ValueError("CE endpoints must use a loopback host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    return f"{scheme}://{authority}" if scheme else authority


def _validate_ce_url(
    raw: str,
    *,
    schemes: set[str],
    label: str,
    require_tls_for_remote: bool = False,
) -> str:
    import ipaddress
    import urllib.parse

    value = (raw or "").strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError(f"{label} is empty or invalid")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in schemes or not parsed.netloc:
        raise ValueError(f"{label} has an unsupported URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain credentials, query, or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port") from exc
    if require_tls_for_remote:
        host = (parsed.hostname or "").rstrip(".").lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            raise ValueError(f"{label} must use a loopback host")
    return value


def _probe_ce(endpoint: str, timeout: float = 2.0) -> bool:
    """Best-effort, scheme-preserving CE liveness check."""
    import urllib.parse
    import urllib.request

    try:
        target = _normalize_endpoint(endpoint)
    except ValueError:
        return False
    if not target:
        return False
    if "://" in target:
        parsed = urllib.parse.urlsplit(target)
        probe_scheme = "https" if parsed.scheme in {"https", "grpcs"} else "http"
        authority = parsed.netloc
    else:
        probe_scheme, authority = "http", target
    try:
        with urllib.request.urlopen(
            f"{probe_scheme}://{authority}/api/_live", timeout=timeout
        ) as resp:
            return getattr(resp, "status", 200) < 400
    except Exception:
        return False


def _ce_runtime_env(ce: dict) -> dict:
    """Env for direct-SDK subprocesses (ingest/verify): tokenless CE routing."""
    env = {
        "KUMIHO_CLAUDE_MODE": "ce",
        "KUMIHO_SERVER_ENDPOINT": ce["endpoint"],
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
    try:
        endpoint = _normalize_endpoint(endpoint) or DEFAULT_CE_ENDPOINT
        redis_url = (getattr(args, "ce_redis_url", None) or "").strip()
        if redis_url:
            redis_url = _validate_ce_url(
                redis_url,
                schemes={"redis", "rediss"},
                label="CE Redis URL",
                require_tls_for_remote=True,
            )
        llm_base_url = (getattr(args, "ce_llm_base_url", None) or "").strip()
        if llm_base_url:
            llm_base_url = _validate_ce_url(
                llm_base_url,
                schemes={"http", "https"},
                label="CE LLM URL",
                require_tls_for_remote=True,
            )
    except ValueError as exc:
        fail(f"Invalid CE configuration: {exc}")
        raise SystemExit(2) from exc

    ce = {
        "endpoint": endpoint,
        "redis_url": redis_url,
        "llm_base_url": llm_base_url,
    }

    if _probe_ce(endpoint):
        ok(f"CE server detected at {endpoint}")
    else:
        warn(f"No CE server answering at {endpoint} yet")
        warn("Start it first — see github.com/KumihoIO/kumiho-server-community")

    if not AUTO_YES and not ce["llm_base_url"]:
        llm = ask("Local LLM base URL for summarization (optional, blank to skip)", "").strip()
        if llm:
            try:
                ce["llm_base_url"] = _validate_ce_url(
                    llm,
                    schemes={"http", "https"},
                    label="CE LLM URL",
                    require_tls_for_remote=True,
                )
            except ValueError as exc:
                fail(f"Invalid CE configuration: {exc}")
                raise SystemExit(2) from exc

    return ce


def verify_ce(ce: dict) -> None:
    """Confirm the CE server answers on its liveness endpoint."""
    if _probe_ce(ce["endpoint"]):
        ok(f"CE server reachable at {ce['endpoint']}")
    else:
        warn(f"CE server not reachable at {ce['endpoint']} — start kumiho-server CE, "
             "then start a new session")


# ---------------------------------------------------------------------------
# Legacy credential/config writers (not used by active Cloud onboarding)
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
            _account_home() / "Library" / "Application Support" / "Claude"
            / "claude_desktop_config.json"
        )
        xdg = os.getenv("XDG_CONFIG_HOME", "")
        paths.append(
            Path(xdg) / "Claude" / "claude_desktop_config.json"
            if xdg else _account_home() / ".config" / "Claude" / "claude_desktop_config.json"
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
    if (
        env.get("KUMIHO_AUTH_TOKEN") == token
        and env.get("KUMIHO_CLAUDE_HOST") == "claude"
    ):
        return True  # already in sync
    env["KUMIHO_AUTH_TOKEN"] = token
    env["KUMIHO_CLAUDE_HOST"] = "claude"
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
    for k, v in {**updates, "KUMIHO_CLAUDE_HOST": "claude"}.items():
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
        # Keep the remainder of this onboarding run on the newly selected
        # backend too; persisting an empty value does not mutate this process.
        os.environ.pop(k, None)
    # Claude Desktop config — clean every reachable installation, not just the
    # first one (MSIX and classic installs can coexist during migration).
    for desktop_path in _claude_desktop_config_paths():
        _delete_env_from_config(desktop_path, keys)
    # Host launches intentionally distrust route values merged from a project
    # or ambient process. Remove the old backend from the exact global source
    # that the hardened launcher is allowed to restore.
    _merge_user_global_claude_env(remove=tuple(keys))


def _upsert_shell_export(rc_path: Path, key: str, value: str) -> bool:
    """Upsert one shell-quoted export in a shell rc/env file."""
    marker = f"export {key}="
    # Values include user-supplied CE URLs. Double quotes still evaluate
    # command substitutions and backticks, so quote the complete value with
    # the stdlib's POSIX-shell encoder before persisting it into a login file.
    new_line = f"export {key}={shlex.quote(value)}\n"
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
        return _upsert_shell_export(_account_home() / ".zshenv", key, value)

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
        env_dir = _account_home() / ".config" / "environment.d"
        env_dir.mkdir(parents=True, exist_ok=True)
        try:
            (env_dir / "kumiho.conf").write_text(f"{key}={value}\n", encoding="utf-8")
        except Exception:
            pass
        # Also write ~/.profile as portable fallback for non-systemd distros
        _upsert_shell_export(_account_home() / ".profile", key, value)
        return True


def patch_mcp_json(token: str | None) -> None:
    """Legacy compatibility helper; active setup never calls this function.

    Write a token to all reachable MCP config locations.

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
    _neutralize_env_markers(list(_CE_PERSISTED_ENV_KEYS))

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
    updates = dict(pairs)
    stale = tuple(key for key in _CE_PERSISTED_ENV_KEYS if key not in updates)

    # This exact user-global file persists the validated loopback CE routes
    # across plugin updates.
    if _merge_user_global_claude_env(updates, remove=stale):
        ok(f"CE config written to trusted user settings ({CLAUDE_SETTINGS.parent})")

    # 1. OS-level user env vars (inherited by Claude Desktop on next launch)
    for key in stale:
        if (os.getenv(key, "") or "").strip():
            _set_os_env_var(key, "")
        os.environ.pop(key, None)
    for k, v in pairs:
        if _set_os_env_var(k, v):
            ok(f"{k} set as user environment variable (OS level)")
        else:
            warn(f"Could not set OS-level env var {k} — a restart may be needed")

    # 2. Claude Desktop global config (triggers MCP server restart)
    desktop_written = False
    for desktop_path in _claude_desktop_config_paths():
        _delete_env_from_config(desktop_path, list(stale))
        if _try_write_env_to_config(desktop_path, updates):
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

    Cloud mode passes any explicit *token* through unchanged, then delegates
    auth, refresh, discovery, and regional routing to the SDK adapter. CE mode
    routes tokenlessly via the env derived from *ce_env*."""
    if not INGEST_SCRIPT.exists():
        warn(f"Ingestion script not found: {INGEST_SCRIPT}")
        warn("Run: python -m kumiho_memory ingest-skill <SKILL.md>")
        return

    if not ask_yes_no("Ingest skills into Kumiho graph? (populates CognitiveMemory/Skills)"):
        warn("Skipped — run later: python scripts/ingest-skills.py")
        return

    log("Ingesting skills into the graph...")
    if ce_env is not None:
        env = {**os.environ, **_ce_runtime_env(ce_env)}
        # The explicit CE endpoint above intentionally replaces any inherited
        # Cloud endpoint. Only the legacy alias must be removed.
        env.pop("KUMIHO_SERVER_ADDRESS", None)
        adapter = CE_RUNNER
    else:
        env = dict(os.environ)
        if token:
            env["KUMIHO_AUTH_TOKEN"] = token
        env["KUMIHO_PLUGIN_SHARED_HOME"] = str(KUMIHO_DIR)
        adapter = CLOUD_RUNNER
    r = subprocess.run(
        [str(venv_python), "-I", str(adapter), "--script", str(INGEST_SCRIPT)],
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
    """Ask the Cloud adapter and SDK to verify auth plus official discovery."""
    log("Verifying Kumiho Cloud connection...")
    env = dict(os.environ)
    if token:
        env["KUMIHO_AUTH_TOKEN"] = token
    env["KUMIHO_PLUGIN_SHARED_HOME"] = str(KUMIHO_DIR)
    try:
        r = bounded_proc.run(
            [str(venv_python), "-I", str(CLOUD_RUNNER), "--auth-check"],
            timeout=30, env=env,
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
        help="deprecated one-run SDK token pass-through; prefer KUMIHO_AUTH_TOKEN",
    )
    p.add_argument(
        "--token-stdin",
        action="store_true",
        help="deprecated one-run Cloud token pass-through; the token is not saved",
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

    if args.token and args.token_stdin:
        fail("Use only one of --token or --token-stdin")
        return 2
    if args.token_stdin:
        try:
            args.token = (
                getpass.getpass("  Kumiho API token: ")
                if sys.stdin.isatty()
                else sys.stdin.readline()
            )
        except (EOFError, KeyboardInterrupt):
            fail("Could not read the API token from stdin")
            return 2
    one_run_token = bool(args.token and args.token.strip())

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
    # Before the long provisioning step: this is what lets the MCP server and
    # the hooks find an interpreter at all on macOS/Linux.
    write_python_knob(base_python)
    venv_python = setup_venv(base_python)
    write_python_knob(str(venv_python))
    print()

    # A CE-specific flag implies the CE backend.
    if args.ce_endpoint or args.ce_redis_url or args.ce_llm_base_url:
        args.ce = True

    # Step 2: Backend selection + auth/config
    log("Step 2/5: Backend & authentication")
    backend = choose_backend(args)
    token: str | None = None
    cloud_authenticated = False
    ce: dict | None = None
    if backend == "ce":
        ce = setup_ce(args)
    else:
        token, cloud_authenticated = setup_auth(cli_token=args.token)
        if token:
            os.environ["KUMIHO_AUTH_TOKEN"] = token
    print()

    # Step 3: Persist backend selection only. Cloud credentials remain owned by
    # the SDK or by the caller-provided KUMIHO_AUTH_TOKEN environment.
    log("Step 3/5: Backend configuration")
    if ce is not None:
        write_ce_config(ce)
    elif cloud_authenticated:
        _neutralize_env_markers(list(_CE_PERSISTED_ENV_KEYS))
    print()

    # Step 4: Skill ingestion
    log("Step 4/5: Skill ingestion")
    if ce is not None or cloud_authenticated:
        run_ingestion(venv_python, token=token, ce_env=ce)
    else:
        warn("Skipping skill ingestion until the Python SDK can authenticate")
    print()

    # Step 5: Verify
    log("Step 5/5: Verify connection")
    if ce is not None:
        verify_ce(ce)
    elif cloud_authenticated:
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
    elif cloud_authenticated:
        print(f"  Kumiho Cloud authentication was verified by the Python SDK.")
        if one_run_token:
            print(
                f"  {YELLOW}The compatibility token was used only for this "
                f"setup process and was not saved.{RESET}"
            )
            print(
                "  Before restarting Claude, configure a persistent "
                "KUMIHO_AUTH_TOKEN or run kumiho-auth login / kumiho-cli login."
            )
        else:
            print(
                "  Authentication remains in the host environment or the "
                "shared SDK credential store."
            )
        print(f"  Start a new session — the plugin bootstraps on first message.")
    else:
        print(f"  {YELLOW}Remaining:{RESET} Authenticate with one of:")
        print(f"    1. Set KUMIHO_AUTH_TOKEN (preferred)")
        print(f"    2. Run kumiho-auth login or kumiho-cli login")
        print(f"    3. Re-run /kumiho-onboard")
    print()
    print(f"  {DIM}Plugin:  {PLUGIN_DIR}{RESET}")
    print(f"  {DIM}SDK home: {KUMIHO_DIR}{RESET}")
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
