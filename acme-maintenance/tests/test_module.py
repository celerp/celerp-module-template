# SPDX-License-Identifier: MIT
"""The module's shape: its manifest, its domain logic, and its written guidance.

These tests need Celerp importable, as the rest of this suite does: the manifest
declares a permission key that only core can confirm exists, and AGENTS.md cites
core files by line. Run them with the module folder and a Celerp checkout on
PYTHONPATH (see README).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Make the inner package importable when running this test standalone.
MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parent
sys.path.insert(0, str(MODULE_DIR))

CITATION_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|css|js|md)):(\d+)`")


def _manifest() -> dict:
    spec = importlib.util.spec_from_file_location(
        "acme_manifest", MODULE_DIR / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PLUGIN_MANIFEST


def test_manifest_is_well_formed():
    m = _manifest()
    assert m["name"] == "acme-maintenance"
    assert not m["name"].startswith("celerp-"), "celerp- prefix is reserved for official modules"
    assert m["api_routes"] and m["ui_routes"]
    assert "nav" in m["slots"]
    # A dotted version, or Celerp's compatibility check cannot compare it.
    assert re.fullmatch(r"\d+(\.\d+){0,2}", m["min_celerp_version"])


def test_manifest_nav_uses_permission():
    """A25: core nav gating reads "permission"; a "min_role" key is silently ignored."""
    from celerp.services.permissions import _PERMISSIONS_BY_KEY

    nav = _manifest()["slots"]["nav"]
    assert isinstance(nav, list) and nav, "the nav slot must be a list of item dicts"
    for item in nav:
        assert "min_role" not in item, "min_role is not read by core nav gating"
        key = item.get("permission")
        assert key, f"nav item {item.get('label')!r} declares no permission"
        assert key in _PERMISSIONS_BY_KEY, (
            f"permission {key!r} is not in core's registry; permission keys are a "
            f"closed set, so a module reuses one rather than inventing it")


def test_due_logic_and_status():
    """The last-serviced date is passed in: it is derived from the service log now."""
    from acme_maintenance.models import Equipment
    e = Equipment(company_id="c", name="Forklift", interval_days=30)
    assert e.is_due(None) and e.status(None) == "due"        # never serviced -> due
    assert e.due_date(None) is None
    recent = date.today() - timedelta(days=10)
    assert not e.is_due(recent) and e.status(recent) == "ok"
    assert e.due_date(recent) == recent + timedelta(days=30)
    stale = date.today() - timedelta(days=40)
    assert e.is_due(stale) and e.status(stale) == "due"


def test_agents_md_citations_resolve():
    """A47: the guidance an author is told to trust points at lines that exist."""
    import ui
    core_root = Path(ui.__file__).resolve().parents[1]
    agents = REPO_ROOT / "AGENTS.md"
    assert agents.exists(), "the repo ships no AGENTS.md for the next author to read"

    text = agents.read_text()
    citations = CITATION_RE.findall(text)
    assert len(citations) >= 5, f"AGENTS.md cites almost nothing: {citations}"

    broken = []
    for rel, line_no in citations:
        for root in (REPO_ROOT, core_root):
            path = root / rel
            if path.is_file():
                if len(path.read_text(errors="ignore").splitlines()) < int(line_no):
                    broken.append(f"{rel}:{line_no} (file has fewer lines)")
                break
        else:
            broken.append(f"{rel}:{line_no} (no such file)")
    assert broken == [], f"AGENTS.md citations that do not resolve: {broken}"
