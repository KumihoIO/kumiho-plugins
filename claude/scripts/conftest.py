"""pytest configuration for claude/scripts.

Most files here are standalone check scripts, run via ``python <file>.py``:
their ``test_*`` functions take positional args and return bool, which pytest
would mis-collect as fixture requests (ERROR) or return-not-None warnings. Only
test_backfill_ingest.py is pytest-native. Exclude the script-style files from
collection so a plain ``pytest claude/scripts/`` is green — each still runs on
its own via ``python <file>.py`` (see each file's module docstring).
"""

collect_ignore = [
    "test_backfill_inventory.py",
    "test_ce_mode.py",
    "test_discovery_env.py",
    "test_placeholder_defaults.py",
]
