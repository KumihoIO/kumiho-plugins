"""Insight settings reach hooks after Desktop placeholder expansion."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("insight_config_launcher", Path(__file__).with_name("run_kumiho_mcp.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_insight_settings_are_resolved_and_snapshotted(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_state_dir", lambda: tmp_path)
    monkeypatch.delenv("KUMIHO_CLAUDE_HOST", raising=False)
    expected = {"KUMIHO_REFLEX_INSIGHTS": "1", "KUMIHO_REFLEX_LEARNED_SOURCES": "0", "KUMIHO_REFLEX_INSIGHT_MAX_CHARS": "5120"}
    for key, value in expected.items():
        monkeypatch.setenv(key, "${" + key + ":-" + value + "}")
    launcher._sanitize_placeholder_env_vars()
    snapshot = json.loads((tmp_path / "reflex.config.json").read_text())
    assert {key: snapshot[key] for key in expected} == expected


def test_codex_does_not_overwrite_claude_insight_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_state_dir", lambda: tmp_path)
    monkeypatch.setenv("KUMIHO_CLAUDE_HOST", "codex")
    launcher._publish_reflex_config()
    assert not (tmp_path / "reflex.config.json").exists()
