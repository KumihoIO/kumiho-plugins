#!/usr/bin/env python3
"""Guard: the vendored launcher must be byte-identical to the canonical one.

Marketplace snapshots copy only the plugin directory, so the codex shim
falls back to ``_vendored_launcher.py`` when ``../claude`` is absent. This
test keeps one source of truth by failing whenever the vendored copy
drifts from ``claude/scripts/run_kumiho_mcp.py``.

Fix on failure::

    cp claude/scripts/run_kumiho_mcp.py codex/scripts/_vendored_launcher.py
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CANONICAL = _HERE.parent.parent / "claude" / "scripts" / "run_kumiho_mcp.py"
VENDORED = _HERE / "_vendored_launcher.py"


def main() -> int:
    if not CANONICAL.exists():
        print("SKIP: canonical launcher not present (plugin snapshot checkout)")
        return 0
    if not VENDORED.exists():
        print(f"FAIL: vendored launcher missing: {VENDORED}")
        return 1
    if CANONICAL.read_bytes() != VENDORED.read_bytes():
        print(
            "FAIL: vendored launcher drifted from canonical.\n"
            f"  canonical: {CANONICAL}\n  vendored:  {VENDORED}\n"
            "  fix: cp claude/scripts/run_kumiho_mcp.py "
            "codex/scripts/_vendored_launcher.py"
        )
        return 1
    print("PASS: vendored launcher matches canonical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
