# SPDX-License-Identifier: MIT
"""UI for acme-maintenance: the equipment list, a printable calendar, and one
equipment record with its service history and files.

Four things here are worth copying into your own module.

1. Every fragment is rendered with `to_xml`. fastcore's `FT.__str__` returns the
   element id, so `HTMLResponse(str(block))` sends the browser the word
   "maint-content" where a table should be. Core renders fragments the same way
   (`ui/routes/account.py`).
2. The cells are core's cells. `display_cell` and `editable_cell` take the URL
   they save to, so a module points them at its own REST surface and inherits the
   whole interaction: double-click to edit, Enter or blur to save, ESC to cancel,
   `--` for an empty value.
3. Fetching and rendering are separate. `_list_view`, `_detail_view` and
   `_calendar_view` take plain data and return markup, so a rendering test needs
   neither a database nor a network, and each route handler stays short enough to
   read in one go.
4. A module ships no CSS. Every class below already exists in core's stylesheet,
   which is why there is no `maint-` class anywhere in this file.

The page asks the same permission the API enforces, so a viewer is not offered a
control that would only fail. That is presentation, not protection: every write
still goes through `require_permission` in `routes.py`, and a hand-made request
gets a 403 there.
"""
from __future__ import annotations

import calendar as _calendar
import json
import logging
from datetime import date, datetime
from urllib.parse import urlencode

import httpx
from fasthtml.common import (
    A, Button, Div, H3, Input, P, Script, Span, Style, Table, Tbody, Td, Th,
    Thead, Tr, to_xml,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from celerp.services.permissions import role_has_permission
from ui.components.files import files_section
from ui.components.shell import base_shell, flash, page_header
from ui.components.table import (
    COLUMN_FILTER_JS, EMPTY, ENHANCED_TABLE_JS, breadcrumbs, bulk_toolbar,
    date_range_filter, display_cell, editable_cell, empty_state_cta, filter_th,
    search_bar, sortable_th, status_cards, table_pager,
)
from ui.config import API_BASE, COOKIE_NAME, get_role

log = logging.getLogger(__name__)

API = "/api/maintenance/equipment"
CONTENT_ID = "maint-content"
DETAIL_ID = "maint-detail"
TABLE_ID = "maint-table"

# Which house cell type edits each field, and the name its editor carries. An
# editor replaces the cell it sits in, so the column header is no longer beside
# it: without an aria-label the control has no accessible name at all.
CELL_TYPES = {
    "name": "text",
    "location": "select",
    "serviced_at": "date",
    "interval_days": "number",
    "last_cost": "money",
    "notes": "text",
    "instructions": "textarea",
}
CELL_LABELS = {
    "name": "Equipment name",
    "location": "Location",
    "serviced_at": "Last serviced",
    "interval_days": "Interval in days",
    "last_cost": "Last cost",
    "notes": "Notes",
    "instructions": "Service instructions",
}

LIST_ERROR = ("The equipment list could not be loaded, so none of it is shown below. "
              "That is a connection problem, not an empty list.")
DETAIL_ERROR = "This equipment record could not be loaded, so none of it is shown below."

# One month grid does not fit a portrait page. `@page` is the only styling this
# module adds, and it goes through the shell's supported extra_head hook.
CALENDAR_PRINT_CSS = "@page { size: A4 landscape; margin: 10mm; }"

# Core's `.bulk-toolbar` box is `display: none` until it also carries `is-active`
# (app.css), which is how inventory's action bar stays out of the way until rows
# are ticked. The shared `bulk_toolbar` never hides itself, so the module wraps it
# in that box and toggles the class from the count of ticked rows, on the same two
# events the shared toolbar JS listens to.
BULK_REVEAL_JS = """
(function(){
  if(window.__maintBulkReveal) return; window.__maintBulkReveal = true;
  function sync(){
    var wrap = document.getElementById('maint-bulkwrap');
    if(!wrap) return;
    var n = document.querySelectorAll('#maint-table .bulk-select:checked').length;
    if(n > 0){ wrap.classList.add('is-active'); } else { wrap.classList.remove('is-active'); }
  }
  document.addEventListener('change', sync);
  document.addEventListener('htmx:afterSwap', sync);
  sync();
})();
"""


def _api(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=5)


# ── talking to the module's own API ────────────────────────────────────────────

async def _call(request: Request, method: str, url: str, **kw) -> tuple[int, dict]:
    """One API call. Status 0 means the request never landed at all.

    Returning the status instead of raising is what lets each route decide: 401
    sends the user to log in, 403 is a role that may read but not write, and a
    call that never landed renders an error state rather than an empty list.
    """
    try:
        async with _api(request) as c:
            r = await getattr(c, method)(url, **kw)
    except Exception as exc:  # noqa: BLE001 - any transport failure reads the same to the user
        log.warning("maintenance: %s %s failed: %s", method.upper(), url, exc)
        return 0, {}
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    return r.status_code, payload if isinstance(payload, dict) else {"items": payload}


def _detail_message(payload: dict) -> str:
    """The API's own message for a rejected write, if it sent one."""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:                    # FastAPI validation shape
        return str(detail[0].get("msg", "")) or ""
    return ""


def _toast(message: str, kind: str = "success") -> dict:
    """Header that makes core's toast listener speak (`shell.py`)."""
    return {"HX-Trigger": json.dumps({"celerpToast": {"message": message, "type": kind}})}


def _login_fragment() -> HTMLResponse:
    """An expired session inside HTMX: send the browser to the login page."""
    return HTMLResponse("", status_code=401, headers={"HX-Redirect": "/login"})


async def _context(request: Request) -> tuple[dict, bool]:
    """The company settings the chrome needs, and whether this role may write.

    Permissions can be re-pointed per company in settings, so the answer comes
    from the same helper and the same key the API router enforces.
    """
    status, company = await _call(request, "get", "/companies/me")
    settings = (company.get("settings") or {}) if status == 200 else {}
    return settings, role_has_permission(settings, get_role(request), "edit_inventory")


async def _location_names(request: Request) -> list[str] | None:
    """The company's locations, or None when there is no list to choose from.

    None covers both a company with no locations yet and a call that failed. The
    cell degrades to free text either way rather than opening an empty dropdown.
    """
    status, payload = await _call(request, "get", "/companies/me/locations")
    if status != 200:
        return None
    names = [str(loc.get("name") or "") for loc in payload.get("items", []) if loc.get("name")]
    return names or None


# ── request parameters ────────────────────────────────────────────────────────

def _params(request: Request) -> dict:
    """The view state, all of it from the URL so it survives Back and a bookmark."""
    q = request.query_params
    return {"q": q.get("q", ""), "status": q.get("status", ""), "show": q.get("show", ""),
            "view": q.get("view", ""), "month": q.get("month", "")}


def _url(params: dict, **overrides) -> str:
    """`/maintenance` carrying the given view state. A None override drops the key."""
    merged = {**params, **overrides}
    query = urlencode({k: v for k, v in merged.items() if v})
    return f"/maintenance?{query}" if query else "/maintenance"


def _month(raw: str) -> tuple[str, str | None]:
    """A `YYYY-MM` month, plus the sentence to show when the input was unusable."""
    today = date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    if not raw:
        return current, None
    try:
        parsed = datetime.strptime(raw, "%Y-%m")
    except ValueError:
        return current, f"{raw} is not a month, so this is {current}."
    return f"{parsed.year:04d}-{parsed.month:02d}", None


def _step_month(month: str, step: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    total = year * 12 + (mon - 1) + step
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _month_label(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{_calendar.month_name[mon]} {year}"


# ── cells ─────────────────────────────────────────────────────────────────────

def _status_badge(status: str) -> Span:
    label, cls = ("Due", "badge badge--warning") if status == "due" else ("OK", "badge")
    return Span(label, cls=cls)


def _cell(item: dict, field: str, *, can_edit: bool, locations: list[str] | None = None):
    """The house display cell for one field, pointed at this module's edit route."""
    equipment_id = str(item["id"])
    extra: dict = {}
    if CELL_TYPES[field] == "select":
        extra["options"] = list(locations or [])
    if field == "name":
        # The name is the way into the record, the same as inventory's SKU cell:
        # one click opens it, a double-click still edits it in place.
        extra["link_href"] = f"/maintenance/{equipment_id}"
    return display_cell(equipment_id, field, item.get(field), cell_type=CELL_TYPES[field],
                        editable=can_edit,
                        edit_url=f"/maintenance/{equipment_id}/cell/{field}/edit", **extra)


def _editor(equipment_id: str, field: str, value, *, locations: list[str] | None = None):
    """The in-place editor for one field. ESC and save both come from core."""
    cell_type = CELL_TYPES[field]
    options = None
    if cell_type == "select":
        if locations:
            options = list(locations)
        else:
            cell_type = "text"          # no list to choose from: type it (D4)
    return editable_cell(equipment_id, field, value, cell_type=cell_type, options=options,
                         aria_label=CELL_LABELS[field],
                         patch_url=f"/maintenance/{equipment_id}/cell/{field}")


# ── the list view ─────────────────────────────────────────────────────────────

def _visible(items: list[dict], params: dict) -> list[dict]:
    """The rows the current filters leave, in the order the API returned them."""
    shown = items
    if params["status"] in ("due", "ok"):
        shown = [i for i in shown if i["status"] == params["status"]]
    needle = params["q"].strip().lower()
    if needle:
        shown = [i for i in shown
                 if needle in (i.get("name") or "").lower()
                 or needle in (i.get("location") or "").lower()]
    return shown


def _row(item: dict, *, locations: list[str] | None, can_edit: bool) -> Tr:
    equipment_id = str(item["id"])
    return Tr(
        Td(Input(type="checkbox", cls="bulk-select", name="selected", value=equipment_id),
           cls="col-checkbox"),
        _cell(item, "name", can_edit=can_edit),
        _cell(item, "location", can_edit=can_edit, locations=locations),
        _cell(item, "serviced_at", can_edit=can_edit),
        _cell(item, "interval_days", can_edit=can_edit),
        Td(item.get("due_date") or EMPTY, cls="cell"),
        _cell(item, "last_cost", can_edit=can_edit),
        Td(_status_badge(item["status"]), cls="cell",
           **{"data-filter-value": item["status"]}),
        Td(_row_action(item, can_edit=can_edit), cls="cell cell--actions"),
        cls="data-row")


def _row_action(item: dict, *, can_edit: bool):
    """One control per row: archive, or restore when the row is already archived."""
    if not can_edit:
        return ""
    equipment_id = str(item["id"])
    if item.get("archived"):
        return Button("↺", type="button", cls="btn btn--xs btn--ghost", title="Restore",
                      hx_post=f"/maintenance/{equipment_id}/restore",
                      hx_target=f"#{CONTENT_ID}", hx_swap="outerHTML")
    return Button("×", type="button", cls="btn btn--xs btn--danger", title="Archive",
                  hx_post=f"/maintenance/{equipment_id}/archive",
                  hx_target=f"#{CONTENT_ID}", hx_swap="outerHTML",
                  hx_confirm="Archive this equipment? You can bring it back from the archived list.")


def _table(items: list[dict], *, locations: list[str] | None, can_edit: bool) -> Table:
    head = Thead(Tr(
        Th(Input(type="checkbox", cls="bulk-select-all"), cls="col-checkbox"),
        sortable_th("Name", 1, center=True),
        filter_th("Location", 2, center=True),
        sortable_th("Last serviced", 3, center=True),
        sortable_th("Interval (days)", 4, right=True),
        sortable_th("Next due", 5, center=True),
        sortable_th("Last cost", 6, right=True),
        filter_th("Status", 7, center=True),
        Th("", cls="cell--actions"),
    ))
    body = Tbody(*[_row(i, locations=locations, can_edit=can_edit) for i in items])
    return Table(head, body, cls="data-table js-table", id=TABLE_ID,
                 **{"data-page-size": "25"})


def _content(items: list[dict], params: dict, *, locations: list[str] | None,
             can_edit: bool, error: str | None = None) -> Div:
    """The block every list write swaps: cards, the action box, and the table."""
    if error:
        return Div(
            flash(error, kind="error"),
            empty_state_cta("Could not load equipment", "Retry", _url(params)),
            id=CONTENT_ID)
    shown = _visible(items, params)
    due = sum(1 for i in items if i["status"] == "due")
    cards = [{"label": "Due", "count": due, "status": "due", "color": "yellow"},
             {"label": "OK", "count": len(items) - due, "status": "ok", "color": "green"}]
    if not shown and (params["q"] or params["status"]):
        block = empty_state_cta("No equipment matches these filters.",
                                "Clear filters", _url(params, q=None, status=None))
    elif not shown:
        block = empty_state_cta(
            "No equipment yet",
            *(("Add equipment", "/maintenance/create-blank", True) if can_edit else ()))
    else:
        block = Div(_table(shown, locations=locations, can_edit=can_edit), table_pager(TABLE_ID))
    return Div(
        status_cards(cards, _url(params, status=None), params["status"] or None,
                     total_override=len(items)),
        Div(date_range_filter(TABLE_ID, 3, "Last serviced"), cls="filter-bar"),
        (Div(bulk_toolbar(TABLE_ID, [
            {"value": "serviced", "label": "Mark serviced", "method": "post",
             "url": "/maintenance/mark-serviced", "target": f"#{CONTENT_ID}",
             "swap": "outerHTML"},
        ]), cls="bulk-toolbar", id="maint-bulkwrap") if can_edit else ""),
        block,
        id=CONTENT_ID)


def _list_view(items: list[dict], params: dict, *, locations: list[str] | None,
               can_edit: bool, error: str | None = None) -> Div:
    """The whole list page body, from breadcrumbs down."""
    actions = [search_bar("Search name or location", target=f"#{CONTENT_ID}",
                          url="/maintenance/content"),
               A("Calendar view", href=_url(params, view="calendar"),
                 cls="btn btn--sm btn--ghost")]
    if params["show"] == "archived":
        actions.append(A("Active equipment", href=_url(params, show=None),
                         cls="btn btn--sm btn--ghost"))
    else:
        actions.append(A("Archived", href=_url(params, show="archived"),
                         cls="btn btn--sm btn--ghost"))
    if can_edit:
        actions.append(Button("Add equipment", type="button", cls="btn btn--primary",
                              hx_post="/maintenance/create-blank", hx_swap="none"))
    return Div(
        breadcrumbs([("Operations", None), ("Maintenance", None)]),
        page_header("Equipment maintenance", *actions),
        _content(items, params, locations=locations, can_edit=can_edit, error=error),
        # The shared enhancers: sorting and paging, the column funnels, and the
        # reveal for the action box. `bulk_toolbar` ships its own script, so the
        # page must not add a second copy of it.
        Script(ENHANCED_TABLE_JS), Script(COLUMN_FILTER_JS), Script(BULK_REVEAL_JS))


# ── the detail view ───────────────────────────────────────────────────────────

def _detail_field(label: str, item: dict, field: str, *, locations: list[str] | None,
                  can_edit: bool) -> Tr:
    return Tr(Td(label, cls="detail-label"),
              _cell(item, field, can_edit=can_edit, locations=locations))


def _history(item: dict, logs: list[dict], *, can_edit: bool) -> Table:
    """Service history, newest first. Only the newest entry can be taken back."""
    equipment_id = str(item["id"])
    rows = []
    for index, entry in enumerate(logs):
        undo = ""
        if can_edit and index == 0:
            undo = Button("×", type="button", cls="btn btn--xs btn--ghost",
                          title="Remove this entry",
                          hx_delete=f"/maintenance/{equipment_id}/service-log/{entry['id']}",
                          hx_target=f"#{DETAIL_ID}", hx_swap="outerHTML",
                          hx_confirm="Remove the newest service entry? "
                                     "The one before it becomes the last service.")
        rows.append(Tr(
            Td(entry.get("serviced_at") or EMPTY, cls="cell"),
            Td(entry.get("cost") if entry.get("cost") not in (None, "") else EMPTY,
               cls="cell cell--number"),
            Td(entry.get("note") or EMPTY, cls="cell"),
            Td(undo, cls="cell cell--actions"),
            cls="data-row"))
    if not rows:
        rows = [Tr(Td("No services recorded", colspan="4", cls="cell"), cls="data-row")]
    return Table(
        Thead(Tr(Th("Serviced", cls="cell--center"), Th("Cost", cls="cell--number"),
                 Th("Note", cls="cell--center"), Th("", cls="cell--actions"))),
        Tbody(*rows), cls="data-table")


def _detail_view(item: dict, logs: list[dict], files: list[dict], *,
                 locations: list[str] | None, can_edit: bool) -> Div:
    """The whole detail page body: identity, instructions, history, files."""
    equipment_id = str(item["id"])
    actions = []
    if can_edit:
        actions.append(Button("Mark serviced", type="button", cls="btn btn--primary",
                              hx_post=f"/maintenance/{equipment_id}/mark-serviced",
                              hx_target=f"#{DETAIL_ID}", hx_swap="outerHTML"))
        if item.get("archived"):
            actions.append(Button("Restore", type="button", cls="btn btn--sm btn--ghost",
                                  hx_post=f"/maintenance/{equipment_id}/restore?from=detail",
                                  hx_swap="none"))
        else:
            actions.append(Button("Archive", type="button", cls="btn btn--sm btn--danger",
                                  hx_post=f"/maintenance/{equipment_id}/archive?from=detail",
                                  hx_swap="none",
                                  hx_confirm="Archive this equipment? "
                                             "You can bring it back from the archived list."))
    identity = Div(
        H3("Equipment", cls="section-title"),
        Div(Table(
            _detail_field("Name", item, "name", locations=locations, can_edit=can_edit),
            _detail_field("Location", item, "location", locations=locations, can_edit=can_edit),
            _detail_field("Last serviced", item, "serviced_at", locations=None, can_edit=can_edit),
            _detail_field("Interval (days)", item, "interval_days", locations=None,
                          can_edit=can_edit),
            Tr(Td("Next due", cls="detail-label"),
               Td(item.get("due_date") or EMPTY, cls="cell")),
            Tr(Td("Status", cls="detail-label"),
               Td(_status_badge(item["status"]), cls="cell")),
            _detail_field("Last cost", item, "last_cost", locations=None, can_edit=can_edit),
            _detail_field("Notes", item, "notes", locations=None, can_edit=can_edit),
            cls="detail-table"), cls="detail-card"),
        cls="section")
    instructions = Div(
        H3("Service instructions", cls="section-title"),
        Div(Table(_detail_field("Procedure", item, "instructions", locations=None,
                                can_edit=can_edit), cls="detail-table"), cls="detail-card"),
        cls="section")
    history = Div(
        H3("Service history", cls="section-title"),
        Div(_history(item, logs, can_edit=can_edit), cls="detail-card"),
        cls="section")
    return Div(
        breadcrumbs([("Maintenance", "/maintenance"), (item.get("name") or "Equipment", None)]),
        page_header(item.get("name") or "Equipment", *actions),
        Div(
            Div(identity, instructions, history, cls="detail-col-left"),
            # The shared files section against this module's own file routes: the
            # tag vocabulary and the hero image belong to core's own entities, and
            # a module's files are not part of Company Files (see the README).
            Div(files_section("equipment", equipment_id, files, can_tag=False,
                              can_describe=False, can_set_hero=False, can_upload=can_edit,
                              show_linked=False, title="Files",
                              base_url=f"/maintenance/{equipment_id}/files"),
                cls="detail-col-right"),
            cls="detail-layout"),
        id=DETAIL_ID)


# ── the calendar view ─────────────────────────────────────────────────────────

def _calendar_view(items: list[dict], month: str, message: str | None = None) -> Div:
    """A month of due dates, one printed page wide."""
    year, mon = int(month[:4]), int(month[5:7])
    due_by_day: dict[str, list[dict]] = {}
    for item in items:
        due = item.get("due_date") or ""
        if due[:7] == month:
            due_by_day.setdefault(due, []).append(item)
    weeks = []
    for week in _calendar.Calendar(firstweekday=0).monthdatescalendar(year, mon):
        cells = []
        for day in week:
            if day.month != mon:
                cells.append(Td("", cls="cell"))
                continue
            iso = day.isoformat()
            entries = [Div(A(i.get("name") or "Equipment", href=f"/maintenance/{i['id']}",
                             cls="table-link"))
                       for i in due_by_day.get(iso, [])]
            cells.append(Td(Div(str(day.day), cls="text-muted"), *entries,
                            cls="cell", **{"data-day": iso}))
        weeks.append(Tr(*cells, cls="data-row"))
    grid = Table(
        Thead(Tr(*[Th(name, cls="cell--center") for name in _calendar.day_abbr])),
        Tbody(*weeks), cls="data-table")
    actions = [
        A("List view", href="/maintenance", cls="btn btn--sm btn--ghost"),
        A(f"‹ {_month_label(_step_month(month, -1))}",
          href=f"/maintenance?view=calendar&month={_step_month(month, -1)}",
          cls="btn btn--sm btn--ghost"),
        A(f"{_month_label(_step_month(month, 1))} ›",
          href=f"/maintenance?view=calendar&month={_step_month(month, 1)}",
          cls="btn btn--sm btn--ghost"),
        Button("Print", type="button", cls="btn btn--primary", onclick="window.print()"),
    ]
    body = [
        breadcrumbs([("Maintenance", "/maintenance"), (_month_label(month), None)]),
        page_header(f"Service calendar, {_month_label(month)}", *actions),
    ]
    if message:
        body.append(flash(message, kind="warning"))
    body.append(Div(grid, cls="detail-card"))
    if not due_by_day:
        body.append(P("No services due this month", cls="text-muted"))
    return Div(*body)


# ── routes ────────────────────────────────────────────────────────────────────

def setup_ui_routes(app) -> None:
    """Entry point the module loader calls to register UI pages."""

    async def _list_data(request: Request, params: dict) -> tuple[int, list[dict]]:
        query = {"show": params["show"]} if params["show"] else None
        status, payload = await _call(request, "get", API, params=query)
        return status, payload.get("items", [])

    async def _list_fragment(request: Request, *, message: str | None = None,
                             kind: str = "success") -> HTMLResponse:
        """The list block, re-read from the API, with the filters still applied."""
        params = _params(request)
        status, items = await _list_data(request, params)
        if status == 401:
            return _login_fragment()
        _settings, can_edit = await _context(request)
        locations = await _location_names(request)
        block = _content(items, params, locations=locations, can_edit=can_edit,
                         error=None if status == 200 else LIST_ERROR)
        return HTMLResponse(to_xml(block),
                            headers=_toast(message, kind) if message else {})

    async def _detail_data(request: Request, equipment_id: str) -> tuple[int, dict]:
        return await _call(request, "get", f"{API}/{equipment_id}")

    async def _detail_fragment(request: Request, equipment_id: str, *,
                               message: str | None = None,
                               kind: str = "success") -> HTMLResponse:
        status, payload = await _detail_data(request, equipment_id)
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload) or DETAIL_ERROR, "error"))
        _settings, can_edit = await _context(request)
        locations = await _location_names(request)
        block = _detail_view(payload["item"], payload.get("logs", []), payload.get("files", []),
                             locations=locations, can_edit=can_edit)
        return HTMLResponse(to_xml(block),
                            headers=_toast(message, kind) if message else {})

    async def _files_fragment(request: Request, equipment_id: str, *,
                              message: str | None = None,
                              kind: str = "success") -> HTMLResponse:
        """Just the files section, which is what its own controls swap."""
        status, payload = await _call(request, "get", f"{API}/{equipment_id}/files")
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload)
                                               or "The files could not be loaded", "error"))
        _settings, can_edit = await _context(request)
        q = request.query_params
        section = files_section(
            "equipment", equipment_id, payload.get("items", []), can_tag=False,
            can_describe=False, can_set_hero=False, can_upload=can_edit, show_linked=False,
            title="Files", base_url=f"/maintenance/{equipment_id}/files",
            page=int(q.get("page") or 1), sort_dir=q.get("sort_dir") or "desc",
            tag_filter=q.get("tag_filter", ""), date_from=q.get("date_from", ""),
            date_to=q.get("date_to", ""), search=q.get("search", ""))
        return HTMLResponse(to_xml(section),
                            headers=_toast(message, kind) if message else {})

    # ── pages ──

    @app.get("/maintenance")
    async def maintenance_page(request: Request):
        params = _params(request)
        status, items = await _list_data(request, params)
        if status == 401:
            return RedirectResponse("/login", status_code=302)
        if status == 403:
            return RedirectResponse("/dashboard", status_code=302)
        settings, can_edit = await _context(request)
        extra_head = None
        if params["view"] == "calendar":
            month, message = _month(params["month"])
            body = _calendar_view(items, month, message=message)
            extra_head = [Style(CALENDAR_PRINT_CSS)]
        else:
            locations = await _location_names(request)
            body = _list_view(items, params, locations=locations, can_edit=can_edit,
                              error=None if status == 200 else LIST_ERROR)
        return await base_shell(body, title="Maintenance - Celerp", nav_active="maintenance",
                                request=request, company_settings=settings,
                                extra_head=extra_head)

    @app.get("/maintenance/content")
    async def maintenance_content(request: Request):
        if request.headers.get("HX-Request") != "true":
            query = request.url.query
            return RedirectResponse(f"/maintenance{'?' + query if query else ''}",
                                    status_code=302)
        return await _list_fragment(request)

    @app.get("/maintenance/{equipment_id}")
    async def maintenance_detail(request: Request, equipment_id: str):
        status, payload = await _detail_data(request, equipment_id)
        if status == 401:
            return RedirectResponse("/login", status_code=302)
        if status == 403:
            return RedirectResponse("/dashboard", status_code=302)
        settings, can_edit = await _context(request)
        if status != 200:
            gone = status == 404
            body = Div(
                breadcrumbs([("Maintenance", "/maintenance"), ("Equipment", None)]),
                flash("That equipment no longer exists." if gone else DETAIL_ERROR,
                      kind="error"),
                empty_state_cta("It may have been removed by someone else.",
                                "Back to the list", "/maintenance"))
            page = await base_shell(body, title="Maintenance - Celerp",
                                    nav_active="maintenance", request=request,
                                    company_settings=settings)
            return HTMLResponse(to_xml(page), status_code=404 if gone else 502)
        locations = await _location_names(request)
        body = _detail_view(payload["item"], payload.get("logs", []), payload.get("files", []),
                            locations=locations, can_edit=can_edit)
        return await base_shell(body, title=f"{payload['item'].get('name') or 'Equipment'} - Celerp",
                                nav_active="maintenance", request=request,
                                company_settings=settings)

    # ── writes ──

    @app.post("/maintenance/create-blank")
    async def maintenance_create_blank(request: Request):
        """Add is one click: create a real record and open it (the core pattern)."""
        status, payload = await _call(request, "post", API, json={})
        if status == 401:
            return _login_fragment()
        if status not in (200, 201) or not payload.get("id"):
            message = _detail_message(payload) or "The equipment could not be created"
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(message, "error"))
        return HTMLResponse("", status_code=204,
                            headers={"HX-Redirect": f"/maintenance/{payload['id']}"})

    @app.post("/maintenance/mark-serviced")
    async def maintenance_mark_serviced(request: Request):
        form = await request.form()
        ids = [str(i) for i in form.getlist("selected") if i]
        status, payload = await _call(request, "post", f"{API}/mark-serviced",
                                      json={"ids": ids})
        if status == 401:
            return _login_fragment()
        if status != 200:
            return await _list_fragment(
                request, kind="error",
                message=_detail_message(payload) or "Nothing was marked serviced")
        updated, skipped = payload.get("updated", 0), payload.get("skipped", 0)
        message = f"Marked {updated} serviced"
        if skipped:
            message += f", {skipped} skipped because they are no longer there"
        return await _list_fragment(request, message=message)

    @app.post("/maintenance/{equipment_id}/mark-serviced")
    async def maintenance_mark_one_serviced(request: Request, equipment_id: str):
        status, payload = await _call(request, "post", f"{API}/mark-serviced",
                                      json={"ids": [equipment_id]})
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload)
                                               or "It was not marked serviced", "error"))
        if not payload.get("updated"):
            return await _detail_fragment(request, equipment_id, kind="error",
                                          message="That equipment is no longer there")
        return await _detail_fragment(request, equipment_id,
                                      message="Marked serviced today")

    @app.delete("/maintenance/{equipment_id}/service-log/{log_id}")
    async def maintenance_undo_service(request: Request, equipment_id: str, log_id: str):
        status, payload = await _call(request, "delete",
                                      f"{API}/{equipment_id}/service-log/{log_id}")
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload)
                                               or "That entry was not removed", "error"))
        message = ("Service entry removed" if payload.get("removed")
                   else "That entry was already gone")
        return await _detail_fragment(request, equipment_id, message=message)

    async def _archive_or_restore(request: Request, equipment_id: str, action: str):
        status, payload = await _call(request, "post", f"{API}/{equipment_id}/{action}")
        if status == 401:
            return _login_fragment()
        if status != 200:
            message = _detail_message(payload) or "That equipment no longer exists"
            if request.query_params.get("from") == "detail":
                return HTMLResponse("", status_code=status or 502,
                                    headers=_toast(message, "error"))
            return await _list_fragment(request, message=message, kind="error")
        done = "archived" if action == "archive" else "restored"
        if request.query_params.get("from") == "detail":
            # The record has changed which list it belongs to, so the page it is
            # on is no longer the page to be on.
            target = "/maintenance" if action == "archive" else f"/maintenance/{equipment_id}"
            return HTMLResponse("", status_code=204,
                                headers={"HX-Redirect": target,
                                         **_toast(f"Equipment {done}")})
        return await _list_fragment(request, message=f"Equipment {done}")

    @app.post("/maintenance/{equipment_id}/archive")
    async def maintenance_archive(request: Request, equipment_id: str):
        return await _archive_or_restore(request, equipment_id, "archive")

    @app.post("/maintenance/{equipment_id}/restore")
    async def maintenance_restore(request: Request, equipment_id: str):
        return await _archive_or_restore(request, equipment_id, "restore")

    # ── click-to-edit cells ──

    @app.get("/maintenance/{equipment_id}/cell/{field}/edit")
    async def cell_edit(request: Request, equipment_id: str, field: str):
        if field not in CELL_TYPES:
            return HTMLResponse("", status_code=404)
        status, payload = await _detail_data(request, equipment_id)
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload) or DETAIL_ERROR, "error"))
        locations, warning = None, None
        if CELL_TYPES[field] == "select":
            locations = await _location_names(request)
            if locations is None:
                warning = ("The location list is unavailable, so this is a free-text "
                           "field for now.")
        editor = _editor(equipment_id, field, payload["item"].get(field), locations=locations)
        return HTMLResponse(to_xml(editor),
                            headers=_toast(warning, "warning") if warning else {})

    @app.get("/maintenance/{equipment_id}/cell/{field}/display")
    async def cell_display(request: Request, equipment_id: str, field: str):
        """ESC and an untouched editor both restore the cell from stored data."""
        if field not in CELL_TYPES:
            return HTMLResponse("", status_code=404)
        status, payload = await _detail_data(request, equipment_id)
        if status == 401:
            return _login_fragment()
        if status != 200:
            return HTMLResponse("", status_code=status or 502,
                                headers=_toast(_detail_message(payload) or DETAIL_ERROR, "error"))
        _settings, can_edit = await _context(request)
        locations = await _location_names(request) if CELL_TYPES[field] == "select" else None
        return HTMLResponse(to_xml(_cell(payload["item"], field, can_edit=can_edit,
                                         locations=locations)))

    @app.patch("/maintenance/{equipment_id}/cell/{field}")
    async def cell_save(request: Request, equipment_id: str, field: str):
        if field not in CELL_TYPES:
            return HTMLResponse("", status_code=404)
        form = await request.form()
        value = form.get("value", "")
        status, payload = await _call(request, "patch",
                                      f"{API}/{equipment_id}/field/{field}",
                                      json={"value": value})
        if status == 401:
            return _login_fragment()
        locations = await _location_names(request) if CELL_TYPES[field] == "select" else None
        if status != 200:
            # Rejected: give the editor back with the value still in it and say
            # why, rather than swallowing the edit or hiding the control.
            message = _detail_message(payload) or "That value was not saved"
            return HTMLResponse(to_xml(_editor(equipment_id, field, value,
                                               locations=locations)),
                                headers=_toast(message, "error"))
        return HTMLResponse(to_xml(_cell(payload, field, can_edit=True, locations=locations)))

    # ── files: the contract the shared files section calls ──

    @app.post("/maintenance/{equipment_id}/files")
    async def files_upload(request: Request, equipment_id: str):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            return await _files_fragment(request, equipment_id, kind="error",
                                         message="Choose a file to upload")
        content = await upload.read()
        status, payload = await _call(
            request, "post", f"{API}/{equipment_id}/files",
            files={"file": (upload.filename, content,
                            upload.content_type or "application/octet-stream")})
        if status == 401:
            return _login_fragment()
        if status != 200:
            return await _files_fragment(
                request, equipment_id, kind="error",
                message=_detail_message(payload) or "The file was not saved")
        return await _files_fragment(request, equipment_id,
                                     message=f"{payload.get('filename', 'File')} uploaded")

    @app.get("/maintenance/{equipment_id}/files/_section")
    async def files_section_fragment(request: Request, equipment_id: str):
        return await _files_fragment(request, equipment_id)

    @app.delete("/maintenance/{equipment_id}/files/{file_id}")
    async def files_delete(request: Request, equipment_id: str, file_id: str):
        status, payload = await _call(request, "delete",
                                      f"{API}/{equipment_id}/files/{file_id}")
        if status == 401:
            return _login_fragment()
        if status != 200:
            return await _files_fragment(
                request, equipment_id, kind="error",
                message=_detail_message(payload) or "The file was not deleted")
        message = "File deleted" if payload.get("deleted") else "That file was already gone"
        return await _files_fragment(request, equipment_id, message=message)

    @app.get("/maintenance/{equipment_id}/files/{file_id}/download")
    async def files_download(request: Request, equipment_id: str, file_id: str):
        """The bytes come through the module, so the storage path stays server side."""
        try:
            async with _api(request) as c:
                r = await c.get(f"{API}/{equipment_id}/files/{file_id}/download")
        except Exception as exc:  # noqa: BLE001
            log.warning("maintenance: download failed: %s", exc)
            return HTMLResponse("The file could not be read", status_code=502)
        if r.status_code == 401:
            return RedirectResponse("/login", status_code=302)
        if r.status_code != 200:
            return HTMLResponse("That file is not available", status_code=r.status_code)
        headers = {}
        disposition = r.headers.get("content-disposition")
        if disposition:
            headers["content-disposition"] = disposition
        return Response(r.content, media_type=r.headers.get("content-type",
                                                            "application/octet-stream"),
                        headers=headers)

    log.info("acme-maintenance: UI routes registered")
