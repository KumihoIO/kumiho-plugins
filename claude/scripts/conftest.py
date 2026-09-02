"""pytest configuration for claude/scripts.

Most files here are standalone check scripts, run via ``python <file>.py``:
their ``test_*`` functions take positional args and return bool, which pytest
would mis-collect as fixture requests (ERROR) or return-not-None warnings. Only
test_backfill_ingest.py is pytest-native. Exclude the script-style files from
collection so a plain ``pytest claude/scripts/`` is green -- each still runs on
its own via ``python <file>.py`` (see each file's module docstring).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

collect_ignore = [
    "test_backfill_inventory.py",
    "test_ce_mode.py",
    "test_discovery_env.py",
    "test_placeholder_defaults.py",
]

#: The settings filenames ``run_kumiho_mcp._candidate_settings_paths`` looks for
#: in every directory it walks.
_SETTINGS_NAMES = ("settings.local.json", "settings.json")


def _ancestor_settings(start: Path) -> list[Path]:
    """Every ``.claude/settings*.json`` at or above *start*, as the launcher sees it."""
    hits = []
    for base in [start, *start.parents]:
        for name in _SETTINGS_NAMES:
            candidate = base / ".claude" / name
            if candidate.exists():
                hits.append(candidate)
    return hits


@pytest.fixture(autouse=True)
def hermetic_home(tmp_path_factory, monkeypatch):
    """Give every test an empty HOME and a working directory with no config above it.

    The code under test deliberately hunts for configuration in the places a
    real installation keeps it. ``run_kumiho_mcp._hydrate_env_from_local_config``
    reads, in order: ``<plugin root>/.env.local``, ``~/.kumiho/.env.local``,
    ``.claude/settings{,.local}.json`` in the working directory **and every
    parent of it**, ``~/.claude/settings{,.local}.json``, the plugin's
    ``.mcp.json``, and finally the cached bearer token in
    ``~/.kumiho/kumiho_authentication.json``. First value found wins.

    That is right in production and poison in a test process. Two things went
    wrong without this fixture, on a machine that has the plugin installed:

    * ``test_reflex_prefetch.py::test_auth_sentinel_skips_before_any_subprocess``
      failed. It asserts the worker skips when there is no auth token; the real
      ``~/.claude/settings.json`` pins ``KUMIHO_CLAUDE_MODE=ce``, the worker took
      the CE branch, which resolves an endpoint instead of hitting the sentinel,
      and the assertion was about a code path the test never reached. A test
      that passes or fails depending on whose machine it runs on is not testing
      the thing it names.
    * More quietly, ``~/.kumiho/kumiho_authentication.json`` hydrated the
      developer's **real bearer token** into the test process. Nothing here
      sends it anywhere -- the one subprocess is replaced by a spy -- but a
      suite should not be loading live credentials at all, and "nothing sends
      it anywhere" is a property of today's code, not a guarantee.

    Redirecting ``HOME``/``USERPROFILE`` fixes the first two reads and neither
    of the directory-walk ones, which is the subtle half. On Windows the
    pytest temp root lives inside the user profile directory itself, so
    a working directory inside it has the real home as an *ancestor* -- the walk
    climbs straight back out to the very file we just hid. So the working
    directory is chosen and then **verified**: the fake home if its ancestry is
    clean, otherwise the filesystem anchor, which has no ancestors at all.

    Ambient ``KUMIHO_*`` and ``CLAUDE_*`` variables are cleared too, so a
    default under test is the default the code computes rather than whatever
    the developer's shell exports. The plugin's own tracked ``.mcp.json`` is
    left alone: it is repo content, identical for everyone, and part of what is
    under test.
    """
    home = tmp_path_factory.mktemp("hermetic-home")
    (home / ".claude").mkdir()
    (home / ".kumiho").mkdir()

    for name in list(os.environ):
        if name.startswith(("KUMIHO_", "CLAUDE_")):
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Only consulted when USERPROFILE is absent, but keep them consistent so
    # the redirection cannot half-apply on some other Windows build.
    drive, tail = os.path.splitdrive(str(home))
    if drive:
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", tail)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    # The credential cache honours this override directly, so the real
    # ~/.kumiho stays unread and unwritten even if HOME were ever missed.
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(home / ".kumiho"))

    workdir = home if not _ancestor_settings(home) else Path(home.anchor)
    monkeypatch.chdir(workdir)

    # Assert rather than assume. expanduser reads a different variable on each
    # platform, and a redirection that silently failed would hand the whole
    # suite back its non-hermetic behaviour with no signal at all -- which is
    # exactly how the two bugs above survived.
    assert Path.home() == home, f"HOME redirection did not take: {Path.home()}"
    leaks = _ancestor_settings(Path.cwd().resolve())
    assert not leaks, f"working directory still sees real settings: {leaks}"

    return home
