"""systemd --user service for the gateway (Linux)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

UNIT_NAME = "kumiho-gpt-connect.service"


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _exec_start() -> str:
    return f'"{sys.executable}" -m kumiho_gpt_connect serve'


def install() -> None:
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Unit]\n"
        "Description=Kumiho Memory -> ChatGPT connector\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={_exec_start()}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=False)
    # Keep the service running after logout / at boot.
    subprocess.run(["loginctl", "enable-linger"], check=False)
    print(f"[kumiho-gpt-connect] installed systemd user unit: {_unit_path()}", file=sys.stderr)


def remove() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    _unit_path().unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def status() -> str:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", UNIT_NAME],
        capture_output=True, text=True, check=False,
    )
    return (r.stdout or r.stderr).strip() or "unknown"
