# SPDX-License-Identifier: MIT
"""A minimal test: the manifest is well-formed and the model computes due dates.

Real module test suites run against a Celerp test database (see the built-in
modules' tests/ for that pattern). This one stays dependency-free so it runs
anywhere, and demonstrates the two things worth pinning: manifest shape and
your own domain logic.
"""
from datetime import date, timedelta
import importlib
import sys
from pathlib import Path

# Make the inner package importable when running this test standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

manifest = importlib.import_module("__init__").PLUGIN_MANIFEST if False else None


def test_manifest_is_well_formed():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "acme_manifest", Path(__file__).resolve().parents[1] / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.PLUGIN_MANIFEST
    assert m["name"] == "acme-maintenance"
    assert not m["name"].startswith("celerp-"), "celerp- prefix is reserved for official modules"
    assert m["api_routes"] and m["ui_routes"]
    assert "nav" in m["slots"]


def test_due_logic():
    from acme_maintenance.models import Equipment
    e = Equipment(company_id="c", name="Forklift", interval_days=30)
    assert e.is_due()                                   # never serviced → due
    e.serviced_at = date.today() - timedelta(days=10)
    assert not e.is_due()                               # serviced recently → not due
    e.serviced_at = date.today() - timedelta(days=40)
    assert e.is_due()                                   # past the interval → due
