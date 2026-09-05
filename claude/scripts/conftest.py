"""pytest configuration for claude/scripts.

Most files here are standalone check scripts, run via ``python <file>.py``:
their ``test_*`` functions take positional args and return bool, which pytest
would mis-collect as fixture requests (ERROR) or return-not-None warnings. Only
test_backfill_ingest.py is pytest-native. Exclude the script-style files from
collection so a plain ``pytest claude/scripts/`` is green — each still runs on
its own via ``python <file>.py`` (see each file's module docstring).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_real_claude_host_markers(monkeypatch):
    """Never let a developer's host session redirect tests into real state.

    Host-mode tests opt back in explicitly after this fixture has cleared the
    inherited markers.  This matters when pytest itself was started by Claude:
    a temp ``KUMIHO_CLAUDE_HOME`` is intentionally ignored under host
    isolation, so leaving the marker set could make queue tests overwrite the
    account's real pending-capture file.
    """
    for key in (
        "KUMIHO_CLAUDE_HOST",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
    ):
        monkeypatch.delenv(key, raising=False)

collect_ignore = [
    "test_backfill_inventory.py",
    "test_ce_mode.py",
    "test_discovery_env.py",
    "test_placeholder_defaults.py",
]
