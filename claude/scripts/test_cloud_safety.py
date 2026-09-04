#!/usr/bin/env python3
"""Offline security contracts for Claude Cloud routing."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent


def _launcher():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "kumiho_cloud_safety_launcher", SCRIPTS / "run_kumiho_mcp.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = _launcher()


def test_claude_manifest_marks_the_host_for_environment_isolation():
    manifest = json.loads(
        (Path(__file__).parents[1] / ".mcp.json").read_text(encoding="utf-8")
    )
    env = manifest["mcpServers"]["kumiho-memory"]["env"]
    assert env["KUMIHO_CLAUDE_HOST"] == "claude"


def test_claude_manifest_bootstraps_only_from_persistent_plugin_data():
    manifest = json.loads(
        (Path(__file__).parents[1] / ".mcp.json").read_text(encoding="utf-8")
    )
    server = manifest["mcpServers"]["kumiho-memory"]
    assert server["command"] == "${CLAUDE_PLUGIN_DATA}/venv/bin/python"
    assert "KUMIHO_PYTHON" not in server["command"]
    assert ":-python" not in server["command"]


class _Response:
    def __init__(self, body: dict):
        self._payload = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


@pytest.mark.parametrize(
    "value",
    [
        "http://control.example.test",
        "https://user:secret@control.example.test",
        "https://control.example.test?redirect=1",
        "https://control.example.test/#fragment",
        "file:///tmp/control",
    ],
)
def test_control_plane_rejects_unsafe_urls(monkeypatch, value):
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", value)
    with pytest.raises(RuntimeError):
        L._load_control_plane_url()


def test_control_plane_keeps_https_and_loopback_http(monkeypatch):
    monkeypatch.setenv(
        "KUMIHO_CONTROL_PLANE_URL", "https://private-control.example.test/base/"
    )
    assert L._load_control_plane_url() == "https://private-control.example.test/base"
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", "http://127.0.0.1:8181")
    assert L._load_control_plane_url() == "http://127.0.0.1:8181"


def test_project_local_claude_settings_preserve_token_and_ce_but_not_cloud_route(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    project_settings = tmp_path / ".claude" / "settings.json"
    project_settings.parent.mkdir()
    project_settings.write_text(
        json.dumps(
            {
                "env": {
                    "KUMIHO_AUTH_TOKEN": "project-token",
                    "KUMIHO_CLAUDE_MODE": "ce",
                    "KUMIHO_CLAUDE_SERVER_ENDPOINT": "grpcs://ce.example.test:7443",
                    "KUMIHO_CONTROL_PLANE_URL": "https://attacker.invalid",
                    "KUMIHO_TENANT_HINT": "attacker-tenant",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    candidates = L._candidate_settings_paths()
    assert project_settings in candidates

    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_TENANT_HINT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project_settings])
    L._hydrate_env_from_claude_settings()
    assert os.environ["KUMIHO_AUTH_TOKEN"] == "project-token"
    assert os.environ["KUMIHO_CLAUDE_MODE"] == "ce"
    assert os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] == (
        "grpcs://ce.example.test:7443"
    )
    assert "KUMIHO_CONTROL_PLANE_URL" not in os.environ
    assert "KUMIHO_TENANT_HINT" not in os.environ


def test_hostile_ambient_project_routing_is_removed_before_cached_token(
    tmp_path, monkeypatch
):
    """Host-preloaded project env must not route a shared bearer off-site."""
    account_home = tmp_path / "os-account-home"
    project_settings = tmp_path / "project" / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        json.dumps(
            {
                "env": {
                    "KUMIHO_CONTROL_PLANE_URL": "https://attacker.invalid",
                    "KUMIHO_TENANT_HINT": "attacker-tenant",
                    "KUMIHO_FIREBASE_ID_TOKEN": "attacker-token",
                    "KUMIHO_DISCOVERY_CACHE_FILE": str(tmp_path / "route.json"),
                    "KUMIHO_CONFIG_DIR": str(tmp_path / "attacker-runtime"),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "project-home"))
    monkeypatch.setattr(L, "_account_home", lambda: account_home)
    for key, value in {
        "KUMIHO_CONTROL_PLANE_URL": "https://attacker.invalid",
        "KUMIHO_CONTROL_PLANE_API_URL": "https://attacker.invalid/api",
        "KUMIHO_TENANT_HINT": "attacker-tenant",
        "KUMIHO_FIREBASE_API_KEY": "attacker-key",
        "KUMIHO_FIREBASE_ID_TOKEN": "attacker-token",
        "KUMIHO_FIREBASE_PROJECT_ID": "attacker-project",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN": "1",
        "KUMIHO_AUTO_CONFIGURE": "1",
        "KUMIHO_DISCOVERY_CACHE_FILE": str(tmp_path / "route.json"),
        "KUMIHO_WORKSPACE_ROOT": str(tmp_path),
        "KUMIHO_ENV_FILE": str(tmp_path / "attacker.env"),
        "KUMIHO_CONFIG_DIR": str(tmp_path / "attacker-runtime"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "attacker-claude"),
        "KUMIHO_CLAUDE_HOME": str(tmp_path / "attacker-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "attacker-xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "attacker-xdg-cache"),
        "APPDATA": str(tmp_path / "attacker-appdata"),
        "LOCALAPPDATA": str(tmp_path / "attacker-localappdata"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project_settings])
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    monkeypatch.setattr(L, "_load_bearer_token", lambda: "cached-user-token")

    L._hydrate_env_from_local_config()

    assert os.environ["KUMIHO_AUTH_TOKEN"] == "cached-user-token"
    assert L._load_control_plane_url() == "https://control.kumiho.cloud"
    restored = {"HOME", "USERPROFILE"}
    if os.name == "nt":
        restored.update({"HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"})
    for key in (*L._HOST_UNTRUSTED_CLOUD_ENV, *L._HOST_UNTRUSTED_PATH_ENV):
        if key in restored:
            continue
        assert key not in os.environ
    assert os.environ["HOME"] == str(account_home)
    assert os.environ["USERPROFILE"] == str(account_home)
    assert L._venv_dir() == account_home / ".kumiho" / "venv"
    assert L._cached_kumiho_auth_path() == (
        account_home / ".kumiho" / "kumiho_authentication.json"
    )
    assert all(
        account_home == path or account_home in path.parents
        for path in L._claude_desktop_config_paths()
    )


def test_user_global_routing_and_absolute_runtime_root_are_restored(
    tmp_path, monkeypatch
):
    project = tmp_path / "project" / ".claude" / "settings.json"
    user_global = tmp_path / "home" / ".claude" / "settings.local.json"
    project.parent.mkdir(parents=True)
    user_global.parent.mkdir(parents=True)
    project.write_text(
        json.dumps(
            {"env": {"KUMIHO_CONTROL_PLANE_URL": "https://attacker.invalid"}}
        ),
        encoding="utf-8",
    )
    trusted_root = (tmp_path / "trusted-kumiho").absolute()
    user_global.write_text(
        json.dumps(
            {
                "env": {
                    "KUMIHO_CONTROL_PLANE_URL": "https://private.example.test",
                    "KUMIHO_TENANT_HINT": "private-tenant",
                    "KUMIHO_CONFIG_DIR": str(trusted_root),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", "https://attacker.invalid")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "attacker-runtime"))
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project, user_global])
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    monkeypatch.setattr(L, "_load_bearer_token", lambda: "cached-user-token")

    L._hydrate_env_from_local_config()

    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://private.example.test"
    )
    assert os.environ["KUMIHO_TENANT_HINT"] == "private-tenant"
    assert os.environ["KUMIHO_CONFIG_DIR"] == str(trusted_root)
    assert L._venv_dir() == trusted_root / "venv"


def test_project_and_user_global_settings_compose_per_key(tmp_path, monkeypatch):
    project = tmp_path / "project" / ".claude" / "settings.local.json"
    user_global = tmp_path / "home" / ".claude" / "settings.json"
    project.parent.mkdir(parents=True)
    user_global.parent.mkdir(parents=True)
    project.write_text(
        json.dumps({"env": {"KUMIHO_AUTH_TOKEN": "project-token"}}),
        encoding="utf-8",
    )
    user_global.write_text(
        json.dumps(
            {
                "env": {
                    "KUMIHO_CONTROL_PLANE_URL": "https://private.example.test",
                    "KUMIHO_TENANT_HINT": "private-tenant",
                }
            }
        ),
        encoding="utf-8",
    )
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_TENANT_HINT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        L, "_candidate_settings_paths", lambda: [project, user_global]
    )
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )

    L._hydrate_env_from_claude_settings()

    assert os.environ["KUMIHO_AUTH_TOKEN"] == "project-token"
    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://private.example.test"
    )
    assert os.environ["KUMIHO_TENANT_HINT"] == "private-tenant"


def test_cloud_discovery_forces_tls_despite_hostile_override(monkeypatch):
    monkeypatch.setenv("KUMIHO_SERVER_USE_TLS", "false")
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "token")
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", raising=False)
    monkeypatch.setattr(L, "_discovery_token_candidates", lambda: ["token"])
    monkeypatch.setattr(
        L.urllib.request,
        "urlopen",
        lambda *_a, **_kw: _Response(
            {"region": {"grpc_authority": "grpcs://region.example.test:7443"}}
        ),
    )
    L._bootstrap_server_endpoint()
    assert os.environ["KUMIHO_SERVER_ENDPOINT"] == (
        "grpcs://region.example.test:7443"
    )
    assert os.environ["KUMIHO_SERVER_USE_TLS"] == "true"
    assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"


def test_cloud_discovery_rejects_plaintext_target(monkeypatch):
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "token")
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", raising=False)
    monkeypatch.setattr(L, "_discovery_token_candidates", lambda: ["token"])
    monkeypatch.setattr(
        L.urllib.request,
        "urlopen",
        lambda *_a, **_kw: _Response(
            {"region": {"grpc_authority": "grpc://region.example.test:7443"}}
        ),
    )
    with pytest.raises(RuntimeError, match="non-TLS"):
        L._bootstrap_server_endpoint()


def test_invalid_ce_endpoint_is_never_echoed(monkeypatch, capsys):
    secret_url = "https://user:password@ce.example.test:9190"
    monkeypatch.setenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", secret_url)
    assert L._resolve_ce_endpoint() == L.DEFAULT_CE_ENDPOINT
    output = capsys.readouterr().err
    assert "password" not in output
    assert secret_url not in output
