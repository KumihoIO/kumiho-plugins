"""Fixtures for the live end-to-end suite.

These tests talk to a *real* dev-mode server over real HTTP, which talks to a
real Kumiho CE gRPC backend and a real Redis. Nothing here is mocked — that is
the point: the hermetic suite in ``tests/`` proves the wiring, this proves the
whole stack agrees on the contract.

Everything skips itself when CE is not listening, so ``pytest -q`` in CI stays
green on a machine with no backend.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, Optional

import pytest

CE_HOST = os.environ.get("KUMIHO_E2E_CE_HOST", "127.0.0.1")
CE_PORT = int(os.environ.get("KUMIHO_E2E_CE_PORT", "9190"))
REDIS_HOST = os.environ.get("KUMIHO_E2E_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("KUMIHO_E2E_REDIS_PORT", "6379"))
SERVER_PORT = int(os.environ.get("KUMIHO_E2E_PORT", "8080"))

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.socket() as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _healthz(port: int, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as fh:
            return json.load(fh)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip the whole directory when the backend is not up."""
    missing = []
    if not port_open(CE_HOST, CE_PORT):
        missing.append(f"Kumiho CE gRPC at {CE_HOST}:{CE_PORT}")
    if not port_open(REDIS_HOST, REDIS_PORT):
        missing.append(f"Redis at {REDIS_HOST}:{REDIS_PORT}")
    if not missing:
        return
    skip = pytest.mark.skip(reason="live backend not reachable: " + ", ".join(missing))
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


class LiveServer:
    """A dev-mode ``kumiho_cloud_mcp`` on loopback, spawned or borrowed."""

    def __init__(self, port: int, log_path: Optional[Path], spawned: bool) -> None:
        self.port = port
        self.log_path = log_path
        self.spawned = spawned

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def healthz(self) -> dict:
        payload = _healthz(self.port)
        assert payload is not None, "healthz did not answer"
        return payload

    def log_lines(self) -> list:
        """Structured startup/request log lines, when we own the process."""
        if self.log_path is None or not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out


@pytest.fixture(scope="session")
def live_server(tmp_path_factory) -> Iterator[LiveServer]:
    """Reuse a dev-mode server on the port if there is one, else spawn one.

    Borrowing keeps a hand-driven debugging loop (uvicorn in one terminal,
    pytest in another) working; spawning is what CI and a cold checkout do. A
    server that answers but is *not* in dev mode is refused rather than driven
    — these tests write memories.
    """
    existing = _healthz(SERVER_PORT)
    if existing is not None:
        if existing.get("dev_mode") != "ce":
            pytest.skip(
                f"something is already serving port {SERVER_PORT} and it is not "
                f"KUMIHO_MCP_DEV_MODE=ce (dev_mode={existing.get('dev_mode')!r})"
            )
        yield LiveServer(SERVER_PORT, None, spawned=False)
        return

    log_path = tmp_path_factory.mktemp("e2e") / "server.log"
    env = dict(os.environ)
    env.update(
        {
            "KUMIHO_MCP_DEV_MODE": "ce",
            "KUMIHO_MCP_LOG_LEVEL": "INFO",
            "PYTHONUNBUFFERED": "1",
            "PORT": str(SERVER_PORT),
            "KUMIHO_LOCAL_SERVER_ENDPOINT": f"{CE_HOST}:{CE_PORT}",
            "KUMIHO_MCP_DEV_REDIS_URL": f"redis://{REDIS_HOST}:{REDIS_PORT}",
        }
    )
    # Ambient single-tenant credentials must not leak into a hosted process;
    # dropping them here is also what makes the run reproducible on a laptop
    # that happens to have the plugin configured.
    for name in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_SERVICE_TOKEN",
        "KUMIHO_SESSION_ID",
        "KUMIHO_MEMORY_DECISIONS",
    ):
        env.pop(name, None)

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "kumiho_cloud_mcp.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(SERVER_PORT),
            ],
            cwd=str(PACKAGE_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "server exited during startup:\n"
                        + log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    )
                if _healthz(SERVER_PORT) is not None:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("server did not become healthy within 60s")
            yield LiveServer(SERVER_PORT, log_path, spawned=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
