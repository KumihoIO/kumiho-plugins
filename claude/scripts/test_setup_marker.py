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
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "project-runtime"))

    mod = _load("kumiho_setup_host_root_guard", "setup.py")

    assert mod.KUMIHO_DIR == Path.home() / ".kumiho"
    assert "KUMIHO_CONFIG_DIR" not in os.environ


def test_claude_setup_restores_absolute_user_global_runtime_root(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    trusted_root = tmp_path / "trusted-kumiho"
    settings = fake_home / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"env": {"KUMIHO_CONFIG_DIR": str(trusted_root.absolute())}}),
        encoding="utf-8",
    )
    _patch_native_account_home(monkeypatch, fake_home.absolute())
    monkeypatch.setenv("HOME", str(tmp_path / "project-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "project-home"))
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "claude")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "project-runtime"))

    mod = _load("kumiho_setup_user_global_root", "setup.py")

    assert mod.KUMIHO_DIR == trusted_root.absolute()
    assert os.environ["KUMIHO_CONFIG_DIR"] == str(trusted_root.absolute())
    assert os.environ["HOME"] == str(fake_home.absolute())
    assert os.environ["USERPROFILE"] == str(fake_home.absolute())
    assert mod.CLAUDE_SETTINGS == fake_home.absolute() / ".claude" / "settings.json"
    assert all(
        fake_home.absolute() == path or fake_home.absolute() in path.parents
        for path in mod._claude_desktop_config_paths()
    )


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
        child_env = runs.kwargs_for(command)["env"]
        assert all(key not in child_env for key in secrets)
        assert child_env["KUMIHO_PROVISION_TEST_SENTINEL"] == "preserved"


def test_token_stdin_keeps_cloud_credential_out_of_argv(wizard, monkeypatch):
    cached = []
    monkeypatch.setattr(wizard, "cache_token", lambda token: cached.append(token) or True)
    monkeypatch.setattr(wizard, "find_python", lambda: None)
    monkeypatch.setattr(wizard.sys, "stdin", io.StringIO("Bearer test-cloud-token\n"))

    assert wizard.main(["--token-stdin", "--yes"]) == 1
    assert cached == ["test-cloud-token"]


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


def test_desktop_config_writers_add_claude_host_provenance(wizard, tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"kumiho-memory": {"env": {}}}}),
        encoding="utf-8",
    )

    assert wizard._try_write_token_to_config(config, "cloud-token")
    env = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]["env"]
    assert env == {
        "KUMIHO_AUTH_TOKEN": "cloud-token",
        "KUMIHO_CLAUDE_HOST": "claude",
    }

    env.pop("KUMIHO_CLAUDE_HOST")
    config.write_text(
        json.dumps({"mcpServers": {"kumiho-memory": {"env": env}}}),
        encoding="utf-8",
    )
    assert wizard._try_write_env_to_config(config, {"KUMIHO_CLAUDE_MODE": "ce"})
    env = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]["env"]
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
