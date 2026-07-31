#!/usr/bin/env python3
"""Tests for the launcher's install decision (kumiho-plugins#45 items 2 and 6).

``_needs_install`` used to be ``marker_text != spec_text``, which broke in two
directions at once on 2026-07-31:

* **Releases never arrived.** An install whose marker string already equalled
  the current spec string was never reinstalled, so bumping the floor shipped
  nothing.
* **Installs thrashed.** A cached plugin 0.18.0 declaring
  ``kumiho-memory[all]>=1.2.0`` and a stale Claude Desktop rpm snapshot still
  declaring ``>=0.17.1`` share one state dir. Each launch rewrote the other's
  marker, so every alternating start reinstalled a venv that already satisfied
  both floors (measured: venv at kumiho-memory 1.2.0, marker text ``>=0.17.1``).

The decision is now "is the venv below the floor", so both stop.

Run: python -m pytest claude/scripts/test_needs_install.py -q
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import venv
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent


def _launcher():
    sys.path.insert(0, str(SCRIPTS))  # the launcher imports bounded_proc by name
    spec = importlib.util.spec_from_file_location(
        "run_kumiho_mcp", SCRIPTS / "run_kumiho_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _launcher()


@pytest.mark.parametrize("spec,expected", [
    ("kumiho[mcp]>=0.10.8 kumiho-memory[all]>=1.2.1",
     [("kumiho", frozenset({"mcp"}), "0.10.8"),
      ("kumiho-memory", frozenset({"all"}), "1.2.1")]),
    ("kumiho kumiho-memory>=1.2.0",
     [("kumiho", frozenset(), ""), ("kumiho-memory", frozenset(), "1.2.0")]),
    ("kumiho-memory[all]", [("kumiho-memory", frozenset({"all"}), "")]),
])
def test_spec_floors_parses_extras_and_operators(spec, expected):
    reqs, understood = L._spec_floors(spec)
    assert understood
    assert reqs == expected


@pytest.mark.parametrize("spec,floor", [
    ("kumiho==0.11.0", "0.11.0"),
    ("kumiho===0.11.0", "0.11.0"),
    ("kumiho~=1.2.0", "1.2.0"),
    ("kumiho>0.10.8", "0.10.8"),
])
def test_every_minimum_implying_operator_becomes_a_floor(spec, floor):
    """`>=` was the only operator read, so `==0.9` parsed as "no floor at all"
    and a venv at any version reported as satisfying it."""
    reqs, understood = L._spec_floors(spec)
    assert understood
    assert reqs == [("kumiho", frozenset(), floor)]


@pytest.mark.parametrize("spec", [
    "kumiho<2.0",
    "kumiho!=1.0",
    "--pre",
    "./wheels/kumiho-1.0-py3-none-any.whl",
    "git+https://github.com/KumihoIO/kumiho",
])
def test_unevaluable_tokens_force_a_reinstall_rather_than_a_guess(spec):
    """Flags, URLs and paths used to match the name regex and be looked up as
    distributions -- absent by definition, so every launch reinstalled. And a
    ceiling we cannot evaluate must never be reported as satisfied."""
    _reqs, understood = L._spec_floors(spec)
    assert not understood


@pytest.mark.parametrize("lo,hi", [
    ("0.17.1", "1.2.0"),      # the exact pair that thrashed
    ("0.10.7", "0.10.8"),
    ("1.2", "1.2.1"),
    ("1.9", "1.10"),          # numeric, not lexicographic
    ("1.2.0rc1", "1.2.0"),    # a release candidate is BELOW its release
    ("1.2.0.dev1", "1.2.0"),  # ...and so is a dev build, despite being longer
    ("1.2.0b2", "1.2.0rc1"),
])
def test_version_ordering(lo, hi):
    assert L._below_floor(lo, hi)
    assert not L._below_floor(hi, lo)


def test_equal_versions_are_not_below_the_floor():
    assert not L._below_floor("1.2.1", "1.2.1")
    assert not L._below_floor("1.2.1.0", "1.2.1")


def test_missing_venv_python_needs_install(tmp_path):
    assert L._needs_install(tmp_path / "nope.exe", tmp_path / "marker", "kumiho>=1.0")


@pytest.fixture(scope="module")
def bare_venv(tmp_path_factory):
    """A real venv with neither kumiho package -- the 'must install' baseline."""
    d = tmp_path_factory.mktemp("venv")
    venv.create(d, with_pip=False)
    py = d / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.exists():
        pytest.skip("venv creation produced no interpreter")
    return py


def test_absent_distribution_needs_install(bare_venv, tmp_path):
    assert L._needs_install(bare_venv, tmp_path / "marker", "kumiho-memory>=1.2.0")


def test_probe_of_a_broken_interpreter_falls_back_to_install(tmp_path):
    """Unknowable satisfaction must reinstall, never silently skip."""
    fake = tmp_path / ("python.exe" if sys.platform == "win32" else "python")
    fake.write_text("not an interpreter", encoding="utf-8")
    assert L._needs_install(fake, tmp_path / "marker", "kumiho>=1.0")


def test_installed_versions_reports_absent_as_none(bare_venv):
    got = L._installed_versions(bare_venv, ["kumiho-memory"])
    assert got.get("kumiho-memory") is None
    assert got.get("__modules__") is False


def _venv_at_current_floors(modules_ok=True):
    """A stubbed venv holding exactly what DEFAULT_PACKAGE_SPEC asks for.

    Derived from the spec rather than hardcoded, so a routine floor bump does not
    turn these into failures about the wrong thing -- hardcoding 1.2.0 here made
    the 1.2.1 bump fail a test whose subject is thrash, not versions.
    """
    reqs, _ = L._spec_floors(L.DEFAULT_PACKAGE_SPEC)
    installed = {name: floor or "0" for name, _extras, floor in reqs}
    installed["__modules__"] = modules_ok
    return lambda *_, **__: installed


def _bumped(spec):
    """The same spec with every floor's minor raised -- an unreleased future."""
    out = []
    for name, extras, floor in L._spec_floors(spec)[0]:
        parts = [n for n, _rank in L._version_key(floor or "0")]
        parts[1 if len(parts) > 1 else 0] += 1
        suffix = "[%s]" % ",".join(sorted(extras)) if extras else ""
        out.append("%s%s>=%s" % (name, suffix, ".".join(str(p) for p in parts)))
    return " ".join(out)


def test_a_satisfied_venv_is_not_reinstalled_by_a_lower_floor(monkeypatch, tmp_path):
    """The thrash itself: a stale snapshot's older floor must be a no-op against
    a venv the newer spec already provisioned."""
    monkeypatch.setattr(L, "_installed_versions", _venv_at_current_floors())
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    stale = "kumiho[mcp]>=0.10.7 kumiho-memory[all]>=0.17.1"
    assert not L._needs_install(py, tmp_path / "marker", stale)
    assert not L._needs_install(py, tmp_path / "marker", L.DEFAULT_PACKAGE_SPEC)


def test_a_raised_floor_still_triggers_install(monkeypatch, tmp_path):
    """The other half: bumping the floor must actually reach existing installs."""
    monkeypatch.setattr(L, "_installed_versions", _venv_at_current_floors())
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    assert L._needs_install(py, tmp_path / "marker", _bumped(L.DEFAULT_PACKAGE_SPEC))


def test_an_extras_change_reinstalls_even_though_versions_still_satisfy(monkeypatch, tmp_path):
    """importlib.metadata does not record which extras were installed, so a
    version compare alone is blind to kumiho[mcp] -> kumiho[mcp,cli]. The marker
    supplies that one fact -- name+extras identity only, never versions, so the
    text-equality thrash stays fixed."""
    monkeypatch.setattr(L, "_installed_versions", _venv_at_current_floors())
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    marker = tmp_path / "marker"

    marker.write_text(L.DEFAULT_PACKAGE_SPEC, encoding="utf-8")
    assert not L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)

    # same extras, different floors -- the thrash pair, still a no-op
    marker.write_text("kumiho[mcp]>=0.10.7 kumiho-memory[all]>=0.17.1", encoding="utf-8")
    assert not L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)

    # extras genuinely changed -- that IS a new install
    marker.write_text("kumiho[mcp,cli]>=0.10.8 kumiho-memory[all]>=1.2.1", encoding="utf-8")
    assert L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)


def test_importable_modules_are_required_even_at_a_good_version(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "_installed_versions", _venv_at_current_floors(modules_ok=False))
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    assert L._needs_install(py, tmp_path / "marker", L.DEFAULT_PACKAGE_SPEC)


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
