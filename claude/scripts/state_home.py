#!/usr/bin/env python3
"""Trusted runtime-state paths for Claude hooks and their workers.

Claude hooks inherit the host's merged environment, where project settings can
override user settings.  State shared with the MCP server must therefore come
from the operating-system account and exact user-global Claude settings, not
from ambient project-controlled HOME/path variables.  Direct maintenance runs
retain the historical environment override behavior.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _concrete(value: str) -> bool:
    text = (value or "").strip()
    return bool(
        text
        and not any(char in value for char in "\r\n\0")
        and "${" not in text
    )


def _running_as_claude_plugin() -> bool:
    """Recognize an installed hook or an explicit one-way secure latch.

    ``--claude-host`` uses the host marker when an agent drains the queue from
    its shell, where Claude does not provide ``CLAUDE_PLUGIN_ROOT``.  Forging
    this marker can only select the stricter OS-account path policy.
    """
    if (os.getenv("KUMIHO_CLAUDE_HOST", "") or "").strip().lower() == "claude":
        return True
    root = (os.getenv("CLAUDE_PLUGIN_ROOT", "") or "").strip()
    if not _concrete(root):
        return False
    try:
        return Path(root).resolve() == Path(__file__).resolve().parent.parent
    except (OSError, RuntimeError, ValueError):
        return False


def _native_account_home() -> "Path | None":
    """Resolve the OS account home without consulting ambient HOME values."""
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
    return home if home is not None and home.is_absolute() else None


def _trusted_user_state_home(account_home: Path) -> "Path | None":
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
        raw = env.get("KUMIHO_CLAUDE_HOME") if isinstance(env, dict) else None
        if not isinstance(raw, str) or not _concrete(raw):
            continue
        candidate = Path(raw.strip())
        if candidate.is_absolute():
            return candidate
    return None


def _trusted_user_artifact_dir(account_home: Path) -> "Path | None":
    """Return a local absolute artifact path from exact user-global settings.

    Session artifacts contain the raw conversation. A repository-controlled
    environment must not redirect them into the checkout or to a Windows UNC
    share merely by being opened in Claude.
    """
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
        raw = env.get("KUMIHO_ARTIFACT_DIR") if isinstance(env, dict) else None
        if not isinstance(raw, str) or not _concrete(raw):
            continue
        text = raw.strip()
        # Reject UNC/device paths on every platform. On POSIX a leading double
        # slash has implementation-defined network semantics, so it is rejected
        # for the same privacy reason.
        if text.startswith(("\\\\", "//")):
            continue
        candidate = Path(text)
        if candidate.is_absolute():
            return candidate
    return None


def _secure_host_state_dir() -> Path:
    account_home = _native_account_home()
    if account_home is not None:
        trusted = _trusted_user_state_home(account_home)
        if trusted is not None:
            return trusted
        if os.name == "nt":
            return account_home / "AppData" / "Local" / "kumiho-claude"
        return account_home / ".cache" / "kumiho-claude"

    # Fail closed on unusual platforms where native account lookup is absent.
    # Hook callers swallow state-write failures, and a plugin-local fallback is
    # preferable to trusting a project-provided HOME or plugin-data path.
    return Path(__file__).resolve().parent.parent / ".kumiho-state"


def state_dir() -> Path:
    """Return the shared Claude state dir for a hook or direct invocation."""
    if _running_as_claude_plugin():
        return _secure_host_state_dir()
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv(
            "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
        )
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def artifact_dir() -> Path:
    """Resolve the conversation-artifact directory with hook provenance.

    Automatic Claude hooks accept an override only from exact user-global
    settings. Direct maintenance invocations retain the historical ambient
    environment and preferences-cache behavior.
    """
    if _running_as_claude_plugin():
        account_home = _native_account_home()
        if account_home is not None:
            trusted = _trusted_user_artifact_dir(account_home)
            return trusted if trusted is not None else account_home / ".kumiho" / "artifacts"
        return Path(__file__).resolve().parent.parent / ".kumiho-artifacts"

    from_env = (os.getenv("KUMIHO_ARTIFACT_DIR", "") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    prefs_path = Path.home() / ".kumiho" / "agent_preferences.json"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        configured = prefs.get("artifact_dir", "") if isinstance(prefs, dict) else ""
        if isinstance(configured, str) and configured.strip():
            return Path(configured.strip()).expanduser()
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return Path.home() / ".kumiho" / "artifacts"


def secured_hook_child_env() -> dict[str, str]:
    """Copy the environment and bind detached children to Claude isolation."""
    env = dict(os.environ)
    env["KUMIHO_CLAUDE_HOST"] = "claude"
    env["KUMIHO_CLAUDE_HOME"] = str(_secure_host_state_dir())
    env["CLAUDE_PLUGIN_ROOT"] = str(Path(__file__).resolve().parent.parent)
    # A child can derive the persistent data directory from a cache install.
    # Never forward a project-overridable path into venv-alias maintenance.
    env.pop("CLAUDE_PLUGIN_DATA", None)
    untrusted = frozenset(key.upper() for key in (
        "KUMIHO_CLAUDE_PACKAGE_SPEC",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
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
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
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
    ))
    for actual in tuple(env):
        if actual.upper() in untrusted:
            env.pop(actual, None)
    account_home = _native_account_home()
    if account_home is not None:
        env["HOME"] = str(account_home)
        env["USERPROFILE"] = str(account_home)
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("XDG_CACHE_HOME", None)
        if os.name == "nt":
            drive, tail = os.path.splitdrive(str(account_home))
            env["HOMEDRIVE"] = drive
            env["HOMEPATH"] = tail or "\\"
            env["APPDATA"] = str(account_home / "AppData" / "Roaming")
            env["LOCALAPPDATA"] = str(account_home / "AppData" / "Local")
    else:
        for key in (
            "HOME",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
        ):
            env.pop(key, None)
    return env
