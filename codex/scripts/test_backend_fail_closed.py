"""Pure unit regressions for Codex backend isolation and Cloud adaptation."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
SHIM_PATH = SCRIPT_DIR / "run_kumiho_mcp.py"
INGEST_PATH = SCRIPT_DIR / "ingest_skills.py"
CLOUD_ADAPTER_PATH = SCRIPT_DIR / "run_kumiho_cloud.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_backend_environment(monkeypatch, shim) -> None:
    keys = {
        *shim.CODEX_DEDICATED_ENV,
        *shim.CE_ROUTING_ENV,
        *shim.CLOUD_ROUTING_ENV,
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CLAUDE_HOST",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def _assert_failed_backend_is_scrubbed(shim) -> None:
    assert os.environ["KUMIHO_AUTH_TOKEN"] == ""
    for key in {
        *shim.CODEX_DEDICATED_ENV,
        *shim.CE_ROUTING_ENV,
        *shim.CLOUD_ROUTING_ENV,
    }:
        assert key not in os.environ


def test_missing_codex_config_defaults_to_cloud_with_shared_explicit_token(
    tmp_path, monkeypatch
):
    shim = _load_module(SHIM_PATH, "kumiho_codex_missing_config_test")
    _clear_backend_environment(monkeypatch, shim)
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "explicit-shared-api-token")
    monkeypatch.setenv("KUMIHO_CLAUDE_MODE", "ce")
    monkeypatch.setenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", "wrong-host:9190")

    shim._apply_codex_config(tmp_path / "missing-codex.json")

    assert os.environ[shim.CODEX_BACKEND_ENV] == "cloud"
    assert os.environ["KUMIHO_AUTH_TOKEN"] == "explicit-shared-api-token"
    assert "KUMIHO_CLAUDE_MODE" not in os.environ
    assert "KUMIHO_CLAUDE_SERVER_ENDPOINT" not in os.environ


def test_invalid_explicit_ce_fails_closed_and_scrubs_token(
    tmp_path, monkeypatch, capsys
):
    shim = _load_module(SHIM_PATH, "kumiho_codex_invalid_explicit_ce_test")
    _clear_backend_environment(monkeypatch, shim)
    secret = "must-not-survive-invalid-ce"
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", secret)
    monkeypatch.setenv(shim.CODEX_BACKEND_ENV, "ce")
    monkeypatch.setenv(shim.CODEX_ENDPOINT_ENV, "https://user:secret@example.test")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", "https://wrong.example.test")

    with pytest.raises(SystemExit) as stopped:
        shim._apply_codex_config(tmp_path / "missing-codex.json")

    assert stopped.value.code == 2
    _assert_failed_backend_is_scrubbed(shim)
    assert secret not in capsys.readouterr().err


def test_malformed_existing_config_fails_closed_and_scrubs_token(
    tmp_path, monkeypatch, capsys
):
    shim = _load_module(SHIM_PATH, "kumiho_codex_malformed_config_test")
    _clear_backend_environment(monkeypatch, shim)
    config_path = tmp_path / "codex.json"
    config_path.write_text('{"schema_version": 1,', encoding="utf-8")
    secret = "must-not-survive-malformed-config"
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", secret)
    monkeypatch.setenv("KUMIHO_SERVER_ENDPOINT", "wrong.example.test:443")

    with pytest.raises(SystemExit) as stopped:
        shim._apply_codex_config(config_path)

    assert stopped.value.code == 2
    _assert_failed_backend_is_scrubbed(shim)
    assert secret not in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--provision", "--self-test"])
def test_backend_independent_maintenance_can_repair_bad_config(
    tmp_path, monkeypatch, flag
):
    shim = _load_module(SHIM_PATH, f"kumiho_codex_maintenance_{flag[2:]}")
    launcher = tmp_path / "shared_launcher.py"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(shim, "_LAUNCHER_CANDIDATES", (launcher,))
    # shim.main writes this through os.environ directly. Register an initial
    # value so monkeypatch removes it at teardown even when it began absent.
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "")
    monkeypatch.setattr(
        shim,
        "_apply_codex_config",
        lambda: pytest.fail("maintenance parsed the broken backend config"),
    )
    launched = []
    monkeypatch.setattr(
        shim.runpy,
        "run_path",
        lambda path, run_name: launched.append((path, run_name)),
    )
    monkeypatch.setattr(sys, "argv", [str(SHIM_PATH), flag])

    shim.main()

    assert launched == [(str(launcher), "__main__")]
    assert os.environ["KUMIHO_CLAUDE_HOST"] == "codex"


def test_cloud_ingestion_reuses_sibling_adapter_and_rejects_unauthenticated(
    tmp_path, monkeypatch
):
    ingest = _load_module(INGEST_PATH, "kumiho_codex_ingest_adapter_test")
    adapter = _load_module(
        CLOUD_ADAPTER_PATH,
        "kumiho_codex_ingest_sibling_cloud_adapter_test",
    )
    calls = []

    def prepare_environment():
        calls.append(("prepare",))
        return tmp_path / ".kumiho"

    def configure_authenticated(**kwargs):
        calls.append(("configure", kwargs))
        return True

    monkeypatch.setattr(adapter, "_prepare_environment", prepare_environment)
    monkeypatch.setattr(adapter, "_configure_cloud", configure_authenticated)
    monkeypatch.setitem(sys.modules, "run_kumiho_cloud", adapter)

    ingest._configure_backend("cloud", {"backend": "cloud"})

    assert Path(adapter.__file__).resolve() == CLOUD_ADAPTER_PATH.resolve()
    assert calls == [("prepare",), ("configure", {"force_refresh": True})]

    calls.clear()

    def configure_unauthenticated(**kwargs):
        calls.append(("configure", kwargs))
        return False

    monkeypatch.setattr(adapter, "_configure_cloud", configure_unauthenticated)
    with pytest.raises(RuntimeError, match="authentication.*unavailable"):
        ingest._configure_backend("cloud", {"backend": "cloud"})
    assert calls == [("prepare",), ("configure", {"force_refresh": True})]


def test_cloud_adapter_uses_public_sdk_discovery_contract(tmp_path, monkeypatch):
    adapter = _load_module(
        CLOUD_ADAPTER_PATH,
        "kumiho_codex_public_discovery_contract_test",
    )
    calls = []
    public_client = object()
    fake_kumiho = types.ModuleType("kumiho")

    def client_from_discovery(**kwargs):
        calls.append(kwargs)
        return public_client

    fake_kumiho.client_from_discovery = client_from_discovery
    cache_path = tmp_path / "official-cloud" / "discovery-cache.json"
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "explicit-api-token")
    monkeypatch.setenv("KUMIHO_DISCOVERY_CACHE_FILE", str(cache_path))

    result = adapter._discover_cloud_client(
        fake_kumiho,
        force_refresh=True,
    )

    assert result is public_client
    assert calls == [
        {
            "id_token": None,
            "control_plane_url": adapter.OFFICIAL_CONTROL_PLANE_URL,
            "cache_path": str(cache_path),
            "force_refresh": True,
        }
    ]
