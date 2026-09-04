#!/usr/bin/env python3
"""Offline contracts for Claude hook state isolation and worker inheritance."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import state_home  # noqa: E402


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(
        "state_home_test_" + filename.replace("-", "_").removesuffix(".py"),
        SCRIPTS / filename,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mark_real_hook(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN_ROOT))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str((tmp_path / "plugin-data").absolute()))


def _default_state(account_home: Path) -> Path:
    if os.name == "nt":
        return account_home / "AppData" / "Local" / "kumiho-claude"
    return account_home / ".cache" / "kumiho-claude"


def test_real_hook_uses_absolute_user_global_state_not_project_paths(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    trusted_state = (tmp_path / "trusted-state").absolute()
    settings = account_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": str(trusted_state)}}),
        encoding="utf-8",
    )
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    for key, value in {
        "KUMIHO_CLAUDE_HOME": str(tmp_path / "project-state"),
        "HOME": str(tmp_path / "project-home"),
        "USERPROFILE": str(tmp_path / "project-profile"),
        "XDG_CONFIG_HOME": str(tmp_path / "project-xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "project-xdg-cache"),
        "APPDATA": str(tmp_path / "project-appdata"),
        "LOCALAPPDATA": str(tmp_path / "project-localappdata"),
        "KUMIHO_CLAUDE_HOST": "codex",
    }.items():
        monkeypatch.setenv(key, value)

    assert state_home.state_dir() == trusted_state
    child = state_home.secured_hook_child_env()
    assert child["KUMIHO_CLAUDE_HOST"] == "claude"
    assert child["KUMIHO_CLAUDE_HOME"] == str(trusted_state)
    assert child["HOME"] == str(account_home)
    assert child["USERPROFILE"] == str(account_home)
    assert "XDG_CONFIG_HOME" not in child
    assert "XDG_CACHE_HOME" not in child
    if os.name == "nt":
        assert child["APPDATA"] == str(account_home / "AppData" / "Roaming")
        assert child["LOCALAPPDATA"] == str(account_home / "AppData" / "Local")


@pytest.mark.parametrize(
    "value",
    [
        "relative",
        "../escape",
        "~/state",
        "${KUMIHO_CLAUDE_HOME}",
        "/absolute/${PROJECT}/state",
        "bad\npath",
        "bad\0path",
    ],
)
def test_real_hook_rejects_non_absolute_or_malformed_user_state(
    tmp_path, monkeypatch, value
):
    account_home = (tmp_path / "account").absolute()
    settings = account_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": value}}),
        encoding="utf-8",
    )
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "project-cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "project-local"))

    assert state_home.state_dir() == _default_state(account_home)


def test_real_hook_without_global_override_uses_native_account_default(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "project-cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "project-local"))

    assert state_home.state_dir() == _default_state(account_home)


def test_agent_shell_host_latch_uses_the_same_secure_state(tmp_path, monkeypatch):
    account_home = (tmp_path / "account").absolute()
    trusted_state = (tmp_path / "trusted-state").absolute()
    settings = account_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": str(trusted_state)}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)

    assert state_home.state_dir() == trusted_state


def test_direct_maintenance_keeps_legacy_environment_override(tmp_path, monkeypatch):
    for key in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "KUMIHO_CLAUDE_HOST"):
        monkeypatch.delenv(key, raising=False)
    direct_state = (tmp_path / "direct-state").absolute()
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(direct_state))

    assert state_home.state_dir() == direct_state


def test_all_detached_hook_children_receive_host_and_state_binding(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    trusted_state = (tmp_path / "trusted-state").absolute()
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    monkeypatch.setenv(
        "KUMIHO_CLAUDE_PACKAGE_SPEC", "https://attacker.invalid/evil.whl"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "attacker-python"))
    monkeypatch.setenv("https_proxy", "http://attacker.invalid:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "attacker-ca.pem"))
    monkeypatch.setenv("KUMIHO_LLM_BASE_URL", "https://attacker.invalid/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid/openai")
    monkeypatch.setenv("KUMIHO_MEMORY_CODE_AUTOMINE", "1")
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    monkeypatch.setattr(state_home, "_secure_host_state_dir", lambda: trusted_state)

    session = _load("session-bootstrap.py")
    capture = _load("code-capture-hook.py")
    observe = _load("reflex-observe.py")
    monkeypatch.setattr(observe.rs, "gate", lambda *_args, **_kwargs: True)
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return object()

    monkeypatch.setattr(session.subprocess, "Popen", fake_popen)
    session._repair_stale_desktop_entry()
    capture._spawn("code_ingest_worker.py", [str(tmp_path)])
    observe._spawn_prefetch({"cwd": str(tmp_path)}, "session-1")

    assert len(calls) == 3
    for argv, kwargs in calls:
        assert argv[1] == "-I"
        child = kwargs["env"]
        assert child["KUMIHO_CLAUDE_HOST"] == "claude"
        assert child["KUMIHO_CLAUDE_HOME"] == str(trusted_state)
        assert child["CLAUDE_PLUGIN_ROOT"] == str(PLUGIN_ROOT)
        assert "CLAUDE_PLUGIN_DATA" not in child
        assert "KUMIHO_CLAUDE_PACKAGE_SPEC" not in child
        assert "PYTHONPATH" not in child
        assert "https_proxy" not in child
        assert "REQUESTS_CA_BUNDLE" not in child
        assert "KUMIHO_LLM_BASE_URL" not in child
        assert "OPENAI_BASE_URL" not in child
        assert "KUMIHO_MEMORY_CODE_AUTOMINE" not in child
        assert child["HOME"] == str(account_home)


def test_session_repair_never_executes_a_forged_plugin_root(tmp_path, monkeypatch):
    attacker_root = (tmp_path / "attacker-plugin").absolute()
    attacker_launcher = attacker_root / "scripts" / "run_kumiho_mcp.py"
    attacker_launcher.parent.mkdir(parents=True)
    attacker_launcher.write_text("raise SystemExit('should never run')\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(attacker_root))
    session = _load("session-bootstrap.py")
    calls = []
    monkeypatch.setattr(
        session.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    session._repair_stale_desktop_entry()

    assert calls == []


def test_three_state_consumers_delegate_to_the_shared_resolver():
    for filename in (
        "session-bootstrap.py",
        "reflex_state.py",
        "code_capture_pending.py",
    ):
        source = (SCRIPTS / filename).read_text(encoding="utf-8")
        assert 'os.getenv("KUMIHO_CLAUDE_HOME"' not in source
        assert "state_home.state_dir()" in source


def test_artifact_hook_ignores_project_home_and_ambient_destination(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    trusted_artifacts = (tmp_path / "trusted-artifacts").absolute()
    settings = account_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_ARTIFACT_DIR": str(trusted_artifacts)}}),
        encoding="utf-8",
    )
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("KUMIHO_ARTIFACT_DIR", str(tmp_path / "project-artifacts"))

    assert state_home.artifact_dir() == trusted_artifacts


@pytest.mark.parametrize(
    "value",
    ["relative/artifacts", "${PROJECT}/artifacts", "//server/share", r"\\server\share"],
)
def test_artifact_hook_rejects_relative_placeholder_and_remote_overrides(
    tmp_path, monkeypatch, value
):
    account_home = (tmp_path / "account").absolute()
    settings = account_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_ARTIFACT_DIR": value}}), encoding="utf-8"
    )
    _mark_real_hook(monkeypatch, tmp_path)
    monkeypatch.setattr(state_home, "_native_account_home", lambda: account_home)
    monkeypatch.setenv("KUMIHO_ARTIFACT_DIR", str(tmp_path / "project-artifacts"))

    assert state_home.artifact_dir() == account_home / ".kumiho" / "artifacts"


def test_direct_artifact_maintenance_keeps_legacy_environment_override(
    tmp_path, monkeypatch
):
    for key in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "KUMIHO_CLAUDE_HOST"):
        monkeypatch.delenv(key, raising=False)
    direct = tmp_path / "relative-artifacts"
    monkeypatch.setenv("KUMIHO_ARTIFACT_DIR", str(direct))

    assert state_home.artifact_dir() == direct


def test_session_artifact_script_delegates_to_the_shared_resolver():
    source = (SCRIPTS / "save-session-artifact.py").read_text(encoding="utf-8")
    assert "state_home.artifact_dir()" in source
    assert 'os.getenv("KUMIHO_ARTIFACT_DIR"' not in source
