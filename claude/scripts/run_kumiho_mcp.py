#!/usr/bin/env python3
"""Bootstrap and run Kumiho MCP server for Claude plugin environments."""

from __future__ import annotations

import argparse
import base64
import contextlib
import contextvars
import ctypes
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ``python -I`` deliberately removes the script directory from ``sys.path``.
# Every host-controlled entry point uses isolated mode so a checkout-provided
# ``sitecustomize`` cannot run before this launcher gets a chance to sanitize
# the environment; add back only this launcher's own sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bounded_proc


DEFAULT_PACKAGE_SPEC = "kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0"
MARKER_FILE = ".installed-packages.txt"
#: Asking an existing venv what it has installed. This is on the server-start
#: critical path, so it is short -- a venv that cannot answer in this long is
#: broken, and the caller reinstalls.
PROBE_TIMEOUT_S = 60
STARTUP_PROBE_TIMEOUT_S = 5
#: A hung package manager must not hold the cross-host runtime forever.  The
#: process-tree terminator below makes this a real bound even when pip starts a
#: build backend of its own.
PIP_TIMEOUT_S = 1800
# A successful full runtime probe leaves a fingerprint beside the shared venv.
# Startup can trust it without repeating a probe that may exceed the host's
# five-second launch budget on antivirus-scanned or network-backed profiles.
RUNTIME_ATTESTATION_FILE = ".runtime-ready.json"
#: Product token for the discovery User-Agent. The version half is read from
#: the plugin manifest at startup (see ``_default_discovery_user_agent``)
#: rather than pinned here -- a pinned copy sat at 0.16.0 through five minor
#: releases and made server-side version telemetry meaningless. The token
#: itself stays ``kumiho-claude``: it is a wire identifier the control plane
#: and its edge rules already match on, so renaming it is a protocol change
#: rather than housekeeping.
DISCOVERY_USER_AGENT_PRODUCT = "kumiho-claude"
#: Used only when the manifest is unreadable (a partial install, or a host
#: that copies scripts/ without .claude-plugin/). Deliberately not a version
#: number: a wrong number is worse telemetry than an honest "unknown".
DISCOVERY_USER_AGENT_UNKNOWN_VERSION = "unknown"

# Self-hosted Community Edition (CE) defaults.  CE is bound through an explicit
# tokenless SDK client at KUMIHO_SERVER_ENDPOINT; a local Redis URL backs CE
# working memory (Cloud gets Redis through the control-plane proxy).
DEFAULT_CE_ENDPOINT = "127.0.0.1:9190"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"
#: Idle TTL (seconds) of the CE working-memory buffer. kumiho-memory's own
#: default is 3600, tuned for shared Upstash; a local Redis holds a day of
#: session buffer for nothing, and an hour lost the buffer mid-session whenever
#: one turn or one pause ran longer than that (diagnosed 2026-09-04: every
#: mid-session bucket re-creation was a >60 min gap or a consolidate).
DEFAULT_CE_WORKING_MEMORY_TTL = "86400"
CE_MODE_VALUES = {"ce", "community", "self-hosted", "self_hosted", "selfhosted", "local"}

# Claude Code applies project ``.claude/settings*.json`` environment entries
# before it starts plugin MCP servers.  Those values are therefore ambient by
# the time this launcher runs and cannot be distinguished from a shell export.
# Never let repository-controlled Cloud routing pair with the user's shared
# ``~/.kumiho`` bearer.  Host launches clear these names first, then the
# settings reader below restores only the small allowlist it read directly
# from user-global ``~/.claude/settings*.json``.
_HOST_UNTRUSTED_CLOUD_ENV = (
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
)
_HOST_UNTRUSTED_PATH_ENV = (
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA",
    "KUMIHO_CONFIG_DIR",
    "CLAUDE_CONFIG_DIR",
    "KUMIHO_CLAUDE_HOME",
    "KUMIHO_PLUGIN_SHARED_HOME",
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
)
_HOST_UNTRUSTED_SECRET_ENV = (
    "KUMIHO_AUTH_TOKEN",
)
_HOST_UNTRUSTED_PROVISION_ENV = (
    "KUMIHO_CLAUDE_PACKAGE_SPEC",
)
_HOST_UNTRUSTED_TRANSPORT_ENV = (
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
_HOST_UNTRUSTED_DATA_ROUTE_ENV = (
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
# A repository may point at services on the user's own machine. Any route that
# crosses the machine boundary must come from an exact user-global Claude
# settings file instead.
_HOST_PROJECT_LOOPBACK_ROUTE_ENV = frozenset({
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
_HOST_TRUSTED_GLOBAL_PATH_ENV = frozenset({
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
_HOST_PERSISTED_USER_ENV = (
    "KUMIHO_AUTH_TOKEN",
    *_HOST_UNTRUSTED_CLOUD_ENV,
    "KUMIHO_CONFIG_DIR",
    "KUMIHO_CLAUDE_HOME",
    *_HOST_UNTRUSTED_PROVISION_ENV,
    *_HOST_UNTRUSTED_TRANSPORT_ENV,
    *_HOST_UNTRUSTED_DATA_ROUTE_ENV,
)
_HOST_KINDS = {"claude", "codex"}

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
_HOST_TRUSTED_PROVISION_TRANSPORT_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "AWS_CA_BUNDLE",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "GRPC_PROXY",
    "NO_GRPC_PROXY", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
})
_TRUSTED_SETTINGS_TRANSPORT_ENV: dict[str, str] = {}
_TRUSTED_GLOBAL_CE_KEYS: set[str] = set()

_READY_LOCK_WAIT_S = 5.0
_READY_LOCK_BACKOFF_S = 0.05

# Package managers and build backends never need host credentials. Keeping
# these out of provisioning children prevents a dependency build hook from
# inheriting Cloud bearer tokens or optional model-provider keys.
_PROVISION_SECRET_ENV = frozenset({
    "KUMIHO_AUTH_TOKEN",
    "KUMIHO_FIREBASE_API_KEY",
    "KUMIHO_FIREBASE_ID_TOKEN",
    "KUMIHO_FIREBASE_PROJECT_ID",
    "KUMIHO_USE_CONTROL_PLANE_TOKEN",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CODEX_BACKEND",
    "KUMIHO_CODEX_CE_ENDPOINT",
    "KUMIHO_CODEX_CE_REDIS_URL",
    "KUMIHO_CODEX_CE_LLM_BASE_URL",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_LLM_API_KEY",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_ENV_FILE",
    "KUMIHO_DISCOVERY_CACHE_FILE",
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
    "OPENAI_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "HF_TOKEN",
})
_PROVISION_SECRET_SUFFIXES = (
    "_API_KEY",
    "_AUTH_TOKEN",
    "_ACCESS_TOKEN",
    "_SECRET_KEY",
    "_TOKEN",
)
_PROVISION_CONTROL_ENV = frozenset({
    "KUMIHO_CLAUDE_PROVISION_LOCK_TOKEN",
    "KUMIHO_CLAUDE_PROVISION_SYNC",
    "KUMIHO_CLAUDE_PACKAGE_SPEC",
})

#: CREATE_NO_WINDOW: for a child whose output we capture and that spawns nothing
#: itself (mklink). A child that may spawn console processes of its own gets
#: _hidden_console_kwargs() instead, so its descendants inherit a hidden console.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class _CodexPrefixStream:
    """Render shared-launcher diagnostics as Codex without changing Claude."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, value):
        return self._stream.write(
            value.replace("[kumiho-claude]", "[kumiho-codex]")
        )

    def flush(self):
        return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _configure_host_diagnostics() -> None:
    if (
        (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() == "codex"
        and sys.stderr is not None
        and not isinstance(sys.stderr, _CodexPrefixStream)
    ):
        sys.stderr = _CodexPrefixStream(sys.stderr)


def _onboard_command_label() -> str:
    return "$kumiho-onboard" if os.getenv("KUMIHO_CLAUDE_HOST") == "codex" else "/kumiho-onboard"


def _hidden_console_kwargs() -> dict:
    """Keep a console-subsystem child off the screen when WE have no console.

    Detached workers (and the launcher when Desktop spawns it) run without a
    console, so every console child -- pip, git, a ``python -m`` run -- would
    allocate a NEW, visible one: the console window that flashed on Windows
    for the duration of each background job.  SW_HIDE on the STARTUPINFO hides
    that window and, unlike CREATE_NO_WINDOW, the hidden console is inherited
    by the child's own children, so git spawned by kumiho_memory stays hidden
    too.  No-op when a console is inherited, and on POSIX.
    """
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": info}


def _state_dir() -> Path:
    override = os.getenv("KUMIHO_CLAUDE_HOME", "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"

    xdg = os.getenv("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "kumiho-claude"
    return Path.home() / ".cache" / "kumiho-claude"


def _plugin_data_dir() -> "Path | None":
    """The host's per-plugin writable directory, or None if not discoverable.

    Measured: it is ``<config>/plugins/data/<plugin>-<marketplace>``, carries NO
    version component, and survives plugin updates -- this machine's
    ``kumiho-memory-kumiho-plugins`` was created 2026-07-12 and is still the same
    directory at 0.18.1, and a sibling from 2026-03-23 outlived four months of
    releases. That stability is what makes it the only place a hook can name an
    interpreter: hook exec-form commands substitute ``${CLAUDE_PLUGIN_DATA}`` but
    nothing else writable.
    """
    env = (os.getenv("CLAUDE_PLUGIN_DATA", "") or "").strip()
    if env and "${" not in env:            # unexpanded placeholder -> not a path
        return Path(env)
    # Not every caller is host-spawned (the wizard, --provision, a self-test), so
    # derive it from our own location in the plugin cache:
    #   <config>/plugins/cache/<marketplace>/<plugin>/<version>/scripts/<this>
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


def _host_launch_isolated() -> bool:
    """Whether repository-provided ambient routing must be distrusted."""
    return (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() in _HOST_KINDS


def _service_route_is_loopback(raw: object) -> bool:
    """Return whether a service URL/authority resolves only to loopback.

    This is a provenance check, not a complete backend validator. The normal
    CE/Redis/LLM validators still enforce their supported schemes and shapes.
    """
    if not isinstance(raw, str) or any(char in raw for char in "\r\n\0"):
        return False
    value = raw.strip()
    if not value or _looks_like_placeholder(value):
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


def _account_home() -> Path:
    """Return the OS account home without trusting host-injected HOME values."""
    if not _host_launch_isolated():
        return Path.home()
    try:
        if os.name == "nt":
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            # CSIDL_PROFILE is resolved by Windows for the current access token.
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
            "[kumiho-claude] Could not resolve the operating-system account home; "
            "refusing a host-provided runtime path."
        )
    return home


def _trusted_persisted_user_environment() -> dict[str, str]:
    """Read environment values from user-owned persistent sources.

    Claude merges project settings over its process environment, so the live
    value has ambiguous provenance. Reading the underlying OS-user store (or
    the exact files written by setup.py) preserves existing installations
    without trusting a repository override.
    """
    wanted = set(_HOST_PERSISTED_USER_ENV)
    if (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() == "codex":
        # Codex backend/auth routing comes only from codex.json. The transport
        # and package identity are host-neutral and may be shared.
        wanted = set((*_HOST_UNTRUSTED_PROVISION_ENV, *_HOST_UNTRUSTED_TRANSPORT_ENV))
    found: dict[str, str] = {}
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                for key in wanted:
                    try:
                        raw, _kind = winreg.QueryValueEx(handle, key)
                    except OSError:
                        continue
                    if isinstance(raw, str):
                        found[key] = raw
        except (ImportError, OSError):
            pass
    else:
        home = _account_home()
        for path in (
            home / ".config" / "environment.d" / "kumiho.conf",
            home / ".zshenv",
            home / ".profile",
        ):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                text = line.strip()
                if text.startswith("export "):
                    text = text[7:].lstrip()
                key, separator, encoded = text.partition("=")
                key = key.strip()
                if not separator or key not in wanted or key in found:
                    continue
                try:
                    values = shlex.split(encoded, comments=True, posix=True)
                except ValueError:
                    continue
                if len(values) == 1:
                    found[key] = values[0]

    trusted: dict[str, str] = {}
    for key, raw in found.items():
        value = raw.strip()
        if (
            not value
            or "${" in value
            or "$" in value
            or "`" in value
            or any(char in raw for char in "\r\n\0")
        ):
            continue
        if key in _HOST_TRUSTED_GLOBAL_PATH_ENV:
            path = Path(value)
            if not path.is_absolute():
                continue
            value = str(path)
        trusted[key] = value
    return trusted


def _hydrate_trusted_persisted_user_environment() -> None:
    if not _host_launch_isolated():
        return
    for key, value in _trusted_persisted_user_environment().items():
        _set_env_if_absent(key, value, "the OS user environment")


def _clear_host_untrusted_environment() -> None:
    """Drop Cloud route/cache and runtime-root values injected by a project.

    This deliberately runs before credentials are loaded.  Claude's trusted
    user-global settings are read back by :func:`_hydrate_env_from_claude_settings`;
    Codex restores no Claude setting and applies its own backend file instead.
    Direct maintenance invocations without an explicit host retain the legacy
    environment override behavior used by developers and tests.
    """
    if not _host_launch_isolated():
        return
    host = (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower()
    ambient_token = (os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip()
    safe_project_routes = {}
    for key in _HOST_UNTRUSTED_DATA_ROUTE_ENV:
        value = (os.getenv(key, "") or "").strip()
        if _host_config_value_allowed(key, value):
            safe_project_routes[key] = value
    account_home = _account_home()
    untrusted = frozenset(key.upper() for key in (
        *_HOST_UNTRUSTED_CLOUD_ENV,
        *_HOST_UNTRUSTED_PATH_ENV,
        *_HOST_UNTRUSTED_SECRET_ENV,
        *_HOST_UNTRUSTED_PROVISION_ENV,
        *_HOST_UNTRUSTED_TRANSPORT_ENV,
        *_HOST_UNTRUSTED_DATA_ROUTE_ENV,
    ))
    # Environment keys are case-sensitive on POSIX. Normalize comparisons so
    # lowercase proxy aliases cannot bypass the host boundary.
    for actual in tuple(os.environ):
        if actual.upper() in untrusted:
            os.environ.pop(actual, None)
    # Keep subsequent stdlib/SDK Path.home and user-config lookups pinned to
    # the OS account even if the host originally inherited hostile values.
    os.environ["HOME"] = str(account_home)
    os.environ["USERPROFILE"] = str(account_home)
    if os.name == "nt":
        drive, tail = os.path.splitdrive(str(account_home))
        os.environ["HOMEDRIVE"] = drive
        os.environ["HOMEPATH"] = tail or "\\"
        os.environ["APPDATA"] = str(account_home / "AppData" / "Roaming")
        os.environ["LOCALAPPDATA"] = str(account_home / "AppData" / "Local")
    if host == "codex" and ambient_token and not _looks_like_placeholder(ambient_token):
        os.environ["KUMIHO_AUTH_TOKEN"] = ambient_token
    os.environ.update(safe_project_routes)


def _kumiho_home() -> Path:
    """Cross-host Kumiho home used for the single shared runtime."""
    override = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    if override:
        return Path(override) if _host_launch_isolated() else Path(override).expanduser()
    return _account_home() / ".kumiho"


def _venv_dir() -> Path:
    """The one runtime shared by Claude and Codex."""
    return _kumiho_home() / "venv"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _windows_pe_executable(path: Path) -> bool:
    """Reject text/DOS/NE files before Windows can show a modal app dialog."""
    if os.name != "nt":
        return True
    try:
        size = path.stat().st_size
        if size < 68:
            return False
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(header[0x3C:0x40], "little")
            if pe_offset < 64 or pe_offset > min(size - 4, 4 * 1024 * 1024):
                return False
            stream.seek(pe_offset)
            return stream.read(4) == b"PE\0\0"
    except OSError:
        return False


def _windows_system_executable(name: str) -> str:
    """Resolve a Windows system binary without trusting PATH/SystemRoot."""
    if os.name != "nt":
        return name
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetSystemDirectoryW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint,
        )
        kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if 0 < length < len(buffer):
            return str(Path(buffer.value) / name)
    except Exception:
        pass
    return str(Path(r"C:\Windows\System32") / name)


def _windows_powershell_executable() -> str:
    """Resolve inbox Windows PowerShell without consulting mutable PATH."""
    system_dir = Path(_windows_system_executable("cmd.exe")).parent
    return str(system_dir / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _create_windows_junction(link: Path, target: Path):
    """Create a junction while keeping both paths out of shell source text."""
    values = (str(link), str(target))
    if any(any(char in value for char in '\r\n\0"') for value in values):
        return None
    env = _provision_subprocess_env({
        "KUMIHO_JUNCTION_LINK": values[0],
        "KUMIHO_JUNCTION_TARGET": values[1],
    })
    try:
        return bounded_proc.run(
            [
                _windows_powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; "
                "New-Item -ItemType Junction "
                "-Path $env:KUMIHO_JUNCTION_LINK "
                "-Target $env:KUMIHO_JUNCTION_TARGET "
                "-ErrorAction Stop | Out-Null",
            ],
            env=env,
            timeout=30,
        )
    except bounded_proc.ProcessAborted:
        raise
    except (OSError, subprocess.SubprocessError):
        return None


def _create_directory_alias(link: Path, target: Path) -> bool:
    """Create a directory symlink (POSIX) or junction (Windows)."""
    try:
        if os.name != "nt":
            os.symlink(target, link, target_is_directory=True)
            return True
        result = _create_windows_junction(link, target)
        return result is not None and result.returncode == 0 and link.is_dir()
    except bounded_proc.ProcessAborted:
        raise
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_plugin_data_venv_alias(venv_dir: Path) -> None:
    """Keep Claude's fixed hook path pointed at the cross-host runtime.

    Older releases put a real venv under ``CLAUDE_PLUGIN_DATA``. It cannot be
    reused after a move because venv launchers embed absolute paths, so retain
    it as a recoverable ``venv.pre-shared*`` backup and install an alias. A
    locked Windows directory is left untouched and retried on the next start.
    """
    if (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() == "codex":
        return
    _assert_provision_lock_owned()
    data = _plugin_data_dir()
    if data is None or not _venv_python(venv_dir).exists():
        return
    link = data / "venv"
    try:
        if os.path.lexists(link) and link.resolve() == venv_dir.resolve():
            return
    except OSError:
        pass

    _assert_provision_lock_owned()
    data.mkdir(parents=True, exist_ok=True)
    migration_token = _acquire_alias_migration_lock(data)
    if migration_token is None:
        print(
            "[kumiho-claude] The previous hook runtime is busy; deferring its "
            "shared-runtime migration.",
            file=sys.stderr,
        )
        return
    try:
        # Another launcher may have completed the migration while this process
        # waited for the legacy Desktop lock.
        try:
            if os.path.lexists(link) and link.resolve() == venv_dir.resolve():
                return
        except OSError:
            pass
        _assert_provision_lock_owned()
        _migrate_plugin_data_venv_alias(data, link, venv_dir)
    finally:
        _release_alias_migration_lock(data, migration_token)


def _migrate_plugin_data_venv_alias(
    data: Path, link: Path, venv_dir: Path
) -> None:
    """Move one idle legacy venv aside and replace it with the shared alias.

    Ordering is what makes this survive a crash.  The alias is built first,
    under a temporary name, so the slow step (a PowerShell junction, up to a
    couple of seconds on Windows) happens while the legacy venv is still in
    place and every hook can still spawn.  Only then are the two renames done,
    each of which is a single directory-entry update.  A kill between them
    leaves either the legacy venv or the finished alias at ``link`` -- never
    an absent path that neither ``.mcp.json`` nor a hook could start from and
    that this launcher could not come back to repair.
    """
    if not os.path.lexists(link):
        _assert_provision_lock_owned()
        if not _create_directory_alias(link, venv_dir):
            print(
                f"[kumiho-claude] Could not link the hook runtime {link} to {venv_dir}.",
                file=sys.stderr,
            )
        return

    staged = data / "venv.shared-alias.tmp"
    if os.path.lexists(staged):
        # A previous attempt died after staging.  A junction/symlink is a
        # single directory entry, so removing it never touches the target.
        try:
            if os.name == "nt":
                os.rmdir(staged)
            else:
                os.unlink(staged)
        except OSError:
            shutil.rmtree(staged, ignore_errors=True)
    _assert_provision_lock_owned()
    if not _create_directory_alias(staged, venv_dir):
        print(
            f"[kumiho-claude] Could not link the hook runtime {link} to {venv_dir}; "
            "the previous hook runtime is left in place.",
            file=sys.stderr,
        )
        return

    backup = data / "venv.pre-shared"
    suffix = 1
    while os.path.lexists(backup):
        backup = data / f"venv.pre-shared.{suffix}"
        suffix += 1
    try:
        _assert_provision_lock_owned()
        link.rename(backup)
    except OSError as exc:
        print(
            f"[kumiho-claude] Could not migrate the previous hook runtime "
            f"yet ({exc}); retry after active hooks exit.",
            file=sys.stderr,
        )
        try:
            if os.name == "nt":
                os.rmdir(staged)
            else:
                os.unlink(staged)
        except OSError:
            pass
        return
    print(
        f"[kumiho-claude] Preserved the previous hook runtime at {backup}.",
        file=sys.stderr,
    )
    try:
        _assert_provision_lock_owned()
        staged.rename(link)
    except OSError as exc:
        print(
            f"[kumiho-claude] Could not install the hook runtime alias ({exc}); "
            "restoring the previous hook runtime.",
            file=sys.stderr,
        )
        try:
            backup.rename(link)
        except OSError:
            pass


def _link_windows_bin(venv_dir: Path) -> None:
    """Give a Windows venv a POSIX-shaped ``bin/python`` via a directory junction.

    This is what lets ONE literal string -- ``${CLAUDE_PLUGIN_DATA}/venv/bin/python``
    -- name the interpreter in an exec-form hook on every platform. Exec-form
    hooks are raw ``child_process.spawn`` with no shell and no PATHEXT, so a
    shipped ``.cmd`` or an extensionless dispatcher does not resolve; the
    junction does.

    Measured constraint: the junction must live INSIDE the venv it serves. One
    pointing at an external venv's Scripts makes sys.prefix resolve to the
    junction's parent and site-packages come back empty. Junctions need no
    admin rights.
    """
    if os.name != "nt":
        return
    bin_dir, scripts = venv_dir / "bin", venv_dir / "Scripts"
    if bin_dir.exists() or not scripts.is_dir():
        return
    _assert_provision_lock_owned()
    r = _create_windows_junction(bin_dir, scripts)
    # Report rather than fail silently: without this junction every hook is
    # unstartable, and a hook that never fires looks like a plugin that does
    # nothing rather than one that is broken.
    hook_python = bin_dir / "pythonw.exe"
    if not hook_python.exists() or (r is not None and r.returncode != 0):
        print("[kumiho-claude] Hook interpreter %s is missing; hooks will not "
              "fire until this is repaired (mklink said: %s)"
              % (hook_python, ((r.stderr or r.stdout).strip()[:120] if r else "n/a")),
              file=sys.stderr)


def _ensure_hook_interpreter(venv_dir: Path) -> None:
    """Make ``<venv>/bin/pythonw`` resolvable on every platform.

    hooks.json names ONE literal interpreter, ``${CLAUDE_PLUGIN_DATA}/venv/bin/pythonw``.
    On Windows that is the venv's real ``pythonw.exe`` through the bin junction:
    a GUI-subsystem binary never allocates a console, which is what stopped a
    console window from flashing on every hook under Desktop, where claude.exe
    has no console for its children to inherit.  On POSIX no such binary
    exists, so a symlink to ``bin/python`` provides the same name.
    """
    _link_windows_bin(venv_dir)
    if os.name != "nt":
        _link_posix_pythonw(venv_dir)


def _link_posix_pythonw(venv_dir: Path) -> None:
    """``bin/pythonw`` -> ``python`` inside a POSIX venv, a copy where the
    filesystem refuses symlinks. Idempotent and never raises: a hook that
    cannot start is reported, not turned into a launcher failure."""
    bin_dir = venv_dir / "bin"
    target, link = bin_dir / "python", bin_dir / "pythonw"
    if link.exists() or not target.exists():
        return
    try:
        _assert_provision_lock_owned()
        os.symlink("python", link)
    except OSError:
        try:
            _assert_provision_lock_owned()
            shutil.copy2(target, link)
        except OSError as exc:
            print("[kumiho-claude] Could not create %s (%s); hooks will not fire "
                  "until it exists" % (link, exc), file=sys.stderr)


def _provision_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment suitable for venv/pip/build-backend children."""
    env = os.environ.copy()
    for key in tuple(env):
        normalized = key.upper()
        if normalized in _PROVISION_CONTROL_ENV:
            continue
        if (
            normalized in _PROVISION_SECRET_ENV
            or normalized.endswith(_PROVISION_SECRET_SUFFIXES)
            or (
                _host_launch_isolated()
                and (
                    normalized in _HOST_PROVISION_ENV_EXACT_SCRUB
                    or normalized.startswith(_HOST_PROVISION_ENV_PREFIXES)
                )
            )
        ):
            env.pop(key, None)
    if _host_launch_isolated():
        # Proxy and CA settings are useful to pip, but only when they came
        # from the OS user's persistent environment. Rehydrate that exact
        # source after removing host/project-injected values.
        trusted_transport = {
            **_TRUSTED_SETTINGS_TRANSPORT_ENV,
            **_trusted_persisted_user_environment(),
        }
        for key in _HOST_TRUSTED_PROVISION_TRANSPORT_ENV:
            value = trusted_transport.get(key)
            # A project may overwrite a previously loaded settings value in
            # the live environment. Do not let that overwrite inherit the
            # stale trusted value; a missing value is safe to restore.
            if value and (
                key not in _TRUSTED_SETTINGS_TRANSPORT_ENV
                or key not in os.environ
                or os.environ.get(key) == value
            ):
                env[key] = value
    if extra:
        env.update(extra)
    return env


def _run(
    cmd: list[str], *, check: bool = True, timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> int:
    # Redirect stdout → stderr so pip/venv output never pollutes the MCP
    # stdio channel.  Claude Desktop connects stdout directly to its
    # JSON-RPC parser, so any stray text there hangs the connection.
    result = bounded_proc.run(
        cmd,
        timeout=timeout,
        env=env,
        stdout=sys.stderr,
        stderr=None,
    )
    returncode = result.returncode
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, cmd)
    return returncode


#: Pre-release stages and their sort position relative to the bare release (0).
#: PEP 440's order is dev < alpha < beta < rc < release < post. Longest-prefix
#: first, so "alpha" is not eaten by "a".
_PRE_RELEASE_RANKS = (
    ("dev", -4),
    ("alpha", -3), ("a", -3),
    ("beta", -2), ("b", -2),
    ("rc", -1), ("pre", -1), ("c", -1),
)


def _suffix_rank(suffix: str) -> int:
    for marker, rank in _PRE_RELEASE_RANKS:
        if suffix.startswith(marker):
            return rank
    return 1  # post-releases and local versions sort ABOVE the bare release


def _version_key(value: str) -> list:
    """``[(number, rank)]`` per dot-chunk; rank -1 pre-release, +1 post.

    Deliberately not PEP 440 -- importing ``packaging`` would make the launcher
    depend on the very venv it exists to provision. But the rank is not
    optional: a plain leading-digit compare made ``1.2.0rc1`` EQUAL to
    ``1.2.0``, so a release candidate satisfied a floor that wanted the release.
    """
    key = []
    for chunk in str(value).split("."):
        cut = 0
        while cut < len(chunk) and chunk[cut].isdigit():
            cut += 1
        digits, suffix = chunk[:cut], chunk[cut:].lstrip("-_").lower()
        rank = _suffix_rank(suffix) if suffix else 0
        key.append((int(digits) if digits else 0, rank))
    return key


def _below_floor(have: str, floor: str) -> bool:
    """Is ``have`` strictly below ``floor``? Padded so length never decides.

    Without padding ``1.2.0.dev1`` compares ABOVE ``1.2.0`` purely by being
    longer -- the exact inversion the rank exists to prevent.
    """
    a, b = _version_key(have), _version_key(floor)
    pad = (0, 0)
    width = max(len(a), len(b))
    a += [pad] * (width - len(a))
    b += [pad] * (width - len(b))
    return a < b


#: A PEP 508 distribution name, then optional extras, then whatever follows.
#: The name shape is strict on purpose: the old ``[A-Za-z0-9._-]+`` happily
#: matched ``--pre`` and ``./wheels/x.whl``, turning pip flags and paths into
#: "distributions" that are never installed -- so every launch reinstalled.
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*(?P<rest>.*)$"
)

#: Operators that put a LOWER bound on the installed version. ``~=`` and ``==``
#: do too (a prefix match still cannot go below its own floor). Order matters:
#: the two-character forms must be tried before ``>``.
_FLOOR_RE = re.compile(r"^(?:===|==|~=|>=|>)\s*([^,;\s]+)")

#: And the UPPER bound. Needed because a real pin arrived: ``mcp`` 2.0.0 removed
#: ``Server.list_tools()``, which kumiho's MCP server calls at construction, so
#: a fresh install silently produced a server that could not start. Without
#: understanding ``<`` the spec would be "unevaluable" and every single launch
#: would reinstall.
_CEILING_RE = re.compile(r"^(?:<=|<)\s*([^,;\s]+)")

#: Anything that is not a plain requirement: pip flags, URLs, local paths.
_NOT_A_REQUIREMENT = ("-", ".", "/", "\\")


def _spec_floors(package_spec: str):
    """``([(name, extras, floor, ceiling)], understood)`` for the spec's tokens.

    ``kumiho[mcp]>=0.10.8`` -> ``("kumiho", frozenset({"mcp"}), "0.10.8", "")``
    ``mcp<2``               -> ``("mcp", frozenset(), "", "2")``

    ``understood`` is False when a token carries a constraint this parser cannot
    evaluate as a floor (``<``, ``!=``, a VCS URL, a bare wheel path). The
    caller must then reinstall rather than assume satisfaction: silently
    dropping an operator is how ``==0.9`` came back as "no floor" and reported
    a venv at 2.0 as satisfying it.
    """
    reqs, understood = [], True
    for token in shlex.split(package_spec):
        if token.startswith(_NOT_A_REQUIREMENT) or "://" in token:
            understood = False
            continue
        m = _REQUIREMENT_RE.match(token)
        if not m:
            understood = False
            continue
        extras = frozenset(
            e.strip() for e in (m.group("extras") or "").split(",") if e.strip()
        )
        rest = (m.group("rest") or "").strip()
        floor_match, ceiling_match = _FLOOR_RE.match(rest), _CEILING_RE.match(rest)
        if floor_match:
            reqs.append((m.group("name"), extras, floor_match.group(1), ""))
        elif ceiling_match:
            reqs.append((m.group("name"), extras, "", ceiling_match.group(1)))
        elif rest:
            understood = False  # a constraint, but not one we can evaluate
        else:
            reqs.append((m.group("name"), extras, "", ""))
    return reqs, understood


def _installed_versions(
    python_path: Path, requirements: list, timeout_s: float = PROBE_TIMEOUT_S
) -> dict:
    """Installed versions/modules/extras, or ``{}`` when unknowable."""
    # find_spec on a DOTTED name imports the parent package, so a plain
    # find_spec('kumiho.mcp_server') raises ModuleNotFoundError on exactly the
    # empty venv this is meant to report on -- which killed the whole probe
    # rather than answering "not installed".
    if not _windows_pe_executable(python_path):
        return {}
    probe = (
        "import json,os,sys,importlib.util\n"
        "from importlib.metadata import distribution,version,PackageNotFoundError\n"
        "def have(m):\n"
        "    try: return importlib.util.find_spec(m) is not None\n"
        "    except (ImportError, ValueError): return False\n"
        "reqs=json.loads(sys.argv[1])\n"
        "expected=os.path.normcase(os.path.realpath(sys.argv[2]))\n"
        "prefix=os.path.normcase(os.path.realpath(sys.prefix))\n"
        "base=os.path.normcase(os.path.realpath(sys.base_prefix))\n"
        "out={'__python_ok__':sys.version_info >= (3,10) and prefix == expected and prefix != base}\n"
        "for n,_extras in reqs:\n"
        "    try: out[n]=version(n)\n"
        "    except PackageNotFoundError: out[n]=None\n"
        "out['__modules__']=all(have(m)\n"
        "                       for m in ('kumiho.mcp_server','kumiho_memory'))\n"
        "try:\n"
        "    try:\n"
        "        from packaging.markers import default_environment\n"
        "        from packaging.requirements import Requirement\n"
        "    except ImportError:\n"
        "        from pip._vendor.packaging.markers import default_environment\n"
        "        from pip._vendor.packaging.requirements import Requirement\n"
        "    extras_ok=True\n"
        "    for n,extras in reqs:\n"
        "        dist=distribution(n)\n"
        "        for extra in extras:\n"
        "            env=default_environment(); env['extra']=extra\n"
        "            for raw in (dist.requires or []):\n"
        "                dep=Requirement(raw)\n"
        "                if dep.marker is not None and dep.marker.evaluate(env):\n"
        "                    try: dep_version=version(dep.name)\n"
        "                    except PackageNotFoundError: extras_ok=False; continue\n"
        "                    if dep.specifier and not dep.specifier.contains(dep_version, prereleases=True):\n"
        "                        extras_ok=False\n"
        "    out['__extras__']=extras_ok\n"
        "except Exception:\n"
        "    out['__extras__']=not any(extras for _n,extras in reqs)\n"
        "print(json.dumps(out))\n"
    )
    normalized = [
        [entry[0], sorted(entry[1])]
        if isinstance(entry, (tuple, list)) and len(entry) >= 2
        else [entry, []]
        for entry in requirements
    ]
    try:
        r = bounded_proc.run(
            [
                str(python_path),
                "-I",
                "-c",
                probe,
                json.dumps(normalized),
                str(python_path.parent.parent.resolve()),
            ],
            timeout=timeout_s,
            env=_provision_subprocess_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _needs_install(
    python_path: Path,
    marker_path: Path,
    package_spec: str,
    probe_timeout_s: float = PROBE_TIMEOUT_S,
) -> bool:
    """Is the venv below what ``package_spec`` requires?

    Compares INSTALLED VERSIONS, not marker text. The old ``marker !=
    package_spec`` string equality had two failure modes, both measured on
    2026-07-31 (kumiho-plugins#45 items 2 and 6):

    * A newer floor never reached an install whose marker text already matched
      -- releases could ship forever without arriving.
    * Two plugin copies with different floors (a cached 0.18.0 declaring
      ``>=1.2.0`` and a stale Claude Desktop rpm snapshot still declaring
      ``>=0.17.1``) share ONE state dir, so each launch rewrote the other's
      marker and triggered a full reinstall -- on a venv that already satisfied
      both.

    ``marker_path`` is consulted for ONE thing the installed versions cannot
    answer: which EXTRAS were installed. ``importlib.metadata`` does not record
    them, so ``kumiho[mcp]`` -> ``kumiho[mcp,cli]`` is invisible to a version
    compare. Comparing only the name+extras identity -- never the versions --
    detects that without bringing the text-equality thrash back, because the two
    plugin copies that thrashed declared identical extras and differed only in
    their floors.
    """
    if not python_path.exists():
        return True

    reqs, understood = _spec_floors(package_spec)
    if not understood or not reqs:
        return True  # a constraint we cannot evaluate -> reinstall, never assume

    marker_matches = False
    if marker_path.exists():
        try:
            previous, prev_ok = _spec_floors(
                marker_path.read_text(encoding="utf-8").strip())
        except OSError:
            previous, prev_ok = [], False
        identity = {(n, e) for n, e, _f, _c in reqs}
        if not prev_ok or {(n, e) for n, e, _f, _c in previous} != identity:
            return True
        marker_matches = True

    installed = _installed_versions(
        python_path,
        [(name, extras) for name, extras, _floor, _ceiling in reqs],
        timeout_s=probe_timeout_s,
    )
    if not installed:
        return True  # cannot establish satisfaction -> reinstall, as before
    if not installed.get("__python_ok__"):
        return True
    if not installed.get("__modules__"):
        return True
    # Verify the dependencies selected by requested extras on every probe.
    # The marker records WHICH extras were requested; it cannot prove their
    # dependencies still exist after an interrupted Desktop/pip update.
    if any(extras for _name, extras, _f, _c in reqs):
        if not installed.get("__extras__"):
            return True

    for name, _extras, floor, ceiling in reqs:
        have = installed.get(name)
        if not have:
            return True
        if floor and _below_floor(have, floor):
            return True
        # A ceiling is not cosmetic here: mcp 2.0.0 removed an API kumiho calls
        # at server construction, so an install ABOVE the ceiling starts and
        # then fails. Reinstall pins it back down.
        if ceiling and not _below_floor(have, ceiling):
            return True
    return False


def _install_dependencies(python_path: Path, package_spec: str) -> None:
    provision_env = _provision_subprocess_env()
    if _run(
        [str(python_path), "-I", "-m", "pip", "--version"],
        check=False,
        timeout=PROBE_TIMEOUT_S,
        env=provision_env,
    ):
        _run(
            [str(python_path), "-I", "-m", "ensurepip", "--upgrade"],
            timeout=PIP_TIMEOUT_S,
            env=provision_env,
        )
    _run(
        [str(python_path), "-I", "-m", "pip", "install", "--upgrade", "pip"],
        timeout=PIP_TIMEOUT_S,
        env=provision_env,
    )
    packages = shlex.split(package_spec) if package_spec else shlex.split(DEFAULT_PACKAGE_SPEC)
    _run(
        [str(python_path), "-I", "-m", "pip", "install", "--upgrade", *packages],
        timeout=PIP_TIMEOUT_S,
        env=provision_env,
    )


#: Set by the detached provisioner and by the wizard/self-test, which are not on
#: a startup clock and must actually do the work rather than delegate it again.
_SYNC_PROVISION_ENV = "KUMIHO_CLAUDE_PROVISION_SYNC"
_PROVISION_LOCK_TOKEN_ENV = "KUMIHO_CLAUDE_PROVISION_LOCK_TOKEN"

# Bound to the lock-owning thread for the duration of provisioning. Long
# subprocesses observe the same loss through bounded_proc.abort_scope; pure
# filesystem steps call the guard directly so they cannot keep mutating the
# shared runtime after another owner has replaced any member of the lock set.
_ACTIVE_PROVISION_LOCK_GUARD = contextvars.ContextVar(
    "kumiho_active_provision_lock_guard", default=None
)

#: A cold provision was measured at 205-320 s and is slower on a poor link, so
#: the staleness window is well above that. Past it we assume the holder died
#: and break the lock rather than wedging the install forever.
PROVISION_LOCK_STALE_S = 1800


def _provision_log_path() -> Path:
    return _state_dir() / "provision.log"


def _marker_path() -> Path:
    """Extras/install contract beside the shared venv it describes."""
    return _venv_dir().parent / MARKER_FILE


def _runtime_attestation_path() -> Path:
    return _venv_dir().parent / RUNTIME_ATTESTATION_FILE


def _site_packages_dirs(venv_dir: Path) -> list[Path]:
    if os.name == "nt":
        return [venv_dir / "Lib" / "site-packages"]
    return sorted((venv_dir / "lib").glob("python*/site-packages"))


def _venv_layout_version(venv_dir: Path, python_path: Path) -> str | None:
    """Prove the attested executable still belongs to a Python 3.10+ venv."""
    expected = _venv_python(venv_dir)
    if os.path.normcase(str(expected.absolute())) != os.path.normcase(
        str(python_path.absolute())
    ):
        return None
    config = venv_dir / "pyvenv.cfg"
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"(?mi)^\s*version\s*=\s*(\d+)\.(\d+)(?:\.\d+)?\s*$", text)
    if not match or (int(match.group(1)), int(match.group(2))) < (3, 10):
        return None
    return match.group(0).partition("=")[2].strip()


def _runtime_fingerprint(
    venv_dir: Path, python_path: Path, marker_path: Path, package_spec: str
) -> dict | None:
    """Cheap mutation fingerprint for a runtime that passed a full probe."""
    if not _windows_pe_executable(python_path):
        return None
    python_version = _venv_layout_version(venv_dir, python_path)
    if python_version is None:
        return None
    requirements, understood = _spec_floors(package_spec)
    if not understood or not requirements:
        return None
    try:
        marker_text = marker_path.read_text(encoding="utf-8").strip()
        python_stat = python_path.stat()
        config_stat = (venv_dir / "pyvenv.cfg").stat()
        sites = []
        for site in _site_packages_dirs(venv_dir):
            stat = site.stat()
            sites.append([str(site.resolve()), stat.st_mtime_ns])
        if marker_text != package_spec or not sites:
            return None
        return {
            "schema": 2,
            "package_spec": package_spec,
            "python": str(python_path.resolve()),
            "python_size": python_stat.st_size,
            "python_mtime_ns": python_stat.st_mtime_ns,
            "python_version": python_version,
            "pyvenv_size": config_stat.st_size,
            "pyvenv_mtime_ns": config_stat.st_mtime_ns,
            "marker_mtime_ns": marker_path.stat().st_mtime_ns,
            "site_packages": sites,
        }
    except OSError:
        return None


def _write_runtime_attestation(
    venv_dir: Path, python_path: Path, marker_path: Path, package_spec: str
) -> None:
    fingerprint = _runtime_fingerprint(
        venv_dir, python_path, marker_path, package_spec
    )
    if fingerprint is None:
        return
    target = _runtime_attestation_path()
    _assert_provision_lock_owned()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        _assert_provision_lock_owned()
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            payload = (json.dumps(fingerprint, sort_keys=True) + "\n").encode("utf-8")
            if os.write(fd, payload) != len(payload):
                raise OSError("short runtime-attestation write")
            os.fsync(fd)
        finally:
            os.close(fd)
        _assert_provision_lock_owned()
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _runtime_attestation_matches(
    venv_dir: Path, python_path: Path, marker_path: Path, package_spec: str
) -> bool:
    try:
        recorded = json.loads(
            _runtime_attestation_path().read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    current = _runtime_fingerprint(
        venv_dir, python_path, marker_path, package_spec
    )
    return current is not None and recorded == current


def _write_install_marker(marker_path: Path, package_spec: str) -> None:
    """Atomically record the package/extras contract for the shared venv."""
    _assert_provision_lock_owned()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temp = marker_path.with_name(
        f".{marker_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        _assert_provision_lock_owned()
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            payload = package_spec.encode("utf-8")
            if os.write(fd, payload) != len(payload):
                raise OSError("short install-marker write")
            os.fsync(fd)
        finally:
            os.close(fd)
        _assert_provision_lock_owned()
        os.replace(temp, marker_path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_json_atomic(target: Path, body: dict) -> None:
    """Replace a user-owned JSON config without exposing a torn partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        payload = (json.dumps(body, indent=2) + "\n").encode("utf-8")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise OSError("short JSON config write")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _provision_lock_path() -> Path:
    # Beside the shared ~/.kumiho/venv it guards, not beside a host's state dir.
    # Claude, Codex, and Desktop must all observe the same reservation.
    return _venv_dir().parent / "provision.lock"


def _desktop_compat_lock_candidates() -> list[Path]:
    """All host-owned legacy lock names this plugin is allowed to touch."""
    data_dirs: list[Path] = []
    active = _plugin_data_dir()
    if active is not None:
        data_dirs.append(active)
    home = _account_home()
    data_dirs.extend(
        [
            home / ".claude" / "plugins" / "data"
            / "kumiho-memory-kumiho-plugins",
            home / ".codex" / "plugins" / "data"
            / "kumiho-memory-kumiho-plugins",
            _state_dir(),
        ]
    )
    canonical = os.path.normcase(str(_provision_lock_path().absolute()))
    locks: list[Path] = []
    seen: set[str] = set()
    for data in data_dirs:
        lock = (data / "provision.lock").absolute()
        key = os.path.normcase(str(lock))
        if key != canonical and key not in seen:
            seen.add(key)
            locks.append(lock)
    return locks


def _desktop_compat_lock_paths() -> list[Path]:
    """Legacy locks currently guarding the shared venv through an alias.

    Kumiho Desktop versions that predate the shared-runtime layout preserve
    the lexical Claude/Codex alias path when choosing where to place their
    lock. Hold those locks alongside the canonical one whenever the alias
    resolves to ``~/.kumiho/venv`` so Desktop and both plugins remain mutually
    exclusive even during a rolling upgrade.
    """
    shared = _venv_dir()
    try:
        shared_resolved = shared.resolve()
    except OSError:
        return []
    locks: list[Path] = []
    for lock in _desktop_compat_lock_candidates():
        alias = lock.parent / "venv"
        try:
            if not os.path.lexists(alias) or alias.resolve() != shared_resolved:
                continue
        except OSError:
            continue
        locks.append(lock)
    return locks


def _compat_locks_from_record(record: dict) -> list[Path] | None:
    """Validate and restore the frozen lock bundle from a canonical record."""
    raw = record.get("compat_locks")
    if raw is None:
        # Rolling-upgrade compatibility for a lock written by an older plugin.
        return _desktop_compat_lock_paths()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return None
    allowed = {
        os.path.normcase(str(path.absolute())): path
        for path in _desktop_compat_lock_candidates()
    }
    result: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        key = os.path.normcase(str(Path(item).expanduser().absolute()))
        if key not in allowed or key in seen:
            return None
        seen.add(key)
        result.append(allowed[key])
    return result


def _read_lock_at(lock: Path) -> dict:
    try:
        raw = lock.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            return {"pid": int(raw), "desktop_legacy": True}
        body = json.loads(raw)
        return body if isinstance(body, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _read_provision_lock() -> dict:
    return _read_lock_at(_provision_lock_path())


def _windows_process_api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    )
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    )
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _process_start_marker(pid: int) -> str | None:
    """Stable birth marker used to distinguish a live owner from PID reuse."""
    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = (("low", ctypes.c_ulong), ("high", ctypes.c_ulong))

        try:
            kernel32 = _windows_process_api()
            kernel32.GetProcessTimes.argtypes = (
                ctypes.c_void_p,
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
            )
            kernel32.GetProcessTimes.restype = ctypes.c_int
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                created, exited, kernel, user = (
                    FileTime(), FileTime(), FileTime(), FileTime()
                )
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                value = (created.high << 32) | created.low
                return f"win:{value}"
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8")
        # The command name is parenthesized and may contain spaces. Fields
        # after the final ')' start at stat field 3; starttime is field 22.
        fields = raw[raw.rfind(")") + 2 :].split()
        if len(fields) > 19:
            return f"proc:{fields[19]}"
    except (OSError, ValueError):
        pass
    ps_path = next(
        (candidate for candidate in ("/bin/ps", "/usr/bin/ps")
         if Path(candidate).is_file()),
        None,
    )
    if ps_path is None:
        return None
    try:
        result = subprocess.run(
            [ps_path, "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
            env=_provision_subprocess_env(),
            **_hidden_console_kwargs(),
        )
        marker = result.stdout.strip() if result.returncode == 0 else ""
        return f"ps:{marker}" if marker else None
    except (OSError, subprocess.SubprocessError):
        return None


def _process_is_alive(pid: object, expected_start: object = None) -> bool:
    """Conservatively determine whether the exact recorded owner still exists."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    alive = pid == os.getpid()
    if not alive and os.name == "nt":
        # os.kill(pid, 0) is not a portable liveness probe on Windows. Query
        # the process handle without requesting termination rights instead.
        try:
            kernel32 = _windows_process_api()
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5  # access denied => alive
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True  # uncertainty must not permit concurrent pip
                alive = exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True  # fail closed: preserve the lock
    elif not alive:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            return False
        except PermissionError:
            alive = True
        except OSError:
            return True
    if alive and isinstance(expected_start, str) and expected_start:
        actual_start = _process_start_marker(pid)
        if actual_start is not None and actual_start != expected_start:
            return False
    return alive


def _lock_record_bytes(
    token: str, *, adopted: bool, compat_locks: list[Path] | None = None
) -> bytes:
    record = {
        "pid": os.getpid(),
        "process_start": _process_start_marker(os.getpid()),
        "token": token,
        "created_at": time.time(),
        "adopted": adopted,
    }
    if compat_locks is not None:
        record["compat_locks"] = [str(path.absolute()) for path in compat_locks]
    return json.dumps(record).encode("utf-8")


def _remove_abandoned_lock(lock: Path) -> bool:
    """Remove a dead-owner lock, or an unparseable lock after the stale bound."""
    try:
        observed = lock.stat()
        record = _read_lock_at(lock)
        pid = record.get("pid")
        known_owner = isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        age = time.time() - observed.st_mtime
        expected_start = record.get("process_start")
        # Legacy Desktop/OpenClaw locks may contain only a numeric PID. Age is
        # never authority to steal a lock from a live process: a slow pip can
        # legitimately exceed the stale window. Structured records additionally
        # use process_start to reject PID reuse.
        if known_owner and _process_is_alive(pid, expected_start):
            return False
        if not known_owner and age <= PROVISION_LOCK_STALE_S:
            return False
        current = lock.stat()
        if (
            not os.path.samestat(observed, current)
            or current.st_mtime_ns != observed.st_mtime_ns
            or current.st_size != observed.st_size
            or _read_lock_at(lock) != record
        ):
            return False
        lock.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _acquire_alias_migration_lock(data: Path) -> str | None:
    """Reserve a legacy per-plugin venv before renaming it into a backup."""
    lock = (data / "provision.lock").absolute()
    token = secrets.token_hex(16)
    for _attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _remove_abandoned_lock(lock):
                continue
            return None
        except OSError:
            return None
        write_failed = False
        try:
            record = _lock_record_bytes(
                token, adopted=True, compat_locks=[]
            )
            if os.write(fd, record) != len(record):
                raise OSError("short alias-migration-lock write")
            os.fsync(fd)
        except OSError:
            write_failed = True
        finally:
            try:
                os.close(fd)
            except OSError:
                write_failed = True
        if write_failed:
            try:
                if _read_lock_at(lock).get("token") == token:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        else:
            return token
    return None


def _release_alias_migration_lock(data: Path, token: str) -> None:
    lock = (data / "provision.lock").absolute()
    try:
        observed = lock.stat()
        record = _read_lock_at(lock)
        current = lock.stat()
        if (
            record.get("token") == token
            and record.get("pid") == os.getpid()
            and current.st_mtime_ns == observed.st_mtime_ns
            and current.st_size == observed.st_size
        ):
            lock.unlink(missing_ok=True)
    except (FileNotFoundError, OSError):
        pass


def _compat_lock_in_progress() -> bool:
    for lock in _desktop_compat_lock_paths():
        try:
            if lock.exists() and not _remove_abandoned_lock(lock):
                return True
        except OSError:
            return True
    return False


def _acquire_desktop_compat_locks(token: str, targets: list[Path]) -> bool:
    acquired: list[Path] = []
    record = _lock_record_bytes(
        token, adopted=False, compat_locks=targets
    )
    success = False
    try:
        for lock in targets:
            lock.parent.mkdir(parents=True, exist_ok=True)
            for _attempt in range(2):
                try:
                    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    if _remove_abandoned_lock(lock):
                        continue
                    return False
                try:
                    if os.write(fd, record) != len(record):
                        raise OSError("short compatibility-lock write")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                acquired.append(lock)
                break
            else:
                return False
        success = True
        return True
    except OSError:
        return False
    finally:
        if not success:
            for lock in acquired:
                try:
                    if _read_lock_at(lock).get("token") == token:
                        lock.unlink(missing_ok=True)
                except OSError:
                    pass


def _adopt_desktop_compat_locks(token: str, targets: list[Path]) -> bool:
    record = _lock_record_bytes(
        token, adopted=True, compat_locks=targets
    )
    for lock in targets:
        temp: Path | None = None
        try:
            observed = lock.stat()
            if _read_lock_at(lock).get("token") != token:
                return False
            temp = lock.with_name(f".{lock.name}.{token}.tmp")
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                if os.write(fd, record) != len(record):
                    raise OSError("short compatibility-lock adoption write")
                os.fsync(fd)
            finally:
                os.close(fd)
            current = lock.stat()
            if (
                current.st_mtime_ns != observed.st_mtime_ns
                or current.st_size != observed.st_size
                or _read_lock_at(lock).get("token") != token
            ):
                return False
            os.replace(temp, lock)
        except (FileExistsError, FileNotFoundError, OSError):
            return False
        finally:
            try:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            except OSError:
                pass
    return True


def _refresh_provision_lock(token: str) -> bool:
    """Heartbeat only the complete lock bundle owned by this process/token.

    Compatibility locks are the only mutex older Desktop builds observe.  Do
    not make the canonical lock look fresh until every compatibility lock has
    been ownership-checked and refreshed; otherwise losing one alias could let
    an old writer enter the shared venv while this provisioner still runs pip.
    """
    lock = _provision_lock_path()

    def stable_owned(path: Path):
        observed = path.stat()
        record = _read_lock_at(path)
        current = path.stat()
        if (
            record.get("token") != token
            or record.get("pid") != os.getpid()
            or not os.path.samestat(observed, current)
            or current.st_mtime_ns != observed.st_mtime_ns
            or current.st_size != observed.st_size
            or _read_lock_at(path) != record
        ):
            return None
        return observed, record

    def refresh_owned(path: Path, observed, record: dict) -> bool:
        current = path.stat()
        if (
            not os.path.samestat(observed, current)
            or current.st_mtime_ns != observed.st_mtime_ns
            or current.st_size != observed.st_size
            or _read_lock_at(path) != record
        ):
            return False
        os.utime(path, None)
        refreshed = path.stat()
        after = _read_lock_at(path)
        return (
            os.path.samestat(observed, refreshed)
            and after.get("token") == token
            and after.get("pid") == os.getpid()
        )

    try:
        canonical = stable_owned(lock)
        if canonical is None:
            return False
        _canonical_observed, record = canonical
        compat_locks = _compat_locks_from_record(record)
        if compat_locks is None:
            return False

        compat_snapshots = []
        for compat in compat_locks:
            snapshot = stable_owned(compat)
            if snapshot is None:
                return False
            compat_snapshots.append((compat, *snapshot))

        # Refresh aliases first. The canonical mtime is the public "bundle is
        # live" signal and therefore commits only after all legacy mutexes do.
        for compat, observed, compat_record in compat_snapshots:
            if not refresh_owned(compat, observed, compat_record):
                return False

        # Re-check the complete alias set immediately before the canonical
        # commit. A replacement after an earlier touch must fail closed.
        if any(stable_owned(compat) is None for compat in compat_locks):
            return False
        canonical = stable_owned(lock)
        if canonical is None:
            return False
        return refresh_owned(lock, *canonical)
    except (FileNotFoundError, OSError):
        return False


def _assert_provision_lock_owned() -> None:
    """Synchronously fail before a shared-runtime mutation after lock loss.

    Helpers are also exercised independently by tests and compatibility callers;
    outside a heartbeat context there is no active provisioner to guard.
    """
    guard = _ACTIVE_PROVISION_LOCK_GUARD.get()
    if guard is not None:
        guard()


@contextlib.contextmanager
def _provision_lock_heartbeat(token: str):
    """Keep a live long-running installer from ever looking stale."""
    stop = threading.Event()
    lost = threading.Event()
    refresh_mutex = threading.Lock()

    def refresh() -> bool:
        # The heartbeat and the owner thread can both reach this code. Without
        # serialization their own utime calls invalidate each other's stable
        # snapshots and manufacture a false lock-loss event.
        with refresh_mutex:
            return _refresh_provision_lock(token)

    def assert_owned() -> None:
        if lost.is_set() or not refresh():
            lost.set()
            raise bounded_proc.ProcessAborted(["shared-runtime-provisioning"])

    def heartbeat() -> None:
        while not stop.wait(15):
            if not refresh():
                lost.set()
                return

    if not refresh():
        raise RuntimeError("lost the shared-runtime provisioning lock")
    worker = threading.Thread(
        target=heartbeat,
        name="kumiho-provision-lock-heartbeat",
        daemon=True,
    )
    worker.start()
    failed = False
    guard_token = _ACTIVE_PROVISION_LOCK_GUARD.set(assert_owned)
    try:
        with bounded_proc.abort_scope(lost):
            yield
    except bounded_proc.ProcessAborted as exc:
        failed = True
        raise RuntimeError("lost the shared-runtime provisioning lock") from exc
    except BaseException:
        failed = True
        raise
    finally:
        _ACTIVE_PROVISION_LOCK_GUARD.reset(guard_token)
        stop.set()
        worker.join(timeout=2)
        if lost.is_set() and not failed:
            raise RuntimeError("lost the shared-runtime provisioning lock")


def _remove_stale_provision_lock() -> bool:
    """Remove one dead-owner lock without deleting a newer replacement."""
    return _remove_abandoned_lock(_provision_lock_path())


def _provision_in_progress() -> bool:
    """Is another process already building this venv?

    Provisioning is the one operation here that MUST NOT run twice at once:
    two ``pip install`` runs against a single venv interleave their writes.
    Observed while testing the detached first-run provisioner -- a concurrent
    ``--self-test`` started a second pip against the same tree. Same lock idiom
    as ``code_ingest_worker``, staleness included, because a provisioner that is
    killed mid-install must not lock the venv out permanently.
    """
    lock = _provision_lock_path()
    try:
        if lock.exists() and not _remove_stale_provision_lock():
            return True
        return _compat_lock_in_progress()
    except OSError:
        return True  # uncertainty must never allow two writers into one venv


def _acquire_provision_lock(reservation_token: str | None = None) -> str | None:
    """Atomically acquire, or adopt, the single provisioning reservation."""
    lock = _provision_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    token = reservation_token or secrets.token_hex(16)
    compat_locks = _desktop_compat_lock_paths()
    for _attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            current = _read_provision_lock()
            if reservation_token and current.get("token") == reservation_token:
                frozen = _compat_locks_from_record(current)
                if frozen is None:
                    return None
                # Adopt every compatibility lock first. The canonical record
                # is the handoff commit/ACK point observed by the parent, so
                # adopted=True there guarantees the whole frozen bundle was
                # already transferred to this child.
                return (
                    reservation_token
                    if (
                        _adopt_desktop_compat_locks(reservation_token, frozen)
                        and _adopt_provision_lock(reservation_token, frozen)
                    )
                    else None
                )
            if _remove_stale_provision_lock():
                continue
            return None
        except OSError:
            return None
        owned = None
        write_failed = False
        try:
            try:
                owned = os.fstat(fd)
            except OSError:
                # The O_EXCL create just succeeded. A path stat gives cleanup
                # an identity guard while the still-open handle prevents a
                # Windows leak if fstat itself is the failing operation.
                try:
                    owned = lock.stat()
                except OSError:
                    pass
                raise
            else:
                record = _lock_record_bytes(
                    token, adopted=False, compat_locks=compat_locks
                )
                if os.write(fd, record) != len(record):
                    raise OSError("short provisioning-lock write")
                os.fsync(fd)
        except OSError:
            write_failed = True
        finally:
            os.close(fd)
        if write_failed:
            try:
                if owned is None or os.path.samestat(owned, lock.stat()):
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        if not _acquire_desktop_compat_locks(token, compat_locks):
            try:
                if _read_provision_lock().get("token") == token:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return token
    return None


def _acquire_provision_lock_with_wait(
    *, timeout_s: float = _READY_LOCK_WAIT_S,
) -> str | None:
    """Acquire the maintenance lock with a short bounded backoff.

    A ready runtime needs only alias/attestation maintenance. It should not
    fail an otherwise healthy MCP launch merely because a SessionEnd worker is
    finishing that maintenance for a few moments.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        token = _acquire_provision_lock()
        if token is not None:
            return token
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(_READY_LOCK_BACKOFF_S, remaining))


def _adopt_provision_lock(token: str, compat_locks: list[Path]) -> bool:
    """Transfer a parent's reservation to the detached child atomically."""
    lock = _provision_lock_path()
    temp: Path | None = None
    try:
        observed = lock.stat()
        if _read_provision_lock().get("token") != token:
            return False
        temp = lock.with_name(f".{lock.name}.{token}.tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            record = _lock_record_bytes(
                token, adopted=True, compat_locks=compat_locks
            )
            if os.write(fd, record) != len(record):
                raise OSError("short provisioning-lock adoption write")
            os.fsync(fd)
        finally:
            os.close(fd)
        current = lock.stat()
        if (
            current.st_mtime_ns != observed.st_mtime_ns
            or current.st_size != observed.st_size
            or _read_provision_lock().get("token") != token
        ):
            return False
        os.replace(temp, lock)
        return True
    except (FileExistsError, FileNotFoundError, OSError):
        return False
    finally:
        try:
            if temp is not None:
                temp.unlink(missing_ok=True)
        except OSError:
            pass


def _release_provision_lock(token: str) -> None:
    lock = _provision_lock_path()
    compat_locks: list[Path] = []
    try:
        observed = lock.stat()
        record = _read_provision_lock()
        frozen = _compat_locks_from_record(record)
        if frozen is not None:
            compat_locks = frozen
        current = lock.stat()
        if (
            record.get("token") == token
            and record.get("pid") == os.getpid()
            and current.st_mtime_ns == observed.st_mtime_ns
            and current.st_size == observed.st_size
        ):
            lock.unlink(missing_ok=True)
    except (FileNotFoundError, OSError):
        pass
    for compat in compat_locks:
        try:
            observed = compat.stat()
            record = _read_lock_at(compat)
            current = compat.stat()
            if (
                record.get("token") == token
                and record.get("pid") == os.getpid()
                and current.st_mtime_ns == observed.st_mtime_ns
                and current.st_size == observed.st_size
            ):
                compat.unlink(missing_ok=True)
        except (FileNotFoundError, OSError):
            pass


def _provisioning_is_synchronous() -> bool:
    return bool((os.getenv(_SYNC_PROVISION_ENV, "") or "").strip())


def _spawn_detached_provisioning(lock_token: str) -> bool:
    """Build the venv in a process that outlives this one.

    A cold provision was measured at 205-320 s; the host's MCP startup budget is
    30 s (``MCP_TIMEOUT``, default 30000 in the shipped binary). Doing it inline
    is not slow-but-working, it is guaranteed failure: the host gives up, closes
    the transport and kills this process, taking pip with it mid-install, and the
    next session starts over. Detaching is what lets the work finish at all.

    Best-effort by construction -- if the spawn fails the caller still exits with
    a message, which is strictly better than blocking until killed.
    """
    # Not DEVNULL: this child is the only thing standing between a new user and
    # a working install, and if it dies there is otherwise NOTHING to look at --
    # the parent has already exited and the host reports only that the server
    # went away. The log path is named in the message the caller prints.
    try:
        log_path = _provision_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        sink = subprocess.DEVNULL
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": sink,
        "stderr": subprocess.STDOUT if sink is not subprocess.DEVNULL else subprocess.DEVNULL,
        "env": _provision_subprocess_env(
            {
                _SYNC_PROVISION_ENV: "1",
                _PROVISION_LOCK_TOKEN_ENV: lock_token,
            }
        ),
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP -- survives our exit.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        child = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--provision"],
            **kwargs
        )
        # Do not leave the parent's reservation behind if Python dies before
        # importing this module and adopting it. The child rewrites the lock
        # with its own pid before any venv or pip mutation begins.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            record = _read_provision_lock()
            if (
                record.get("token") == lock_token
                and record.get("pid") == child.pid
                and record.get("adopted") is True
            ):
                return True
            if child.poll() is not None:
                print(
                    "[kumiho-claude] Background provisioning exited before "
                    "adopting its lock.",
                    file=sys.stderr,
                )
                return False
            time.sleep(0.05)
        try:
            child.terminate()
            child.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                child.kill()
            except OSError:
                pass
        print(
            "[kumiho-claude] Background provisioning did not acknowledge "
            "its lock in time.",
            file=sys.stderr,
        )
        return False
    except OSError as exc:
        print("[kumiho-claude] Could not start background provisioning: %s" % exc,
              file=sys.stderr)
        return False
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()


def _ensure_runtime() -> Path:
    raw_spec = os.getenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "").strip()
    package_spec = DEFAULT_PACKAGE_SPEC if (not raw_spec or _looks_like_placeholder(raw_spec)) else raw_spec
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    venv_dir = _venv_dir()
    marker_path = _marker_path()
    python_path = _venv_python(venv_dir)

    # A detached child has only five seconds to acknowledge its parent's
    # reservation. Adopt it and start the heartbeat before probing an existing
    # (possibly partial or outdated) Desktop venv: that probe may legitimately
    # take longer than the handshake window.
    inherited = (os.getenv(_PROVISION_LOCK_TOKEN_ENV, "") or "").strip() or None
    if inherited:
        lock_token = _acquire_provision_lock(inherited)
        if lock_token is None:
            raise SystemExit(
                "[kumiho-claude] Background provisioning lost its reservation. "
                "Retry from the host or run --provision."
            )
        try:
            with _provision_lock_heartbeat(lock_token):
                return _provision(
                    venv_dir, python_path, marker_path, package_spec
                )
        finally:
            _release_provision_lock(lock_token)

    synchronous = _provisioning_is_synchronous()
    provisioning_locked = _provision_in_progress()
    if provisioning_locked and not python_path.exists():
        raise SystemExit(
            "[kumiho-claude] Another process is provisioning this runtime. "
            "Reconnect the server, or retry once it finishes."
        )

    attested_ready = (
        not provisioning_locked
        and python_path.exists()
        and _runtime_attestation_matches(venv_dir, python_path, marker_path, package_spec)
    )
    # If a writer already owns the lock, do not inspect a mutable venv. The
    # ready-path lock acquisition below waits briefly, then performs the
    # definitive probe after ownership is transferred.
    runtime_ready = python_path.exists() if provisioning_locked else (
        python_path.exists() and (
            attested_ready
            or not _needs_install(
                python_path,
                marker_path,
                package_spec,
                probe_timeout_s=(
                    PROBE_TIMEOUT_S if synchronous else STARTUP_PROBE_TIMEOUT_S
                ),
            )
        )
    )
    if runtime_ready:
        # Alias migration changes which legacy Desktop lock guards this same
        # physical venv. Hold the canonical reservation across the final
        # readiness check and that migration so the lock-set transition cannot
        # race another host beginning pip against the shared runtime.
        maintenance_token = _acquire_provision_lock_with_wait()
        if maintenance_token is None:
            raise SystemExit(
                "[kumiho-claude] Another process began shared-runtime "
                "maintenance. Reconnect the server once it finishes."
            )
        try:
            with _provision_lock_heartbeat(maintenance_token):
                locked_attested = python_path.exists() and (
                    _runtime_attestation_matches(
                        venv_dir, python_path, marker_path, package_spec
                    )
                )
                locked_ready = python_path.exists() and (
                    locked_attested
                    or not _needs_install(
                        python_path,
                        marker_path,
                        package_spec,
                        probe_timeout_s=(
                            PROBE_TIMEOUT_S
                            if synchronous
                            else STARTUP_PROBE_TIMEOUT_S
                        ),
                    )
                )
                if not locked_ready:
                    raise SystemExit(
                        "[kumiho-claude] The shared runtime changed while it "
                        "was being checked. Reconnect to provision it safely."
                    )
                _ensure_hook_interpreter(venv_dir)
                # A successful locked probe safely adopts a compatible
                # Desktop-created venv without running pip.
                if not locked_attested:
                    _write_install_marker(marker_path, package_spec)
                    _write_runtime_attestation(
                        venv_dir, python_path, marker_path, package_spec
                    )
                # Alias migration creates the legacy mutex path that older
                # Desktop builds observe. Once its short migration lock is
                # released, such a build may enter without seeing the already-
                # frozen canonical bundle, so this must be the final mutation.
                _ensure_plugin_data_venv_alias(venv_dir)
        finally:
            _release_provision_lock(maintenance_token)
        return python_path

    if not synchronous:
        missing_python = not python_path.exists()
        reservation = _acquire_provision_lock()
        if reservation is None:
            raise SystemExit(
                "[kumiho-claude] Runtime provisioning is already running in "
                "another process. Reconnect the server, or start a new session, "
                "once it finishes."
            )
        if not _spawn_detached_provisioning(reservation):
            _release_provision_lock(reservation)
            raise SystemExit(
                "[kumiho-claude] Could not start background provisioning. "
                "Retry from a terminal with --provision."
            )
        raise SystemExit(
            "[kumiho-claude] Runtime install/update: the Python environment %s.\n"
            "[kumiho-claude] Provisioning started in the background (~150 MB at most, a few "
            "minutes). This server is exiting now ON PURPOSE -- building it here "
            "would take far longer than the host's MCP startup timeout, so the host "
            "would kill it half-installed and nothing would ever finish.\n"
            "[kumiho-claude] Reconnect the server, or start a new session, once it "
            "completes. Progress and any error are logged to %s.\n"
            "[kumiho-claude] Run %s AFTER it finishes to set "
            "credentials -- starting it now would run a second pip against the "
            "same environment."
            % (
                "is not built yet" if missing_python else "needs an install/update",
                _provision_log_path(),
                _onboard_command_label(),
            )
        )

    # From here on we WILL write to the venv, so atomically take the lock. A
    # synchronous wizard/self-test takes its own reservation.
    lock_token = _acquire_provision_lock()
    if lock_token is None:
        raise SystemExit(
            "[kumiho-claude] Another process is provisioning this environment. "
            "Retry once it finishes (or delete %s if it is stale)."
            % _provision_lock_path()
        )
    try:
        with _provision_lock_heartbeat(lock_token):
            return _provision(venv_dir, python_path, marker_path, package_spec)
    finally:
        _release_provision_lock(lock_token)


def _python_interpreter_works(python_path: Path) -> bool:
    if not python_path.is_file() or not _windows_pe_executable(python_path):
        return False
    try:
        # Keep isolation but let site.py process pyvenv.cfg. With ``-S``,
        # sys.prefix stays at base_prefix and every healthy venv looks broken.
        result = bounded_proc.run(
            [
                str(python_path),
                "-I",
                "-c",
                "import os,sys; "
                "expected=os.path.normcase(os.path.realpath(sys.argv[1])); "
                "prefix=os.path.normcase(os.path.realpath(sys.prefix)); "
                "valid=(sys.version_info >= (3,10) and prefix == expected "
                "and prefix != os.path.normcase(os.path.realpath(sys.base_prefix))); "
                "print('kumiho-venv-ok' if valid else 'kumiho-venv-invalid'); "
                "raise SystemExit(0 if valid else 3)",
                str(python_path.parent.parent.resolve()),
            ],
            timeout=10,
            env=_provision_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "kumiho-venv-ok"


def _base_interpreter_for_venv_creation(venv_dir: Path) -> Path | None:
    """Return the current runtime's external base before a bad venv is moved."""
    if sys.version_info < (3, 10):
        return None
    raw = getattr(sys, "_base_executable", None) or sys.executable
    try:
        candidate = Path(raw).resolve()
        root = venv_dir.resolve()
        candidate.relative_to(root)
        return None
    except ValueError:
        pass
    except OSError:
        return None
    return candidate if candidate.is_file() and _windows_pe_executable(candidate) else None


def _provision(venv_dir: Path, python_path: Path, marker_path: Path,
               package_spec: str) -> Path:
    """Build the venv and install the spec. Caller holds the provisioning lock."""
    needs_install = _needs_install(python_path, marker_path, package_spec)
    runtime_works = python_path.exists() and _python_interpreter_works(python_path)
    if needs_install and venv_dir.exists() and not runtime_works:
        creation_python = _base_interpreter_for_venv_creation(venv_dir)
        if creation_python is None:
            raise RuntimeError(
                "Python 3.10+ is required to rebuild the shared Kumiho runtime; "
                f"run {_onboard_command_label()} with a supported interpreter"
            )
        backup = venv_dir.with_name(
            f"{venv_dir.name}.broken-{os.getpid()}-{secrets.token_hex(4)}"
        )
        try:
            _assert_provision_lock_owned()
            venv_dir.rename(backup)
        except OSError as exc:
            raise RuntimeError(
                f"the existing shared runtime is not executable and could not "
                f"be preserved for repair: {type(exc).__name__}"
            ) from None
        print(
            f"[kumiho-claude] Preserved an unusable shared runtime at {backup}.",
            file=sys.stderr,
        )

    if not python_path.exists():
        creation_python = _base_interpreter_for_venv_creation(venv_dir)
        if creation_python is None:
            raise RuntimeError(
                "Python 3.10+ is required to create the shared Kumiho runtime; "
                f"run {_onboard_command_label()} with a supported interpreter"
            )
        print(f"[kumiho-claude] Creating virtualenv: {venv_dir}", file=sys.stderr)
        try:
            _assert_provision_lock_owned()
            _run(
                [str(creation_python), "-I", "-m", "venv", str(venv_dir)],
                timeout=PIP_TIMEOUT_S,
                env=_provision_subprocess_env(),
            )
        except bounded_proc.ProcessAborted:
            # Ownership loss means another process may already own and mutate
            # this path. Never remove what could now be the new owner's tree.
            raise
        except BaseException as exc:
            # Debian/Ubuntu ship python3 without the venv module's bundled pip
            # (it lives in the separate python3-venv package). Unguarded, this
            # left a HALF-BUILT venv on disk: python_path then existed, so every
            # later launch skipped creation, found no pip, and failed forever.
            # Remove the partial tree so the next launch is a clean retry, and
            # name the package instead of dying with a bare traceback.
            _assert_provision_lock_owned()
            shutil.rmtree(venv_dir, ignore_errors=True)
            print(
                "[kumiho-claude] Could not create the virtualenv at %s: %s\n"
                "[kumiho-claude] On Debian/Ubuntu install the venv module first:"
                "  sudo apt install python3-venv\n"
                "[kumiho-claude] The partial environment was removed; retry after fixing this."
                % (venv_dir, exc),
                file=sys.stderr,
            )
            raise

    if not _python_interpreter_works(python_path):
        raise RuntimeError(
            "the shared runtime is not a valid Python 3.10+ virtual environment"
        )

    # Unconditionally, not only on the creation branch: a venv provisioned by an
    # older version has no junction, and nothing else would ever add one.
    _ensure_hook_interpreter(venv_dir)

    if needs_install:
        print("[kumiho-claude] Installing dependencies (first run downloads "
              "~150 MB and takes several minutes)...", file=sys.stderr)
        try:
            _install_dependencies(python_path, package_spec)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # The single most likely install failure -- no network, a floor not
            # yet on PyPI, a proxy -- used to surface as a raw traceback, which
            # the host shows only as "server failed to start".
            print(
                "[kumiho-claude] Installing '%s' failed (pip exit %s).\n"
                "[kumiho-claude] Common causes: no network or a proxy blocking "
                "PyPI, or a package version that is not published yet.\n"
                "[kumiho-claude] Retry, or pin a different set with "
                "KUMIHO_CLAUDE_PACKAGE_SPEC."
                % (package_spec, getattr(exc, "returncode", "timeout")),
                file=sys.stderr,
            )
            raise

        verification_marker = marker_path.with_name(
            f".{marker_path.name}.verify-{os.getpid()}-{secrets.token_hex(4)}"
        )
        if _needs_install(
            python_path,
            verification_marker,
            package_spec,
            probe_timeout_s=PROBE_TIMEOUT_S,
        ):
            raise RuntimeError(
                "the installed shared runtime did not satisfy the requested "
                "Kumiho package contract"
            )
    # Both an install and a full no-install probe establish this exact
    # contract. Persist that result so a healthy but slow shared runtime does
    # not enter an endless five-second background-handoff loop.
    _write_install_marker(marker_path, package_spec)
    _write_runtime_attestation(venv_dir, python_path, marker_path, package_spec)

    _ensure_plugin_data_venv_alias(venv_dir)

    return python_path


def _warn_auth() -> None:
    auth_token = _load_bearer_token()
    if auth_token:
        return
    print(
        "[kumiho-claude] Warning: KUMIHO_AUTH_TOKEN is not set. "
        "Memory and graph operations will fail until a token is provided.",
        file=sys.stderr,
    )


def _decode_jwt_claims(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        claims = json.loads(decoded.decode("utf-8"))
        if isinstance(claims, dict):
            return claims
    except Exception:
        return None
    return None


def _validate_auth_token() -> None:
    auth_token = _load_bearer_token()
    if not auth_token:
        return

    claims = _decode_jwt_claims(auth_token)
    if claims:
        return

    print(
        "[kumiho-claude] Warning: KUMIHO_AUTH_TOKEN does not look like a JWT. "
        "Use a dashboard-minted Kumiho API token.",
        file=sys.stderr,
    )


def _load_bearer_token() -> str:
    value = _clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if _looks_like_placeholder(value):
        value = ""
    if value:
        return value
    return _load_cached_kumiho_token()


def _cached_kumiho_auth_path() -> Path:
    config_dir = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "kumiho_authentication.json"
    return _account_home() / ".kumiho" / "kumiho_authentication.json"


def _read_cached_kumiho_credentials() -> dict | None:
    path = _cached_kumiho_auth_path()

    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _load_cached_kumiho_token() -> str:
    body = _read_cached_kumiho_credentials()
    if not body:
        return ""

    now = int(time.time())
    # Session tokens (from `kumiho-auth login`) have expiry checks.
    # Dashboard API tokens are long-lived; expiry check is optional.
    candidates = (
        ("control_plane_token", "cp_expires_at"),
        ("id_token", "expires_at"),
        ("api_token", "api_token_expires_at"),
    )
    for token_key, expiry_key in candidates:
        raw = body.get(token_key)
        if not isinstance(raw, str):
            continue
        token = _clean_token_candidate(raw.strip())
        if not token or _looks_like_placeholder(token):
            continue
        expiry = body.get(expiry_key)
        if isinstance(expiry, (int, float)) and int(expiry) <= now + 30:
            continue
        return token
    return ""


def _discovery_token_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(candidate: str) -> None:
        token = _clean_token_candidate((candidate or "").strip())
        if not token or _looks_like_placeholder(token):
            return
        if token in seen:
            return
        seen.add(token)
        out.append(token)

    add((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    add(_load_bearer_token())

    body = _read_cached_kumiho_credentials()
    if isinstance(body, dict):
        now = int(time.time())
        for token_key, expiry_key in (
            ("control_plane_token", "cp_expires_at"),
            ("id_token", "expires_at"),
            ("api_token", "api_token_expires_at"),
        ):
            raw = body.get(token_key)
            if not isinstance(raw, str):
                continue
            expiry = body.get(expiry_key)
            if isinstance(expiry, (int, float)) and int(expiry) <= now + 30:
                continue
            add(raw)

    return out


def _clean_token_candidate(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _looks_like_placeholder(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    # Guard against unresolved template literals like ${KUMIHO_AUTH_TOKEN:-}
    # being injected as raw strings by a host/plugin runtime.
    return text.startswith("${") and text.endswith("}")


def _set_env_if_absent(key: str, value: str, source: str) -> bool:
    existing = (os.getenv(key, "") or "").strip()
    if existing and not _looks_like_placeholder(existing):
        return False
    candidate = (value or "").strip()
    if key == "KUMIHO_AUTH_TOKEN":
        candidate = _clean_token_candidate(candidate)
    if not candidate or _looks_like_placeholder(candidate):
        return False
    os.environ[key] = candidate
    print(f"[kumiho-claude] Loaded {key} from {source}.", file=sys.stderr)
    return True


def _host_config_value_allowed(
    key: str, value: object, *, user_global: bool = False
) -> bool:
    """Apply the host provenance policy to a config-sourced value."""
    if not _host_launch_isolated() or user_global:
        return True
    if key == "KUMIHO_AUTH_TOKEN":
        return False
    if key in _HOST_UNTRUSTED_DATA_ROUTE_ENV:
        if key == "KUMIHO_CLAUDE_SERVER_ENDPOINT":
            normalized = _normalize_server_target(
                value if isinstance(value, str) else ""
            )
            return bool(normalized and _ce_server_target_is_safe(normalized))
        if key == "UPSTASH_REDIS_URL":
            return isinstance(value, str) and _ce_redis_url_is_safe(value.strip())
        return (
            key in _HOST_PROJECT_LOOPBACK_ROUTE_ENV
            and _service_route_is_loopback(value)
        )
    return key not in (
        *_HOST_UNTRUSTED_CLOUD_ENV,
        *_HOST_UNTRUSTED_PATH_ENV,
        *_HOST_UNTRUSTED_PROVISION_ENV,
        *_HOST_UNTRUSTED_TRANSPORT_ENV,
    )


def _normalize_host_session_id() -> None:
    """Publish the host's session identity as KUMIHO_SESSION_ID.

    kumiho-memory >=1.2.0 resolves an omitted session_id from this ONE
    variable and deliberately ignores CLAUDE_CODE_SESSION_ID: that var
    reaches the server by env inheritance rather than by contract, and
    Claude Code rotates its session on /clear WITHOUT respawning the
    server, so trusting it silently merged the post-/clear conversation
    into the previous one's working-memory bucket.

    Setting it HERE is the point: identity provisioning belongs to the
    host-integration layer, which knows which host it is talking to and —
    unlike the package — has a live channel for rotation (the SessionStart
    hook receives the new id on /clear; kumiho-plugins#45 item 4). Per-host
    knowledge accumulates in this function instead of in the package.

    An explicitly set KUMIHO_SESSION_ID always wins; this only fills the
    gap. Hosts that expose no session identity (Claude Desktop's
    config-spawned servers) get nothing, and the package then asks callers
    for an explicit id rather than guessing — the intended behaviour.

    CODEX_SESSION_ID is listed forward-compatibly, NOT because it works:
    codex-cli 0.145.0 does not define it (a string scan of the shipped
    codex.exe finds 60+ CODEX_* variables and no session id — the closest
    are CODEX_ROLLOUT_TRACE_ROOT, a directory, and the CODEX_TUI_RECORD_*
    capture flags). Codex therefore has NO host identity to normalize, and
    its skill text carries the rule instead: derive one stable id per
    session and pass it on every memory call. Do not read this tuple as
    "Codex is covered".
    """
    if (os.getenv("KUMIHO_SESSION_ID", "") or "").strip():
        return
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"):
        value = (os.getenv(var, "") or "").strip()
        if value and not _looks_like_placeholder(value):
            os.environ["KUMIHO_SESSION_ID"] = value
            return


def _plugin_root() -> Path:
    from_env = (os.getenv("CLAUDE_PLUGIN_ROOT", "") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return Path(__file__).resolve().parents[1]


def _read_dotenv_file(dotenv_path: Path, *, user_global: bool = False) -> None:
    """Parse and apply KEY=VALUE pairs from a single dotenv file."""
    try:
        entries: list[tuple[str, str]] = []
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and (
                (value[0] == '"' and value[-1] == '"')
                or (value[0] == "'" and value[-1] == "'")
            ):
                value = value[1:-1]
            entries.append((key, value))

        for key, value in entries:
            if not _host_config_value_allowed(
                key, value, user_global=user_global
            ):
                # Host-launched Cloud routing and the executable runtime root
                # have provenance requirements. They are restored only from
                # user-global config, never from a checkout-local dotenv file.
                continue
            _set_env_if_absent(key, value, str(dotenv_path))
    except Exception:
        pass


def _hydrate_env_from_dotenv() -> None:
    """Read KEY=VALUE pairs from .env.local files.

    Checks in priority order:
      1. Plugin root (.env.local, .env) — standard location for Claude Code
      2. ~/.kumiho/.env.local — fallback written by setup wizard when the
         plugin directory is read-only (e.g. in Cowork / Claude Desktop)
    """
    # 1. Plugin root
    root = _plugin_root()
    for name in (".env.local", ".env"):
        dotenv_path = root / name
        if dotenv_path.exists():
            _read_dotenv_file(dotenv_path)
            return  # stop after first found at plugin root

    # 2. ~/.kumiho/.env.local fallback
    kumiho_env = _account_home() / ".kumiho" / ".env.local"
    if kumiho_env.exists():
        # This path is anchored beneath the native account home after host
        # sanitation and is the setup wizard's read-only-plugin fallback.
        _read_dotenv_file(kumiho_env, user_global=True)


def _hydrate_env_from_plugin_mcp() -> None:
    root = _plugin_root()
    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        return

    try:
        body = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception:
        return

    if not isinstance(body, dict):
        return
    servers = body.get("mcpServers")
    if not isinstance(servers, dict):
        return
    server = servers.get("kumiho-memory")
    if not isinstance(server, dict):
        return
    env = server.get("env")
    if not isinstance(env, dict):
        return

    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_CLAUDE_MODE",
        *_HOST_UNTRUSTED_DATA_ROUTE_ENV,
    ):
        raw = env.get(key)
        if isinstance(raw, str):
            if not _host_config_value_allowed(key, raw):
                continue
            _set_env_if_absent(key, raw, f"{mcp_path}")


def _candidate_settings_paths() -> list[Path]:
    """Return project-to-user Claude settings in historical priority order."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        claude_dir = base / ".claude"
        add(claude_dir / "settings.local.json")
        add(claude_dir / "settings.json")

    home_claude = _account_home() / ".claude"
    add(home_claude / "settings.local.json")
    add(home_claude / "settings.json")
    return candidates


def _is_user_global_claude_setting(path: Path) -> bool:
    home_claude = _account_home() / ".claude"
    trusted = {
        os.path.normcase(os.path.abspath(home_claude / "settings.local.json")),
        os.path.normcase(os.path.abspath(home_claude / "settings.json")),
    }
    return os.path.normcase(os.path.abspath(path)) in trusted


def _hydrate_shared_package_spec_from_user_settings() -> None:
    """Load the one package identity shared by Claude and Codex.

    The legacy variable name is Claude-specific, but the runtime is not. If
    Codex ignored this exact user-global override, alternating hosts would
    reinstall the shared venv against two different marker identities.
    """
    if not _host_launch_isolated():
        return
    settings_root = _account_home() / ".claude"
    for settings_path in (
        settings_root / "settings.local.json",
        settings_root / "settings.json",
    ):
        try:
            body = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env = body.get("env") if isinstance(body, dict) else None
        raw = env.get("KUMIHO_CLAUDE_PACKAGE_SPEC") if isinstance(env, dict) else None
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if (
            value
            and "${" not in value
            and not any(char in value for char in "\r\n\0")
        ):
            os.environ["KUMIHO_CLAUDE_PACKAGE_SPEC"] = value
            print(
                f"[kumiho-claude] Loaded KUMIHO_CLAUDE_PACKAGE_SPEC from "
                f"{settings_path}.",
                file=sys.stderr,
            )
            return


def _hydrate_env_from_claude_settings() -> None:
    _TRUSTED_GLOBAL_CE_KEYS.clear()
    candidates = _candidate_settings_paths()
    found_any = False
    trusted_global_keys_loaded: set[str] = set()
    for settings_path in candidates:
        if not settings_path.exists():
            continue
        try:
            body = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        env = body.get("env")
        if not isinstance(env, dict):
            continue
        found_any = True
        user_global = _is_user_global_claude_setting(settings_path)
        # Preserve Claude Code's existing project settings support for explicit
        # tokens and CE selection. Project-provided Cloud routes are discarded
        # independently; the Cloud adapter later pins the official origin.
        keys = [
            "KUMIHO_AUTH_TOKEN",
            "KUMIHO_CLAUDE_MODE",
            *_HOST_UNTRUSTED_DATA_ROUTE_ENV,
        ]
        if user_global:
            keys.extend((
                "KUMIHO_CONTROL_PLANE_URL",
                "KUMIHO_CONTROL_PLANE_API_URL",
                "KUMIHO_TENANT_HINT",
                "KUMIHO_FIREBASE_API_KEY",
                "KUMIHO_FIREBASE_PROJECT_ID",
                "KUMIHO_CONFIG_DIR",
                "KUMIHO_CLAUDE_HOME",
                "KUMIHO_CLAUDE_PACKAGE_SPEC",
                *_HOST_UNTRUSTED_TRANSPORT_ENV,
            ))
        for key in keys:
            raw = env.get(key)
            if isinstance(raw, str):
                value = raw.strip()
                if (
                    not value
                    or _looks_like_placeholder(raw)
                    or "${" in raw
                    or any(char in raw for char in "\r\n\0")
                ):
                    continue
                if key in _HOST_TRUSTED_GLOBAL_PATH_ENV:
                    candidate = Path(value)
                    if not candidate.is_absolute():
                        continue
                    value = str(candidate)
                if not _host_config_value_allowed(
                    key, value, user_global=user_global
                ):
                    if key == "KUMIHO_AUTH_TOKEN" and not user_global:
                        print(
                            "[kumiho-claude] Ignoring KUMIHO_AUTH_TOKEN from "
                            "project settings; configure it in the user SDK "
                            "store or OS/user-global environment.",
                            file=sys.stderr,
                        )
                    continue
                # A trusted global remote route must beat an earlier project
                # loopback value. settings.local.json remains higher priority
                # than settings.json through this first-wins set.
                if user_global and key in (
                    *_HOST_UNTRUSTED_CLOUD_ENV,
                    *_HOST_UNTRUSTED_PATH_ENV,
                    *_HOST_UNTRUSTED_PROVISION_ENV,
                    *_HOST_UNTRUSTED_TRANSPORT_ENV,
                    *_HOST_UNTRUSTED_DATA_ROUTE_ENV,
                ):
                    if key in trusted_global_keys_loaded:
                        continue
                    os.environ[key] = value
                    if key in {"KUMIHO_CLAUDE_SERVER_ENDPOINT", "KUMIHO_CLAUDE_MODE"}:
                        _TRUSTED_GLOBAL_CE_KEYS.add(key)
                    if key in _HOST_TRUSTED_PROVISION_TRANSPORT_ENV:
                        _TRUSTED_SETTINGS_TRANSPORT_ENV[key] = value
                    trusted_global_keys_loaded.add(key)
                    print(
                        f"[kumiho-claude] Loaded {key} from {settings_path}.",
                        file=sys.stderr,
                    )
                    continue
                _set_env_if_absent(key, value, f"{settings_path}")
    if not found_any:
        print(
            f"[kumiho-claude] Searched {len(candidates)} settings paths; "
            "none contained a usable env block. "
            "Use %s or set KUMIHO_AUTH_TOKEN in ~/.kumiho/kumiho_authentication.json."
            % _onboard_command_label(),
            file=sys.stderr,
        )


def _align_trusted_control_plane_api_url() -> None:
    """Use a trusted custom discovery base for auth REST unless split explicitly.

    The SDK/auth CLI consumes ``KUMIHO_CONTROL_PLANE_API_URL`` while discovery
    consumes ``KUMIHO_CONTROL_PLANE_URL``. Host launch sanitation means a
    concrete primary URL in a Claude host came from user-global settings; a
    markerless direct invocation is user-controlled by definition. Custom
    deployments historically commonly serve both APIs at that base, so
    inheriting it is safer and more compatible than sending login credentials
    to the official API. An explicit trusted API URL always wins. Codex keeps
    its host-isolated official auth path.
    """
    if (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() == "codex":
        return
    primary = (os.getenv("KUMIHO_CONTROL_PLANE_URL", "") or "").strip()
    api = (os.getenv("KUMIHO_CONTROL_PLANE_API_URL", "") or "").strip()
    if primary and not _looks_like_placeholder(primary) and not api:
        os.environ["KUMIHO_CONTROL_PLANE_API_URL"] = primary


def _hydrate_env_from_local_config() -> None:
    # Host processes inherit project settings before this code runs. Remove
    # untrusted Cloud routes and runtime roots before loading a bearer or
    # resolving an executable; trusted Claude values are restored by directly
    # reading user-global settings below. Codex never reads Claude settings.
    _clear_host_untrusted_environment()
    _hydrate_trusted_persisted_user_environment()
    # Package identity is the narrow exception: both hosts mutate the same
    # ~/.kumiho/venv and therefore must honor one trusted global spec.
    _hydrate_shared_package_spec_from_user_settings()
    # Codex has its own host-specific backend config. Reading Claude project,
    # settings (apart from the shared package identity above), or plugin files
    # here could redirect Codex discovery (and its
    # bearer token) to a Claude-only control plane. Claude keeps the original
    # hydration path unchanged.
    if os.getenv("KUMIHO_CLAUDE_HOST") == "codex":
        # Codex has its own backend selection. Authentication remains in the
        # shared SDK-owned ~/.kumiho store used by Claude and Desktop.
        _apply_codex_backend_override()
        return
    _hydrate_env_from_dotenv()
    _hydrate_env_from_claude_settings()
    _hydrate_env_from_plugin_mcp()
    _apply_codex_backend_override()


def _apply_codex_backend_override() -> None:
    """Make Codex's host-specific backend choice authoritative after hydration."""
    if os.getenv("KUMIHO_CLAUDE_HOST") != "codex":
        return
    # Codex Cloud is intentionally pinned to the official control plane.
    # Tenant routing comes from authenticated discovery, never Claude config.
    os.environ.pop("KUMIHO_CONTROL_PLANE_URL", None)
    os.environ.pop("KUMIHO_CONTROL_PLANE_API_URL", None)
    os.environ.pop("KUMIHO_TENANT_HINT", None)
    os.environ.pop("KUMIHO_FIREBASE_API_KEY", None)
    os.environ.pop("KUMIHO_FIREBASE_ID_TOKEN", None)
    os.environ.pop("KUMIHO_FIREBASE_PROJECT_ID", None)
    os.environ.pop("KUMIHO_USE_CONTROL_PLANE_TOKEN", None)
    os.environ.pop("KUMIHO_WORKSPACE_ROOT", None)
    os.environ.pop("KUMIHO_ENV_FILE", None)
    backend = (os.getenv("KUMIHO_CODEX_BACKEND", "") or "").strip().lower()
    if backend == "cloud":
        for key in (
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
        ):
            os.environ.pop(key, None)
        return
    if backend != "ce":
        return
    endpoint = (os.getenv("KUMIHO_CODEX_CE_ENDPOINT", "") or "").strip()
    if not endpoint:
        return
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = endpoint
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    redis_url = (os.getenv("KUMIHO_CODEX_CE_REDIS_URL", "") or "").strip()
    os.environ["UPSTASH_REDIS_URL"] = _resolve_ce_redis_url(
        redis_url or DEFAULT_CE_REDIS_URL
    )
    llm_base_url = (os.getenv("KUMIHO_CODEX_CE_LLM_BASE_URL", "") or "").strip()
    os.environ.pop("KUMIHO_LLM_BASE_URL", None)
    if llm_base_url:
        os.environ["KUMIHO_LLM_BASE_URL"] = llm_base_url


def _claude_desktop_config_paths() -> list[Path]:
    """Return platform-specific Claude Desktop global config paths.

    On Windows MSIX installs, Claude Desktop reads from a virtualised
    path under LocalAppData\\Packages instead of the standard %APPDATA%
    location.  We check the MSIX path first, then the standard path.
    """
    paths: list[Path] = []
    if os.name == "nt":
        # MSIX virtualised path (Windows Store / official installer).
        local_appdata = os.getenv("LOCALAPPDATA", "")
        if local_appdata:
            msix_base = Path(local_appdata) / "Packages"
            if msix_base.exists():
                for entry in msix_base.iterdir():
                    if entry.name.startswith("Claude_") and entry.is_dir():
                        candidate = (
                            entry / "LocalCache" / "Roaming" / "Claude"
                            / "claude_desktop_config.json"
                        )
                        paths.append(candidate)
                        break
        # Standard (non-MSIX) path.
        appdata = os.getenv("APPDATA", "")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(
            _account_home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        xdg_config = os.getenv("XDG_CONFIG_HOME", "")
        if xdg_config:
            paths.append(Path(xdg_config) / "Claude" / "claude_desktop_config.json")
        else:
            paths.append(_account_home() / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def _try_sync_token_to_config(config_path: Path, token: str) -> bool:
    """Attempt to write *token* into a single MCP config file.

    Returns True on success, False on any error.
    """
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
        entry = servers.get(name)
        if isinstance(entry, dict):
            server = entry
            break
    if server is None:
        return False

    env = server.get("env")
    if not isinstance(env, dict):
        return False

    current = (env.get("KUMIHO_AUTH_TOKEN") or "").strip()
    if current == token:
        return True  # already in sync

    env["KUMIHO_AUTH_TOKEN"] = token
    try:
        _write_json_atomic(config_path, body)
        print(
            f"[kumiho-claude] Synced KUMIHO_AUTH_TOKEN into {config_path.name}.",
            file=sys.stderr,
        )
        return True
    except Exception:
        return False


def _running_from_a_host_install() -> bool:
    """Is this launcher living where the HOST put it, or in a working copy?

    Deliberately reads ``__file__`` only, never the environment: a dev checkout
    inherits CLAUDE_PLUGIN_DATA and friends from whatever session started it, so
    an env-based answer says "installed" for a worktree.

    A host install sits at ``<config>/plugins/cache/<marketplace>/<plugin>/<version>/``
    or in a Desktop agent-mode snapshot under ``rpm/plugin_<id>/``. Anything else
    -- ``--plugin-dir``, a clone, a git worktree -- is a working copy whose path
    moves, gets deleted, or is on a branch under active edit.

    Measured 2026-08-03: running this repo's own test suite from a git worktree
    repointed the machine's real Claude Desktop config at that worktree. The
    tests spawn the SessionStart hook, the hook spawns this launcher, and the
    launcher wrote its own location into every config it could find -- including
    the Windows MSIX one under LOCALAPPDATA that the tests had not redirected.
    Nothing about that path is the user's plugin.
    """
    parts = Path(__file__).resolve().parts
    if "cache" in parts:
        i = len(parts) - 1 - parts[::-1].index("cache")
        if len(parts) >= i + 4:          # cache/<marketplace>/<plugin>/<version>/...
            return True
    return any(p == "rpm" for p in parts)


def _desktop_bootstrap_enabled() -> bool:
    """Claude Desktop config writes are for Claude hosts only.

    The launcher is shared with other hosts (the codex plugin vendors a
    copy and sets ``KUMIHO_CLAUDE_HOST=codex``).  A non-Claude host must
    never create or rewrite Claude Desktop config files: on a machine
    without Claude Desktop that fabricates config trees, and on a machine
    WITH it, it could repoint Desktop at another host's plugin snapshot or
    another tenant's token.
    """
    host = (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower()
    if host not in ("", "claude", "claude-code", "claude-desktop", "cowork"):
        return False
    # ...and only from where the host installed us. A working copy must never
    # write its own path into a config the user keeps: see
    # _running_from_a_host_install for the incident this prevents.
    if not _running_from_a_host_install():
        print(
            "[kumiho-claude] Running from a working copy (%s); leaving Claude "
            "Desktop configs alone. Install the plugin to have them managed."
            % Path(__file__).resolve().parent.parent,
            file=sys.stderr,
        )
        return False
    return True


def _bootstrap_desktop_server_entries() -> None:
    """Ensure Claude Desktop configs have a kumiho-memory server entry.

    Writes absolute paths (no ``${...}`` templates) so Claude Desktop can
    launch the server without shell variable resolution.  Called on every
    startup so the config self-heals if the entry was wiped or never created.

    Uses the actual running script location (``__file__``) rather than
    ``CLAUDE_PLUGIN_ROOT`` env because the env variable may point to a
    non-versioned or stale path.
    """
    # Always derive plugin root from the actual running script, not env.
    plugin_root = Path(__file__).resolve().parents[1]
    script_path = plugin_root / "scripts" / "run_kumiho_mcp.py"
    if not script_path.exists():
        return  # Not in a standard plugin layout; skip.

    # _venv_dir(), never _state_dir()/"venv": this writes an ABSOLUTE interpreter
    # path into the user's Desktop config, and _has_valid_entry below only
    # validates args[0], so a wrong `command` here is never repaired again.
    venv_py = _venv_python(_venv_dir())
    command = str(venv_py) if venv_py.exists() else sys.executable

    server_entry: dict = {
        "command": command,
        "args": ["-I", str(script_path)],
        "env": {
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
            "KUMIHO_CLAUDE_HOST": "claude",
        },
    }
    for desktop_path in _claude_desktop_config_paths():
        try:
            if desktop_path.exists():
                body = json.loads(desktop_path.read_text(encoding="utf-8"))
                if not isinstance(body, dict):
                    body = {}
            else:
                body = {}
        except Exception:
            continue

        # Check if already configured *with a valid script path*.
        # If the entry exists but points to a missing file (e.g. stale version
        # path), fall through and overwrite it with the correct paths.
        def _has_valid_entry(b: dict) -> bool:
            servers = b.get("mcpServers")
            if not isinstance(servers, dict):
                return False
            entry = servers.get("kumiho-memory")
            if not isinstance(entry, dict):
                return False
            args = entry.get("args") or []
            if args != ["-I", str(script_path)]:
                return False        # absent, or pinned to another version
            cmd = entry.get("command")
            if not cmd or not Path(cmd).exists():
                return False        # interpreter went away with an old venv
            entry_env = entry.get("env")
            if not isinstance(entry_env, dict) or entry_env.get(
                "KUMIHO_CLAUDE_HOST"
            ) != "claude":
                return False        # add provenance hardening to legacy entries
            try:
                return Path(cmd).resolve() == Path(command).resolve()
            except OSError:
                return False

        def _is_managed_legacy_entry(entry: object) -> bool:
            if not isinstance(entry, dict):
                return False
            args = entry.get("args") or []
            if not args:
                return False
            raw = args[1] if args[:1] == ["-I"] and len(args) > 1 else args[0]
            if not isinstance(raw, str):
                return False
            try:
                if Path(raw).resolve() == script_path.resolve():
                    return True
            except OSError:
                pass
            normalized = raw.replace("\\", "/").lower()
            return (
                normalized.endswith("/scripts/run_kumiho_mcp.py")
                and (
                    "kumiho-memory" in normalized
                    or "kumiho-plugins" in normalized
                )
            )

        servers = body.get("mcpServers")
        legacy = servers.get("kumiho") if isinstance(servers, dict) else None
        managed_legacy = _is_managed_legacy_entry(legacy)
        if _has_valid_entry(body):
            if managed_legacy:
                # One physical server under two names starts two MCP processes.
                # Remove only an entry positively identified as this plugin;
                # an unrelated user-owned server named "kumiho" is preserved.
                servers.pop("kumiho", None)
                try:
                    _write_json_atomic(desktop_path, body)
                except OSError:
                    pass
            continue  # Valid canonical entry — no other repair needed.

        # Not configured — bootstrap or repair it without discarding backend
        # selection, Redis/LLM routing, or unrelated host-owned fields.
        servers = body.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            body["mcpServers"] = servers
        previous = servers.get("kumiho-memory")
        if not isinstance(previous, dict):
            previous = legacy if managed_legacy else {}
        repaired = dict(previous)
        repaired["command"] = command
        repaired["args"] = ["-I", str(script_path)]
        previous_env = previous.get("env")
        repaired_env = dict(previous_env) if isinstance(previous_env, dict) else {}
        repaired_env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        repaired_env["KUMIHO_CLAUDE_HOST"] = "claude"
        if _ce_mode_enabled():
            repaired_env.pop("KUMIHO_AUTH_TOKEN", None)
        # In Cloud mode, preserve a token already present in a legacy entry
        # for compatibility, but never copy an ambient/project credential into
        # global Desktop configuration. Authentication persistence belongs to
        # the SDK or to a host-level KUMIHO_AUTH_TOKEN set by the user.
        repaired["env"] = repaired_env
        servers["kumiho-memory"] = repaired
        if managed_legacy:
            servers.pop("kumiho", None)
        try:
            _write_json_atomic(desktop_path, body)
            print(
                f"[kumiho-claude] Bootstrapped kumiho-memory server entry in {desktop_path.name}.",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[kumiho-claude] Could not write {desktop_path}: {exc}",
                file=sys.stderr,
            )


def _sync_token_to_desktop_config() -> None:
    """Write the resolved token into Claude Desktop's global config.

    This triggers Claude Desktop to restart the MCP server so it picks up
    the new token immediately.  We deliberately skip the plugin-local
    ``.mcp.json`` — that file is git-tracked and must stay clean (template
    variables only).  The credential cache and ``.env.local`` serve Claude
    Code; the Desktop config serves Claude Desktop.
    """
    token = _clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if not token or _looks_like_placeholder(token):
        return

    for desktop_path in _claude_desktop_config_paths():
        if _try_sync_token_to_config(desktop_path, token):
            return


def _build_discovery_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/discovery/tenant"):
        return base
    if base.endswith("/api/discovery"):
        return f"{base}/tenant"
    if base.endswith("/api"):
        return f"{base}/discovery/tenant"
    return f"{base}/api/discovery/tenant"


def _load_control_plane_url() -> str:
    raw = (os.getenv("KUMIHO_CONTROL_PLANE_URL", "") or "").strip()
    if _looks_like_placeholder(raw):
        raw = ""
    value = raw or "https://control.kumiho.cloud"
    if any(char in value for char in "\r\n\0"):
        raise RuntimeError("Control-plane URL contains invalid characters.")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Control-plane URL is not a supported HTTP(S) URL.")
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeError("Control-plane URL has an invalid port.") from exc
    if parsed.scheme.lower() == "http":
        host = parsed.hostname.lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            raise RuntimeError(
                "A non-loopback control plane must use HTTPS."
            )
    return value.rstrip("/")


def _plugin_manifest_version() -> "str | None":
    """The version declared by the plugin manifest shipped beside this file.

    One stdlib ``json`` read of a file already on disk, once per process, on a
    path that is about to do a network round trip anyway -- so deriving the
    user-agent costs nothing measurable and adds no dependency.

    Both manifest names are tried because ``codex/scripts/_vendored_launcher.py``
    is a byte-identical copy of this file (guarded by test_launcher_parity.py)
    that sits next to ``.codex-plugin`` instead of ``.claude-plugin``.
    """
    root = Path(__file__).resolve().parent.parent
    for manifest in (
        root / ".claude-plugin" / "plugin.json",
        root / ".codex-plugin" / "plugin.json",
    ):
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        except Exception:
            continue
        if isinstance(version, str) and version.strip():
            return version.strip()
    return None


def _default_discovery_user_agent() -> str:
    version = _plugin_manifest_version() or DISCOVERY_USER_AGENT_UNKNOWN_VERSION
    return f"{DISCOVERY_USER_AGENT_PRODUCT}/{version}"


def _load_discovery_user_agent() -> str:
    raw = (os.getenv("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", "") or "").strip()
    if not raw or _looks_like_placeholder(raw):
        return _default_discovery_user_agent()
    return raw


def _normalize_server_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target or any(char in target for char in "\r\n\0"):
        return None

    has_scheme = "://" in target
    parsed = urllib.parse.urlsplit(target if has_scheme else f"//{target}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme and scheme not in {"http", "https", "grpc", "grpcs"}:
        return None
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        if not scheme:
            return None
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    # Preserve TLS-bearing schemes. The SDK uses them to choose a secure gRPC
    # channel; reducing grpcs://host:7443 to host:7443 silently downgrades it.
    return f"{scheme}://{authority}" if scheme else authority


def _ce_server_target_is_safe(
    target: str, *, allow_user_global_tls: bool = False
) -> bool:
    has_scheme = "://" in target
    parsed = urllib.parse.urlsplit(target if has_scheme else f"//{target}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        if not allow_user_global_tls:
            return False
        scheme = urllib.parse.urlsplit(
            target if has_scheme else f"//{target}"
        ).scheme.lower()
        return scheme in {"https", "grpcs"}


def _ce_redis_url_is_safe(value: str) -> bool:
    """Allow CE working memory only on the local machine."""
    if any(char in value for char in "\r\n\0"):
        return False
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() not in {"redis", "rediss"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
    ):
        return False
    try:
        parsed.port
    except ValueError:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_ce_redis_url(raw: str | None = None) -> str:
    value = (raw if raw is not None else os.getenv("UPSTASH_REDIS_URL", "") or "").strip()
    if not value or _looks_like_placeholder(value):
        value = DEFAULT_CE_REDIS_URL
    if not _ce_redis_url_is_safe(value):
        raise SystemExit(
            "[kumiho-claude] Refusing a non-loopback CE Redis URL; "
            "CE services must run on the local machine."
        )
    return value


def _ce_mode_enabled() -> bool:
    """True when the plugin should target a self-hosted kumiho-server CE endpoint.

    Opt-in only, so the fail-fast cloud default is preserved.  Enabled by
    ``KUMIHO_CLAUDE_MODE=ce`` (or ``community`` / ``self-hosted`` / ``local``),
    or implicitly when ``KUMIHO_CLAUDE_SERVER_ENDPOINT`` is set to a real value.
    """
    mode = (os.getenv("KUMIHO_CLAUDE_MODE", "") or "").strip().lower()
    if mode and not _looks_like_placeholder(mode) and mode in CE_MODE_VALUES:
        return True
    endpoint = (os.getenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", "") or "").strip()
    return bool(endpoint) and not _looks_like_placeholder(endpoint)


def _resolve_ce_endpoint() -> str | None:
    """Return the normalized CE gRPC endpoint when CE mode is on, else ``None``."""
    if not _ce_mode_enabled():
        return None
    raw = (os.getenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", "") or "").strip()
    if raw and not _looks_like_placeholder(raw):
        normalized = _normalize_server_target(raw)
        if normalized:
            if not _ce_server_target_is_safe(
                normalized,
                # User-global Claude settings mark the key when they load it.
                # The OS user environment (HKCU / ~/.profile) is the other
                # trusted source and is hydrated earlier with set-if-absent
                # semantics, so compare the live value against it directly
                # instead of relying on a mark that hydration cannot leave.
                allow_user_global_tls=(
                    "KUMIHO_CLAUDE_SERVER_ENDPOINT" in _TRUSTED_GLOBAL_CE_KEYS
                    or (
                        _host_launch_isolated()
                        and raw == (
                            _trusted_persisted_user_environment().get(
                                "KUMIHO_CLAUDE_SERVER_ENDPOINT", ""
                            ) or ""
                        ).strip()
                    )
                ),
            ):
                raise SystemExit(
                    "[kumiho-claude] Refusing a non-loopback CE endpoint unless "
                    "it is a TLS URL from user-global settings."
                )
            return normalized
        print(
            "[kumiho-claude] Could not parse the configured CE endpoint; "
            f"falling back to {DEFAULT_CE_ENDPOINT} without logging its value.",
            file=sys.stderr,
        )
    return DEFAULT_CE_ENDPOINT


def _bootstrap_ce_endpoint(endpoint: str) -> None:
    """Point the SDK at a self-hosted CE server instead of cloud discovery.

    CE is launched through the plugin's explicit-client adapter after the
    endpoint and its supporting services have been restricted to loopback.
    """
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
    os.environ.pop("KUMIHO_LOCAL_SERVER_ENDPOINT", None)
    for key in (
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "KUMIHO_LOCAL_REDIS_URL",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    ):
        os.environ.pop(key, None)
    os.environ["KUMIHO_SERVER_ENDPOINT"] = endpoint
    scheme = endpoint.partition("://")[0].lower() if "://" in endpoint else ""
    if scheme in {"grpcs", "https"}:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
        os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    else:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
    # A token would flip the SDK back to control-plane discovery; CE runs
    # tokenless and enforces its own auth at the server.
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    os.environ["UPSTASH_REDIS_URL"] = _resolve_ce_redis_url()
    # Empty counts as unset: on Desktop the ${VAR:-} placeholder resolves to "".
    if not (os.environ.get("KUMIHO_WORKING_MEMORY_TTL", "") or "").strip():
        os.environ["KUMIHO_WORKING_MEMORY_TTL"] = DEFAULT_CE_WORKING_MEMORY_TTL
    print(
        f"[kumiho-claude] CE mode: routing to self-hosted endpoint {endpoint} "
        "(control-plane discovery and cloud auth skipped).",
        file=sys.stderr,
    )


def _bootstrap_server_endpoint() -> None:
    ce_endpoint = _resolve_ce_endpoint()
    if ce_endpoint:
        _bootstrap_ce_endpoint(ce_endpoint)
        return

    preset_endpoint = os.getenv("KUMIHO_SERVER_ENDPOINT", "").strip() or os.getenv("KUMIHO_SERVER_ADDRESS", "").strip()
    if preset_endpoint:
        print(
            "[kumiho-claude] Ignoring pre-set KUMIHO_SERVER_ENDPOINT/KUMIHO_SERVER_ADDRESS; "
            "resolving endpoint via control-plane discovery.",
            file=sys.stderr,
        )
    # Always clear inherited endpoint/transport decisions so Cloud discovery
    # cannot be downgraded after it returns a TLS target.
    os.environ.pop("KUMIHO_SERVER_ENDPOINT", None)
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
    os.environ.pop("KUMIHO_LOCAL_SERVER_ENDPOINT", None)
    os.environ.pop("KUMIHO_SERVER_USE_TLS", None)
    os.environ["KUMIHO_REQUIRE_TLS"] = "1"

    token_candidates = _discovery_token_candidates()
    if not token_candidates:
        print(
            "[kumiho-claude] KUMIHO_AUTH_TOKEN is not set; skipping discovery bootstrap. "
            "MCP tools will load, but authenticated calls will fail until token is provided.",
            file=sys.stderr,
        )
        # Set a sentinel endpoint so the SDK does NOT fall back to
        # localhost:8080.  The .invalid TLD is guaranteed to never
        # resolve (RFC 6761), producing a clear "not connected" error.
        os.environ["KUMIHO_SERVER_ENDPOINT"] = "needs-auth.kumiho.invalid:443"
        os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
        return

    control_plane_url = _load_control_plane_url()
    discovery_url = _build_discovery_url(control_plane_url)
    tenant_hint = os.getenv("KUMIHO_TENANT_HINT", "").strip()
    discovery_user_agent = _load_discovery_user_agent()

    payload: dict[str, str] = {}
    if tenant_hint:
        payload["tenant_hint"] = tenant_hint

    body_text: str | None = None
    last_error: Exception | None = None
    request_body = json.dumps(payload).encode("utf-8")

    for index, bearer in enumerate(token_candidates, start=1):
        request = urllib.request.Request(
            discovery_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "User-Agent": discovery_user_agent,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                body_text = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if os.getenv("KUMIHO_CLAUDE_HOST") == "codex":
                request_id = ""
                try:
                    request_id = exc.headers.get("x-request-id", "").strip()
                except Exception:
                    request_id = ""
                request_id = re.sub(r"[^A-Za-z0-9._:-]", "?", request_id)[:80]
                detail = f" request_id={request_id}" if request_id else ""
            else:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8")
                except Exception:
                    detail = ""
                detail = detail.strip().replace("\n", " ")
                if detail:
                    detail = f" {detail[:160]}"
            print(
                f"[kumiho-claude] Discovery candidate #{index} failed ({exc.code}).{detail}",
                file=sys.stderr,
            )
            last_error = exc
        except Exception as exc:
            print(
                f"[kumiho-claude] Discovery candidate #{index} request error: {exc}",
                file=sys.stderr,
            )
            last_error = exc

    if body_text is None:
        if last_error is None:
            raise RuntimeError("Control-plane discovery failed with no usable token candidates.")
        raise RuntimeError(f"Control-plane discovery failed across all token candidates: {last_error}")

    try:
        body = json.loads(body_text)
    except json.JSONDecodeError:
        raise RuntimeError("Control-plane discovery returned invalid JSON.")

    region = body.get("region")
    if not isinstance(region, dict):
        raise RuntimeError("Control-plane discovery response missing region routing.")

    raw_target = ""
    grpc_authority = region.get("grpc_authority")
    if isinstance(grpc_authority, str) and grpc_authority.strip():
        raw_target = grpc_authority
    else:
        server_url = region.get("server_url")
        if isinstance(server_url, str) and server_url.strip():
            raw_target = server_url

    resolved_target = _normalize_server_target(raw_target)
    if not resolved_target:
        raise RuntimeError("Control-plane discovery response missing gRPC target.")
    parsed_target = urllib.parse.urlsplit(
        resolved_target if "://" in resolved_target else f"//{resolved_target}"
    )
    try:
        resolved_port = parsed_target.port
    except ValueError:
        resolved_port = None
    resolved_scheme = parsed_target.scheme.lower()
    if resolved_scheme in {"http", "grpc"} or (
        not resolved_scheme and resolved_port != 443
    ):
        raise RuntimeError("Control-plane discovery returned a non-TLS gRPC target.")

    os.environ["KUMIHO_SERVER_ENDPOINT"] = resolved_target
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
    # The current SDK consults this override after parsing the endpoint scheme.
    # Pin it true as well as REQUIRE_TLS so a stale false value can never turn
    # an authenticated Cloud channel into plaintext.
    os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
    os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    print(
        f"[kumiho-claude] Resolved KUMIHO_SERVER_ENDPOINT={resolved_target} via discovery bootstrap.",
        file=sys.stderr,
    )


def _placeholder_default(value: str) -> str | None:
    """For a literal ``${VAR:-default}`` (or ``${VAR-default}``) placeholder,
    return the *default* the shell would have substituted; ``None`` for a
    bare ``${VAR}`` with no default.

    Claude Desktop does not expand ``${VAR:-default}`` — it passes the whole
    string through literally — so the launcher has to do the substitution the
    shell would have done, honoring the default the .mcp.json author declared.
    """
    text = value.strip()
    if not (text.startswith("${") and text.endswith("}")):
        return None
    inner = text[2:-1]
    for sep in (":-", "-"):
        idx = inner.find(sep)
        if idx > 0:
            return inner[idx + len(sep):]
    return None


def _sanitize_placeholder_env_vars() -> None:
    """Resolve or strip unresolved ``${VAR:-default}`` placeholders that
    Claude Desktop passes through as literal strings.

    A declared default is the author's *intended* value, not garbage: a
    ``${VAR:-default}`` is resolved to ``default`` (so e.g.
    ``KUMIHO_MEMORY_CODE=${KUMIHO_MEMORY_CODE:-1}`` correctly enables Decision
    Memory on Desktop instead of being silently cleared to off). A bare
    ``${VAR}`` with no default is cleared, so downstream code (pip install,
    log-level parsing, auth) never receives a raw template literal.
    """
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CLAUDE_PACKAGE_SPEC",
        "KUMIHO_CLAUDE_DISCOVERY_USER_AGENT",
        "KUMIHO_MCP_LOG_LEVEL",
        "KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_WORKING_MEMORY_TTL",
        "KUMIHO_MEMORY_CODE",
        "KUMIHO_MEMORY_CODE_AUTOMINE",
        # Memory-reflex knobs. Declaring a name in .mcp.json is only half the
        # wiring: on Desktop the ${VAR:-default} arrives literally, so a name
        # missing from this tuple reaches the worker as a raw template string and
        # the declared default silently never applies.
        "KUMIHO_REFLEX",
        "KUMIHO_REFLEX_PREFETCH",
        "KUMIHO_REFLEX_LIMIT",
        "KUMIHO_REFLEX_MIN_INTERVAL_S",
        "KUMIHO_REFLEX_MAX_CHARS",
        "KUMIHO_REFLEX_TTL_S",
        "KUMIHO_REFLEX_FLOOR",
        "KUMIHO_REFLEX_CONSOLIDATE_FLOOR",
        "KUMIHO_REFLEX_SESSION_BUDGET_CHARS",
        "KUMIHO_REFLEX_STORE_PROMPT",
        "KUMIHO_ARTIFACT_MAX_BYTES",
    ):
        raw = (os.getenv(key, "") or "").strip()
        if not raw or not _looks_like_placeholder(raw):
            continue
        default = _placeholder_default(raw)
        if default is not None and default != "":
            os.environ[key] = default
            print(
                f"[kumiho-claude] Resolved {key} to its declared default.",
                file=sys.stderr,
            )
        else:
            os.environ.pop(key, None)
            print(
                f"[kumiho-claude] Cleared unresolved placeholder for {key}.",
                file=sys.stderr,
            )
    _publish_reflex_config()


_REFLEX_CONFIG_KEYS = (
    "KUMIHO_REFLEX",
    "KUMIHO_REFLEX_PREFETCH",
    "KUMIHO_REFLEX_LIMIT",
    "KUMIHO_REFLEX_MIN_INTERVAL_S",
    "KUMIHO_REFLEX_MAX_CHARS",
    "KUMIHO_REFLEX_TTL_S",
    "KUMIHO_REFLEX_FLOOR",
    "KUMIHO_REFLEX_CONSOLIDATE_FLOOR",
    "KUMIHO_REFLEX_SESSION_BUDGET_CHARS",
    "KUMIHO_REFLEX_STORE_PROMPT",
    "KUMIHO_ARTIFACT_MAX_BYTES",
    # Not a reflex knob, but memory-reflex.py names the buffer idle expiry in
    # its consolidation line and must say what is actually configured.
    "KUMIHO_WORKING_MEMORY_TTL",
)


def _publish_reflex_config() -> None:
    """Snapshot the resolved reflex knobs where the HOOKS can read them.

    Hooks inherit the CLI's environment, not this server's, so anything declared
    only in ``.mcp.json`` was invisible to them -- the declarations implied
    controls that silently did nothing. This runs right after placeholder
    sanitization, so the values written are the ones the author actually
    declared, including on Desktop where ``${VAR:-default}`` arrives literally.

    Best-effort: a failure here costs a knob, never a session.
    """
    # Codex has no Claude hooks and shares neither their control surface nor
    # their lifecycle. A Codex MCP start must not overwrite the snapshot that
    # concurrently running Claude hooks consume from the shared state dir.
    if os.getenv("KUMIHO_CLAUDE_HOST") == "codex":
        return
    try:
        values = {}
        for key in _REFLEX_CONFIG_KEYS:
            raw = (os.getenv(key, "") or "").strip()
            if raw and not _looks_like_placeholder(raw):
                values[key] = raw
        target = _state_dir() / "reflex.config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(target) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(values, fh, ensure_ascii=True)
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001 - never fail startup over a config snapshot
        pass


def _llm_base_url_is_safe(value: str) -> bool:
    """Allow plaintext model traffic only to the local machine."""
    if any(char in value for char in "\r\n\0"):
        return False
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    try:
        parsed.port
    except ValueError:
        return False
    host = parsed.hostname.rstrip(".").lower()
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if _ce_mode_enabled():
        return loopback
    if parsed.scheme.lower() == "https":
        return True
    return loopback


def _configure_llm_fallback() -> None:
    key_vars = (
        "KUMIHO_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_CLIENT_SECRET",
        "AZURE_OPENAI_API_KEY",
        "HF_TOKEN",
    )
    provider_route_vars = (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "AZURE_OPENAI_ENDPOINT",
    )
    ce_mode = _ce_mode_enabled()
    if ce_mode:
        # CE permits one canonical, validated loopback model route. Provider
        # aliases and key-only configurations can otherwise select their
        # vendors' public defaults behind the plugin's back.
        for key in provider_route_vars:
            os.environ.pop(key, None)
    base_url = (os.getenv("KUMIHO_LLM_BASE_URL", "") or "").strip()
    if ce_mode and (not base_url or _looks_like_placeholder(base_url)):
        for key in key_vars:
            os.environ.pop(key, None)
        os.environ["KUMIHO_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "kumiho-claude-fallback"
        os.environ["KUMIHO_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        return
    if (
        base_url
        and not _looks_like_placeholder(base_url)
        and not _llm_base_url_is_safe(base_url)
    ):
        # Never pair a real ambient provider key with a plaintext remote model
        # endpoint. Keep core memory tools available by pinning enrichment to
        # the same local dead-port fallback used by keyless mode.
        for key in key_vars:
            os.environ.pop(key, None)
        os.environ["KUMIHO_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "kumiho-claude-fallback"
        os.environ["KUMIHO_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        print(
            "[kumiho-claude] Ignored an unsafe LLM base URL: CE model "
            "endpoints must be loopback and Cloud remote endpoints must use "
            "HTTPS. Optional LLM enrichment is disabled; "
            "core memory tools remain available.",
            file=sys.stderr,
        )
        return
    if os.getenv("KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK", "").strip().lower() in {"1", "true", "yes"}:
        return
    if any(os.getenv(var, "").strip() for var in key_vars):
        return

    # A self-provided LLM base URL (e.g. a local Ollama / llama.cpp server, or a
    # CE deployment's own model endpoint) means a real model is reachable even
    # without an API key.  Honor it instead of pinning the dead-port fallback;
    # OpenAI-compatible local servers accept any non-empty key.
    if base_url and not _looks_like_placeholder(base_url):
        os.environ.setdefault("KUMIHO_LLM_PROVIDER", "openai")
        os.environ.setdefault("OPENAI_API_KEY", "kumiho-local-llm")
        # A base URL alone is not enough: the openai-provider model default
        # (gpt-4o) is almost never served by self-hosted endpoints, so the
        # enrichment calls would still fail — say so instead of implying done.
        model_hint = ""
        if not (os.getenv("KUMIHO_LLM_MODEL", "") or "").strip():
            model_hint = (
                " KUMIHO_LLM_MODEL is not set, so the provider default "
                "(gpt-4o) is used — self-hosted servers typically do not "
                "serve it; set KUMIHO_LLM_MODEL to a model your endpoint "
                "hosts (e.g. llama3.1)."
            )
        print(
            f"[kumiho-claude] Using self-provided LLM endpoint {base_url} for "
            f"summarization.{model_hint}",
            file=sys.stderr,
        )
        return

    os.environ.setdefault("KUMIHO_LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "kumiho-claude-fallback")
    os.environ.setdefault("KUMIHO_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    # Keyless is the plugin's default posture, not a degraded mode: the core
    # memory tools do their extraction in the in-loop agent and need no LLM.
    # Only the optional enrichment paths call one, and the placeholder config
    # above (dummy key + dead-port base URL) makes those calls fail fast
    # instead of hanging on a missing endpoint.
    mode = "CE mode" if ce_mode else "no API key detected"
    opt_in = (
        "set KUMIHO_LLM_BASE_URL to a loopback OpenAI-compatible endpoint AND "
        "KUMIHO_LLM_MODEL to a model it serves (e.g. local Ollama llama3.1)."
        if ce_mode
        else "set KUMIHO_LLM_BASE_URL to an OpenAI-compatible endpoint AND "
        "KUMIHO_LLM_MODEL to a model it serves, or set KUMIHO_LLM_API_KEY / "
        "OPENAI_API_KEY / ANTHROPIC_API_KEY."
    )
    print(
        f"[kumiho-claude] Keyless operation ({mode}): core memory tools "
        "(reflect, decompose, code_capture, code_why, recall) fully work "
        "without an LLM, and so does consolidation when the agent supplies "
        "the summary (kumiho_memory_consolidate summary=...). Optional LLM "
        "enrichment (automatic edge discovery, Dream State, summarizer-written "
        "consolidation summaries) is off — a fail-fast placeholder LLM config "
        "is pinned (dummy OPENAI_API_KEY, dead-port KUMIHO_LLM_BASE_URL). To "
        f"opt in, {opt_in}",
        file=sys.stderr,
    )


def main() -> int:
    _configure_host_diagnostics()
    parser = argparse.ArgumentParser(description="Run Kumiho MCP with auto-bootstrap.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Provision runtime and verify required modules, then exit.",
    )
    parser.add_argument(
        "--repair-desktop-entry",
        action="store_true",
        help="Rewrite a stale Claude Desktop server entry and exit. Spawned by "
             "the SessionStart hook, the only caller guaranteed to be the "
             "CURRENT plugin version -- the launcher a stale entry names cannot "
             "repair itself.",
    )
    parser.add_argument(
        "--provision",
        action="store_true",
        help="Build the runtime and exit. Used by the detached first-run "
             "provisioner, which is not on the host's MCP startup clock.",
    )
    args, passthrough = parser.parse_known_args()

    # Detached provisioning must transfer the parent's reservation within the
    # five-second handshake in _spawn_detached_provisioning.  In particular,
    # do not repair a Windows bin junction or re-read host config first: both
    # can be slow, and the parent already sanitized/hydrated the inherited
    # environment before it spawned this child.
    inherited_reservation = (
        os.getenv(_PROVISION_LOCK_TOKEN_ENV, "") or ""
    ).strip()
    if args.provision and inherited_reservation:
        os.environ[_SYNC_PROVISION_ENV] = "1"
        _ensure_runtime()
        print("[kumiho-claude] Provisioning complete.", file=sys.stderr)
        return 0

    # Both flags mean "do the work here"; neither is on a startup clock, so
    # neither may hand provisioning off to yet another detached child.
    if args.provision or args.self_test:
        os.environ[_SYNC_PROVISION_ENV] = "1"

    if args.repair_desktop_entry:
        _hydrate_env_from_local_config()
        _sanitize_placeholder_env_vars()
        if _desktop_bootstrap_enabled():
            _bootstrap_desktop_server_entries()
        return 0

    if args.provision:
        # Nothing else: no discovery, no Desktop config, no auth. Provisioning
        # must succeed for a user who has not authenticated yet.
        _hydrate_env_from_local_config()
        _sanitize_placeholder_env_vars()
        _ensure_runtime()
        print("[kumiho-claude] Provisioning complete.", file=sys.stderr)
        return 0

    _hydrate_env_from_local_config()
    _sanitize_placeholder_env_vars()
    # Validate/adopt/provision the shared runtime before auth or discovery.
    # _ensure_runtime holds the canonical ~/.kumiho/provision.lock while it
    # repairs the hook interpreter and migrates Claude's compatibility alias,
    # so hooks never see a half-mutated venv and another host cannot start pip
    # during the lock-set transition.
    python_path = _ensure_runtime()
    _normalize_host_session_id()
    # The Desktop server-entry self-heal is auth-independent (its token embed is
    # already guarded), so it runs in both modes — otherwise a CE user's stale
    # entry would never be repaired after a plugin upgrade.  Claude hosts only:
    # a vendored copy running under codex must never touch Desktop configs.
    if _desktop_bootstrap_enabled():
        _bootstrap_desktop_server_entries()
    cloud_mode = not _ce_mode_enabled()
    if cloud_mode:
        # Both hosts use the same narrow adapter. It pins the official control
        # plane, then delegates token loading, refresh, and discovery to the
        # Python SDK.
        os.environ.pop("KUMIHO_SERVER_ENDPOINT", None)
        os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
        os.environ["KUMIHO_PLUGIN_SHARED_HOME"] = str(_kumiho_home())
    else:
        ce_endpoint = _resolve_ce_endpoint()
        if ce_endpoint is None:  # defensive: cloud_mode is derived from the same predicate
            raise SystemExit("[kumiho-memory] CE mode has no configured endpoint.")
        _bootstrap_ce_endpoint(ce_endpoint)
    _configure_llm_fallback()

    if args.self_test:
        check_code = (
            "import importlib.util,sys;"
            "mods=('kumiho.mcp_server','kumiho_memory');"
            "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
            "print('ok' if not missing else 'missing:' + ','.join(missing));"
            "sys.exit(0 if not missing else 1)"
        )
        return _run(
            [str(python_path), "-I", "-c", check_code],
            check=False,
            timeout=PROBE_TIMEOUT_S,
            env=_provision_subprocess_env(),
        )

    if _ce_mode_enabled():
        ce_runner = Path(__file__).resolve().with_name("run_kumiho_ce.py")
        if not ce_runner.is_file():
            raise SystemExit(
                f"[kumiho-claude] CE runtime adapter is missing: {ce_runner}"
            )
        cmd = [str(python_path), "-I", str(ce_runner), *passthrough]
    else:
        cloud_runner = Path(__file__).resolve().with_name("run_kumiho_cloud.py")
        if not cloud_runner.is_file():
            raise SystemExit(
                f"[kumiho-memory] Cloud runtime adapter is missing: {cloud_runner}"
            )
        cmd = [str(python_path), "-I", str(cloud_runner), *passthrough]
    # On Windows os.execv spawns a new process and immediately exits the
    # current one.  Claude Desktop monitors the original PID; when it exits
    # the transport is closed ~85 ms later even though the child is still
    # running.  subprocess.run keeps this process alive (waiting) so Claude
    # Desktop never detects a premature exit.  stdin/stdout/stderr are
    # inherited by the child automatically (no redirection needed).
    if os.name == "nt":
        proc = subprocess.run(cmd, **_hidden_console_kwargs())
        return proc.returncode
    # On POSIX, true exec replaces the process image in-place (same PID).
    os.execv(str(python_path), cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
