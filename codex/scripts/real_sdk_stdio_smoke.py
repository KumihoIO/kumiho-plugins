#!/usr/bin/env python3
"""GitHub-CI smoke for the installed Kumiho SDK through the Codex launcher.

This is intentionally not named ``test_*.py``: the ordinary unit suite uses
fake SDK modules and must stay offline.  The dedicated CI job installs the
declared distribution requirements in a temporary shared venv, then invokes
this script explicitly for both hosts and both backend configurations.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TOOLS = {
    "kumiho_memory_engage",
    "kumiho_memory_recall",
    "kumiho_memory_reflect",
}


def _reader(stream, output: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            output.put(line)
    finally:
        output.put(None)


def _collect_lines(stream, output: list[str]) -> None:
    for line in iter(stream.readline, ""):
        output.append(line)


def _require_route_marker(
    host: str, backend: str, stderr_lines: list[str], *, timeout: float = 5.0
) -> None:
    marker = (
        "CE mode: routing to self-hosted endpoint 127.0.0.1:9"
        if backend == "ce"
        else (
            "Official Cloud discovery is not ready"
            if host == "codex"
            else "KUMIHO_AUTH_TOKEN is not set; skipping discovery bootstrap"
        )
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker in "".join(stderr_lines):
            return
        time.sleep(0.05)
    raise RuntimeError(
        f"{host}/{backend} did not select its expected real SDK adapter "
        f"(missing stderr marker {marker!r})"
    )


def _response(
    process: subprocess.Popen[str],
    output: queue.Queue[str | None],
    request_id: int,
    *,
    timeout: float = 30.0,
) -> dict:
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        try:
            line = output.get(timeout=remaining)
        except queue.Empty:
            break
        if line is None:
            break
        text = line.strip()
        if not text:
            continue
        seen.append(text[:500])
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise RuntimeError(
        f"MCP response {request_id} timed out/closed "
        f"(exit={process.poll()}, stdout={seen[-3:]})"
    )


def _send(process: subprocess.Popen[str], message: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


def _run_host_backend(
    host: str,
    backend: str,
    home: Path,
    python: Path,
    plugin_root: Path,
) -> None:
    config_dir = home / ".kumiho"
    config_dir.mkdir(parents=True, exist_ok=True)
    if host == "codex":
        config: dict[str, object] = {"schema_version": 1, "backend": backend}
        if backend == "ce":
            # Port 9 is deliberately closed on the runner. Server construction
            # and tool discovery must not need a live CE deployment.
            config.update(
                endpoint="127.0.0.1:9",
                redis_url="redis://127.0.0.1:9",
                llm_base_url="",
            )
        (config_dir / "codex.json").write_text(
            json.dumps(config) + "\n", encoding="utf-8"
        )

    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("KUMIHO_") or key.startswith("UPSTASH_REDIS_"):
            env.pop(key, None)
    env.update(
        HOME=str(home),
        KUMIHO_CLAUDE_PROVISION_SYNC="1",
        KUMIHO_CLAUDE_PACKAGE_SPEC=(
            "kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0"
        ),
        KUMIHO_MEMORY_DECISIONS="1",
        KUMIHO_AUTO_ASSESS="1",
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_CONFIG_HOME=str(home / ".config"),
        CLAUDE_CONFIG_DIR=str(home / ".claude"),
        CLAUDE_PLUGIN_DATA=str(home / "claude-plugin-data"),
    )
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    if host == "claude":
        env["KUMIHO_CLAUDE_HOST"] = "claude"
        # Claude's existing direct-Python entry is exercised here. Codex must
        # deliberately receive no override so its native Node entry proves it
        # can select the Desktop/shared venv on both operating systems.
        env["KUMIHO_PYTHON"] = str(python)
        if backend == "ce":
            env.update(
                KUMIHO_CLAUDE_MODE="ce",
                KUMIHO_CLAUDE_SERVER_ENDPOINT="127.0.0.1:9",
                UPSTASH_REDIS_URL="redis://127.0.0.1:9",
            )
    node = shutil.which("node", path=env.get("PATH"))
    if host == "codex" and not node:
        raise RuntimeError("node is unavailable")
    launcher = plugin_root / "scripts" / (
        "run_kumiho_mcp.mjs" if host == "codex" else "run_kumiho_mcp.py"
    )
    if not launcher.is_file():
        raise RuntimeError(f"installed {host} snapshot omitted its launcher")
    if host == "codex":
        command = [node, str(launcher)]
    else:
        # The CE cell runs first and models the documented one-time onboarding:
        # it enters through a verified bootstrap interpreter, adopts the shared
        # runtime, and creates Claude's persistent data-dir alias. The Cloud
        # cell then starts through that alias, which is the shipped .mcp.json
        # lifecycle on both POSIX and Windows.
        declared = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        manifest_command = declared["mcpServers"]["kumiho-memory"]["command"]
        if manifest_command != "${CLAUDE_PLUGIN_DATA}/venv/bin/python":
            raise RuntimeError(f"unexpected Claude MCP command: {manifest_command!r}")
        alias_python = home / "claude-plugin-data" / "venv" / "bin" / "python"
        executable = alias_python.with_name("python.exe") if os.name == "nt" else alias_python
        if backend == "cloud":
            if not executable.is_file():
                raise RuntimeError("Claude onboarding did not create its persistent venv alias")
            command = [str(executable), str(launcher)]
        else:
            command = [str(python), str(launcher)]

    process = subprocess.Popen(
        command,
        cwd=plugin_root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=(os.name == "posix"),
    )
    assert process.stdout is not None and process.stderr is not None
    stdout: queue.Queue[str | None] = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(target=_reader, args=(process.stdout, stdout), daemon=True).start()
    threading.Thread(
        target=_collect_lines, args=(process.stderr, stderr_lines), daemon=True
    ).start()

    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "kumiho-ci", "version": "1"},
                },
            },
        )
        initialized = _response(process, stdout, 1, timeout=60)
        if "result" not in initialized:
            raise RuntimeError(f"{host}/{backend} initialize failed: {initialized}")
        _send(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        _send(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed = _response(process, stdout, 2)
        tools = listed.get("result", {}).get("tools", [])
        names = {
            tool.get("name") for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        missing = EXPECTED_TOOLS - names
        if missing:
            raise RuntimeError(f"{host}/{backend} tools/list omitted {sorted(missing)}")

        # The canonical launcher and the Codex Cloud adapter log only after
        # they have configured the real SDK client. This proves each matrix
        # cell selected the requested host/backend route without requiring an
        # external Cloud account or a live CE deployment in pull-request CI.
        _require_route_marker(host, backend, stderr_lines)

        # Exercise the real MCP call dispatcher without contacting either
        # backend. The SDK defines unknown-tool handling as a local operation.
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "__kumiho_ci_unknown_tool__",
                    "arguments": {},
                    "_meta": {"openai/threadId": f"ci-{backend}-thread"},
                },
            },
        )
        called = _response(process, stdout, 3)
        if "error" in called:
            raise RuntimeError(
                f"{host}/{backend} tools/call returned JSON-RPC error: {called}"
            )
        result = called.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        expected_error = "Unknown tool: __kumiho_ci_unknown_tool__"
        decoded_errors: list[object] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                try:
                    decoded_errors.append(json.loads(block.get("text", "")))
                except (json.JSONDecodeError, TypeError):
                    continue
        if {"error": expected_error} not in decoded_errors:
            raise RuntimeError(
                f"{host}/{backend} tools/call did not reach the real Kumiho "
                f"dispatcher: {called}"
            )
        print(
            f"PASS: real SDK {host} {backend} initialize/list/call "
            f"({len(names)} tools)"
        )
    except Exception as exc:
        diagnostic = "".join(stderr_lines)[-4000:]
        raise RuntimeError(
            f"{exc}\n{host}/{backend} stderr tail:\n{diagnostic}"
        ) from exc
    finally:
        _stop(process)


def main() -> int:
    raw_home = (os.getenv("KUMIHO_REAL_SDK_HOME", "") or "").strip()
    raw_python = (os.getenv("KUMIHO_REAL_SDK_PYTHON", "") or "").strip()
    if not raw_home or not raw_python:
        print(
            "KUMIHO_REAL_SDK_HOME and KUMIHO_REAL_SDK_PYTHON are required",
            file=sys.stderr,
        )
        return 2
    home = Path(raw_home).resolve()
    python = Path(raw_python).resolve()
    if not python.is_file() or ROOT in home.parents or home == ROOT:
        print("integration paths are invalid", file=sys.stderr)
        return 2
    snapshots = {
        host: home / f"installed-kumiho-memory-{host}"
        for host in ("codex", "claude")
    }
    for host, plugin_root in snapshots.items():
        if plugin_root.exists():
            shutil.rmtree(plugin_root)
        shutil.copytree(ROOT / host, plugin_root)
    for host in ("codex", "claude"):
        for backend in ("ce", "cloud"):
            _run_host_backend(host, backend, home, python, snapshots[host])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
