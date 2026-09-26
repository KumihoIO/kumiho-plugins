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


def _no_bounded_login(*_a, **_k):
    pytest.fail("the sign-in child must not run under bounded_proc's job object")


def test_oauth_runs_the_sdk_browser_login_on_the_official_issuer(onboard, monkeypatch):
    runs = _Runs()
    checks = []
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_API_URL", "https://control.invalid")
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    monkeypatch.setattr(onboard.subprocess, "run", runs)
    # On Windows bounded_proc's job would kill a browser the SDK starts.
    monkeypatch.setattr(onboard.bounded_proc, "run", _no_bounded_login)
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
        "--timeout", str(onboard.OAUTH_BROWSER_WAIT_S),
    ]
    env = kwargs["env"]
    assert "KUMIHO_AUTH_TOKEN" not in env
    assert "KUMIHO_CONTROL_PLANE_API_URL" not in env
    assert env["KUMIHO_CONTROL_PLANE_URL"] == onboard.OFFICIAL_CONTROL_PLANE_URL
    assert "stdout" not in kwargs and "capture_output" not in kwargs
    assert kwargs["timeout"] > onboard.OAUTH_BROWSER_WAIT_S
    assert checks == [{"drop_auth_token": True}]
    assert (onboard._config_dir() / "codex.json").is_file()


def test_oauth_refuses_while_an_explicit_token_would_win(onboard, monkeypatch, capsys):
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "ambient-token")
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    monkeypatch.setattr(
        onboard.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("a sign-in the runtime would ignore must not run"),
    )

    assert not onboard._configure_cloud(
        Path(sys.executable), non_interactive=False, reauth=False, oauth=True
    )
    captured = capsys.readouterr()
    assert "takes precedence" in captured.err
    assert "ambient-token" not in captured.out + captured.err
    assert not (onboard._config_dir() / "codex.json").exists()


def test_oauth_does_not_need_a_terminal(onboard, monkeypatch):
    class NoTty:
        def isatty(self):
            return False

    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(onboard.sys, "stdin", NoTty())
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: True)
    runs = _Runs()
    monkeypatch.setattr(onboard.subprocess, "run", runs)
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
    monkeypatch.delenv("KUMIHO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(onboard, "_sdk_supports_oauth", lambda _python: False)
    monkeypatch.setattr(
        onboard.subprocess,
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
    monkeypatch.setattr(onboard.subprocess, "run", _Runs(returncode=2))
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


def test_oauth_repairs_an_invalid_backend_config_as_an_explicit_cloud_choice(
    onboard, monkeypatch
):
    def invalid():
        raise ValueError("the existing Codex backend config names an unknown backend")

    seen = {}
    monkeypatch.setattr(onboard, "_provision", lambda: Path(sys.executable))
    monkeypatch.setattr(onboard, "_existing_config", invalid)
    monkeypatch.setattr(
        onboard, "_configure_cloud", lambda _python, **kw: seen.update(kw) or False
    )

    # Configuration was reached (returns 2 because the stub declines), rather
    # than stopping early to demand an explicit backend.
    assert onboard.main(["--oauth"]) == 2
    assert seen["oauth"] is True


def test_package_spec_matches_the_vendored_launcher_default(onboard, monkeypatch):
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)
    launcher = importlib.util.spec_from_file_location(
        "kumiho_codex_launcher_spec_test", HERE / "_vendored_launcher.py"
    )
    module = importlib.util.module_from_spec(launcher)
    sys.path.insert(0, str(HERE))
    launcher.loader.exec_module(module)
    assert onboard._package_spec() == module.DEFAULT_PACKAGE_SPEC
    assert onboard._oauth_package_spec(module.DEFAULT_PACKAGE_SPEC).startswith(
        "kumiho[mcp]>=0.15.0 "
    )


def test_oauth_run_provisions_with_the_raised_floor(onboard, monkeypatch):
    seen = {}
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)

    def fake_provision():
        seen["spec"] = onboard.os.environ.get("KUMIHO_CLAUDE_PACKAGE_SPEC")
        return None

    monkeypatch.setattr(onboard, "_provision", fake_provision)
    assert onboard.main(["cloud", "--oauth"]) == 1
    assert seen["spec"] == onboard._oauth_package_spec(onboard._package_spec())
    assert "kumiho[mcp]>=0.15.0" in seen["spec"]

    # Without --oauth the plugin-wide spec is left alone.
    monkeypatch.delenv("KUMIHO_CLAUDE_PACKAGE_SPEC", raising=False)
    onboard.main(["cloud", "--non-interactive"])
    assert seen["spec"] is None


def test_onboard_skill_documents_the_oauth_sign_in():
    skill = (HERE.parent / "skills" / "kumiho-onboard" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "--onboard cloud --oauth" in skill
    assert "on this machine" in skill
