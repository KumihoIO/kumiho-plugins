"""Hosted deployments stack revisions strong-only.

``KUMIHO_STACK_MIDDLE_BAND`` is read by the *SDK*, out of ``os.environ``, on
every store — nothing passes it down as an argument. So the property worth
pinning is not "settings parsed a flag" but "after this service starts, the
environment the SDK reads says strong-only", plus the two ways that can go
wrong: an explicit operator value being ignored, and dev mode quietly running a
different gate than production.
"""

from __future__ import annotations

import os

import pytest
from conftest import client_for
from stub_server import build_stub_server

from kumiho_cloud_mcp.app import create_app
from kumiho_cloud_mcp.settings import (
    HOSTED_STACK_MIDDLE_BAND_DEFAULT,
    STACK_MIDDLE_BAND_ENV,
    load_settings,
    middle_band_enabled,
)

pytestmark = pytest.mark.anyio

_ABSENT = object()


@pytest.fixture
def stack_env():
    """Own ``KUMIHO_STACK_MIDDLE_BAND`` for the test, and hand it back after.

    ``create_app`` writes this variable into the real process environment (that
    is the behaviour under test), so the restore has to be unconditional rather
    than monkeypatch's "undo what I recorded" — there is nothing to record when
    the variable starts out absent, which is the interesting case.
    """
    saved = os.environ.get(STACK_MIDDLE_BAND_ENV, _ABSENT)

    class _Env:
        @staticmethod
        def clear() -> None:
            os.environ.pop(STACK_MIDDLE_BAND_ENV, None)

        @staticmethod
        def set(value: str) -> None:
            os.environ[STACK_MIDDLE_BAND_ENV] = value

        @staticmethod
        def get():
            return os.environ.get(STACK_MIDDLE_BAND_ENV, _ABSENT)

    _Env.clear()
    try:
        yield _Env
    finally:
        os.environ.pop(STACK_MIDDLE_BAND_ENV, None)
        if saved is not _ABSENT:
            os.environ[STACK_MIDDLE_BAND_ENV] = saved


def _settings(**over):
    """Settings from an explicit mapping.

    Note that ``load_settings(mapping)`` *replaces* the environment for the
    duration of the call, so a variable that matters has to be in the mapping.
    Deployments call ``load_settings()`` with no argument, which is why the
    tests below assert against that spelling too.
    """
    base = {
        "KUMIHO_MCP_LOG_LEVEL": "CRITICAL",
        "KUMIHO_MCP_ENABLE_SSE": "0",
        "KUMIHO_MCP_JSON_RESPONSE": "1",
    }
    base.update(over)
    return load_settings(base)


# ---------------------------------------------------------------------------
# the setting itself
# ---------------------------------------------------------------------------


def test_hosted_default_is_strong_only(stack_env):
    assert HOSTED_STACK_MIDDLE_BAND_DEFAULT is False
    assert middle_band_enabled() is False
    assert _settings().stack_middle_band is False


def test_the_predicate_matches_the_sdks_own(stack_env):
    """Anything but ``0`` is on — the SDK's rule, not ``_env_bool``'s.

    An operator who wrote ``true`` must get the same answer from /healthz that
    the store path gives itself, or the endpoint reports a mode nothing is in.
    """
    from kumiho.mcp_server import _middle_band_enabled

    for raw in ("1", "true", "on", "yes", "banana", ""):
        stack_env.set(raw)
        assert middle_band_enabled() is True, raw
        assert _middle_band_enabled() is True, raw

    for raw in ("0", " 0 "):
        stack_env.set(raw)
        assert middle_band_enabled() is False, raw
        assert _middle_band_enabled() is False, raw


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


def test_startup_pins_strong_only_into_the_environment(stack_env, fake_clients):
    """The SDK must see ``0`` after startup, however the process was launched.

    A bare ``uvicorn kumiho_cloud_mcp.app:app`` gets no Dockerfile ENV and no
    App Runner env list, so the app itself has to be the thing that sets it.
    """
    from kumiho.mcp_server import _middle_band_enabled, _stack_mode

    assert stack_env.get() is _ABSENT
    # The spelling a deployment uses, against the real (empty) variable.
    assert load_settings().stack_middle_band is False

    create_app(_settings(), server_factory=build_stub_server)

    assert os.environ[STACK_MIDDLE_BAND_ENV] == "0"
    assert _middle_band_enabled() is False
    assert _stack_mode() == "strong-only"


def test_an_explicit_value_wins(stack_env, fake_clients):
    """An operator who turned the band back on keeps it on."""
    from kumiho.mcp_server import _middle_band_enabled, _stack_mode

    stack_env.set("1")
    assert load_settings().stack_middle_band is True

    create_app(
        _settings(KUMIHO_STACK_MIDDLE_BAND="1"), server_factory=build_stub_server
    )

    assert os.environ[STACK_MIDDLE_BAND_ENV] == "1"
    assert _middle_band_enabled() is True
    assert _stack_mode() == "two-band"


def test_an_explicit_zero_is_left_alone(stack_env, fake_clients):
    stack_env.set("0")
    assert load_settings().stack_middle_band is False
    create_app(_settings(KUMIHO_STACK_MIDDLE_BAND="0"), server_factory=build_stub_server)
    assert os.environ[STACK_MIDDLE_BAND_ENV] == "0"


def test_dev_mode_stacks_on_the_production_gate(stack_env, fake_clients):
    """Dev must not exercise a different gate than the one tenants get."""
    from kumiho.mcp_server import _stack_mode

    create_app(
        _settings(KUMIHO_MCP_DEV_MODE="ce", KUMIHO_LOCAL_SERVER_ENDPOINT="127.0.0.1:9190"),
        server_factory=build_stub_server,
    )
    assert os.environ[STACK_MIDDLE_BAND_ENV] == "0"
    assert _stack_mode() == "strong-only"


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


async def test_healthz_reports_the_stacking_mode(settings, control_plane, fake_clients, stack_env):
    app = create_app(settings, server_factory=build_stub_server)
    async with client_for(app, control_plane) as http:
        payload = (await http.get("/healthz")).json()

    assert payload["stacking"] == {"middle_band": False}


async def test_healthz_follows_the_environment_not_the_snapshot(
    settings, control_plane, fake_clients, stack_env
):
    """Read live, so the endpoint cannot report a mode the SDK is not in."""
    app = create_app(settings, server_factory=build_stub_server)
    stack_env.set("1")
    async with client_for(app, control_plane) as http:
        payload = (await http.get("/healthz")).json()

    assert payload["stacking"] == {"middle_band": True}
