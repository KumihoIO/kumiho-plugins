"""Cloudflare Tunnel adapter.

Two modes:

* **Named tunnel (token)** — ``cloudflared tunnel run --token <token>``. The
  public hostname is configured once in the Cloudflare dashboard (Zero Trust →
  Tunnels), with an ingress rule pointing at ``http://127.0.0.1:<gateway_port>``.
  Stable URL, survives restarts. Requires the hostname in config so we know the
  public URL. This is the recommended production setup.

* **Quick tunnel (no token)** — ``cloudflared tunnel --url http://127.0.0.1:port``.
  Prints an ephemeral ``https://<random>.trycloudflare.com`` URL we parse from
  output. Zero config, but the URL changes every run — fine for testing, not
  for a connector you paste once.

``cloudflared`` is fetched to ``~/.kumiho/gpt/bin`` if not already on PATH.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from .. import config as cfgmod
from .base import Tunnel, TunnelError

_QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


class CloudflareTunnel(Tunnel):
    def __init__(self, local_port: int, token: str = "", hostname: str = "") -> None:
        super().__init__(local_port, token)
        self.hostname = hostname.strip()
        self._proc: Optional[subprocess.Popen] = None
        self._url: Optional[str] = None

    def start(self) -> str:
        exe = _ensure_cloudflared()
        if self.token:
            # Named tunnel — hostname is configured externally in Cloudflare.
            if not self.hostname:
                raise TunnelError(
                    "Cloudflare named tunnel needs the public hostname. Set it in "
                    "the dashboard's ingress (→ http://127.0.0.1:%d) and pass "
                    "--cloudflare-hostname." % self.local_port
                )
            cmd = [exe, "tunnel", "run", "--token", self.token]
            self._proc = subprocess.Popen(cmd, stdout=sys.stderr, stderr=sys.stderr)
            self._url = f"https://{self.hostname}"
            return self._url
        # Quick tunnel — parse the ephemeral URL from output.
        cmd = [exe, "tunnel", "--url", f"http://127.0.0.1:{self.local_port}", "--no-autoupdate"]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        self._url = self._scan_for_url(timeout=40)
        if not self._url:
            self.stop()
            raise TunnelError("cloudflared did not report a trycloudflare.com URL")
        return self._url

    def _scan_for_url(self, timeout: float) -> Optional[str]:
        found: dict = {}

        def reader() -> None:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                sys.stderr.write(line)
                m = _QUICK_URL_RE.search(line)
                if m and "url" not in found:
                    found["url"] = m.group(0)
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if "url" in found:
                return found["url"]
            if self._proc and self._proc.poll() is not None:
                return None
            time.sleep(0.3)
        return None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def _ensure_cloudflared() -> str:
    on_path = shutil.which("cloudflared")
    if on_path:
        return on_path
    dest_dir = cfgmod.config_dir() / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    dest = dest_dir / exe_name
    if dest.exists():
        return str(dest)

    asset = _asset_name()
    url = f"{_RELEASE}/{asset}"
    print(f"[kumiho-gpt-connect] downloading cloudflared: {url}", file=sys.stderr)
    tmp = dest_dir / asset
    urllib.request.urlretrieve(url, tmp)
    if asset.endswith(".tgz"):
        with tarfile.open(tmp) as tf:
            member = next((m for m in tf.getmembers() if m.name.endswith("cloudflared")), None)
            if not member:
                raise TunnelError("cloudflared binary not found inside archive")
            with tf.extractfile(member) as src, open(dest, "wb") as out:  # type: ignore[arg-type]
                shutil.copyfileobj(src, out)
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(dest)
    if os.name != "nt":
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return str(dest)


def _asset_name() -> str:
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sysname == "linux":
        return "cloudflared-linux-arm64" if arm else "cloudflared-linux-amd64"
    if sysname == "darwin":
        return "cloudflared-darwin-arm64.tgz" if arm else "cloudflared-darwin-amd64.tgz"
    if sysname == "windows":
        return "cloudflared-windows-amd64.exe"
    raise TunnelError(f"unsupported platform for cloudflared: {sysname}/{machine}")
