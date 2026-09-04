#!/usr/bin/env python3
"""Tests for the onboarding wizard's end of the provisioning handshake.

The venv is only half of it. ``reflex_prefetch_worker._venv_ready`` requires the
interpreter AND ``~/.kumiho/.installed-packages.txt``, so a wizard that
installs the packages and never writes that marker leaves auto-recall and the
reflect/consolidate nudges dead on every turn, with nothing but
``skip: venv not provisioned`` in reflex.log. The MCP server keeps starting
regardless -- it decides by comparing installed versions and consults the marker
only for extras identity -- which is exactly why this hid for a full working
session (kumiho-plugins#65).

Nothing here runs pip or builds a venv: ``bounded_proc.run`` is replaced and the
interpreter is an empty file.

Run: python -m pytest claude/scripts/test_setup_marker.py -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))  # both modules import bounded_proc by name
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("run_kumiho_mcp", "run_kumiho_mcp.py")


class _Runs:
    """Stands in for every subprocess the wizard shells out to.

    ``pip_returncode`` is the whole point: it is the difference between a
    successful install (marker) and a failed one (no marker, ever).
    """

    def __init__(self, pip_returncode: int = 0):
        self.pip_returncode = pip_returncode
        self.calls: list[list[str]] = []
        self.call_kwargs: list[dict] = []

    def __call__(self, cmd, timeout=None, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        self.call_kwargs.append(kwargs)
        rc = self.pip_returncode if "pip" in argv else 0
        return subprocess.CompletedProcess(
            argv, rc, stdout="", stderr="pip exploded" if rc else "")

    def pip_argv(self) -> list[str]:
        for argv in self.calls:
            if "pip" in argv and "install" in argv:
                return argv
        return []

    def kwargs_for(self, argv_token: str) -> dict:
        for argv, kwargs in zip(self.calls, self.call_kwargs):
            if argv_token in argv:
                return kwargs
        return {}


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    """The wizard, pointed at a throwaway state dir with a venv already built.

    ``VENV_DIR`` is resolved at import time, so the environment has to be set
    before the module is loaded -- same reason the launcher suites reload it.
    """
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "kumiho"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pdata"))
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)
    mod = _load("kumiho_setup", "setup.py")
    # Both layouts, so ``link_windows_bin`` sees its junction already in place
    # and no real ``mklink`` runs here.
    for sub in ("Scripts", "bin"):
        d = mod.VENV_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "python.exe").write_text("", encoding="utf-8")
        (d / "python").write_text("", encoding="utf-8")
    assert mod.VENV_PYTHON.exists()
    # The fixture's interpreter is intentionally an empty file. Keep every
    # ordinary unit test away from Windows executable probing; the dedicated
    # broken-runtime test below controls this result explicitly.
    monkeypatch.setattr(mod.LAUNCHER, "_python_interpreter_works", lambda _path: True)
    return mod


def _marker(tmp_path) -> Path:
    return tmp_path / "kumiho" / L.MARKER_FILE


def _patch_native_account_home(monkeypatch, home: Path) -> None:
    if os.name == "nt":
        import ctypes

        def fake_sh_get_folder_path(_hwnd, _csidl, _token, _flags, buffer):
            buffer.value = str(home)
            return 0

        monkeypatch.setattr(
            ctypes.windll.shell32, "SHGetFolderPathW", fake_sh_get_folder_path
        )
    else:
        import pwd

        class Account:
            pw_dir = str(home)

        monkeypatch.setattr(pwd, "getpwuid", lambda _uid: Account())


def test_host_launched_setup_rejects_ambient_runtime_root(tmp_path, monkeypatch):
    attacker_data = tmp_path / "attacker-plugin-data"
    attacker_venv = attacker_data / "venv"
    attacker_venv.mkdir(parents=True)
    sentinel = attacker_venv / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "project-runtime"))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "attacker-root"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(attacker_data))
    monkeypatch.setenv(
        "KUMIHO_CLAUDE_PACKAGE_SPEC", "https://attacker.invalid/evil.whl"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "attacker-python"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://attacker.invalid/simple")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "attacker-ca.pem"))
    monkeypatch.setenv("KUMIHO_LLM_BASE_URL", "https://attacker.invalid/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid/openai")
    monkeypatch.setenv("KUMIHO_MEMORY_CODE_AUTOMINE", "1")

    mod = _load("kumiho_setup_host_root_guard", "setup.py")

    assert mod.KUMIHO_DIR == Path.home() / ".kumiho"
    assert "KUMIHO_CONFIG_DIR" not in os.environ
    assert "CLAUDE_PLUGIN_ROOT" not in os.environ
    assert "CLAUDE_PLUGIN_DATA" not in os.environ
    assert "KUMIHO_CLAUDE_PACKAGE_SPEC" not in os.environ
    assert "PYTHONPATH" not in os.environ
    assert "HTTPS_PROXY" not in os.environ
    assert "REQUESTS_CA_BUNDLE" not in os.environ
    assert "KUMIHO_LLM_BASE_URL" not in os.environ
    assert "OPENAI_BASE_URL" not in os.environ
    assert "KUMIHO_MEMORY_CODE_AUTOMINE" not in os.environ
    provision_env = mod._provision_subprocess_env()
    assert "PIP_INDEX_URL" not in provision_env
    assert "HTTPS_PROXY" not in provision_env
    assert mod.LAUNCHER._plugin_data_dir() is None
    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_claude_setup_restores_trusted_roots_but_pins_cloud_and_local_routes(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    trusted_root = tmp_path / "trusted-kumiho"
    trusted_state = tmp_path / "trusted-state"
    trusted_ca = (tmp_path / "trusted-ca.pem").absolute()
    settings = fake_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({
            "env": {
                "KUMIHO_CONFIG_DIR": str(trusted_root.absolute()),
                "KUMIHO_CLAUDE_HOME": str(trusted_state.absolute()),
                "KUMIHO_CONTROL_PLANE_URL": "https://private.example.test",
                "KUMIHO_CONTROL_PLANE_API_URL": "https://auth.example.test",
                "KUMIHO_FIREBASE_API_KEY": "private-firebase-key",
                "KUMIHO_FIREBASE_PROJECT_ID": "private-firebase-project",
                "KUMIHO_CLAUDE_PACKAGE_SPEC": "kumiho[mcp]==9.9.9",
                "HTTPS_PROXY": "http://trusted-proxy.example.test:8080",
                "REQUESTS_CA_BUNDLE": str(trusted_ca),
                "KUMIHO_LLM_BASE_URL": "https://trusted-llm.example.test/v1",
                "KUMIHO_MEMORY_CODE_AUTOMINE": "1",
            }
        }),
        encoding="utf-8",
    )
    _patch_native_account_home(monkeypatch, fake_home.absolute())
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "project-home"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "project-runtime"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))

    mod = _load("kumiho_setup_user_global_root", "setup.py")

    assert mod.KUMIHO_DIR == trusted_root.absolute()
    assert os.environ["KUMIHO_CONFIG_DIR"] == str(trusted_root.absolute())
    assert os.environ["KUMIHO_CLAUDE_HOME"] == str(trusted_state.absolute())
    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ
    assert "KUMIHO_FIREBASE_API_KEY" not in os.environ
    assert "KUMIHO_FIREBASE_PROJECT_ID" not in os.environ
    assert mod.package_spec() == "kumiho[mcp]==9.9.9"
    assert os.environ["HTTPS_PROXY"] == "http://trusted-proxy.example.test:8080"
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(trusted_ca)
    assert "KUMIHO_LLM_BASE_URL" not in os.environ
    assert "KUMIHO_MEMORY_CODE_AUTOMINE" not in os.environ
    assert mod._launcher_state_dir() == trusted_state.absolute()
    assert os.environ["HOME"] == str(fake_home.absolute())
    assert os.environ["USERPROFILE"] == str(fake_home.absolute())
    assert mod.CLAUDE_SETTINGS == fake_home.absolute() / ".claude" / "settings.json"
    assert all(
        fake_home.absolute() == path or fake_home.absolute() in path.parents
        for path in mod._claude_desktop_config_paths()
    )


def test_claude_setup_rejects_relative_user_global_state_home(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    settings = fake_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": "relative-state"}}),
        encoding="utf-8",
    )
    _patch_native_account_home(monkeypatch, fake_home.absolute())
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "project-home"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))

    mod = _load("kumiho_setup_relative_state_guard", "setup.py")

    assert "KUMIHO_CLAUDE_HOME" not in os.environ
    if os.name == "nt":
        expected = fake_home.absolute() / "AppData" / "Local" / "kumiho-claude"
    else:
        expected = fake_home.absolute() / ".cache" / "kumiho-claude"
    assert mod._launcher_state_dir() == expected


def test_claude_setup_scrubs_split_auth_api_and_pins_official_discovery(
    tmp_path, monkeypatch
):
    fake_home = (tmp_path / "home").absolute()
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {
            "KUMIHO_CONTROL_PLANE_URL": "https://discovery.example.test",
            "KUMIHO_CONTROL_PLANE_API_URL": "https://auth.example.test",
        }}),
        encoding="utf-8",
    )
    _patch_native_account_home(monkeypatch, fake_home)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_API_URL", "https://attacker.invalid")

    _load("kumiho_setup_split_control_plane", "setup.py")

    assert os.environ["KUMIHO_CONTROL_PLANE_URL"] == (
        "https://control.kumiho.cloud"
    )
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ


@pytest.mark.parametrize(
    ("key", "loopback", "remote"),
    [
        (
            "KUMIHO_CLAUDE_SERVER_ENDPOINT",
            "grpcs://127.0.0.1:7443",
            "grpcs://ce.example.test:7443",
        ),
        (
            "UPSTASH_REDIS_URL",
            "rediss://127.0.0.1:6380/0",
            "rediss://redis.example.test:6380/0",
        ),
        (
            "KUMIHO_LLM_BASE_URL",
            "https://127.0.0.1:11434/v1",
            "https://llm.example.test/v1",
        ),
    ],
)
def test_host_setup_accepts_only_loopback_ce_routes(
    wizard, key, loopback, remote
):
    assert wizard._project_route_is_allowed(key, loopback)
    assert not wizard._project_route_is_allowed(key, remote)


def test_claude_setup_rejects_embedded_placeholder_in_absolute_state_home(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    settings = fake_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    placeholder_path = str((tmp_path / "state").absolute() / "${PROJECT}")
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CLAUDE_HOME": placeholder_path}}),
        encoding="utf-8",
    )
    _patch_native_account_home(monkeypatch, fake_home.absolute())
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "project-home"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path / "project-state"))

    mod = _load("kumiho_setup_embedded_state_guard", "setup.py")

    assert "KUMIHO_CLAUDE_HOME" not in os.environ
    assert mod.KUMIHO_DIR == fake_home.absolute() / ".kumiho"


def test_trusted_settings_merge_updates_local_and_removes_stale_from_both(
    tmp_path, monkeypatch
):
    settings_root = (tmp_path / ".claude").absolute()
    settings_local = settings_root / "settings.local.json"
    settings_json = settings_root / "settings.json"
    settings_root.mkdir(parents=True)
    settings_local.write_text(
        json.dumps({"env": {
            "SENTINEL_LOCAL": "keep",
            "KUMIHO_CLAUDE_SERVER_ENDPOINT": "grpcs://old.example.test:7443",
            "KUMIHO_LLM_BASE_URL": "https://old-llm.example.test/v1",
        }}),
        encoding="utf-8",
    )
    settings_json.write_text(
        json.dumps({"env": {
            "SENTINEL_JSON": "keep",
            "KUMIHO_LLM_BASE_URL": "https://also-old.example.test/v1",
        }}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(settings_root))
    mod = _load("kumiho_setup_settings_precedence", "setup.py")

    assert mod._merge_user_global_claude_env(
        {"KUMIHO_CLAUDE_MODE": "ce"},
        remove=("KUMIHO_CLAUDE_SERVER_ENDPOINT", "KUMIHO_LLM_BASE_URL"),
    )

    local_env = json.loads(settings_local.read_text(encoding="utf-8"))["env"]
    json_env = json.loads(settings_json.read_text(encoding="utf-8"))["env"]
    assert local_env["SENTINEL_LOCAL"] == "keep"
    assert local_env["KUMIHO_CLAUDE_MODE"] == "ce"
    assert "KUMIHO_CLAUDE_SERVER_ENDPOINT" not in local_env
    assert "KUMIHO_LLM_BASE_URL" not in local_env
    assert json_env == {"SENTINEL_JSON": "keep"}


def test_find_python_uses_an_external_base_for_shared_runtime_repair(wizard, monkeypatch):
    calls = []

    def version(cmd, timeout=None, **kwargs):
        argv = [str(c) for c in cmd]
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout="Python 3.11.9\n", stderr="")

    monkeypatch.setattr(wizard.bounded_proc, "run", version)

    expected = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    assert wizard.find_python() == expected
    assert calls == [[expected, "--version"]]


def test_find_python_fails_closed_when_the_external_base_is_broken(
        wizard, monkeypatch):
    calls = []

    def version(cmd, timeout=None, **kwargs):
        argv = [str(c) for c in cmd]
        calls.append(argv)
        raise OSError("invalid external interpreter")

    monkeypatch.setattr(wizard.bounded_proc, "run", version)

    expected = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    assert wizard.find_python() is None
    assert calls == [[expected, "--version"]]


def test_setup_quarantines_a_broken_shared_runtime_and_rebuilds_it(
        wizard, monkeypatch):
    old_runtime = wizard.VENV_DIR
    marker = old_runtime / "preserve-me.txt"
    marker.write_text("old runtime", encoding="utf-8")
    interpreter_checks = iter((False, True))
    monkeypatch.setattr(
        wizard.LAUNCHER,
        "_python_interpreter_works",
        lambda _path: next(interpreter_checks),
    )
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")

    backups = list(old_runtime.parent.glob("venv.broken-*"))
    assert len(backups) == 1
    assert (backups[0] / "preserve-me.txt").read_text(encoding="utf-8") == "old runtime"
    assert "venv" in next(argv for argv in runs.calls if "venv" in argv)


def test_venv_and_pip_children_do_not_inherit_runtime_secrets(
        wizard, monkeypatch):
    secrets = (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "UPSTASH_REDIS_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_LOCAL_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "UPSTASH_REDIS_REST_TOKEN",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_LLM_BASE_URL",
    )
    for key in secrets:
        monkeypatch.setenv(key, "must-not-reach-provisioning")
    monkeypatch.setenv("KUMIHO_PROVISION_TEST_SENTINEL", "preserved")

    # Force both provisioning subprocesses down their mocked paths. Removing
    # this empty fixture file never touches a real interpreter.
    wizard.VENV_PYTHON.unlink()
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")

    for command in ("venv", "pip"):
        child_argv = next(argv for argv in runs.calls if command in argv)
        assert child_argv[1] == "-I"
        child_env = runs.kwargs_for(command)["env"]
        assert all(key not in child_env for key in secrets)
        assert child_env["KUMIHO_PROVISION_TEST_SENTINEL"] == "preserved"


def test_direct_setup_keeps_legacy_package_index_and_proxy_environment(
    wizard, monkeypatch
):
    monkeypatch.delenv("KUMIHO_CLAUDE_HOST", raising=False)
    values = {
        "PIP_INDEX_URL": "https://packages.example.test/simple",
        "HTTPS_PROXY": "http://proxy.example.test:8080",
        "PYTHONPATH": "developer-python-path",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    child = wizard._provision_subprocess_env()

    for key, value in values.items():
        assert child[key] == value


def test_legacy_token_pass_through_is_ephemeral(wizard, monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        wizard,
        "_sdk_cloud_auth_works",
        lambda token=None: calls.append(token) or True,
    )

    token, authenticated = wizard.setup_auth("  test-cloud-token  ")

    assert (token, authenticated) == ("test-cloud-token", True)
    assert calls == ["test-cloud-token"]
    assert "KUMIHO_AUTH_TOKEN" not in os.environ
    output = capsys.readouterr().out
    assert "not saved" in output
    assert "persistent KUMIHO_AUTH_TOKEN" in output


def test_onboard_defaults_without_requesting_or_persisting_a_token():
    command = (SCRIPTS.parent / "commands" / "kumiho-onboard.md").read_text(
        encoding="utf-8"
    )

    assert "Never pause to ask which backend" in command
    assert "--token-stdin --yes" not in command
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --yes' in command
    assert "does not own or persist Cloud credentials" in command


def test_noninteractive_setup_never_opens_an_sdk_login_prompt(
    wizard, monkeypatch, capsys
):
    class TtyInput(io.StringIO):
        def isatty(self):
            return True

    wizard.AUTO_YES = True
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(wizard, "_sdk_cloud_auth_works", lambda _token=None: False)
    monkeypatch.setattr(wizard.sys, "stdin", TtyInput())
    monkeypatch.setattr(
        wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "--yes onboarding must not open an interactive SDK login"
        ),
    )

    assert wizard.setup_auth() == (None, False)
    output = capsys.readouterr().out
    assert "kumiho-auth login" in output
    assert "kumiho-cli login" in output


def test_legacy_token_stdin_summary_says_not_saved(wizard, monkeypatch, capsys):
    monkeypatch.setattr(wizard.sys, "stdin", io.StringIO("test-cloud-token\n"))
    monkeypatch.setattr(wizard, "find_python", lambda: "python3")
    monkeypatch.setattr(wizard, "write_python_knob", lambda _python: None)
    monkeypatch.setattr(wizard, "setup_venv", lambda _python: wizard.VENV_PYTHON)
    monkeypatch.setattr(
        wizard,
        "setup_auth",
        lambda cli_token=None: (cli_token.strip(), True),
    )
    monkeypatch.setattr(wizard, "_neutralize_env_markers", lambda _keys: None)
    monkeypatch.setattr(wizard, "run_ingestion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wizard, "verify_connection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wizard,
        "cache_token",
        lambda _token: pytest.fail("active setup must not cache a Cloud token"),
    )
    monkeypatch.setattr(
        wizard,
        "patch_mcp_json",
        lambda _token: pytest.fail("active setup must not persist a Cloud token"),
    )

    assert wizard.main(["--token-stdin", "--yes"]) == 0
    output = capsys.readouterr().out
    assert "used only for this setup process" in output
    assert "was not saved" in output
    assert "test-cloud-token" not in output


def test_cloud_ingestion_preserves_explicit_token_for_the_sdk_adapter(
    wizard, monkeypatch
):
    captured = {}
    monkeypatch.setattr(wizard, "ask_yes_no", lambda _prompt: True)

    def fake_run(command, **kwargs):
        captured["command"] = [str(part) for part in command]
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(wizard.subprocess, "run", fake_run)

    wizard.run_ingestion(wizard.VENV_PYTHON, token="explicit-cloud-token")

    assert captured["command"] == [
        str(wizard.VENV_PYTHON),
        "-I",
        str(wizard.CLOUD_RUNNER),
        "--script",
        str(wizard.INGEST_SCRIPT),
    ]
    assert captured["env"]["KUMIHO_AUTH_TOKEN"] == "explicit-cloud-token"
    assert captured["env"]["KUMIHO_PLUGIN_SHARED_HOME"] == str(
        wizard.KUMIHO_DIR
    )


def test_cloud_verification_delegates_tokenless_auth_to_the_sdk_adapter(
    wizard, monkeypatch
):
    captured = {}
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)

    def fake_run(command, **kwargs):
        captured["command"] = [str(part) for part in command]
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(wizard.bounded_proc, "run", fake_run)

    wizard.verify_connection(wizard.VENV_PYTHON, token=None)

    assert captured["command"] == [
        str(wizard.VENV_PYTHON),
        "-I",
        str(wizard.CLOUD_RUNNER),
        "--auth-check",
    ]
    assert "KUMIHO_AUTH_TOKEN" not in captured["env"]
    assert captured["env"]["KUMIHO_PLUGIN_SHARED_HOME"] == str(
        wizard.KUMIHO_DIR
    )


def test_standalone_skill_ingest_reenters_the_cloud_adapter_once(
    monkeypatch,
):
    monkeypatch.setenv("_KUMIHO_ADAPTER_BOUND", "hostile-env-value")
    ingest = _load("kumiho_standalone_ingest", "ingest-skills.py")
    calls = []
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.setattr(
        ingest.subprocess,
        "run",
        lambda command: calls.append([str(part) for part in command])
        or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ingest-skills.py", "--dry-run"],
    )

    assert ingest._KUMIHO_ADAPTER_BOUND is False
    assert ingest._dispatch_through_backend_adapter() == 0
    assert calls == [[
        sys.executable,
        "-I",
        str(SCRIPTS / "run_kumiho_cloud.py"),
        "--script",
        str(SCRIPTS / "ingest-skills.py"),
        "--dry-run",
    ]]

    monkeypatch.setattr(ingest, "_KUMIHO_ADAPTER_BOUND", True)
    assert ingest._dispatch_through_backend_adapter() is None
    assert len(calls) == 1


def test_token_stdin_and_legacy_argv_token_are_mutually_exclusive(
        wizard, monkeypatch):
    monkeypatch.setattr(
        wizard.sys, "stdin", io.StringIO("must-not-be-read\n"))

    assert wizard.main([
        "--token", "legacy-token", "--token-stdin", "--yes",
    ]) == 2


def test_cloud_cleanup_removes_every_ce_route_from_every_desktop_config(
        wizard, tmp_path, monkeypatch):
    paths = [tmp_path / "classic.json", tmp_path / "msix.json"]
    for path in paths:
        path.write_text(json.dumps({"mcpServers": {"kumiho-memory": {"env": {
            "KUMIHO_AUTH_TOKEN": "cloud-token",
            **{key: "stale-ce-value" for key in wizard._CE_PERSISTED_ENV_KEYS},
        }}}}), encoding="utf-8")
    for key in wizard._CE_PERSISTED_ENV_KEYS:
        monkeypatch.setenv(key, "stale-ce-value")

    persisted = []
    monkeypatch.setattr(wizard, "_claude_desktop_config_paths", lambda: paths)
    monkeypatch.setattr(
        wizard, "_set_os_env_var", lambda key, value: persisted.append((key, value)) or True)

    wizard._neutralize_env_markers(list(wizard._CE_PERSISTED_ENV_KEYS))

    assert persisted == [(key, "") for key in wizard._CE_PERSISTED_ENV_KEYS]
    assert all(key not in os.environ for key in wizard._CE_PERSISTED_ENV_KEYS)
    for path in paths:
        env = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["kumiho-memory"]["env"]
        assert env == {"KUMIHO_AUTH_TOKEN": "cloud-token"}


def test_ce_desktop_config_writer_adds_claude_host_provenance(
    wizard, tmp_path
):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"kumiho-memory": {"env": {}}}}),
        encoding="utf-8",
    )

    assert wizard._try_write_env_to_config(config, {"KUMIHO_CLAUDE_MODE": "ce"})
    env = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]["env"]
    assert env["KUMIHO_CLAUDE_MODE"] == "ce"
    assert env["KUMIHO_CLAUDE_HOST"] == "claude"


def test_shell_export_quotes_ce_urls_without_command_substitution(
        wizard, tmp_path):
    rc = tmp_path / ".profile"
    value = 'http://llm.test/";$(touch should-never-run);`id`'

    assert wizard._upsert_shell_export(rc, "KUMIHO_LLM_BASE_URL", value)

    assert rc.read_text(encoding="utf-8") == (
        f"export KUMIHO_LLM_BASE_URL={shlex.quote(value)}\n"
    )


# ------------------------------------------------------- the missing marker

def test_a_successful_install_writes_the_provisioning_marker(wizard, tmp_path, monkeypatch):
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    assert wizard._setup_venv_locked("python3") == wizard.VENV_PYTHON
    assert _marker(tmp_path).exists(), (
        "onboarding finished without the marker: auto-recall and the "
        "reflect/consolidate nudges stay dead every turn")


def test_the_marker_holds_the_spec_the_launcher_would_have_written(wizard, tmp_path, monkeypatch):
    """Byte-for-byte what ``_provision`` writes, because ``_needs_install``
    parses it: a marker whose extras identity differs from the spec makes the
    launcher reinstall the venv the wizard just built."""
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == L.DEFAULT_PACKAGE_SPEC


def test_the_packages_installed_are_the_packages_the_marker_claims(wizard, tmp_path, monkeypatch):
    """No second hardcoded package list. The wizard used to carry its own copy
    of the spec, which is how a marker could truthfully be written and still
    describe something other than what pip was given."""
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    installed = runs.pip_argv()
    for token in shlex.split(_marker(tmp_path).read_text(encoding="utf-8")):
        assert token in installed


def test_a_failed_install_writes_no_marker(wizard, tmp_path, monkeypatch):
    """A marker over a broken install is worse than none: the launcher would
    read it as extras-identity agreement and the worker as a green light."""
    runs = _Runs(pip_returncode=1)
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    with pytest.raises(SystemExit):
        wizard._setup_venv_locked("python3")
    assert not _marker(tmp_path).exists()


def test_a_custom_spec_is_installed_and_recorded_verbatim(wizard, tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "kumiho[mcp]>=9.9.9")
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == "kumiho[mcp]>=9.9.9"
    assert "kumiho[mcp]>=9.9.9" in runs.pip_argv()


def test_an_unexpanded_placeholder_spec_falls_back_to_the_launcher_default(
        wizard, tmp_path, monkeypatch):
    """Hosts that do not expand ``${KUMIHO_CLAUDE_PACKAGE_SPEC:-...}`` pass the
    literal through; pip would be handed nonsense and the marker would record
    it."""
    monkeypatch.setenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "${KUMIHO_CLAUDE_PACKAGE_SPEC:-x}")
    runs = _Runs()
    monkeypatch.setattr(wizard.bounded_proc, "run", runs)

    wizard._setup_venv_locked("python3")
    assert _marker(tmp_path).read_text(encoding="utf-8") == L.DEFAULT_PACKAGE_SPEC


# ------------------------------------------- the two consumers, end to end

def test_the_prefetch_worker_calls_the_venv_ready_after_onboarding(
        wizard, tmp_path, monkeypatch):
    """The assertion that actually matters: the worker's own probe, against the
    state onboarding leaves behind."""
    monkeypatch.setattr(wizard.bounded_proc, "run", _Runs())
    worker = _load("reflex_prefetch_worker", "reflex_prefetch_worker.py")

    assert worker._venv_ready(L) is None       # marker not written yet
    wizard._setup_venv_locked("python3")
    assert worker._venv_ready(L) == wizard.VENV_PYTHON


def test_the_launcher_does_not_reinstall_over_the_wizards_marker(wizard, tmp_path, monkeypatch):
    """``_needs_install`` compares the marker's name+extras identity to the
    spec's. A marker the wizard wrote must read as the same identity, or the
    first server start pays the cold install again."""
    monkeypatch.setattr(wizard.bounded_proc, "run", _Runs())
    wizard._setup_venv_locked("python3")

    previous, prev_ok = L._spec_floors(
        _marker(tmp_path).read_text(encoding="utf-8"))
    reqs, ok = L._spec_floors(L.DEFAULT_PACKAGE_SPEC)
    assert prev_ok and ok
    assert {(n, e) for n, e, _f, _c in previous} == {(n, e) for n, e, _f, _c in reqs}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
