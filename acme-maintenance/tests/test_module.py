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
# AGENTS.md ends with one anchor per citation: the cited line's exact text, so a
# line that moves fails the check instead of silently pointing at its neighbour.
ANCHOR_HEADING = "## Citation anchors"
ANCHOR_RE = re.compile(r"^- `([A-Za-z0-9_./-]+):(\d+)`: `(.*)`$", re.MULTILINE)


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


def _citation_problems(text: str, roots: tuple[Path, ...], core_root: Path | None,
                       core_is_254: bool) -> list[str]:
    """Every `path:line` in `text` must name a line of code whose text is its
    anchor, and every anchor must belong to a citation. Celerp paths cite 2.5.4
    lines, so they are resolved only when `core_is_254`."""
    body, _, anchor_block = text.partition(ANCHOR_HEADING)
    anchors = {(rel, n): a for rel, n, a in ANCHOR_RE.findall(anchor_block)}
    cited = set(CITATION_RE.findall(body))
    problems = [f"{rel}:{n} (anchor for no citation)" for rel, n in sorted(anchors.keys() - cited)]
    for rel, line_no in sorted(cited):
        anchor = anchors.get((rel, line_no))
        if anchor is None:
            problems.append(f"{rel}:{line_no} (no anchor under {ANCHOR_HEADING!r})")
            continue
        for root in roots:
            path = root / rel
            if path.is_file():
                if root == core_root and not core_is_254:
                    break
                lines = path.read_text(errors="ignore").splitlines()
                if len(lines) < int(line_no):
                    problems.append(f"{rel}:{line_no} (file has fewer lines)")
                elif not (code := lines[int(line_no) - 1].strip()) or code.startswith("#"):
                    problems.append(f"{rel}:{line_no} (not a line of code: {code!r})")
                elif code != anchor:
                    problems.append(f"{rel}:{line_no} (now reads {code!r}, anchor {anchor!r})")
                break
        else:
            if core_is_254 or not rel.startswith(("celerp/", "ui/")):
                problems.append(f"{rel}:{line_no} (no such file)")
    return problems


def test_agents_md_citations_resolve():
    """A47: the guidance an author is told to trust points at code, not at a blank
    line or a comment. Celerp paths cite 2.5.4 lines, so they are checked only
    against a Celerp checkout that has 2.5.4's module checks."""
    import ui
    from celerp.modules import importer
    core_root = Path(ui.__file__).resolve().parents[1]
    core_is_254 = hasattr(importer, "table_prefix_problem")
    agents = REPO_ROOT / "AGENTS.md"
    assert agents.exists(), "the repo ships no AGENTS.md for the next author to read"

    text = agents.read_text()
    citations = CITATION_RE.findall(text.partition(ANCHOR_HEADING)[0])
    assert len(citations) >= 5, f"AGENTS.md cites almost nothing: {citations}"
    broken = _citation_problems(text, (REPO_ROOT, core_root), core_root, core_is_254)
    assert broken == [], f"AGENTS.md citations that do not resolve: {broken}"


_MOVED = ("See `m.py:1` for the return value.\n\n"
          "## Citation anchors\n\n"
          "- `m.py:1`: `return 1`\n")


def test_citation_check_catches_a_moved_line(tmp_path):
    """Code inserted above a cited line moves it: the citation still lands on a
    line of code, just not the one the guidance means. Only the anchor, the
    cited line's exact text, can tell the two apart."""
    (tmp_path / "m.py").write_text("def kept():\n    return 1\n")
    problems = _citation_problems(_MOVED, (tmp_path,), None, True)
    assert any("m.py:1" in p for p in problems), problems


def test_citation_check_needs_an_anchor_for_every_citation(tmp_path):
    (tmp_path / "m.py").write_text("def kept():\n    return 1\n")
    problems = _citation_problems("See `m.py:2`.\n", (tmp_path,), None, True)
    assert any("m.py:2" in p for p in problems), problems


def test_citation_check_accepts_a_matching_anchor(tmp_path):
    (tmp_path / "m.py").write_text("def kept():\n    return 1\n")
    text = _MOVED.replace("m.py:1", "m.py:2")
    assert _citation_problems(text, (tmp_path,), None, True) == []
