"""ngrok tunnel adapter (via pyngrok — pip-installed, auto-downloads the agent).

Free ngrok gives a random ``*.ngrok-free.app`` URL that changes each run; a
reserved domain (paid) gives a stable one. The authtoken is the "tunnel token"
the installer asks for.
"""

from __future__ import annotations

import sys
from typing import Optional

from .base import Tunnel, TunnelError


class NgrokTunnel(Tunnel):
    def __init__(self, local_port: int, token: str = "", domain: str = "") -> None:
        super().__init__(local_port, token)
        self.domain = domain.strip()
        self._tunnel = None

    def start(self) -> str:
        try:
            from pyngrok import conf, ngrok
        except ImportError as exc:  # pragma: no cover
            raise TunnelError("pyngrok is not installed (pip install pyngrok)") from exc

        if self.token:
            conf.get_default().auth_token = self.token
        connect_kwargs = {"proto": "http", "bind_tls": True}
        if self.domain:
            connect_kwargs["domain"] = self.domain
        try:
            self._tunnel = ngrok.connect(self.local_port, **connect_kwargs)
        except Exception as exc:  # pragma: no cover
            raise TunnelError(f"ngrok failed to start: {exc}") from exc
        url = self._tunnel.public_url
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        print(f"[kumiho-gpt-connect] ngrok tunnel: {url}", file=sys.stderr)
        return url

    def stop(self) -> None:
        if self._tunnel is not None:
            try:
                from pyngrok import ngrok

                ngrok.disconnect(self._tunnel.public_url)
            except Exception:
                pass
            self._tunnel = None
