"""Community Edition backend: detect a running CE server, or guide the user
through the existing turnkey CE installer (which already does the Docker
one-shot for Neo4j + Redis) and wait for it to come up.

We deliberately DELEGATE to the kumiho-server community installer rather than
re-provisioning Neo4j/Redis here — that installer owns the Docker one-shot and
the ``kumiho_server onboard`` wizard (EULA, credentials, launch).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

import httpx

from .backend import CE_ENDPOINT

_LIVE_URL = f"http://{CE_ENDPOINT}/api/_live"
_INSTALL_SH = "curl -fsSL https://github.com/KumihoIO/kumiho-server-community/releases/latest/download/install.sh | sh"
_INSTALL_PS1 = "irm https://github.com/KumihoIO/kumiho-server-community/releases/latest/download/install.ps1 | iex"


def probe_ce(timeout: float = 1.0) -> bool:
    """True when a self-hosted CE server answers on the loopback endpoint."""
    try:
        r = httpx.get(_LIVE_URL, timeout=timeout)
    except httpx.HTTPError:
        return False
    if r.status_code >= 400:
        return False
    try:
        return r.json().get("deployment_mode") == "self_hosted_ce"
    except ValueError:
        return False


def ensure_ce(wait: float = 0.0) -> bool:
    """Ensure a CE server is reachable. Returns True if up.

    If it is not up, print the one-liner that installs + onboards CE (Docker
    one-shot). Optionally poll for up to ``wait`` seconds so an operator can run
    the installer in another terminal without restarting this command.
    """
    if probe_ce():
        return True

    installer = _INSTALL_PS1 if os.name == "nt" else _INSTALL_SH
    print(
        "\n[kumiho-gpt-connect] No local Kumiho CE server detected on "
        f"{CE_ENDPOINT}.\n"
        "Install + start it with the community one-liner (it runs the Docker\n"
        "one-shot for Neo4j + Redis and the onboard wizard):\n\n"
        f"    {installer}\n\n"
        "Then run `kumiho_server onboard` if the installer did not start it.\n",
        file=sys.stderr,
    )
    if wait <= 0:
        return False

    print(f"[kumiho-gpt-connect] waiting up to {int(wait)}s for CE to come up…", file=sys.stderr)
    deadline = time.time() + wait
    while time.time() < deadline:
        if probe_ce():
            print("[kumiho-gpt-connect] CE is up.", file=sys.stderr)
            return True
        time.sleep(2.0)
    return False
