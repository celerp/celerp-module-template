# SPDX-License-Identifier: MIT
"""The printable calendar view.

The list and the calendar are one page with two renderings, chosen by `?view=` so
the state lives in the URL (GDR 2m). These tests pin the grid, the honest handling
of a month nobody can parse, and the print rule that makes the sheet usable.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

from htmlq import parse


def test_calendar_view_renders_month(env):
    """A17: every day of the month is a cell, and a due item sits on its own day."""
    due_on = date(2026, 8, 14)
    serviced = due_on - timedelta(days=90)
    eq_id = env.equipment("Lathe", interval_days=90)
    env.service_log(eq_id, serviced_at=serviced)

    tree = parse(env.get("/maintenance?view=calendar&month=2026-08").text)
    days = tree.find_all(**{"data-day": ...})
    got = sorted(n.attrs["data-day"] for n in days)
    expected = sorted(f"2026-08-{d:02d}" for d in
                      range(1, _calendar.monthrange(2026, 8)[1] + 1))
    assert got == expected, f"grid does not cover August 2026: {got}"

    cell = tree.find(**{"data-day": due_on.isoformat()})
    assert "Lathe" in cell.text(), f"the due item is not on {due_on}: {cell.text()!r}"
    other = tree.find(**{"data-day": "2026-08-15"})
    assert "Lathe" not in other.text(), "the item is repeated on days it is not due"

    links = " ".join(a.attrs.get("href", "") for a in tree.find_all("a"))
    assert "view=calendar" in links or "month=2026-09" in links, \
        "the calendar offers no way to move between months"


def test_invalid_month_falls_back_to_current(env):
    """A18: a month nobody can parse shows this month and says so (GDR 2d)."""
    r = env.get("/maintenance?view=calendar&month=2026-13")
    assert r.status_code == 200
    tree = parse(r.text)
    # The shell ships an empty `#global-ui-error` flash on every page, so the check is
    # that one of the banners actually names the month that could not be read.
    said = [n.text() for n in tree.find_all(cls="flash")]
    assert any("2026-13" in text for text in said), f"the fallback happened silently: {said}"
    today = date.today()
    first = tree.find(**{"data-day": today.replace(day=1).isoformat()})
    assert first is not None, "the grid is not the current month"


def test_calendar_sets_landscape_print_rule(env):
    """A19: a month grid on portrait A4 is unreadable, so the page asks for landscape."""
    env.equipment("Lathe")
    markup = env.get("/maintenance?view=calendar&month=2026-08").text
    assert "@page" in markup and "landscape" in markup
    tree = parse(markup)
    controls = tree.find(cls="page-actions")
    assert controls is not None
    # Core's print stylesheet hides `button` and `.btn` (app.css), so every control
    # in the header is already off the printed page. A module cannot ship CSS, so
    # "hidden when printed" has to mean "built from things core already hides".
    printable = [e for e in controls.elements()
                 if e.tag != "button" and "btn" not in e.classes]
    assert not printable, f"these controls would print with the grid: {printable}"
    list_markup = env.get("/maintenance").text
    assert "landscape" not in list_markup, "the list view is portrait, and should stay so"
