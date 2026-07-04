"""launchd LaunchAgent for the gateway (macOS)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .. import config as cfgmod

LABEL = "ai.kumiho.gpt-connect"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install() -> None:
    log = cfgmod.config_dir() / "serve.log"
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>Label</key><string>{LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"    <string>{sys.executable}</string>\n"
        "    <string>-m</string>\n"
        "    <string>kumiho_gpt_connect</string>\n"
        "    <string>serve</string>\n"
        "  </array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><true/>\n"
        f"  <key>StandardOutPath</key><string>{log}</string>\n"
        f"  <key>StandardErrorPath</key><string>{log}</string>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(path)], check=False)
    print(f"[kumiho-gpt-connect] installed LaunchAgent: {path}", file=sys.stderr)


def remove() -> None:
    path = _plist_path()
    subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    path.unlink(missing_ok=True)


def status() -> str:
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True, check=False)
    return "loaded" if r.returncode == 0 else "not loaded"
