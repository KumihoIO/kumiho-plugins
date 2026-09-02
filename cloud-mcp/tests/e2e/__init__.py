"""Live end-to-end tests (skipped without a local Kumiho CE server).

This file is not decoration. Without it pytest imports both ``tests/conftest.py``
and ``tests/e2e/conftest.py`` under the bare module name ``conftest``, and the
second one wins — so every hermetic test's ``from conftest import ...`` starts
resolving against the e2e fixtures and the whole suite fails to collect. Making
``e2e`` a package moves this directory's modules under an ``e2e.`` prefix, which
is why the tests here import ``e2e.conftest`` / ``e2e._client`` explicitly.
"""
