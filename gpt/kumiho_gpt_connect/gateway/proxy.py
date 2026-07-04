"""Manage the inner ``mcp-proxy`` process that wraps stdio ``kumiho-mcp``.

We do not re-implement the MCP protocol. ``mcp-proxy`` runs the stdio server
and exposes it over local HTTP/SSE on loopback; the gateway then reverse-proxies
to it after checking the OAuth Bearer.

NOTE: the exact ``mcp-proxy`` CLI and the HTTP path it exposes vary by version.
The command is overridable via ``KUMIHO_GPT_INNER_CMD`` (shell-split) and the
MCP path via config ``mcp_path`` so this survives upstream changes.
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import time
from typing import Optional

from .. import config as cfgmod


class InnerMcp:
    def __init__(self, cfg: cfgmod.Config, env: Optional[dict] = None) -> None:
        self.cfg = cfg
        self.env = env or {}
        self._proc: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.inner_port}"

    def _command(self) -> list[str]:
        override = (os.getenv("KUMIHO_GPT_INNER_CMD", "") or "").strip()
        if override:
            return shlex.split(override)
        # stdio MCP server to wrap. kumiho-mcp is the console script from
        # kumiho[mcp]; fall back to the module form.
        stdio_cmd = ["kumiho-mcp"]
        return [
            "mcp-proxy",
            "--host", "127.0.0.1",
            "--port", str(self.cfg.inner_port),
            "--",
            *stdio_cmd,
        ]

    def start(self) -> None:
        env = os.environ.copy()
        env.update(self.env)
        # The inner stdout/stderr must never touch our HTTP channel; capture to
        # this process's stderr for logs.
        self._proc = subprocess.Popen(
            self._command(),
            env=env,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"inner mcp-proxy exited early (code {self._proc.returncode}). "
                    "Check that kumiho-mcp and mcp-proxy are installed and the "
                    "backend is reachable."
                )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", self.cfg.inner_port)) == 0:
                    return
            time.sleep(0.4)
        raise TimeoutError(f"inner mcp-proxy did not open port {self.cfg.inner_port} in {timeout}s")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
