"""Tunnel interface. A tunnel exposes the loopback gateway as public HTTPS."""

from __future__ import annotations

import abc


class Tunnel(abc.ABC):
    """Publishes ``http://127.0.0.1:<local_port>`` at a public https URL."""

    def __init__(self, local_port: int, token: str = "") -> None:
        self.local_port = local_port
        self.token = token

    @abc.abstractmethod
    def start(self) -> str:
        """Bring the tunnel up and return the public base URL (https://...)."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Tear the tunnel down."""


class TunnelError(RuntimeError):
    pass
