#!/usr/bin/env python3
"""Guards for the codex plugin's vendored artifacts.

1. The vendored launcher must match the canonical claude launcher —
   marketplace snapshots copy only the plugin directory, so the codex shim
   falls back to ``_vendored_launcher.py`` when ``../claude`` is absent.
   Any edit to ``claude/scripts/run_kumiho_mcp.py`` (e.g. a package-floor
   bump) MUST be re-vendored or snapshot users ship stale behavior.

   Fix on failure::

       cp claude/scripts/run_kumiho_mcp.py codex/scripts/_vendored_launcher.py

2. Every versioned release surface must carry the same version.  The native
   Codex marketplace intentionally has no plugin version, while the Claude
   marketplace, both plugin manifests, and the Claude README do.

Runnable standalone (``python test_launcher_parity.py``) and collected by
pytest (``pytest codex/scripts/test_launcher_parity.py``).
"""

import json
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
CANONICAL = _REPO / "claude" / "scripts" / "run_kumiho_mcp.py"
VENDORED = _HERE / "_vendored_launcher.py"

#: Modules the launcher imports by plain name, which therefore resolve out of
#: whichever scripts/ directory it was started from. A snapshot that ships the
#: launcher without these does not start at all.
VENDORED_DEPS = (
    "bounded_proc.py",
    "codex_thread_context.py",
    "run_kumiho_ce.py",
    "run_kumiho_cloud.py",
)

#: JSON release surfaces whose version string must move in lockstep.  Do not
#: add .agents/plugins/marketplace.json: the native Codex marketplace schema
#: deliberately does not put a version on plugin entries.
JSON_VERSION_SURFACES = (
    _REPO / ".claude-plugin" / "marketplace.json",
    _REPO / "claude" / ".claude-plugin" / "plugin.json",
    _REPO / "codex" / ".codex-plugin" / "plugin.json",
)
CLAUDE_README = _REPO / "claude" / "README.md"
VERSION_SURFACES = JSON_VERSION_SURFACES + (CLAUDE_README,)
README_VERSION = re.compile(r"^Version: \*\*([^*]+)\*\*", re.MULTILINE)


def _surface_versions() -> dict:
    versions = {}
    for path in JSON_VERSION_SURFACES:
        body = json.loads(path.read_text(encoding="utf-8"))
        if "plugins" in body:  # marketplace manifest
            versions[str(path.relative_to(_REPO))] = body["plugins"][0]["version"]
        else:  # plugin manifest
            versions[str(path.relative_to(_REPO))] = body["version"]
    match = README_VERSION.search(CLAUDE_README.read_text(encoding="utf-8"))
    assert match, "claude/README.md has no 'Version: **x.y.z**' line"
    versions[str(CLAUDE_README.relative_to(_REPO))] = match.group(1).strip()
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


def test_vendored_launcher_dependencies_match_canonical():
    """The launcher's own imports must be vendored beside it, not just the
    launcher. A missing sibling is an ImportError at MCP server startup, which
    the host reports only as "server failed to start"."""
    if not CANONICAL.exists():
        return  # plugin snapshot checkout
    for name in VENDORED_DEPS:
        canonical, vendored = CANONICAL.parent / name, _HERE / name
        assert vendored.exists(), (
            f"vendored dependency missing: {vendored} — fix: "
            f"cp claude/scripts/{name} codex/scripts/{name}"
        )
        assert _normalized(canonical) == _normalized(vendored), (
            f"vendored {name} drifted from canonical — fix: "
            f"cp claude/scripts/{name} codex/scripts/{name}"
        )


def test_manifest_versions_locked():
    missing = [str(p.relative_to(_REPO)) for p in VERSION_SURFACES if not p.exists()]
    if missing and not CANONICAL.exists():
        return  # snapshot checkout
    assert not missing, f"version surface(s) missing: {missing}"
    versions = _surface_versions()
    assert len(set(versions.values())) == 1, (
        f"plugin versions diverged — bump all four surfaces together: {versions}"
    )


def test_claude_marketplace_still_targets_claude_plugin():
    """Codex-native packaging must not redirect the Claude marketplace."""
    marketplace = _REPO / ".claude-plugin" / "marketplace.json"
    if not marketplace.exists() and not CANONICAL.exists():
        return  # plugin snapshot checkout
    body = json.loads(marketplace.read_text(encoding="utf-8"))
    entries = [p for p in body.get("plugins", [])
               if p.get("name") == "kumiho-memory"]
    assert len(entries) == 1, "Claude marketplace must have one kumiho-memory entry"
    assert entries[0].get("source") == "./claude", (
        "Claude marketplace source must remain './claude'"
    )


def test_provision_lock_acquisition_is_atomic_and_transferable():
    """One launcher wins; only its detached child can adopt the reservation."""
    if not CANONICAL.exists():
        return
    sys.path.insert(0, str(CANONICAL.parent))
    spec = importlib.util.spec_from_file_location(
        "kumiho_launcher_lock_test", CANONICAL
    )
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    old_home = os.environ.get("KUMIHO_CLAUDE_HOME")
    old_config_dir = os.environ.get("KUMIHO_CONFIG_DIR")
    old_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    try:
        with tempfile.TemporaryDirectory(prefix="kumiho-lock-") as temp:
            os.environ["KUMIHO_CLAUDE_HOME"] = temp
            os.environ["KUMIHO_CONFIG_DIR"] = temp
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            owner = launcher._acquire_provision_lock()
            assert owner, "first process did not acquire the lock"
            assert launcher._acquire_provision_lock() is None, (
                "a second process acquired the same provisioning lock"
            )
            assert launcher._acquire_provision_lock(owner) == owner
            assert launcher._acquire_provision_lock("wrong-owner") is None
            launcher._release_provision_lock("wrong-owner")
            assert launcher._provision_lock_path().exists()
            launcher._release_provision_lock(owner)
            assert not launcher._provision_lock_path().exists()
    finally:
        if old_home is None:
            os.environ.pop("KUMIHO_CLAUDE_HOME", None)
        else:
            os.environ["KUMIHO_CLAUDE_HOME"] = old_home
        if old_config_dir is None:
            os.environ.pop("KUMIHO_CONFIG_DIR", None)
        else:
            os.environ["KUMIHO_CONFIG_DIR"] = old_config_dir
        if old_data is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = old_data


def main() -> int:
    failures = 0
    for test in (test_vendored_launcher_matches_canonical,
                 test_vendored_launcher_dependencies_match_canonical,
                 test_manifest_versions_locked,
                 test_claude_marketplace_still_targets_claude_plugin,
                 test_provision_lock_acquisition_is_atomic_and_transferable):
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
