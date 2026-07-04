"""Scheduled Task (at logon) for the gateway (Windows)."""

from __future__ import annotations

import subprocess
import sys

TASK_NAME = "KumihoGptConnect"


def _tr() -> str:
    # schtasks needs the whole run string as one argument; quote the exe.
    return f'"{sys.executable}" -m kumiho_gpt_connect serve'


def install() -> None:
    subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _tr(),
         "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
        check=False,
    )
    # Start it now too, so the user does not have to log out/in first.
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=False)
    print(f"[kumiho-gpt-connect] installed scheduled task: {TASK_NAME}", file=sys.stderr)


def remove() -> None:
    subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], check=False)
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)


def status() -> str:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True, check=False
    )
    return "installed" if r.returncode == 0 else "not installed"
