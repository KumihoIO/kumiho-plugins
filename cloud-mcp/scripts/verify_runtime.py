"""Fail the image build on missing native libraries or security backports."""
import bz2
import ctypes
import gzip
import json
import lzma
import pathlib
import socket
import sqlite3
import ssl
import subprocess
import sys
import zlib

# Official Ubuntu advisories: CVE-2026-19499, CVE-2026-5435, USN-8954-1.
FLOORS = {"libc6": "2.43-2ubuntu2.4", "zlib1g": "1:1.3.dfsg+really1.3.1-1ubuntu3.2"}
packages = {}
for package, minimum in FLOORS.items():
    version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", package], text=True)
    subprocess.run(["dpkg", "--compare-versions", version, "ge", minimum], check=True)
    packages[package] = version

checked = 0
for root in ("/usr/local", "/opt/venv"):
    for path in pathlib.Path(root).rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
        if "not found" in result.stdout + result.stderr:
            raise RuntimeError(f"Unresolved native dependency: {path}")
        checked += 1

payload = ("Kumiho native runtime / 한글 / \u2603" * 256).encode()
for module in (zlib, gzip, bz2, lzma):
    assert module.decompress(module.compress(payload)) == payload
assert sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,)
assert socket.getaddrinfo("localhost", 443)
assert ssl.create_default_context().get_ca_certs()
ctypes.CDLL(None)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
print(json.dumps({"native_runtime": "passed", "python": sys.version.split()[0], "elf_files": checked, "packages": packages, "openssl": ssl.OPENSSL_VERSION}))
