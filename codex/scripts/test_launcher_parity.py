#!/usr/bin/env python3
"""Guards for the codex plugin's vendored artifacts.

1. The vendored launcher must match the canonical claude launcher —
   marketplace snapshots copy only the plugin directory, so the codex shim
   falls back to ``_vendored_launcher.py`` when ``../claude`` is absent.
   Any edit to ``claude/scripts/run_kumiho_mcp.py`` (e.g. a package-floor
   bump) MUST be re-vendored or snapshot users ship stale behavior.

   Fix on failure::

       cp claude/scripts/run_kumiho_mcp.py codex/scripts/_vendored_launcher.py

2. The four plugin manifests must carry the same version — codex plugin
   updates key on the declared semver, so a claude-only bump silently pins
   codex users to a stale snapshot.

Runnable standalone (``python test_launcher_parity.py``) and collected by
pytest (``pytest codex/scripts/test_launcher_parity.py``).
"""

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
CANONICAL = _REPO / "claude" / "scripts" / "run_kumiho_mcp.py"
VENDORED = _HERE / "_vendored_launcher.py"

#: Every manifest whose version string must move in lockstep.
MANIFESTS = (
    _REPO / ".claude-plugin" / "marketplace.json",
    _REPO / "claude" / ".claude-plugin" / "plugin.json",
    _REPO / ".codex-plugin" / "marketplace.json",
    _REPO / "codex" / ".codex-plugin" / "plugin.json",
)


def _manifest_versions() -> dict:
    versions = {}
    for path in MANIFESTS:
        body = json.loads(path.read_text(encoding="utf-8"))
        if "plugins" in body:  # marketplace manifest
            versions[str(path.relative_to(_REPO))] = body["plugins"][0]["version"]
        else:  # plugin manifest
            versions[str(path.relative_to(_REPO))] = body["version"]
    return versions


def _normalized(path: Path) -> bytes:
    # EOL-normalize so mixed checkouts (autocrlf, editors) cannot produce
    # false drift; every semantic difference still fails.
    return path.read_bytes().replace(b"\r\n", b"\n")


def test_vendored_launcher_matches_canonical():
    if not CANONICAL.exists():
        return  # plugin snapshot checkout — nothing to compare against
    assert VENDORED.exists(), f"vendored launcher missing: {VENDORED}"
    assert _normalized(CANONICAL) == _normalized(VENDORED), (
        "vendored launcher drifted from canonical — fix: "
        "cp claude/scripts/run_kumiho_mcp.py codex/scripts/_vendored_launcher.py"
    )


def test_manifest_versions_locked():
    missing = [str(p.relative_to(_REPO)) for p in MANIFESTS if not p.exists()]
    if missing and not CANONICAL.exists():
        return  # snapshot checkout
    assert not missing, f"manifest(s) missing: {missing}"
    versions = _manifest_versions()
    assert len(set(versions.values())) == 1, (
        f"plugin manifest versions diverged — bump all four together: {versions}"
    )


def main() -> int:
    failures = 0
    for test in (test_vendored_launcher_matches_canonical, test_manifest_versions_locked):
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
