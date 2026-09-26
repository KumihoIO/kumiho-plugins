"""Codex onboarding's browser OAuth sign-in (``--onboard cloud --oauth``)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "kumiho_codex_onboard_oauth_test", HERE / "onboard_kumiho.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def onboard(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "kumiho"))
    module = _load()
    monkeypatch.setattr(module, "_config_dir", lambda: tmp_path / "kumiho")
    return module


class _Runs:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        argv = [str(c) for c in command]
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, "", "")


def test_oauth_runs_the_sdk_browser_login_on_the_official_issuer(
    onboard, monkeypatch, capsys
):
    runs = _Runs()
    checks = []
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "ambient-token")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_API_URL", "https://control.invalid")
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    monkeypatch.setattr(onboard.bounded_proc, "run", runs)
    monkeypatch.setattr(
        onboard,
        "_cached_auth_works",
        lambda _python, **kw: checks.append(kw) or True,
    )

    assert onboard._configure_cloud(
        Path(sys.executable), non_interactive=False, reauth=False, oauth=True
    )

    (argv, kwargs), = runs.calls
    assert argv[1:] == [
        "-I", "-m", "kumiho.auth_cli", "login", "--oauth",
        "--client-name", onboard.OAUTH_CLIENT_NAME,
    ]
    env = kwargs["env"]
    assert "KUMIHO_AUTH_TOKEN" not in env
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in env
    assert env["KUMIHO_CONTROL_PLANE_URL"] == onboard.OFFICIAL_CONTROL_PLANE_URL
    assert kwargs["stdout"] is None and kwargs["stderr"] is None
    assert kwargs["timeout"] >= 300
    assert checks == [{"drop_auth_token": True}]
    captured = capsys.readouterr()
    assert "takes precedence" in captured.err
    assert "ambient-token" not in captured.out + captured.err
    assert (onboard._config_dir() / "codex.json").is_file()


def test_oauth_does_not_need_a_terminal(onboard, monkeypatch):
    class NoTty:
        def isatty(self):
            return False

    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(onboard.sys, "stdin", NoTty())
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    runs = _Runs()
    monkeypatch.setattr(onboard.bounded_proc, "run", runs)
    monkeypatch.setattr(onboard, "_cached_auth_works", lambda _python, **kw: True)

    assert onboard._configure_cloud(
        Path(sys.executable),
        non_interactive=False,
        reauth=False,
        oauth=True,
        open_browser=False,
    )
    assert runs.calls[0][0][-1] == "--no-browser"


def test_oauth_fails_closed_on_an_sdk_without_oauth(onboard, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: False)
    monkeypatch.setattr(
        onboard.bounded_proc,
        "run",
        lambda *_a, **_k: pytest.fail("an old SDK must not be asked to sign in"),
    )

    assert not onboard._configure_cloud(
        Path(sys.executable), non_interactive=False, reauth=False, oauth=True
    )
    assert "0.15.0" in capsys.readouterr().err
    assert not (onboard._config_dir() / "codex.json").exists()


def test_oauth_that_is_not_completed_writes_no_backend(onboard, monkeypatch):
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    monkeypatch.setattr(onboard.bounded_proc, "run", _Runs(returncode=2))
    monkeypatch.setattr(
        onboard,
        "_cached_auth_works",
        lambda *_a, **_k: pytest.fail("a failed sign-in must not be verified"),
    )

    assert not onboard._configure_cloud(
        Path(sys.executable), non_interactive=False, reauth=False, oauth=True
    )
    assert not (onboard._config_dir() / "codex.json").exists()


def test_oauth_flag_selects_cloud(onboard):
    args = onboard._parse_args(["--oauth"])
    assert onboard._resolve_backend(args, Path(sys.executable)) == "cloud"


@pytest.mark.parametrize(
    "argv",
    [
        ["ce", "--oauth"],
        ["--oauth", "--ce-endpoint", "127.0.0.1:9190"],
        ["cloud", "--oauth", "--non-interactive"],
        ["cloud", "--no-browser"],
    ],
)
def test_oauth_argument_combinations_are_rejected(onboard, argv):
    with pytest.raises(SystemExit) as exc:
        onboard._parse_args(argv)
    assert exc.value.code == 2


def test_noninteractive_cloud_points_at_the_browser_sign_in(onboard, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "_cached_auth_works", lambda *_a, **_k: False)
    assert not onboard._configure_cloud(
        Path(sys.executable), non_interactive=True, reauth=False
    )
    assert "--onboard cloud --oauth" in capsys.readouterr().err


def test_onboard_skill_documents_the_oauth_sign_in():
    skill = (HERE.parent / "skills" / "kumiho-onboard" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "--onboard cloud --oauth" in skill
    assert "--no-browser" in skill
