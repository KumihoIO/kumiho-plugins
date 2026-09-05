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
     [("kumiho", frozenset({"mcp"}), "0.10.8", ""),
      ("kumiho-memory", frozenset({"all"}), "1.2.1", "")]),
    ("kumiho kumiho-memory>=1.2.0",
     [("kumiho", frozenset(), "", ""), ("kumiho-memory", frozenset(), "1.2.0", "")]),
    ("kumiho-memory[all]", [("kumiho-memory", frozenset({"all"}), "", "")]),
    # Ceiling parsing, added for the mcp<2 stopgap. The pin is gone as of
    # 0.19.0 (kumiho >=0.11.0 bounds mcp itself), but the parser must keep
    # understanding ceilings: a spec it cannot evaluate reinstalls on every
    # single launch, and old markers still carry this token.
    ("mcp<2", [("mcp", frozenset(), "", "2")]),
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
    assert reqs == [("kumiho", frozenset(), floor, "")]


@pytest.mark.parametrize("spec", [
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
    """Unknowable satisfaction must reinstall, never silently skip.

    A text file named ``python.exe`` makes Windows open a modal "16-bit
    application" dialog instead of simply returning a subprocess error.  A
    directory is equally non-executable and fails without involving the GUI.
    """
    fake = tmp_path / "broken-python"
    fake.mkdir()
    assert L._needs_install(fake, tmp_path / "marker", "kumiho>=1.0")


def test_installed_versions_reports_absent_as_none(bare_venv):
    got = L._installed_versions(bare_venv, ["kumiho-memory"])
    assert got.get("kumiho-memory") is None
    assert got.get("__python_ok__") is True
    assert got.get("__modules__") is False


def _venv_at_current_floors(modules_ok=True):
    """A stubbed venv holding exactly what DEFAULT_PACKAGE_SPEC asks for.

    Derived from the spec rather than hardcoded, so a routine floor bump does not
    turn these into failures about the wrong thing -- hardcoding 1.2.0 here made
    the 1.2.1 bump fail a test whose subject is thrash, not versions.
    """
    reqs, _ = L._spec_floors(L.DEFAULT_PACKAGE_SPEC)
    # A ceiling'd requirement must land BELOW its ceiling, not at it.
    installed = {}
    for name, _extras, floor, ceiling in reqs:
        installed[name] = floor or ("1" if not ceiling else str(int(
            L._version_key(ceiling)[0][0]) - 1))
    installed["__modules__"] = modules_ok
    installed["__extras__"] = True
    installed["__python_ok__"] = True
    return lambda *_, **__: installed


def test_a_non_venv_or_old_python_always_needs_repair(monkeypatch, tmp_path):
    installed = _venv_at_current_floors()()
    installed["__python_ok__"] = False
    monkeypatch.setattr(L, "_installed_versions", lambda *_, **__: installed)
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    assert L._needs_install(py, tmp_path / "marker", L.DEFAULT_PACKAGE_SPEC)


def _bumped(spec):
    """The same spec with every floor's minor raised -- an unreleased future."""
    out = []
    for name, extras, floor, ceiling in L._spec_floors(spec)[0]:
        if ceiling and not floor:
            out.append("%s<%s" % (name, ceiling))   # a ceiling cannot be "bumped"
            continue
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

    # same names and extras, different floors -- the thrash pair, still a no-op
    marker.write_text("kumiho[mcp]>=0.10.7 kumiho-memory[all]>=0.17.1",
                      encoding="utf-8")
    assert not L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)

    # extras genuinely changed -- that IS a new install
    marker.write_text("kumiho[mcp,cli]>=0.10.8 kumiho-memory[all]>=1.2.1",
                      encoding="utf-8")
    assert L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)

    # a requirement APPEARING is also a new install -- this is how the mcp<2 pin
    # itself reached an existing venv that predated it.
    marker.write_text("kumiho[mcp]>=0.10.8", encoding="utf-8")
    assert L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)

    # ...and a requirement DISAPPEARING must be too. This is the 0.18.3 -> 0.19.0
    # migration: those venvs carry a marker naming mcp<2 and an mcp held below
    # 2.0. Lifting the pin has to reach them, or they sit on mcp 1.x forever
    # while the spec no longer says anything about mcp at all.
    marker.write_text("kumiho[mcp]>=0.10.8 kumiho-memory[all]>=1.2.1 mcp<2",
                      encoding="utf-8")
    assert L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)


def test_importable_modules_are_required_even_at_a_good_version(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "_installed_versions", _venv_at_current_floors(modules_ok=False))
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    assert L._needs_install(py, tmp_path / "marker", L.DEFAULT_PACKAGE_SPEC)


def test_unmarked_desktop_venv_must_prove_requested_extras(monkeypatch, tmp_path):
    installed = _venv_at_current_floors()()
    installed["__extras__"] = False
    monkeypatch.setattr(L, "_installed_versions", lambda *_, **__: installed)
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    assert L._needs_install(py, tmp_path / "shared-marker", L.DEFAULT_PACKAGE_SPEC)


def test_marked_shared_venv_reinstalls_when_an_extra_dependency_breaks(
    monkeypatch, tmp_path
):
    """A marker states intent, not current health of transitive extra deps."""
    installed = _venv_at_current_floors()()
    installed["__extras__"] = False
    monkeypatch.setattr(L, "_installed_versions", lambda *_, **__: installed)
    py = tmp_path / "python"
    py.write_text("", encoding="utf-8")
    marker = tmp_path / "shared-marker"
    marker.write_text(L.DEFAULT_PACKAGE_SPEC, encoding="utf-8")

    assert L._needs_install(py, marker, L.DEFAULT_PACKAGE_SPEC)


def test_unmarked_desktop_venv_without_standalone_packaging_is_reused(tmp_path):
    """Desktop's venv has pip's vendored packaging, not a top-level package.

    That normal layout must still prove the requested extras; otherwise every
    Claude/Codex start tries an unnecessary network upgrade of a healthy shared
    runtime.
    """
    root = tmp_path / "desktop-runtime"
    venv.create(root, with_pip=True)
    py = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site_dir = Path(subprocess.check_output(
        [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        text=True,
    ).strip())
    assert subprocess.run(
        [str(py), "-c", "import packaging"], capture_output=True
    ).returncode != 0, "fixture unexpectedly has standalone packaging"

    (site_dir / "kumiho").mkdir()
    (site_dir / "kumiho" / "__init__.py").write_text("", encoding="utf-8")
    (site_dir / "kumiho" / "mcp_server.py").write_text("", encoding="utf-8")
    (site_dir / "kumiho_memory").mkdir()
    (site_dir / "kumiho_memory" / "__init__.py").write_text("", encoding="utf-8")

    metadata = {
        "kumiho-9.0.dist-info": (
            "Metadata-Version: 2.1\nName: kumiho\nVersion: 9.0\n"
            "Provides-Extra: mcp\nRequires-Dist: mcp>=1; extra == 'mcp'\n"
        ),
        "kumiho_memory-9.0.dist-info": (
            "Metadata-Version: 2.1\nName: kumiho-memory\nVersion: 9.0\n"
            "Provides-Extra: all\nRequires-Dist: helper>=1; extra == 'all'\n"
        ),
        "mcp-1.0.dist-info": "Metadata-Version: 2.1\nName: mcp\nVersion: 1.0\n",
        "helper-1.0.dist-info": (
            "Metadata-Version: 2.1\nName: helper\nVersion: 1.0\n"
        ),
    }
    for directory, body in metadata.items():
        dist_info = site_dir / directory
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(body + "\n", encoding="utf-8")

    assert not L._needs_install(
        py,
        tmp_path / "absent-marker",
        "kumiho[mcp]>=0.12.2 kumiho-memory[all]>=1.4.0",
    )


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
