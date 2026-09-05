#!/usr/bin/env python3
"""Legacy source-checkout setup for the OpenAI Codex CLI.

Uses ``codex mcp`` to register the portable Node launcher in this directory.
The CLI owns TOML parsing and serialization, including quoted table names and
comments; this helper never edits ``config.toml`` as raw text.

Normal users should install the plugin from the repository marketplace
instead. This script remains for source checkouts and older Codex releases.

Usage::

    python codex/scripts/setup_codex.py            # cloud (uses cached auth)
    KUMIHO_CLAUDE_MODE=ce KUMIHO_CLAUDE_SERVER_ENDPOINT=127.0.0.1:9190 \
        python codex/scripts/setup_codex.py        # self-hosted CE

Afterwards: append ``codex/AGENTS.md`` to your project's ``AGENTS.md`` so
the agent follows the memory protocol. From a full checkout you may also
install the optional decision-capture git hook::

    python codex/scripts/install_git_hook.py /path/to/your/repo
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# This script's own messages contain em-dashes, which a legacy Windows code page
# (cp949) cannot encode. A real console gets a UTF-8 wrapper for free, but a
# redirected or captured stdout does not -- and the branch that trips it is the
# idempotent "already registered" one, so a correct install reported failure.
# Module scope, matching claude/scripts/setup.py and backfill_inventory.py.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SERVER_NAME = "kumiho-memory"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Register Kumiho Memory directly from this checkout. "
            "Prefer the native Codex plugin for normal installs."
        )
    )
    return parser.parse_args(argv)


def _checked_value(value: str) -> str:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("configuration values must not contain control characters")
    return value


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    # An empty CODEX_HOME means "use the default" to Codex; leaving an empty
    # string in place has historically redirected helpers to the working tree.
    if not (env.get("CODEX_HOME", "") or "").strip():
        env.pop("CODEX_HOME", None)
    return env


def _codex_executable(env: dict[str, str]) -> str | None:
    explicit = (env.get("CODEX_CLI_PATH", "") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    return shutil.which("codex", path=env.get("PATH"))


def _configured_servers(codex: str, env: dict[str, str]) -> list[dict] | None:
    result = subprocess.run(
        [codex, "mcp", "list", "--json"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, list) else None


def _is_current(server: dict, shim: Path) -> bool:
    transport = server.get("transport")
    if not isinstance(transport, dict):
        return False
    args = transport.get("args")
    if transport.get("command") != "node" or not isinstance(args, list):
        return False
    return len(args) == 1 and Path(str(args[0])).resolve() == shim.resolve()


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    shim = Path(__file__).resolve().parent / "run_kumiho_mcp.mjs"
    child_env = _child_env()
    codex = _codex_executable(child_env)
    if codex is None:
        print("[kumiho-codex] Codex CLI was not found on PATH.", file=sys.stderr)
        return 1

    servers = _configured_servers(codex, child_env)
    if servers is None:
        print(
            "[kumiho-codex] Codex could not parse the MCP configuration; "
            "no changes were made.",
            file=sys.stderr,
        )
        return 2
    existing = next(
        (server for server in servers if server.get("name") == SERVER_NAME),
        None,
    )
    if existing is not None:
        if _is_current(existing, shim):
            print(f"[kumiho-codex] {SERVER_NAME} is already registered and current.")
            return 0
        print(
            f"[kumiho-codex] {SERVER_NAME} is already registered with a different "
            "command. Run `codex mcp remove kumiho-memory`, then rerun this helper.",
            file=sys.stderr,
        )
        return 2

    env: dict = {
        "KUMIHO_CLAUDE_HOST": "codex",
        "KUMIHO_MEMORY_DECISIONS": "1",
        "KUMIHO_AUTO_ASSESS": "1",
        "KUMIHO_CLAUDE_PACKAGE_SPEC": (
            "kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0"
        ),
    }

    mode = (os.getenv("KUMIHO_CLAUDE_MODE", "") or "").strip()
    endpoint = (os.getenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", "") or "").strip()
    if mode and mode.lower() != "ce":
        print("[kumiho-codex] Legacy mode must be `ce` when set.", file=sys.stderr)
        return 2
    if mode or endpoint:
        env["KUMIHO_CODEX_BACKEND"] = "ce"
        env["KUMIHO_CODEX_CE_ENDPOINT"] = endpoint or "127.0.0.1:9190"
    else:
        env["KUMIHO_CODEX_BACKEND"] = "cloud"

    try:
        checked = {key: _checked_value(str(value)) for key, value in env.items()}
        shim_arg = _checked_value(str(shim))
    except ValueError as exc:
        print(f"[kumiho-codex] Refusing unsafe configuration value: {exc}", file=sys.stderr)
        return 2

    command = [codex, "mcp", "add"]
    for key, value in sorted(checked.items()):
        command.extend(["--env", f"{key}={value}"])
    command.extend([SERVER_NAME, "--", "node", shim_arg])
    result = subprocess.run(
        command,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(
            "[kumiho-codex] Codex did not register the MCP server; no manual "
            "TOML fallback was attempted.",
            file=sys.stderr,
        )
        return 2

    print(f"[kumiho-codex] Registered {SERVER_NAME} through `codex mcp add`.")
    print("[kumiho-codex] Next steps:")
    print("  1. Start a new Codex session so the MCP tool catalog is refreshed")
    print("  2. Append codex/AGENTS.md to your project's AGENTS.md")
    print("  3. Optionally install codex/scripts/install_git_hook.py for commit backfill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
