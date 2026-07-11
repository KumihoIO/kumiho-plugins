#!/usr/bin/env python3
"""One-shot setup: register Kumiho memory with the OpenAI Codex CLI.

Adds an ``[mcp_servers.kumiho-memory]`` block to ``~/.codex/config.toml``
(idempotent — an existing block is left untouched), pointing at the shim
launcher in this directory.  Codex does not expand ``${VAR}`` placeholders
in config values, so auth/mode values are materialized at setup time from
the current environment and the cached ``kumiho-auth`` login.

Usage::

    python codex/scripts/setup_codex.py            # cloud (uses cached auth)
    KUMIHO_CLAUDE_MODE=ce KUMIHO_CLAUDE_SERVER_ENDPOINT=127.0.0.1:50051 \
        python codex/scripts/setup_codex.py        # self-hosted CE

Afterwards: append ``codex/AGENTS.md`` to your project's ``AGENTS.md`` so
the agent follows the memory protocol, and (recommended) install the
decision auto-capture git hook::

    python codex/scripts/install_git_hook.py /path/to/your/repo
"""

import importlib.util
import os
import sys
from pathlib import Path

SECTION = "mcp_servers.kumiho-memory"


def _load_launcher_module():
    path = (
        Path(__file__).resolve().parent.parent.parent
        / "claude" / "scripts" / "run_kumiho_mcp.py"
    )
    spec = importlib.util.spec_from_file_location("kumiho_claude_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    config_path = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    if f"[{SECTION}]" in existing:
        print(f"[kumiho-codex] {config_path} already has [{SECTION}] — leaving it untouched.")
        return 0

    shim = Path(__file__).resolve().parent / "run_kumiho_mcp.py"

    env: dict = {"KUMIHO_MEMORY_CODE": "1", "KUMIHO_AUTO_ASSESS": "1"}

    mode = (os.getenv("KUMIHO_CLAUDE_MODE", "") or "").strip()
    endpoint = (os.getenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", "") or "").strip()
    if mode:
        env["KUMIHO_CLAUDE_MODE"] = mode
    if endpoint:
        env["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = endpoint

    if not mode and not endpoint:
        # Cloud mode: materialize the auth token (Codex cannot expand ${...}).
        launcher = _load_launcher_module()
        token = ""
        try:
            token = launcher._load_bearer_token() or ""
        except Exception:  # noqa: BLE001
            token = ""
        if not token:
            print(
                "[kumiho-codex] No auth token found (env or cached login). "
                "Run `kumiho-auth login` first, or set KUMIHO_CLAUDE_MODE=ce "
                "with KUMIHO_CLAUDE_SERVER_ENDPOINT for a self-hosted CE.",
                file=sys.stderr,
            )
            return 1
        env["KUMIHO_AUTH_TOKEN"] = token

    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KUMIHO_LLM_API_KEY",
                "KUMIHO_LLM_BASE_URL", "KUMIHO_LLM_MODEL"):
        val = (os.getenv(key, "") or "").strip()
        if val:
            env[key] = val

    env_toml = ", ".join(f'{k} = "{_toml_escape(v)}"' for k, v in env.items())
    block = (
        f"\n[{SECTION}]\n"
        f'command = "python"\n'
        f'args = ["{_toml_escape(str(shim))}"]\n'
        f"env = {{ {env_toml} }}\n"
    )

    with open(config_path, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(block)

    print(f"[kumiho-codex] Registered [{SECTION}] in {config_path}")
    print("[kumiho-codex] Next steps:")
    print("  1. Append codex/AGENTS.md to your project's AGENTS.md")
    print("  2. python codex/scripts/install_git_hook.py <your-repo>   # decision auto-capture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
