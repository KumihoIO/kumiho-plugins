#!/usr/bin/env python3
"""Tests for first-run provisioning: the async handoff and its lock.

Measured 2026-07-31: a cold provision takes 205-320 s while the host's MCP
startup budget is 30 s (``MCP_TIMEOUT``, default 30000 in the shipped binary).
Building inline was not slow-but-working, it was guaranteed failure -- the host
gave up, killed the process, and took pip with it, so the FIRST session of every
new install could never connect and the next one started over.

The launcher now hands a cold build to a detached child and exits immediately
(measured 0.8 s). That introduced the one hazard these tests pin: provisioning
must never run twice at once, because two ``pip install`` runs against a single
venv interleave their writes. Observed live while testing -- a concurrent
``--self-test`` started a second pip against the same tree.

Run: python -m pytest claude/scripts/test_provisioning.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _launcher():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_kumiho_mcp", SCRIPTS / "run_kumiho_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _launcher()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMIHO_CLAUDE_HOME", str(tmp_path))
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(L._SYNC_PROVISION_ENV, raising=False)
    monkeypatch.delenv(L._PROVISION_LOCK_TOKEN_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    return tmp_path


def test_the_venv_is_shared_under_kumiho_home_even_when_plugin_data_is_named(monkeypatch, tmp_path):
    """Claude and Codex must resolve the same host-neutral package runtime."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pdata"))
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path / "kumiho"))
    assert L._venv_dir() == tmp_path / "kumiho" / "venv"


def test_an_unexpanded_placeholder_is_not_treated_as_a_path(monkeypatch, tmp_path):
    """A host that does not substitute would otherwise have us build a venv in a
    directory literally named '${CLAUDE_PLUGIN_DATA}'."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "${CLAUDE_PLUGIN_DATA}")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path))
    assert L._venv_dir() == tmp_path / "venv"


def test_the_data_dir_is_derived_when_the_host_does_not_supply_it(monkeypatch, tmp_path):
    """--provision, --self-test and the wizard are not host-spawned, so they get
    no CLAUDE_PLUGIN_DATA and must derive the same path the host would use:
    <config>/plugins/data/<plugin>-<marketplace>, which carries no version and
    survives updates."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    fake = tmp_path / "plugins" / "cache" / "kumiho-plugins" / "kumiho-memory" / "0.18.2" / "scripts" / "run_kumiho_mcp.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "__file__", str(fake))
    assert L._plugin_data_dir() == tmp_path / "plugins" / "data" / "kumiho-memory-kumiho-plugins"


def test_a_dev_checkout_uses_the_shared_kumiho_home(monkeypatch, tmp_path):
    """A checkout and an installed plugin use the same runtime location."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(L, "__file__", str(tmp_path / "repo" / "claude" / "scripts" / "run_kumiho_mcp.py"))
    assert L._plugin_data_dir() is None
    assert L._venv_dir() == tmp_path / "venv"


def test_cold_start_hands_off_and_exits_instead_of_blocking(state, monkeypatch):
    """The whole point: a cold start must not sit on the host's startup clock."""
    spawned = []
    monkeypatch.setattr(
        L, "_spawn_detached_provisioning",
        lambda token: spawned.append(token) or True,
    )
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert len(spawned) == 1, "it must actually start the background build"
    assert L._read_provision_lock()["token"] == spawned[0]
    assert "not built yet" in str(exc.value)
    assert "exiting now ON PURPOSE" in str(exc.value)


def test_a_second_cold_start_does_not_spawn_a_second_provisioner(state, monkeypatch):
    """Hosts retry. Each retry used to be another pip against the same venv."""
    spawned = []
    monkeypatch.setattr(
        L, "_spawn_detached_provisioning",
        lambda token: spawned.append(token) or True,
    )
    with pytest.raises(SystemExit):
        L._ensure_runtime()
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert len(spawned) == 1, "the second attempt must not spawn again"
    assert "Another process is provisioning" in str(exc.value)


def test_existing_runtime_update_is_detached_from_host_startup(state, monkeypatch):
    """Desktop may leave a usable Python with old or partial packages.  Pip
    must not run on the MCP startup clock merely because python.exe exists."""
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    probes = []
    spawned = []

    def needs_update(*_args, probe_timeout_s=L.PROBE_TIMEOUT_S):
        probes.append(probe_timeout_s)
        return True

    monkeypatch.setattr(L, "_needs_install", needs_update)
    monkeypatch.setattr(
        L, "_spawn_detached_provisioning", lambda token: spawned.append(token) or True
    )
    monkeypatch.setattr(
        L, "_provision", lambda *_a: pytest.fail("parent must never run pip")
    )

    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert probes == [L.STARTUP_PROBE_TIMEOUT_S]
    assert len(spawned) == 1
    assert "needs an install/update" in str(exc.value)
    L._release_provision_lock(spawned[0])


def test_detached_child_adopts_and_heartbeats_before_existing_venv_work(
    state, monkeypatch
):
    """The parent's handshake waits only five seconds, so the child cannot
    probe an existing shared runtime before transferring lock ownership."""
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    token = L._acquire_provision_lock()
    assert token
    monkeypatch.setenv(L._PROVISION_LOCK_TOKEN_ENV, token)
    refreshed = []
    real_refresh = L._refresh_provision_lock

    def observed_refresh(value):
        refreshed.append(value)
        return real_refresh(value)

    def observed_provision(*_args):
        record = L._read_provision_lock()
        assert record["pid"] == os.getpid()
        assert record["adopted"] is True
        assert refreshed == [token]
        return venv_python

    monkeypatch.setattr(L, "_refresh_provision_lock", observed_refresh)
    monkeypatch.setattr(L, "_provision", observed_provision)
    monkeypatch.setattr(
        L, "_needs_install", lambda *_a, **_kw: pytest.fail("probe preceded adopt")
    )

    assert L._ensure_runtime() == venv_python
    assert not L._provision_lock_path().exists()


def test_detached_provision_main_reaches_lock_adoption_before_hook_repair(
    state, monkeypatch
):
    """The real child enters through main(); keep pre-handshake host work out
    of that path, not merely out of _ensure_runtime()."""
    token = L._acquire_provision_lock()
    assert token
    monkeypatch.setenv(L._PROVISION_LOCK_TOKEN_ENV, token)
    monkeypatch.setattr(sys, "argv", [str(SCRIPTS / "run_kumiho_mcp.py"), "--provision"])
    calls = []
    monkeypatch.setattr(L, "_ensure_runtime", lambda: calls.append("ensure"))
    monkeypatch.setattr(
        L,
        "_ensure_hook_interpreter",
        lambda *_a: pytest.fail("hook repair ran before lock adoption"),
    )
    monkeypatch.setattr(
        L,
        "_hydrate_env_from_local_config",
        lambda: pytest.fail("host hydration ran before lock adoption"),
    )
    try:
        assert L.main() == 0
        assert calls == ["ensure"]
    finally:
        L._release_provision_lock(token)


def test_live_writer_blocks_probe_of_existing_runtime(state, monkeypatch):
    """Never import metadata from a venv while another process mutates it."""
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    token = L._acquire_provision_lock()
    assert token
    monkeypatch.setattr(
        L, "_needs_install", lambda *_a, **_kw: pytest.fail("mutable venv probed")
    )

    try:
        with pytest.raises(SystemExit) as exc:
            L._ensure_runtime()
        assert "Another process is provisioning" in str(exc.value)
    finally:
        L._release_provision_lock(token)


def test_detached_child_rechecks_and_skips_pip_when_race_is_already_satisfied(
    state, monkeypatch
):
    """A parent probe is advisory; the lock-owning child rechecks, and a
    concurrent successful updater must turn its work into a no-op."""
    venv_dir = state / "venv"
    venv_python = L._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    token = L._acquire_provision_lock()
    assert token
    monkeypatch.setenv(L._PROVISION_LOCK_TOKEN_ENV, token)
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: False)
    monkeypatch.setattr(L, "_python_interpreter_works", lambda *_a: True)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)
    monkeypatch.setattr(L, "_ensure_plugin_data_venv_alias", lambda *_a: None)
    monkeypatch.setattr(
        L, "_install_dependencies", lambda *_a: pytest.fail("pip must be skipped")
    )

    assert L._ensure_runtime() == venv_python
    assert not L._provision_lock_path().exists()


def test_atomic_lock_has_exactly_one_owner(state):
    """O_EXCL closes the check-then-write race between simultaneous starts."""
    first = L._acquire_provision_lock()
    second = L._acquire_provision_lock()
    assert first
    assert second is None
    assert L._read_provision_lock()["token"] == first
    L._release_provision_lock(first)


def test_lock_write_failure_removes_its_own_reservation(state, monkeypatch):
    """A failed write/fsync must not wedge every retry for 30 minutes."""
    real_fsync = L.os.fsync

    def fail_fsync(_fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(L.os, "fsync", fail_fsync)
    assert L._acquire_provision_lock() is None
    assert not L._provision_lock_path().exists()

    monkeypatch.setattr(L.os, "fsync", real_fsync)
    retry = L._acquire_provision_lock()
    assert retry
    L._release_provision_lock(retry)


def test_lock_fstat_failure_closes_handle_and_removes_reservation(state, monkeypatch):
    real_fstat = L.os.fstat

    def fail_fstat(_fd):
        raise OSError("injected fstat failure")

    monkeypatch.setattr(L.os, "fstat", fail_fstat)
    assert L._acquire_provision_lock() is None
    assert not L._provision_lock_path().exists()

    monkeypatch.setattr(L.os, "fstat", real_fstat)
    retry = L._acquire_provision_lock()
    assert retry
    L._release_provision_lock(retry)


def test_detached_child_adopts_only_its_parent_reservation(state):
    token = L._acquire_provision_lock()
    assert token
    assert L._acquire_provision_lock(token) == token
    record = L._read_provision_lock()
    assert record["pid"] == os.getpid()
    assert record["adopted"] is True
    assert L._acquire_provision_lock("wrong-token") is None
    L._release_provision_lock("wrong-token")
    assert L._provision_lock_path().exists()
    L._release_provision_lock(token)
    assert not L._provision_lock_path().exists()


def test_child_death_before_adoption_releases_parent_reservation(state, monkeypatch):
    class DeadChild:
        pid = 987654

        @staticmethod
        def poll():
            return 1

    monkeypatch.setattr(L.subprocess, "Popen", lambda *_a, **_kw: DeadChild())
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert "Could not start background provisioning" in str(exc.value)
    assert not L._provision_lock_path().exists()


def test_sync_provisioning_refuses_to_race_a_live_holder(state, monkeypatch):
    """--provision / --self-test are not on a clock, but they still must not
    interleave pip writes with another provisioner."""
    monkeypatch.setenv(L._SYNC_PROVISION_ENV, "1")
    L._provision_lock_path().write_text(str(os.getpid()), encoding="utf-8")
    called = []
    monkeypatch.setattr(L, "_provision", lambda *a: called.append(1))
    with pytest.raises(SystemExit) as exc:
        L._ensure_runtime()
    assert not called
    assert "Another process is provisioning" in str(exc.value)


def test_a_stale_lock_is_broken_rather_than_wedging_the_install(state, monkeypatch):
    """A provisioner killed mid-install must not lock the venv out forever."""
    lock = L._provision_lock_path()
    lock.write_text("999999", encoding="utf-8")
    old = time.time() - (L.PROVISION_LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    assert not L._provision_in_progress()
    assert not lock.exists(), "the stale lock should have been cleared"


def test_an_old_lock_owned_by_a_live_process_is_never_broken(state):
    """A slow or stuck pip remains the sole writer even past stale threshold."""
    token = L._acquire_provision_lock()
    assert token
    lock = L._provision_lock_path()
    old = time.time() - (L.PROVISION_LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    assert L._provision_in_progress()
    assert lock.exists()
    assert L._refresh_provision_lock(token)
    assert (time.time() - lock.stat().st_mtime) < 5
    L._release_provision_lock(token)
    assert not lock.exists()


def test_an_old_legacy_pid_lock_owned_by_a_live_process_is_never_broken(state):
    lock = L._provision_lock_path()
    lock.write_text(str(os.getpid()), encoding="utf-8")
    old = time.time() - (L.PROVISION_LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    assert L._provision_in_progress()
    assert lock.exists()


def test_a_reused_pid_does_not_resurrect_a_dead_lock(state, monkeypatch):
    lock = L._provision_lock_path()
    lock.write_text(
        json.dumps({
            "pid": os.getpid(),
            "process_start": "birth-marker-from-an-older-process",
            "token": "stale-token",
        }),
        encoding="utf-8",
    )
    old = time.time() - (L.PROVISION_LOCK_STALE_S + 60)
    os.utime(lock, (old, old))
    assert not L._provision_in_progress()
    assert not lock.exists()


def test_provision_heartbeat_runs_for_the_whole_locked_operation(state, monkeypatch):
    token = L._acquire_provision_lock()
    assert token
    calls = []
    real_refresh = L._refresh_provision_lock

    def observed_refresh(value):
        calls.append(value)
        return real_refresh(value)

    monkeypatch.setattr(L, "_refresh_provision_lock", observed_refresh)
    with L._provision_lock_heartbeat(token):
        assert L._provision_lock_path().exists()
    assert calls == [token], "context entry must immediately refresh its lock"
    L._release_provision_lock(token)


def test_lost_compat_lock_aborts_before_refreshing_canonical(state, monkeypatch):
    """Never advertise a live bundle after the legacy mutex was replaced."""
    compat = state / "claude-data" / "provision.lock"
    monkeypatch.setattr(L, "_desktop_compat_lock_paths", lambda: [compat])
    monkeypatch.setattr(L, "_desktop_compat_lock_candidates", lambda: [compat])
    token = L._acquire_provision_lock()
    assert token
    compat.write_text(
        json.dumps({"pid": os.getpid(), "token": "replacement"}),
        encoding="utf-8",
    )
    touched = []
    monkeypatch.setattr(L.os, "utime", lambda path, *_a: touched.append(Path(path)))

    assert not L._refresh_provision_lock(token)
    assert touched == [], "canonical heartbeat must commit only after every alias"
    L._release_provision_lock(token)


def test_lost_compat_lock_is_detected_before_marker_commit(state, monkeypatch):
    compat = state / "claude-data" / "provision.lock"
    monkeypatch.setattr(L, "_desktop_compat_lock_paths", lambda: [compat])
    monkeypatch.setattr(L, "_desktop_compat_lock_candidates", lambda: [compat])
    token = L._acquire_provision_lock()
    assert token

    with pytest.raises(RuntimeError, match="lost the shared-runtime"):
        with L._provision_lock_heartbeat(token):
            compat.write_text(
                json.dumps({"pid": os.getpid(), "token": "replacement"}),
                encoding="utf-8",
            )
            L._write_install_marker(L._marker_path(), L.DEFAULT_PACKAGE_SPEC)

    assert not L._marker_path().exists()
    L._release_provision_lock(token)


def test_lost_lock_event_aborts_bounded_children(state, monkeypatch):
    """Heartbeat cancellation reaches the process-tree runner immediately."""
    token = L._acquire_provision_lock()
    assert token
    monkeypatch.setattr(
        L.bounded_proc,
        "_spawn_process",
        lambda *_a, **_kw: pytest.fail("an already-aborted command must not spawn"),
    )
    try:
        with pytest.raises(RuntimeError, match="lost the shared-runtime"):
            with L._provision_lock_heartbeat(token):
                event = L.bounded_proc._ABORT_EVENT.get()
                assert event is not None
                event.set()
                L.bounded_proc.run(["pip"], timeout=30)
    finally:
        L._release_provision_lock(token)


def test_process_aborted_during_venv_creation_never_removes_the_shared_tree(
    state, monkeypatch
):
    """A replacement owner may already be building the same path after loss."""
    venv_dir = state / "venv"
    python_path = L._venv_python(venv_dir)
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        L, "_base_interpreter_for_venv_creation", lambda *_a: Path(sys.executable)
    )

    def abort_creation(*_args, **_kwargs):
        # Model bounded_proc observing the heartbeat's lost event while the
        # venv child is active. The competing owner has already created data.
        venv_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "new-owner.txt").write_text("keep", encoding="utf-8")
        raise L.bounded_proc.ProcessAborted(["python", "-m", "venv"])

    removed = []
    monkeypatch.setattr(L, "_run", abort_creation)
    monkeypatch.setattr(
        L.shutil,
        "rmtree",
        lambda path, **_kw: removed.append(Path(path)),
    )

    with pytest.raises(L.bounded_proc.ProcessAborted):
        L._provision(
            venv_dir, python_path, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
        )

    assert removed == []
    assert (venv_dir / "new-owner.txt").read_text(encoding="utf-8") == "keep"


def test_lost_lock_blocks_marker_attestation_and_alias_mutations(
    state, monkeypatch
):
    """Every pure filesystem commit consults the synchronous ownership guard."""
    plugin_data = state / "plugin-data"
    venv_dir = state / "venv"
    python_path = L._venv_python(venv_dir)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("python", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setattr(L, "_runtime_fingerprint", lambda *_a: {"schema": 2})

    def lost():
        raise L.bounded_proc.ProcessAborted(["shared-runtime-provisioning"])

    guard_token = L._ACTIVE_PROVISION_LOCK_GUARD.set(lost)
    try:
        with pytest.raises(L.bounded_proc.ProcessAborted):
            L._write_install_marker(L._marker_path(), L.DEFAULT_PACKAGE_SPEC)
        with pytest.raises(L.bounded_proc.ProcessAborted):
            L._write_runtime_attestation(
                venv_dir,
                python_path,
                L._marker_path(),
                L.DEFAULT_PACKAGE_SPEC,
            )
        with pytest.raises(L.bounded_proc.ProcessAborted):
            L._ensure_plugin_data_venv_alias(venv_dir)
    finally:
        L._ACTIVE_PROVISION_LOCK_GUARD.reset(guard_token)

    assert not L._marker_path().exists()
    assert not L._runtime_attestation_path().exists()
    assert not plugin_data.exists()


def test_bounded_runner_times_out_without_waiting_forever():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        L._run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.1,
        )
    assert time.monotonic() - started < 10


def test_the_warm_path_never_provisions(state, monkeypatch):
    """A satisfied venv may attest once, but must never invoke provisioning."""
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    L._marker_path().write_text(L.DEFAULT_PACKAGE_SPEC, encoding="utf-8")
    monkeypatch.setattr(L, "_needs_install", lambda *a, **kw: False)
    monkeypatch.setattr(L, "_provision", lambda *a: pytest.fail("must not provision"))

    assert L._ensure_runtime() == venv_python
    assert not L._provision_lock_path().exists()


def test_warm_path_creates_the_legacy_alias_only_after_attestation(
    state, monkeypatch
):
    """Releasing the new legacy mutex must be the last locked mutation."""
    venv_dir = state / "venv"
    venv_python = L._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("python", encoding="utf-8")
    order = []
    monkeypatch.setattr(L, "_runtime_attestation_matches", lambda *_a: False)
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: False)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: order.append("hook"))
    monkeypatch.setattr(L, "_write_install_marker", lambda *_a: order.append("marker"))
    monkeypatch.setattr(
        L, "_write_runtime_attestation", lambda *_a: order.append("attestation")
    )
    monkeypatch.setattr(
        L, "_ensure_plugin_data_venv_alias", lambda *_a: order.append("alias")
    )

    assert L._ensure_runtime() == venv_python
    assert order == ["hook", "marker", "attestation", "alias"]


def test_full_probe_attestation_breaks_a_slow_runtime_handoff_loop(
    state, monkeypatch
):
    """A detached full probe must leave a fast-path result for the next start."""
    venv_dir = state / "venv"
    venv_python = L._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("python", encoding="utf-8")
    (venv_dir / "pyvenv.cfg").write_text("version = 3.12.0\n", encoding="utf-8")
    site_packages = (
        venv_dir / "Lib" / "site-packages"
        if os.name == "nt"
        else venv_dir / "lib" / "python3.12" / "site-packages"
    )
    site_packages.mkdir(parents=True)
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: False)
    monkeypatch.setattr(L, "_windows_pe_executable", lambda *_a: True)
    monkeypatch.setattr(L, "_python_interpreter_works", lambda *_a: True)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)
    monkeypatch.setattr(L, "_ensure_plugin_data_venv_alias", lambda *_a: None)

    assert L._provision(
        venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
    ) == venv_python
    assert L._runtime_attestation_matches(
        venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
    )

    monkeypatch.setattr(
        L,
        "_needs_install",
        lambda *_a, **_kw: pytest.fail("attested runtime was probed again"),
    )
    assert L._ensure_runtime() == venv_python


def test_runtime_attestation_is_invalidated_by_site_packages_mutation(
    state, monkeypatch
):
    venv_dir = state / "venv"
    venv_python = L._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("python", encoding="utf-8")
    (venv_dir / "pyvenv.cfg").write_text("version = 3.12.0\n", encoding="utf-8")
    site_packages = (
        venv_dir / "Lib" / "site-packages"
        if os.name == "nt"
        else venv_dir / "lib" / "python3.12" / "site-packages"
    )
    site_packages.mkdir(parents=True)
    monkeypatch.setattr(L, "_windows_pe_executable", lambda *_a: True)
    L._write_install_marker(L._marker_path(), L.DEFAULT_PACKAGE_SPEC)
    L._write_runtime_attestation(
        venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
    )
    (site_packages / "external-update.dist-info").mkdir()
    assert not L._runtime_attestation_matches(
        venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
    )


def test_install_must_pass_a_full_probe_before_attestation(state, monkeypatch):
    venv_dir = state / "venv"
    venv_python = L._venv_python(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("python", encoding="utf-8")
    probes = iter((True, True))
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: next(probes))
    monkeypatch.setattr(L, "_python_interpreter_works", lambda _path: True)
    monkeypatch.setattr(L, "_install_dependencies", lambda *_a: None)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)

    with pytest.raises(RuntimeError, match="did not satisfy"):
        L._provision(
            venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
        )

    assert not L._marker_path().exists()
    assert not L._runtime_attestation_path().exists()


def test_existing_runtime_without_python_is_preserved_before_rebuild(
    state, monkeypatch
):
    venv_dir = state / "venv"
    venv_dir.mkdir(parents=True)
    (venv_dir / "desktop-data.txt").write_text("preserve", encoding="utf-8")
    venv_python = L._venv_python(venv_dir)
    probes = iter((True, False))
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: next(probes))
    monkeypatch.setattr(L, "_python_interpreter_works", lambda _path: True)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)
    monkeypatch.setattr(L, "_install_dependencies", lambda *_a: None)
    monkeypatch.setattr(L, "_write_install_marker", lambda *_a: None)
    monkeypatch.setattr(L, "_write_runtime_attestation", lambda *_a: None)
    monkeypatch.setattr(L, "_ensure_plugin_data_venv_alias", lambda *_a: None)

    def create_replacement(*_args, **_kwargs):
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.write_text("replacement", encoding="utf-8")
        return 0

    monkeypatch.setattr(L, "_run", create_replacement)
    assert L._provision(
        venv_dir, venv_python, L._marker_path(), L.DEFAULT_PACKAGE_SPEC
    ) == venv_python

    backups = list(state.glob("venv.broken-*"))
    assert len(backups) == 1
    assert (backups[0] / "desktop-data.txt").read_text(encoding="utf-8") == (
        "preserve"
    )
    assert not (venv_dir / "desktop-data.txt").exists()


def test_provisioning_children_do_not_receive_host_credentials(state, monkeypatch):
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "cloud-bearer")
    monkeypatch.setenv("OPENAI_API_KEY", "model-secret")
    monkeypatch.setenv("KUMIHO_CONTROL_PLANE_URL", "https://custom.example.test")
    monkeypatch.setenv("KUMIHO_FIREBASE_ID_TOKEN", "firebase-secret")
    monkeypatch.setenv("KUMIHO_USE_CONTROL_PLANE_TOKEN", "1")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "redis-secret")
    monkeypatch.setenv("UPSTASH_REDIS_URL", "rediss://user:secret@redis.test")
    monkeypatch.setenv("KUMIHO_UPSTASH_REDIS_URL", "rediss://alias.test")
    monkeypatch.setenv("KUMIHO_LOCAL_REDIS_URL", "rediss://local.test")
    monkeypatch.setenv("KUMIHO_MEMORY_PROXY_URL", "https://proxy.example.test")
    monkeypatch.setenv("KUMIHO_MCP_HOSTED", "1")
    monkeypatch.setenv("KUMIHO_HOSTED_LOCAL_REDIS", "1")
    monkeypatch.setenv("KUMIHO_LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("KUMIHO_CODEX_CE_ENDPOINT", "127.0.0.1:9190")
    monkeypatch.setenv("EXAMPLE_ACCESS_TOKEN", "generic-secret")
    monkeypatch.setenv("PIP_INDEX_URL", "https://packages.example.test/simple")
    monkeypatch.setenv("KUMIHO_CONFIG_DIR", str(state))
    child = L._provision_subprocess_env({
        "KUMIHO_CLAUDE_PROVISION_SYNC": "1",
        "KUMIHO_CLAUDE_PROVISION_LOCK_TOKEN": "reservation",
    })
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "UPSTASH_REDIS_REST_TOKEN",
        "UPSTASH_REDIS_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_LOCAL_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_CODEX_CE_ENDPOINT",
        "EXAMPLE_ACCESS_TOKEN",
    ):
        assert key not in child
    assert child["KUMIHO_CONFIG_DIR"] == str(state)
    assert child["KUMIHO_CLAUDE_PROVISION_SYNC"] == "1"
    assert child["KUMIHO_CLAUDE_PROVISION_LOCK_TOKEN"] == "reservation"
    assert child["PIP_INDEX_URL"] == "https://packages.example.test/simple"


def test_alias_lock_blocks_desktop_and_plugin_from_writing_same_venv(
    state, monkeypatch
):
    compat = state / "claude-data" / "provision.lock"
    monkeypatch.setattr(L, "_desktop_compat_lock_paths", lambda: [compat])
    monkeypatch.setattr(L, "_desktop_compat_lock_candidates", lambda: [compat])
    token = L._acquire_provision_lock()
    assert token
    assert compat.exists()
    assert L._read_lock_at(compat)["token"] == token
    assert L._refresh_provision_lock(token)
    L._release_provision_lock(token)
    assert not compat.exists()
    assert not L._provision_lock_path().exists()

    compat.parent.mkdir(parents=True, exist_ok=True)
    compat.write_text(str(os.getpid()), encoding="utf-8")
    assert L._provision_in_progress()
    assert L._acquire_provision_lock() is None


def test_detached_handoff_commits_canonical_lock_after_compat_bundle(
    state, monkeypatch
):
    compat = state / "claude-data" / "provision.lock"
    monkeypatch.setattr(L, "_desktop_compat_lock_paths", lambda: [compat])
    monkeypatch.setattr(L, "_desktop_compat_lock_candidates", lambda: [compat])
    token = L._acquire_provision_lock()
    assert token
    order = []
    monkeypatch.setattr(
        L,
        "_adopt_desktop_compat_locks",
        lambda adopted, paths: order.append(("compat", adopted, paths)) or True,
    )
    monkeypatch.setattr(
        L,
        "_adopt_provision_lock",
        lambda adopted, paths: order.append(("canonical", adopted, paths)) or True,
    )

    assert L._acquire_provision_lock(token) == token
    assert [step[0] for step in order] == ["compat", "canonical"]
    assert all(step[1] == token and step[2] == [compat] for step in order)
    L._release_provision_lock(token)


def test_compatible_unmarked_desktop_runtime_is_adopted_once(state, monkeypatch):
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "_needs_install", lambda *a, **kw: False)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)
    monkeypatch.setattr(L, "_ensure_plugin_data_venv_alias", lambda *_a: None)

    assert not L._marker_path().exists()
    assert L._ensure_runtime() == venv_python
    assert L._marker_path().read_text(encoding="utf-8") == L.DEFAULT_PACKAGE_SPEC
    assert not L._provision_lock_path().exists()


def test_ready_runtime_alias_migration_holds_the_canonical_lock(
    state, monkeypatch
):
    venv_python = L._venv_python(state / "venv")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("python", encoding="utf-8")
    monkeypatch.setattr(L, "_needs_install", lambda *_a, **_kw: False)
    monkeypatch.setattr(L, "_ensure_hook_interpreter", lambda *_a: None)
    observed = []

    def guarded_alias(_venv_dir):
        record = L._read_provision_lock()
        observed.append(record)
        assert record.get("pid") == os.getpid()
        assert isinstance(record.get("token"), str) and record["token"]

    monkeypatch.setattr(L, "_ensure_plugin_data_venv_alias", guarded_alias)
    assert L._ensure_runtime() == venv_python
    assert len(observed) == 1
    assert not L._provision_lock_path().exists()


def test_the_lock_is_released_even_when_provisioning_raises(state, monkeypatch):
    """pip failing is the common case; it must not leave the venv locked."""
    monkeypatch.setenv(L._SYNC_PROVISION_ENV, "1")

    def boom(*_a):
        raise subprocess.CalledProcessError(1, "pip")

    monkeypatch.setattr(L, "_provision", boom)
    with pytest.raises(subprocess.CalledProcessError):
        L._ensure_runtime()
    assert not L._provision_lock_path().exists()


def test_the_desktop_entry_names_the_venv_that_is_actually_provisioned(state, monkeypatch):
    """Review found _bootstrap_desktop_server_entries still hardcoding
    <state>/venv after the venv moved. That writes an ABSOLUTE interpreter path
    into the user's Desktop config, and _has_valid_entry only validates args[0],
    so a wrong `command` is never repaired again."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(state / "pdata"))
    src = (SCRIPTS / "run_kumiho_mcp.py").read_text(encoding="utf-8")
    assert '_venv_python(_state_dir() / "venv")' not in src, \
        "the Desktop self-heal must use _venv_dir(), not the old state-dir venv"
    assert L._venv_python(L._venv_dir()) == L._venv_python(state / "venv")


def test_desktop_entry_on_legacy_venv_is_rewritten_to_shared_runtime(state, monkeypatch):
    installed = (
        state / "plugins" / "cache" / "kumiho-plugins" / "kumiho-memory"
        / "0.21.0" / "scripts" / "run_kumiho_mcp.py"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "__file__", str(installed))

    shared_python = L._venv_python(L._venv_dir())
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    legacy_python = state / "pdata" / "venv" / "Scripts" / "python.exe"
    legacy_python.parent.mkdir(parents=True)
    legacy_python.write_text("", encoding="utf-8")
    config = state / "claude_desktop_config.json"
    config.write_text(
        json.dumps({
            "mcpServers": {
                "kumiho-memory": {
                    "command": str(legacy_python),
                    "args": [str(installed)],
                    "env": {
                        "SENTINEL": "keep",
                        "KUMIHO_CLAUDE_MODE": "ce",
                        "KUMIHO_CLAUDE_SERVER_ENDPOINT": "grpcs://127.0.0.1:7443",
                        "UPSTASH_REDIS_URL": "rediss://127.0.0.1:6380/0",
                        "KUMIHO_LLM_BASE_URL": "https://127.0.0.1:11434/v1",
                        "KUMIHO_AUTH_TOKEN": "stale-cloud-token",
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "_claude_desktop_config_paths", lambda: [config])
    monkeypatch.setenv("KUMIHO_CLAUDE_MODE", "ce")
    monkeypatch.setenv(
        "KUMIHO_CLAUDE_SERVER_ENDPOINT", "grpcs://127.0.0.1:7443"
    )
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "")

    L._bootstrap_desktop_server_entries()
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]
    assert Path(entry["command"]) == shared_python
    assert entry["args"] == ["-I", str(installed)]
    assert entry["env"]["SENTINEL"] == "keep"
    assert entry["env"]["KUMIHO_CLAUDE_MODE"] == "ce"
    assert entry["env"]["KUMIHO_CLAUDE_SERVER_ENDPOINT"] == (
        "grpcs://127.0.0.1:7443"
    )
    assert entry["env"]["UPSTASH_REDIS_URL"] == "rediss://127.0.0.1:6380/0"
    assert entry["env"]["KUMIHO_LLM_BASE_URL"] == "https://127.0.0.1:11434/v1"
    assert "KUMIHO_AUTH_TOKEN" not in entry["env"]


def test_new_desktop_entry_does_not_copy_ambient_cloud_token(state, monkeypatch):
    installed = (
        state / "plugins" / "cache" / "kumiho-plugins" / "kumiho-memory"
        / "0.21.0" / "scripts" / "run_kumiho_mcp.py"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "__file__", str(installed))
    shared_python = L._venv_python(L._venv_dir())
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    config = state / "claude_desktop_config.json"
    monkeypatch.setattr(L, "_claude_desktop_config_paths", lambda: [config])
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "ambient-cloud-token")
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", raising=False)

    L._bootstrap_desktop_server_entries()

    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]
    assert entry["args"] == ["-I", str(installed)]
    assert entry["env"]["KUMIHO_CLAUDE_HOST"] == "claude"
    assert "KUMIHO_AUTH_TOKEN" not in entry["env"]


def test_cloud_desktop_repair_preserves_only_existing_entry_token(state, monkeypatch):
    installed = (
        state / "plugins" / "cache" / "kumiho-plugins" / "kumiho-memory"
        / "0.21.0" / "scripts" / "run_kumiho_mcp.py"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "__file__", str(installed))
    shared_python = L._venv_python(L._venv_dir())
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    config = state / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"kumiho-memory": {
            "command": "missing-python",
            "args": ["missing-script.py"],
            "env": {
                "KUMIHO_AUTH_TOKEN": "legacy-entry-token",
                "SENTINEL": "keep",
            },
        }}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "_claude_desktop_config_paths", lambda: [config])
    monkeypatch.setenv("KUMIHO_AUTH_TOKEN", "different-ambient-token")
    monkeypatch.delenv("KUMIHO_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("KUMIHO_CLAUDE_SERVER_ENDPOINT", raising=False)

    L._bootstrap_desktop_server_entries()

    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"][
        "kumiho-memory"
    ]
    assert entry["env"]["KUMIHO_AUTH_TOKEN"] == "legacy-entry-token"
    assert entry["env"]["SENTINEL"] == "keep"


def test_managed_legacy_desktop_server_name_is_migrated_without_duplication(
    state, monkeypatch
):
    installed = (
        state / "plugins" / "cache" / "kumiho-plugins" / "kumiho-memory"
        / "0.21.0" / "scripts" / "run_kumiho_mcp.py"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(L, "__file__", str(installed))
    shared_python = L._venv_python(L._venv_dir())
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    config = state / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"kumiho": {
            "command": str(shared_python),
            "args": [str(installed)],
            "env": {"SENTINEL": "keep"},
        }}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "_claude_desktop_config_paths", lambda: [config])

    L._bootstrap_desktop_server_entries()

    servers = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
    assert "kumiho" not in servers
    assert servers["kumiho-memory"]["args"] == ["-I", str(installed)]
    assert servers["kumiho-memory"]["env"]["SENTINEL"] == "keep"


def test_legacy_hook_venv_is_preserved_before_aliasing(state, monkeypatch):
    data = state / "pdata"
    legacy = data / "venv"
    legacy.mkdir(parents=True)
    (legacy / "legacy.txt").write_text("keep", encoding="utf-8")
    shared = state / "venv"
    shared_python = L._venv_python(shared)
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))

    aliases = []

    def fake_alias(link, target):
        aliases.append((link, target))
        link.mkdir()
        return True

    monkeypatch.setattr(L, "_create_directory_alias", fake_alias)
    L._ensure_plugin_data_venv_alias(shared)
    assert aliases == [(legacy, shared)]
    assert (data / "venv.pre-shared" / "legacy.txt").read_text(
        encoding="utf-8"
    ) == "keep"
    assert legacy.is_dir()


def test_live_desktop_lock_defers_legacy_venv_alias_migration(state, monkeypatch):
    data = state / "pdata"
    legacy = data / "venv"
    legacy.mkdir(parents=True)
    (legacy / "legacy.txt").write_text("keep", encoding="utf-8")
    (data / "provision.lock").write_text(str(os.getpid()), encoding="utf-8")
    shared = state / "venv"
    shared_python = L._venv_python(shared)
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.setattr(
        L,
        "_create_directory_alias",
        lambda *_a: pytest.fail("busy legacy runtime must not be moved"),
    )

    L._ensure_plugin_data_venv_alias(shared)

    assert (legacy / "legacy.txt").read_text(encoding="utf-8") == "keep"
    assert not list(data.glob("venv.pre-shared*"))


def test_junction_paths_are_data_not_cmd_source(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(L.bounded_proc, "run", fake_run)
    monkeypatch.setattr(
        L, "_windows_powershell_executable", lambda: "system/powershell.exe"
    )
    link = tmp_path / "link&ver%PATH%!literal"
    target = tmp_path / "target&whoami%TEMP%!literal"
    result = L._create_windows_junction(link, target)
    assert result.returncode == 0
    source = captured["command"][-1]
    assert str(link) not in source and str(target) not in source
    assert "New-Item -ItemType Junction" in source
    assert "$env:KUMIHO_JUNCTION_LINK" in source
    assert "$env:KUMIHO_JUNCTION_TARGET" in source
    assert captured["command"][0] == "system/powershell.exe"
    assert captured["env"]["KUMIHO_JUNCTION_LINK"] == str(link)
    assert captured["env"]["KUMIHO_JUNCTION_TARGET"] == str(target)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction integration")
def test_windows_junction_supports_cmd_metacharacters_as_literal_paths(tmp_path):
    target = tmp_path / "target&literal%PATH%!"
    link = tmp_path / "link&literal%TEMP%!"
    target.mkdir()
    result = L._create_windows_junction(link, target)
    assert result is not None
    assert result.returncode == 0, result.stderr or result.stdout
    assert link.is_dir()
    assert link.resolve() == target.resolve()


def test_the_lock_sits_beside_the_venv_it_guards(state, monkeypatch):
    """A lock in the state dir is not mutual: two launchers can resolve
    different state dirs (KUMIHO_CLAUDE_HOME) while sharing one venv."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(state / "pdata"))
    assert L._provision_lock_path().parent == L._venv_dir().parent


def test_the_detached_provisioner_leaves_something_to_read(state):
    """DEVNULL made a failed first run permanently invisible: the parent has
    already exited and the host reports only that the server went away."""
    assert L._provision_log_path().name.endswith(".log")
    src = (SCRIPTS / "run_kumiho_mcp.py").read_text(encoding="utf-8")
    spawn = src[src.index("def _spawn_detached_provisioning"):]
    spawn = spawn[:spawn.index("\ndef ")]
    assert "_provision_log_path()" in spawn, "the child must write somewhere readable"


def test_existing_shared_runtime_is_safely_adopted_before_backend_routing():
    """Desktop-first installs get the hook alias under the canonical lock."""
    src = (SCRIPTS / "run_kumiho_mcp.py").read_text(encoding="utf-8")
    main = src[src.index("def main() -> int:"):]
    hydration = main.index("_hydrate_env_from_local_config()")
    runtime = main.index("python_path = _ensure_runtime()", hydration)
    routing = main.index("cloud_mode = not _ce_mode_enabled()", runtime)
    assert hydration < runtime < routing
    assert 'with_name("run_kumiho_cloud.py")' in main[routing:]
    assert 'with_name("run_kumiho_ce.py")' in main[routing:]


def test_windows_interpreter_preflight_requires_a_real_pe_signature(
        tmp_path, monkeypatch):
    valid = tmp_path / "valid-python.exe"
    image = bytearray(132)
    image[:2] = b"MZ"
    image[0x3C:0x40] = (128).to_bytes(4, "little")
    image[128:132] = b"PE\0\0"
    valid.write_bytes(image)

    legacy = tmp_path / "legacy-python.exe"
    legacy_image = bytearray(image)
    legacy_image[128:132] = b"NE\0\0"
    legacy.write_bytes(legacy_image)
    text = tmp_path / "text-python.exe"
    text.write_text("fake interpreter", encoding="utf-8")

    monkeypatch.setattr(L.os, "name", "nt")
    assert L._windows_pe_executable(valid)
    assert not L._windows_pe_executable(legacy)
    assert not L._windows_pe_executable(text)

    monkeypatch.setattr(
        L.bounded_proc,
        "run",
        lambda *_a, **_kw: pytest.fail("invalid executable was launched"),
    )
    assert L._installed_versions(text, [("kumiho", set())]) == {}
    assert not L._python_interpreter_works(legacy)


def test_provision_flag_is_accepted_by_the_cli():
    """The detached child re-invokes this file with --provision; if argparse
    rejected it the background build would die instantly and silently."""
    r = subprocess.run([sys.executable, str(SCRIPTS / "run_kumiho_mcp.py"), "--help"],
                       capture_output=True, text=True, timeout=60)
    assert "--provision" in r.stdout


def test_shared_venv_probe_allows_pyvenv_cfg_site_initialization():
    source = (SCRIPTS / "run_kumiho_mcp.py").read_text(encoding="utf-8")
    start = source.index("def _python_interpreter_works")
    end = source.index("def _base_interpreter_for_venv_creation", start)
    probe = source[start:end]
    assert '"-I"' in probe
    assert '"-S"' not in probe, (
        "-S prevents site.py from switching sys.prefix for a healthy venv"
    )


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
