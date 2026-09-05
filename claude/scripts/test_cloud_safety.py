#!/usr/bin/env python3
"""Offline security contracts for Claude Cloud routing."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
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


def _cloud_adapter():
    spec = importlib.util.spec_from_file_location(
        "kumiho_cloud_sdk_adapter_test", SCRIPTS / "run_kumiho_cloud.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLOUD = _cloud_adapter()


@pytest.fixture(autouse=True)
def _isolate_machine_user_environment():
    """Unit tests never inherit the developer/runner's real persisted env."""
    original_environment = os.environ.copy()
    original_reader = L._trusted_persisted_user_environment
    L._trusted_persisted_user_environment = lambda: {}
    try:
        yield
    finally:
        # Launcher/adapter code intentionally edits os.environ directly. Keep
        # those production mutations from becoming another test's input.
        L._trusted_persisted_user_environment = original_reader
        os.environ.clear()
        os.environ.update(original_environment)


def test_claude_manifest_marks_the_host_for_environment_isolation():
    manifest = json.loads(
        (Path(__file__).parents[1] / ".mcp.json").read_text(encoding="utf-8")
    )
    env = manifest["mcpServers"]["kumiho-memory"]["env"]
    assert env["KUMIHO_CLAUDE_HOST"] == "claude"


def test_claude_manifest_launches_from_the_plugin_data_venv_alias():
    """The MCP command must be an absolute interpreter, never PATH-resolved.

    0.21.0 shipped ``"command": "python"``; on Windows hosts without ``python``
    on PATH (the common case -- the App Execution Alias directory is not on
    PATH for every account) the server never started, while every hook in
    hooks.json already launched from the ``${CLAUDE_PLUGIN_DATA}/venv``
    alias. CONNECTORS.md documents that alias as the command; this keeps the
    manifest at it.
    """
    manifest = json.loads(
        (Path(__file__).parents[1] / ".mcp.json").read_text(encoding="utf-8")
    )
    server = manifest["mcpServers"]["kumiho-memory"]
    assert server["command"] == "${CLAUDE_PLUGIN_DATA}/venv/bin/python"
    assert server["args"][:1] == ["-I"]
    assert "KUMIHO_PYTHON" not in server["command"]

    hooks = json.loads(
        (Path(__file__).parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    hook_commands = {
        h["command"]
        for group in hooks["hooks"].values()
        for entry in group
        for h in entry["hooks"]
    }
    assert hook_commands == {"${CLAUDE_PLUGIN_DATA}/venv/bin/pythonw"}


def test_plugin_local_env_example_does_not_own_cloud_auth_or_routing():
    example = (Path(__file__).parents[1] / ".env.local.example").read_text(
        encoding="utf-8"
    )

    assert "KUMIHO_AUTH_TOKEN=" not in example
    assert "KUMIHO_CONTROL_PLANE_URL=" not in example
    assert "KUMIHO_TENANT_HINT=" not in example
    assert "https://control.kumiho.cloud" in example
    assert "kumiho-auth login" in example
    assert "kumiho-cli login" in example


def test_active_cloud_adapter_pins_official_discovery_and_preserves_explicit_token(
    monkeypatch,
):
    monkeypatch.delenv(CLOUD.SHARED_HOME_HANDOFF_ENV, raising=False)
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "explicit-api-token")
    for key in CLOUD._CLOUD_ROUTE_ENV:
        monkeypatch.setenv(key, "https://attacker.invalid")
    monkeypatch.setenv("KUMIHO_CODEX_CONFIG_ROOT", "attacker-root")

    shared_root = CLOUD._prepare_environment()

    assert shared_root == Path.home() / ".kumiho"
    assert os.environ["KUMIHO_AUTH_TOKEN"] == "explicit-api-token"
    assert os.environ["KUMIHO_CONFIG_DIR"] == str(shared_root)
    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert os.environ["KUMIHO_DISCOVERY_CACHE_FILE"] == str(
        shared_root / "official-cloud" / "discovery-cache.json"
    )
    assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"
    assert "KUMIHO_CODEX_CONFIG_ROOT" not in os.environ


def test_cloud_adapter_preserves_explicit_token_and_skips_login_cache(
    monkeypatch,
):
    calls = []
    configured = []
    client = object()
    fake_kumiho = types.ModuleType("kumiho")

    def client_from_discovery(**kwargs):
        calls.append(kwargs)
        return client

    fake_kumiho.client_from_discovery = client_from_discovery
    fake_kumiho.configure_default_client = configured.append
    monkeypatch.setitem(sys.modules, "kumiho", fake_kumiho)
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "explicit-api-token")
    CLOUD._prepare_environment()

    assert CLOUD._configure_cloud(force_refresh=True)
    expected = {
        "id_token": None,
        "control_plane_url": CLOUD.OFFICIAL_CONTROL_PLANE_URL,
        "cache_path": os.environ["KUMIHO_DISCOVERY_CACHE_FILE"],
        "force_refresh": True,
    }
    assert calls == [expected]
    assert configured == [client]
    assert fake_kumiho.auto_configure_from_discovery() is client
    assert calls == [expected, {**expected, "force_refresh": False}]
    assert configured == [client, client]


def test_cloud_adapter_refreshes_sdk_login_before_cached_discovery(monkeypatch):
    events = []
    client = object()
    fake_kumiho = types.ModuleType("kumiho")
    fake_kumiho.__path__ = []
    fake_auth = types.ModuleType("kumiho.auth_cli")

    def client_from_discovery(**kwargs):
        events.append(("discover", kwargs))
        return client

    def ensure_token(*, interactive):
        events.append(("ensure", interactive))
        return "ignored-by-plugin"

    fake_kumiho.client_from_discovery = client_from_discovery
    fake_kumiho.configure_default_client = lambda configured: None
    fake_auth.ensure_token = ensure_token
    monkeypatch.setitem(sys.modules, "kumiho", fake_kumiho)
    monkeypatch.setitem(sys.modules, "kumiho.auth_cli", fake_auth)
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    CLOUD._prepare_environment()

    assert CLOUD._configure_cloud()
    assert [event[0] for event in events] == ["ensure", "discover"]
    attempts = [value for name, value in events if name == "discover"]
    assert len(attempts) == 1
    assert all(call["id_token"] is None for call in attempts)
    assert all(
        call["control_plane_url"] == CLOUD.OFFICIAL_CONTROL_PLANE_URL
        for call in attempts
    )


def test_cloud_adapter_refresh_failure_installs_fail_closed_client(monkeypatch):
    configured = []
    attempts = []
    client = object()
    fake_kumiho = types.ModuleType("kumiho")
    fake_kumiho.__path__ = []
    fake_auth = types.ModuleType("kumiho.auth_cli")

    def client_from_discovery(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return client
        raise RuntimeError("route refresh unavailable")

    fake_kumiho.client_from_discovery = client_from_discovery
    fake_kumiho.configure_default_client = configured.append
    fake_auth.ensure_token = lambda *, interactive: None
    monkeypatch.setitem(sys.modules, "kumiho", fake_kumiho)
    monkeypatch.setitem(sys.modules, "kumiho.auth_cli", fake_auth)
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    CLOUD._prepare_environment()

    assert CLOUD._configure_cloud()
    with pytest.raises(RuntimeError, match="Cloud discovery refresh failed"):
        fake_kumiho.auto_configure_from_discovery()

    assert len(attempts) == 2
    assert configured[0] is client
    assert isinstance(configured[-1], CLOUD._CloudUnavailableClient)


def test_cloud_adapter_failure_disables_ce_fallback_and_names_sdk_logins(
    monkeypatch, capsys
):
    configured = []
    fake_kumiho = types.ModuleType("kumiho")
    fake_kumiho.__path__ = []
    fake_auth = types.ModuleType("kumiho.auth_cli")

    def unavailable(**_kwargs):
        raise RuntimeError("not authenticated")

    def login_unavailable(*, interactive):
        assert interactive is False
        raise RuntimeError("login unavailable")

    fake_kumiho.client_from_discovery = unavailable
    fake_kumiho.configure_default_client = configured.append
    fake_auth.ensure_token = login_unavailable
    monkeypatch.setitem(sys.modules, "kumiho", fake_kumiho)
    monkeypatch.setitem(sys.modules, "kumiho.auth_cli", fake_auth)
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    CLOUD._prepare_environment()

    assert not CLOUD._configure_cloud()
    assert len(configured) == 1
    assert isinstance(configured[0], CLOUD._CloudUnavailableClient)
    with pytest.raises(RuntimeError, match="Cloud is not configured"):
        configured[0].memory_recall()
    with pytest.raises(RuntimeError, match="Cloud is not configured"):
        fake_kumiho.auto_configure_from_discovery()
    output = capsys.readouterr().err
    assert "KUMIHO_AUTH_TOKEN" in output
    assert "kumiho-auth login" in output
    assert "kumiho-cli login" in output


def test_cloud_script_target_injects_adapter_bound_global(monkeypatch):
    captured = {}
    monkeypatch.setenv(CLOUD.BACKEND_BOUND_SENTINEL, "hostile-env-value")
    monkeypatch.setattr(
        CLOUD.runpy,
        "run_path",
        lambda script, **kwargs: captured.update(
            script=script,
            kwargs=kwargs,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_kumiho_cloud.py", "--script", "ingest-skills.py", "--dry-run"],
    )

    CLOUD._run_target()

    assert captured == {
        "script": "ingest-skills.py",
        "kwargs": {
            "run_name": "__main__",
            "init_globals": {CLOUD.BACKEND_BOUND_SENTINEL: True},
        },
    }
    assert sys.argv == ["ingest-skills.py", "--dry-run"]


def test_cloud_adapter_never_runs_an_auxiliary_target_after_auth_failure(
    monkeypatch,
):
    calls = []
    configure_calls = []
    monkeypatch.setattr(CLOUD, "_prepare_environment", lambda: Path.home())
    monkeypatch.setattr(
        CLOUD,
        "_configure_cloud",
        lambda **kwargs: configure_calls.append(kwargs) or False,
    )
    monkeypatch.setattr(CLOUD, "_run_target", lambda: calls.append("target"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_kumiho_cloud.py", "--script", "ingest-skills.py"],
    )

    with pytest.raises(SystemExit) as exc:
        CLOUD.main()

    assert exc.value.code == 1
    assert calls == []
    assert configure_calls == [{"force_refresh": True}]


def test_cloud_adapter_still_starts_mcp_tools_with_a_fail_closed_guard(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(CLOUD, "_prepare_environment", lambda: Path.home())
    monkeypatch.setattr(CLOUD, "_configure_cloud", lambda **_kwargs: False)
    monkeypatch.setattr(CLOUD, "_run_target", lambda: calls.append("mcp"))
    monkeypatch.setattr(sys, "argv", ["run_kumiho_cloud.py"])

    CLOUD.main()

    assert calls == ["mcp"]


@pytest.mark.parametrize(
    "route_key",
    ["KUMIHO_CONTROL_PLANE_URL", "KUMIHO_CONTROL_PLANE_API_URL"],
)
def test_project_settings_reject_project_token_and_preserve_loopback_ce_but_drop_route(
    tmp_path, monkeypatch, route_key
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
                    "KUMIHO_CLAUDE_SERVER_ENDPOINT": "grpcs://127.0.0.1:7443",
                    "KUMIHO_LLM_BASE_URL": "https://attacker.invalid/v1",
                    "OPENAI_BASE_URL": "https://attacker.invalid/openai",
                    "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
                    route_key: "https://attacker.invalid",
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
        "KUMIHO_LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "KUMIHO_MEMORY_CODE_AUTOMINE",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project_settings])
    L._hydrate_env_from_claude_settings()
    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    assert os.environ["KUMIHO_CLAUDE_MODE"] == "ce"
    assert os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] == (
        "grpcs://127.0.0.1:7443"
    )
    assert "KUMIHO_LLM_BASE_URL" not in os.environ
    assert "OPENAI_BASE_URL" not in os.environ
    assert "KUMIHO_MEMORY_CODE_AUTOMINE" not in os.environ
    assert "KUMIHO_CONTROL_PLANE_URL" not in os.environ
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert "KUMIHO_TENANT_HINT" not in os.environ


def test_hostile_ambient_routes_are_removed_without_replacing_explicit_token(
    tmp_path, monkeypatch
):
    """Host-preloaded routes cannot redirect an explicit SDK credential."""
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
    # Several launcher tests intentionally mutate the process environment
    # directly. Clear every asserted route first so this test supplies all of
    # its own hostile inputs instead of inheriting one from an earlier test.
    for key in (
        *L._HOST_UNTRUSTED_CLOUD_ENV,
        *L._HOST_UNTRUSTED_PATH_ENV,
        *L._HOST_UNTRUSTED_PROVISION_ENV,
        *L._HOST_UNTRUSTED_TRANSPORT_ENV,
        *L._HOST_UNTRUSTED_DATA_ROUTE_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
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
        "CLAUDE_PLUGIN_ROOT": str(tmp_path / "attacker-root"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "attacker-data"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "attacker-claude"),
        "KUMIHO_CLAUDE_HOME": str(tmp_path / "attacker-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "attacker-xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "attacker-xdg-cache"),
        "APPDATA": str(tmp_path / "attacker-appdata"),
        "LOCALAPPDATA": str(tmp_path / "attacker-localappdata"),
        "PYTHONPATH": str(tmp_path / "attacker-python"),
        "PYTHONHOME": str(tmp_path / "attacker-python-home"),
        "KUMIHO_CLAUDE_PACKAGE_SPEC": "https://attacker.invalid/evil.whl",
        "HTTP_PROXY": "http://attacker.invalid:8080",
        "https_proxy": "http://attacker.invalid:8081",
        "REQUESTS_CA_BUNDLE": str(tmp_path / "attacker-ca.pem"),
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": str(tmp_path / "grpc-roots.pem"),
        "KUMIHO_SERVER_AUTHORITY": "attacker.invalid",
        "KUMIHO_SERVER_CA_FILE": str(tmp_path / "server-ca.pem"),
        "KUMIHO_LLM_BASE_URL": "https://attacker.invalid/v1",
        "OPENAI_BASE_URL": "https://attacker.invalid/openai",
        "ANTHROPIC_BASE_URL": "https://attacker.invalid/anthropic",
        "AZURE_OPENAI_ENDPOINT": "https://attacker.invalid/azure",
        "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "custom-route-token")
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project_settings])
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    L._hydrate_env_from_local_config()

    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    restored = {"HOME", "USERPROFILE"}
    if os.name == "nt":
        restored.update({"HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"})
    for key in (
        *L._HOST_UNTRUSTED_CLOUD_ENV,
        *L._HOST_UNTRUSTED_PATH_ENV,
        *L._HOST_UNTRUSTED_PROVISION_ENV,
        *L._HOST_UNTRUSTED_TRANSPORT_ENV,
        *L._HOST_UNTRUSTED_DATA_ROUTE_ENV,
    ):
        if key in restored:
            continue
        assert key not in os.environ
    assert "https_proxy" not in os.environ
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


def test_official_ambient_token_is_not_trusted_in_claude_host_isolation(tmp_path, monkeypatch):
    account_home = tmp_path / "os-account-home"
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "official-cloud-token")
    monkeypatch.setenv(
        "KUMIHO_CONTROL_PLANE_URL", "https://control.kumiho.cloud/base/"
    )
    monkeypatch.setenv(
        "KUMIHO_CONTROL_PLANE_API_URL", "https://control.kumiho.cloud:443/api"
    )
    monkeypatch.setattr(L, "_account_home", lambda: account_home)
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [])
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    L._hydrate_env_from_local_config()

    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    assert "KUMIHO_CONTROL_PLANE_URL" not in os.environ
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ


@pytest.mark.parametrize(
    "route_key",
    ["KUMIHO_CONTROL_PLANE_URL", "KUMIHO_CONTROL_PLANE_API_URL"],
)
@pytest.mark.parametrize(
    "route_value",
    [
        "https://private.example.test",
        "${PRIVATE_CONTROL:-https://private.example.test}",
    ],
)
def test_dotenv_explicit_token_survives_while_custom_route_is_dropped(
    tmp_path, monkeypatch, route_key, route_value
):
    dotenv = tmp_path / ".env.local"
    dotenv.write_text(
        "KUMIHO_AUTH_TOKEN=custom-route-token\n"
        f"{route_key}={route_value}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_URL", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_API_URL", raising=False)

    L._read_dotenv_file(dotenv)

    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    assert route_key not in os.environ


@pytest.mark.parametrize(
    "route_key",
    ["KUMIHO_CONTROL_PLANE_URL", "KUMIHO_CONTROL_PLANE_API_URL"],
)
@pytest.mark.parametrize(
    "route_value",
    [
        "https://private.example.test",
        "${PRIVATE_CONTROL:-https://private.example.test}",
    ],
)
def test_plugin_mcp_explicit_token_survives_while_custom_route_is_dropped(
    tmp_path, monkeypatch, route_key, route_value
):
    mcp = tmp_path / ".mcp.json"
    mcp.write_text(
        json.dumps({
            "mcpServers": {
                "kumiho-memory": {
                    "env": {
                        "KUMIHO_AUTH_TOKEN": "custom-route-token",
                        route_key: route_value,
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_URL", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_API_URL", raising=False)
    monkeypatch.setattr(L, "_plugin_root", lambda: tmp_path)

    L._hydrate_env_from_plugin_mcp()

    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    assert route_key not in os.environ


def test_active_cloud_adapter_overrides_direct_custom_cloud_route(monkeypatch):
    monkeypatch.delenv("KUMIHO_CLAUDE_HOST", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_API_URL", raising=False)
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "private-token")
    monkeypatch.setenv(
        "KUMIHO_CONTROL_PLANE_URL", "https://private.example.test"
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy.example.test:8080")
    monkeypatch.setenv("KUMIHO_LLM_BASE_URL", "https://private-llm.example.test/v1")
    monkeypatch.setenv("KUMIHO_MEMORY_CODE_AUTOMINE", "1")
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [])
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    L._hydrate_env_from_local_config()
    os.environ[CLOUD.SHARED_HOME_HANDOFF_ENV] = str(L._kumiho_home())
    CLOUD._prepare_environment()

    assert os.environ["KUMIHO_AUTH_TOKEN"] == "private-token"
    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert os.environ["HTTPS_PROXY"] == "http://private-proxy.example.test:8080"
    assert os.environ["KUMIHO_LLM_BASE_URL"] == (
        "https://private-llm.example.test/v1"
    )
    assert os.environ["KUMIHO_MEMORY_CODE_AUTOMINE"] == "1"


def test_user_global_runtime_roots_survive_while_cloud_routes_are_pinned(
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
    trusted_state = (tmp_path / "trusted-state").absolute()
    trusted_ca = (tmp_path / "trusted-ca.pem").absolute()
    user_global.write_text(
        json.dumps(
            {
                "env": {
                    "KUMIHO_AUTH_TOKEN": "trusted-user-token",
                    "KUMIHO_CONTROL_PLANE_URL": "https://private.example.test",
                    "KUMIHO_TENANT_HINT": "private-tenant",
                    "KUMIHO_FIREBASE_API_KEY": "private-firebase-key",
                    "KUMIHO_FIREBASE_PROJECT_ID": "private-firebase-project",
                    "KUMIHO_CONFIG_DIR": str(trusted_root),
                    "KUMIHO_CLAUDE_HOME": str(trusted_state),
                    "KUMIHO_CLAUDE_PACKAGE_SPEC": "kumiho[mcp]==9.9.9",
                    "HTTPS_PROXY": "http://trusted-proxy.example.test:8080",
                    "REQUESTS_CA_BUNDLE": str(trusted_ca),
                    "KUMIHO_SERVER_AUTHORITY": "private-ce.example.test",
                    "KUMIHO_SERVER_CA_FILE": str(trusted_ca),
                    "KUMIHO_LLM_BASE_URL": "https://trusted-llm.example.test/v1",
                    "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", "https://attacker.invalid")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "attacker-runtime"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "attacker-state"))
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project, user_global])
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    L._hydrate_env_from_local_config()
    assert L._kumiho_home() == trusted_root
    os.environ[CLOUD.SHARED_HOME_HANDOFF_ENV] = str(L._kumiho_home())
    CLOUD._prepare_environment()

    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert "KUMIHO_TENANT_HINT" not in os.environ
    assert "KUMIHO_FIREBASE_API_KEY" not in os.environ
    assert "KUMIHO_FIREBASE_PROJECT_ID" not in os.environ
    assert os.environ["KUMIHO_CLAUDE_PACKAGE_SPEC"] == "kumiho[mcp]==9.9.9"
    assert os.environ["HTTPS_PROXY"] == "http://trusted-proxy.example.test:8080"
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(trusted_ca)
    assert "KUMIHO_SERVER_AUTHORITY" not in os.environ
    assert "KUMIHO_SERVER_CA_FILE" not in os.environ
    assert os.environ["KUMIHO_LLM_BASE_URL"] == (
        "https://trusted-llm.example.test/v1"
    )
    assert os.environ["KUMIHO_MEMORY_CODE_AUTOMINE"] == "1"
    assert os.environ["KUMIHO_AUTH_TOKEN"] == "trusted-user-token"
    assert os.environ["KUMIHO_CONFIG_DIR"] == str(trusted_root)
    assert os.environ["KUMIHO_CLAUDE_HOME"] == str(trusted_state)
    assert L._venv_dir() == trusted_root / "venv"
    assert L._state_dir() == trusted_state


def test_active_adapter_discards_user_global_split_cloud_urls(
    tmp_path, monkeypatch
):
    user_global = tmp_path / "home" / ".claude" / "settings.json"
    user_global.parent.mkdir(parents=True)
    user_global.write_text(
        json.dumps({"env": {
            "KUMIHO_CONTROL_PLANE_URL": "https://discovery.example.test",
            "KUMIHO_CONTROL_PLANE_API_URL": "https://auth.example.test",
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_URL", raising=False)
    monkeypatch.delenv("KUMIHO_CONTROL_PLANE_API_URL", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [user_global])
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )

    L._hydrate_env_from_claude_settings()
    os.environ[CLOUD.SHARED_HOME_HANDOFF_ENV] = str(L._kumiho_home())
    CLOUD._prepare_environment()

    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ


def test_project_package_spec_and_python_bootstrap_values_are_never_hydrated(
    tmp_path, monkeypatch
):
    project = tmp_path / ".claude" / "settings.json"
    project.parent.mkdir()
    project.write_text(
        json.dumps({"env": {
            "KUMIHO_CLAUDE_PACKAGE_SPEC": "https://attacker.invalid/evil.whl",
            "PYTHONPATH": str(tmp_path / "attacker-python"),
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project])
    monkeypatch.setattr(L, "_is_user_global_claude_setting", lambda _path: False)

    L._hydrate_env_from_claude_settings()

    assert "KUMIHO_CLAUDE_PACKAGE_SPEC" not in os.environ
    assert "PYTHONPATH" not in os.environ


def test_host_provisioning_scrubs_package_index_proxy_and_python_startup(
    monkeypatch
):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    hostile = {
        "PIP_INDEX_URL": "https://attacker.invalid/simple",
        "PIP_EXTRA_INDEX_URL": "https://attacker.invalid/extra",
        "PIP_CONFIG_FILE": "attacker-pip.ini",
        "PIPENV_PYPI_MIRROR": "https://attacker.invalid/pipenv",
        "UV_INDEX_URL": "https://attacker.invalid/uv",
        "HTTPS_PROXY": "http://attacker.invalid:8080",
        "REQUESTS_CA_BUNDLE": "attacker-ca.pem",
        "PYTHONPATH": "attacker-python",
        "PYTHONSTARTUP": "attacker-startup.py",
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)

    child = L._provision_subprocess_env()

    assert all(key not in child for key in hostile)


def test_relative_user_global_state_home_is_rejected(tmp_path, monkeypatch):
    user_global = tmp_path / "home" / ".claude" / "settings.json"
    user_global.parent.mkdir(parents=True)
    user_global.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": "relative-state"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_CLAUDE_HOME", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [user_global])
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )

    L._hydrate_env_from_claude_settings()

    assert "KUMIHO_CLAUDE_HOME" not in os.environ


def test_embedded_placeholder_in_absolute_user_global_state_home_is_rejected(
    tmp_path, monkeypatch
):
    user_global = tmp_path / "home" / ".claude" / "settings.json"
    user_global.parent.mkdir(parents=True)
    placeholder_path = str((tmp_path / "state").absolute() / "${PROJECT}")
    user_global.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": placeholder_path}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_CLAUDE_HOME", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [user_global])
    monkeypatch.setattr(
        L, "_is_user_global_claude_setting", lambda path: path == user_global
    )

    L._hydrate_env_from_claude_settings()

    assert "KUMIHO_CLAUDE_HOME" not in os.environ


def test_state_home_override_does_not_move_the_shared_venv(tmp_path, monkeypatch):
    account_home = (tmp_path / "account").absolute()
    trusted_state = (tmp_path / "trusted-state").absolute()
    user_global = account_home / ".claude" / "settings.json"
    user_global.parent.mkdir(parents=True)
    user_global.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": str(trusted_state)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "project-runtime"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))
    monkeypatch.setattr(L, "_account_home", lambda: account_home)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [user_global])
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    monkeypatch.setattr(L, "_load_bearer_token", lambda: None)

    L._hydrate_env_from_local_config()

    assert L._state_dir() == trusted_state
    assert L._venv_dir() == account_home / ".kumiho" / "venv"


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
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
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
    os.environ[CLOUD.SHARED_HOME_HANDOFF_ENV] = str(L._kumiho_home())
    CLOUD._prepare_environment()

    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert "KUMIHO_TENANT_HINT" not in os.environ


def test_project_loopback_llm_survives_but_automine_does_not(
    tmp_path, monkeypatch
):
    project = tmp_path / ".claude" / "settings.json"
    project.parent.mkdir()
    project.write_text(
        json.dumps({"env": {
            "KUMIHO_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.delenv("KUMIHO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("KUMIHO_MEMORY_CODE_AUTOMINE", raising=False)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [project])
    monkeypatch.setattr(L, "_is_user_global_claude_setting", lambda _path: False)

    L._hydrate_env_from_claude_settings()

    assert os.environ["KUMIHO_LLM_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert "KUMIHO_MEMORY_CODE_AUTOMINE" not in os.environ


def test_persisted_os_user_routes_survive_ambiguous_host_environment(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    trusted_ca = (tmp_path / "trusted-ca.pem").absolute()
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("HTTPS_PROXY", "http://project-proxy.invalid:8080")
    monkeypatch.setenv("KUMIHO_LLM_BASE_URL", "https://project.invalid/v1")
    monkeypatch.setenv("KUMIHO_MEMORY_CODE_AUTOMINE", "1")
    monkeypatch.setattr(L, "_account_home", lambda: account_home)
    monkeypatch.setattr(L, "_trusted_persisted_user_environment", lambda: {
        "HTTPS_PROXY": "http://user-proxy.example.test:8080",
        "REQUESTS_CA_BUNDLE": str(trusted_ca),
        "KUMIHO_LLM_BASE_URL": "https://user-llm.example.test/v1",
        "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
    })
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [])
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    monkeypatch.setattr(L, "_load_bearer_token", lambda: None)

    L._hydrate_env_from_local_config()

    assert os.environ["HTTPS_PROXY"] == "http://user-proxy.example.test:8080"
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(trusted_ca)
    assert os.environ["KUMIHO_LLM_BASE_URL"] == "https://user-llm.example.test/v1"
    assert os.environ["KUMIHO_MEMORY_CODE_AUTOMINE"] == "1"


def test_codex_honors_user_global_shared_package_spec(tmp_path, monkeypatch):
    account_home = (tmp_path / "account").absolute()
    settings = account_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {
            "KUMIHO_CLAUDE_PACKAGE_SPEC": "kumiho[mcp]==9.9.9",
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    monkeypatch.setenv("KUMIHO_CLAUDE_PACKAGE_SPEC", L.DEFAULT_PACKAGE_SPEC)
    monkeypatch.setattr(L, "_account_home", lambda: account_home)

    L._hydrate_env_from_local_config()

    assert os.environ["KUMIHO_CLAUDE_PACKAGE_SPEC"] == "kumiho[mcp]==9.9.9"


def test_main_clears_host_paths_before_publishing_reflex_config(
    tmp_path, monkeypatch
):
    account_home = (tmp_path / "account").absolute()
    hostile_state = (tmp_path / "project-state").absolute()
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(hostile_state))
    monkeypatch.setenv("KUMIHO_REFLEX", "1")
    monkeypatch.setattr(L, "_account_home", lambda: account_home)
    monkeypatch.setattr(L, "_hydrate_env_from_dotenv", lambda: None)
    monkeypatch.setattr(L, "_candidate_settings_paths", lambda: [])
    monkeypatch.setattr(L, "_hydrate_env_from_plugin_mcp", lambda: None)
    monkeypatch.setattr(L, "_load_bearer_token", lambda: None)
    monkeypatch.setattr(L, "_desktop_bootstrap_enabled", lambda: False)
    monkeypatch.setattr(sys, "argv", ["run_kumiho_mcp.py", "--repair-desktop-entry"])

    assert L.main() == 0

    assert not hostile_state.exists()
    expected = (
        account_home / "AppData" / "Local" / "kumiho-claude"
        if os.name == "nt"
        else account_home / ".cache" / "kumiho-claude"
    )
    assert (expected / "reflex.config.json").is_file()


def test_active_cloud_launch_enters_the_sdk_adapter_with_shared_home(
    tmp_path, monkeypatch
):
    python_path = tmp_path / "venv" / "python"
    shared_home = (tmp_path / "shared-kumiho").absolute()
    calls = []

    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(shared_home))
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "explicit-cloud-token")
    monkeypatch.setattr(L, "_hydrate_env_from_local_config", lambda: None)
    monkeypatch.setattr(L, "_sanitize_placeholder_env_vars", lambda: None)
    monkeypatch.setattr(L, "_ensure_runtime", lambda: python_path)
    monkeypatch.setattr(L, "_normalize_host_session_id", lambda: None)
    monkeypatch.setattr(L, "_desktop_bootstrap_enabled", lambda: False)
    monkeypatch.setattr(L, "_ce_mode_enabled", lambda: False)
    monkeypatch.setattr(L, "_configure_llm_fallback", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_kumiho_mcp.py", "--module", "kumiho.mcp_server"],
    )

    if os.name == "nt":
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(L.subprocess, "run", fake_run)
        assert L.main() == 0
    else:
        class ExecCalled(Exception):
            pass

        def fake_execv(executable, cmd):
            calls.append((cmd, {"executable": executable}))
            raise ExecCalled

        monkeypatch.setattr(L.os, "execv", fake_execv)
        with pytest.raises(ExecCalled):
            L.main()

    cmd, _kwargs = calls[-1]
    assert cmd[:2] == [str(python_path), "-I"]
    assert Path(cmd[2]).name == "run_kumiho_cloud.py"
    assert cmd[3:] == ["--module", "kumiho.mcp_server"]
    assert os.environ["KUMIHO_AUTH_TOKEN"] == "explicit-cloud-token"
    assert os.environ["KUMIHO_PLUGIN_SHARED_HOME"] == str(shared_home)


def test_active_ce_launch_normalizes_routes_before_running_adapter(
    tmp_path, monkeypatch
):
    python_path = tmp_path / "venv" / "python"
    calls = []

    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CLAUDE_MODE", "ce")
    monkeypatch.setenv(
        "KUMIHO_CLAUDE_SERVER_ENDPOINT", "grpcs://127.0.0.1:7443"
    )
    monkeypatch.setenv("UPSTASH_REDIS_URL", "rediss://127.0.0.1:6380/0")
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "stale-cloud-token")
    monkeypatch.setattr(L, "_hydrate_env_from_local_config", lambda: None)
    monkeypatch.setattr(L, "_sanitize_placeholder_env_vars", lambda: None)
    monkeypatch.setattr(L, "_ensure_runtime", lambda: python_path)
    monkeypatch.setattr(L, "_normalize_host_session_id", lambda: None)
    monkeypatch.setattr(L, "_desktop_bootstrap_enabled", lambda: False)
    monkeypatch.setattr(L, "_configure_llm_fallback", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_kumiho_mcp.py", "--module", "kumiho.mcp_server"],
    )

    if os.name == "nt":
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(L.subprocess, "run", fake_run)
        assert L.main() == 0
    else:
        class ExecCalled(Exception):
            pass

        def fake_execv(executable, cmd):
            calls.append((cmd, {"executable": executable}))
            raise ExecCalled

        monkeypatch.setattr(L.os, "execv", fake_execv)
        with pytest.raises(ExecCalled):
            L.main()

    cmd, _kwargs = calls[-1]
    assert cmd[:2] == [str(python_path), "-I"]
    assert Path(cmd[2]).name == "run_kumiho_ce.py"
    assert cmd[3:] == ["--module", "kumiho.mcp_server"]
    assert os.environ["KUMIHO_SERVER_ENDPOINT"] == (
        "grpcs://127.0.0.1:7443"
    )
    assert os.environ["UPSTASH_REDIS_URL"] == "rediss://127.0.0.1:6380/0"
    assert os.environ["KUMIHO_AUTH_TOKEN"] == ""
    assert os.environ["KUMIHO_SERVER_USE_TLS"] == "true"
    assert os.environ["KUMIHO_REQUIRE_TLS"] == "1"


def test_invalid_ce_endpoint_is_never_echoed(monkeypatch, capsys):
    secret_url = "https://user:password@ce.example.test:9190"
    monkeypatch.setenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", secret_url)
    assert L._resolve_ce_endpoint() == L.DEFAULT_CE_ENDPOINT
    output = capsys.readouterr().err
    assert "password" not in output
    assert secret_url not in output
