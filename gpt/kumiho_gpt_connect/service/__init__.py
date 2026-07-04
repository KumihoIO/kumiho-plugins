"""Auto-launch service management, dispatched by platform.

The service runs ``python -m kumiho_gpt_connect serve`` (using the interpreter
that has the package installed), so it works under a pipx/venv install.
"""

from __future__ import annotations

import os


def _impl():
    if os.name == "nt":
        from . import windows as impl
    else:
        import sys

        if sys.platform == "darwin":
            from . import launchd as impl
        else:
            from . import systemd as impl
    return impl


def install_service() -> None:
    _impl().install()


def remove_service() -> None:
    _impl().remove()


def service_status() -> str:
    return _impl().status()
