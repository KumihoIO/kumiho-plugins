#!/usr/bin/env python3
"""Regression test: Claude Desktop placeholder-default resolution.

Claude Desktop does not expand ``${VAR:-default}`` in .mcp.json — it passes
the literal string through. The launcher must substitute the declared
default the way a shell would, or defaults are lost. The regression that
motivated this: ``KUMIHO_MEMORY_CODE=${KUMIHO_MEMORY_CODE:-1}`` was being
*cleared* instead of resolved to ``1``, silently disabling Decision Memory
(its code tools never registered) on Desktop.

Run:  python claude/scripts/test_placeholder_defaults.py
"""

from __future__ import annotations

import os
import sys

import run_kumiho_mcp as bootstrap


def _reset(env: dict) -> None:
    for k in list(env):
        env.pop(k, None)


def main() -> int:
    cases = {
        # literal .mcp.json values as Desktop passes them through
        "KUMIHO_MEMORY_CODE": ("${KUMIHO_MEMORY_CODE:-1}", "1"),
        "KUMIHO_CONTROL_PLANE_URL": (
            "${KUMIHO_CONTROL_PLANE_URL:-https://control.kumiho.cloud}",
            "https://control.kumiho.cloud",
        ),
        "KUMIHO_MCP_LOG_LEVEL": ("${KUMIHO_MCP_LOG_LEVEL:-INFO}", "INFO"),
        "KUMIHO_CLAUDE_PACKAGE_SPEC": (
            "${KUMIHO_CLAUDE_PACKAGE_SPEC:-kumiho[mcp]>=0.10.5 kumiho-memory[all]>=0.12.1}",
            "kumiho[mcp]>=0.10.5 kumiho-memory[all]>=0.12.1",
        ),
        # empty default -> cleared (equivalent to unset: cloud mode)
        "KUMIHO_CLAUDE_MODE": ("${KUMIHO_CLAUDE_MODE:-}", None),
        # bare placeholder, no default -> cleared (never leak a raw token)
        "KUMIHO_AUTH_TOKEN": ("${KUMIHO_AUTH_TOKEN}", None),
    }

    for key, (literal, _) in cases.items():
        os.environ[key] = literal

    bootstrap._sanitize_placeholder_env_vars()

    failures = []
    for key, (_, expected) in cases.items():
        got = os.environ.get(key)
        if got != expected:
            failures.append(f"  {key}: expected {expected!r}, got {got!r}")

    # unit: _placeholder_default parsing
    pd = bootstrap._placeholder_default
    for literal, expected in (
        ("${A:-1}", "1"),
        ("${A-def}", "def"),
        ("${A:-with spaces & symbols>=1}", "with spaces & symbols>=1"),
        ("${A}", None),
        ("${A:-}", ""),
        ("not-a-placeholder", None),
    ):
        got = pd(literal)
        if got != expected:
            failures.append(f"  _placeholder_default({literal!r}): expected {expected!r}, got {got!r}")

    if failures:
        print("FAIL:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print("OK - placeholder defaults resolved (KUMIHO_MEMORY_CODE -> '1'); "
          "bare/empty placeholders cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
