# SPDX-License-Identifier: MIT
"""What the module's pages render.

These are the tests that catch the defects a module author is most likely to ship:
markup that does not match the house pattern, a class name that exists only in the
module's imagination, and a fragment route that returns something other than HTML.
"""
from __future__ import annotations

import re
from pathlib import Path

from fasthtml.common import to_xml

import acme_maintenance.ui_routes as ui_routes
from htmlq import classes_in, parse

CLASS_RE = re.compile(r'class="([^"]*)"')


def _core_ui_dir() -> Path:
    import ui
    return Path(ui.__file__).parent


def test_search_is_in_page_header(env):
    """A1: the search box belongs in the header actions, upper right."""
    env.equipment("Lathe")
    tree = parse(env.get("/maintenance").text)
    search = tree.find(id="search-input")
    assert search is not None, "the page renders no shared search box"
    assert search.has_ancestor(cls="page-actions"), \
        f"search-input is not inside page-actions; ancestors: {search.ancestors()[:3]}"


def test_all_classes_exist_in_core(env):
    """A2: nothing on the page invents a class name core has never heard of."""
    env.equipment("Lathe", location="Main plant")
    eq_id = env.rows("acme_equipment")[0]["id"]
    markup = env.get("/maintenance").text + env.get(f"/maintenance/{eq_id}").text
    sources = "".join(
        p.read_text(errors="ignore") for p in _core_ui_dir().rglob("*")
        if p.suffix in (".py", ".css", ".js", ".html") and p.is_file()
    )
    unknown = sorted(c for c in classes_in(markup) if c not in sources)
    assert unknown == [], f"classes not found anywhere in core: {unknown}"


def test_list_fragment_is_html(env):
    """A3: an HTMX refresh must return markup, not an element id."""
    env.equipment("Lathe")
    body = env.get("/maintenance/content", htmx=True).text
    assert "<table" in body, f"fragment body is not HTML: {body[:200]!r}"
    assert body.strip() != "maint-content"


def test_fragment_redirects_without_hx_header(env):
    """A4: typing a fragment URL into the browser lands on the real page."""
    r = env.get("/maintenance/content?status=due", htmx=False, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/maintenance?status=due"


def test_filters_preserved_after_mark_serviced(env):
    """A5: a write must not silently drop the filter the user is looking through."""
    due = env.equipment("Overdue lathe")
    env.equipment("Fresh press")
    env.service_log(env.equipment("Recently done"))
    r = env.post("/maintenance/content?status=due", data={"selected": [due]})
    assert r.status_code == 200
    assert "Recently done" not in r.text, "the refresh ignored ?status=due"


def test_location_cell_is_select(env):
    """A7: location is chosen from the company's locations, like everywhere else."""
    eq_id = env.equipment("Lathe", location="Main plant")
    body = env.get(f"/maintenance/{eq_id}/cell/location/edit", htmx=True).text
    assert "<select" in body, f"location editor is not a select: {body[:200]!r}"
    assert "Warehouse" in body


def test_location_select_searchable_over_threshold(make_env):
    """A8: more than ten options means a searchable select (GDR 2i)."""
    env = make_env(locations=[{"id": str(n), "name": f"Bay {n}"} for n in range(1, 12)])
    eq_id = env.equipment("Lathe", location="Bay 1")
    body = env.get(f"/maintenance/{eq_id}/cell/location/edit", htmx=True).text
    assert "combobox-input" in body, "eleven options rendered a plain select"


def test_interval_editor_has_aria_label(env):
    """A9: the editor replaces its cell, so it carries its own name."""
    eq_id = env.equipment("Lathe")
    body = env.get(f"/maintenance/{eq_id}/cell/interval_days/edit", htmx=True).text
    assert "aria-label=" in body, f"interval editor has no accessible name: {body[:200]!r}"


def test_row_has_single_glyph_action(env):
    """A10: one action per row, the standard glyph, no text buttons."""
    env.equipment("Lathe")
    tree = parse(env.get("/maintenance").text)
    row = tree.find(cls="data-row")
    assert row is not None
    actions = row.find(cls="cell--actions")
    assert actions is not None, "the row has no actions cell"
    buttons = actions.find_all("button")
    assert len(buttons) == 1, f"expected one row action, got {[b.text() for b in buttons]}"
    assert buttons[0].text() == "×"
    assert "Mark serviced" not in row.text()
    assert "Delete" not in row.text()


def test_serviced_cell_is_editable_date(env):
    """A11: the last-serviced date can be set by hand, double-click to edit."""
    eq_id = env.equipment("Lathe")
    cell = parse(env.get("/maintenance").text).find(cls="cell--date")
    assert cell is not None, "no date cell on the row"
    assert cell.attrs.get("hx-trigger") == "dblclick"
    editor = env.get(f"/maintenance/{eq_id}/cell/serviced_at/edit", htmx=True).text
    assert 'type="date"' in editor


def test_cells_come_from_shared_component(env):
    """A13: the cells ARE the house cells, not a lookalike."""
    from ui.components.table import display_cell
    eq_id = env.equipment("Lathe")
    expected = to_xml(display_cell(eq_id, "name", "Lathe", cell_type="text",
                                   edit_url=f"/maintenance/{eq_id}/cell/name/edit"))
    assert expected.strip() in env.get("/maintenance").text


def test_detail_page_sections(env):
    """A14: the detail page carries identity, instructions, history and files."""
    eq_id = env.equipment("Lathe", location="Main plant")
    env.service_log(eq_id, note="Belt replaced")
    tree = parse(env.get(f"/maintenance/{eq_id}").text)
    titles = [n.text() for n in tree.find_all(cls="section-title")]
    for expected in ("Equipment", "Service instructions", "Service history", "Files"):
        assert any(expected in title for title in titles), f"missing {expected!r} block; got {titles}"
    assert "Belt replaced" in tree.text()


def test_status_card_colours_known(env):
    """A26: an unsupported colour silently renders grey, so assert the real ones."""
    env.equipment("Overdue lathe")
    env.service_log(env.equipment("Recently done"))
    markup = env.get("/maintenance").text
    assert "status-card--yellow" in markup
    assert "status-card--green" in markup
    assert "status-card--gray" not in markup, "a card colour fell through to grey"


def test_api_failure_renders_error_state(env):
    """A27: an unreachable API says so; it never claims the list is empty."""
    env.inject["/api/maintenance/equipment"] = "raise"
    markup = env.get("/maintenance").text
    assert "flash--error" in markup
    assert "could not be loaded" in markup
    assert "No equipment yet" not in markup


def test_location_fetch_failure_falls_back_to_text(env):
    """A37: no location list means free text plus an explanation, never an empty select."""
    eq_id = env.equipment("Lathe")
    env.inject["/companies/me/locations"] = "raise"
    r = env.get(f"/maintenance/{eq_id}/cell/location/edit", htmx=True)
    assert "<select" not in r.text
    assert 'type="text"' in r.text
    assert "celerpToast" in r.headers.get("HX-Trigger", ""), "the user is not told why"


def test_files_section_exposes_no_static_attachment_url(env):
    """A41: the stored path never reaches the client; downloads go through the module."""
    eq_id = env.equipment("Lathe")
    env.post(f"/maintenance/{eq_id}/files",
             files={"file": ("manual.txt", b"how to service it", "text/plain")})
    markup = env.get(f"/maintenance/{eq_id}").text
    assert "/static/attachments/" not in markup
    assert f"/maintenance/{eq_id}/files/" in markup


def test_bulkbar_wrapper_hidden_by_default(env):
    """A45: the action list appears only once rows are ticked (owner defect 2)."""
    env.equipment("Lathe")
    markup = env.get("/maintenance").text
    wrapper = parse(markup).find(cls="bulk-toolbar")
    assert wrapper is not None, "the shared toolbar is not wrapped in the hidden box"
    assert "is-active" not in wrapper.classes
    assert wrapper.find(cls="bulkbar") is not None, "the wrapper does not hold the shared bar"
    assert "is-active" in markup, "nothing on the page ever reveals the box"


def test_all_emitted_classes_exist_in_core_css(env):
    """A46: every class the module itself emits is styled by core, or rendered by core.

    A name absent from app.css is only allowed when it is a semantic hook core also
    renders without a rule of its own (`cell`, `cell--actions`); anything else is a
    class the module invented, which ships unstyled because a module has no CSS.
    """
    eq_id = env.equipment("Lathe", location="Main plant")
    env.service_log(eq_id)
    item = {"id": eq_id, "name": "Lathe", "location": "Main plant", "interval_days": 90,
            "last_cost": 12.5, "notes": "", "instructions": "", "serviced_at": "2026-07-01",
            "due_date": "2026-09-29", "status": "ok", "archived": False}
    params = {"q": "", "status": "", "show": "", "view": "", "month": ""}
    views = [
        ui_routes._list_view([item], params, locations=["Main plant"], can_edit=True),
        ui_routes._detail_view(item, [{"id": "l1", "serviced_at": "2026-07-01",
                                       "cost": 0, "note": ""}], [],
                               locations=["Main plant"], can_edit=True),
        ui_routes._calendar_view([item], "2026-08"),
    ]
    css = (_core_ui_dir() / "static" / "app.css").read_text()
    core_py = "".join(p.read_text(errors="ignore")
                      for p in _core_ui_dir().rglob("*.py") if p.is_file())
    missing = sorted(
        c for view in views for c in classes_in(to_xml(view))
        if f".{c}" not in css and f'"{c}' not in core_py and f' {c}"' not in core_py
    )
    assert missing == [], f"classes with no core styling and no core caller: {missing}"
