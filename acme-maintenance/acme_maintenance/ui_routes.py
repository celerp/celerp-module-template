# SPDX-License-Identifier: MIT
"""UI for acme-maintenance: the /maintenance page and its click-to-edit cells.

This page demonstrates the standard Celerp list patterns a module should follow
(see the house UX rules):
  - a sortable table (Excel-style, click a header to sort) via the shared
    `sortable_th` + `ENHANCED_TABLE_JS`;
  - an Excel column filter funnel (`filter_th`) and a date-range filter
    (`date_range_filter`) via the shared `COLUMN_FILTER_JS` - both client-side;
  - status filter cards (`status_cards`) and a search box, both reflected in the
    URL (?status=, ?q=), per the "filters go in the URL" rule;
  - bulk selection + a bulk action toolbar (`bulk_toolbar` + BULK_TOOLBAR_JS);
  - click-to-edit cells: double-click a value to edit it in place, Esc cancels,
    Enter/blur saves via HTMX - no page reload, no popup;
  - newest rows first; column headers centered; the money column right-aligned.

The sort/filter/bulk behavior reuses Celerp's own shipped JS (DRY - one source).
The click-to-edit cell is implemented here because the shared `editable_cell`
is bound to the core items API; a module needs its own edit endpoints, so this
shows how to build one that matches the house interaction exactly.
"""
from __future__ import annotations

import logging

import httpx
from fasthtml.common import (
    Button, Div, Form, H2, Input, P, Script, Span, Table, Tbody, Td,
    Th, Thead, Tr,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse

from ui.components.shell import base_shell
from ui.components.table import (
    BULK_TOOLBAR_JS, COLUMN_FILTER_JS, ENHANCED_TABLE_JS, breadcrumbs,
    bulk_toolbar, date_range_filter, empty_state_cta, filter_th, sortable_th,
    status_cards, table_pager,
)
from ui.config import API_BASE, COOKIE_NAME
from ui.i18n import t  # noqa: F401  (kept to show where translated strings would come from)

log = logging.getLogger(__name__)

EMPTY = "--"                 # house rule: empty click-to-edit cells show --, never blank
_TABLE_ID = "maint-table"

# Money uses the shared currency formatter so amounts look like everywhere else.
try:
    from celerp.output.doc_print import fmt_money
except Exception:  # pragma: no cover - defensive if the helper moves
    def fmt_money(v, currency=None):
        return f"{float(v or 0):,.2f}"


def _api(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=5)


# ── click-to-edit cell ────────────────────────────────────────────────────────

def _display_cell(item: dict, field: str, cell_type: str = "text") -> Td:
    """A value cell that turns editable on double-click. Visually marked with
    `cell--clickable` (house rule: editable fields must look editable)."""
    raw = item.get(field)
    if cell_type == "money":
        shown = fmt_money(raw)
        cls, filter_val = "cell cell--clickable cell--number", str(raw or 0)
    else:
        shown = str(raw) if raw not in (None, "") else EMPTY
        cls, filter_val = "cell cell--clickable", shown
    return Td(
        shown,
        title="Double-click to edit",
        cls=cls,
        hx_get=f"/maintenance/{item['id']}/field/{field}/edit",
        hx_target="this", hx_swap="outerHTML", hx_trigger="dblclick",
        **{"data-filter-value": filter_val},
    )


def _edit_cell(equipment_id: str, field: str, value: str, cell_type: str) -> Td:
    """The in-place editor. Enter or blur saves (HTMX PATCH); Esc restores the
    display cell without saving. Mirrors the core cell-edit interaction."""
    display_url = f"/maintenance/{equipment_id}/field/{field}/display"
    save_url = f"/maintenance/{equipment_id}/field/{field}"
    esc = (f"if(event.key==='Escape'){{this._esc=1;"
           f"htmx.ajax('GET','{display_url}',{{target:this.closest('td'),swap:'outerHTML'}});"
           f"}}else if(event.key==='Enter'){{event.preventDefault();this.blur();}}")
    blur = f"if(!this._esc){{htmx.trigger(this,'save');}}"
    common = dict(name="value", hx_patch=save_url, hx_target="closest td",
                  hx_swap="outerHTML", hx_include="this", hx_trigger="save",
                  autofocus=True, cls="cell-input", onkeydown=esc, onblur=blur)
    if cell_type == "number":
        inp = Input(type="number", value=value, step="1", **common)
    elif cell_type == "money":
        inp = Input(type="number", value=value, step="0.01", **common)
    else:
        inp = Input(type="text", value=value, **common)
    return Td(inp, cls="cell cell--editing")


# ── table ─────────────────────────────────────────────────────────────────────

def _status_badge(status: str) -> Span:
    label, cls = ("Due", "badge badge--warning") if status == "due" else ("OK", "badge")
    return Span(label, cls=cls)

_COLS = [  # (label, sort col index, right-aligned?)
    ("Name", 1, False), ("Location", 2, False), ("Last serviced", 3, False),
    ("Interval (days)", 4, False), ("Next due", 5, False), ("Last cost", 6, True),
    ("Status", 7, False),
]


def _row(item: dict) -> Tr:
    iid = item["id"]
    return Tr(
        Td(Input(type="checkbox", cls="bulk-select", value=iid), cls="cell cell--check"),
        _display_cell(item, "name"),
        _display_cell(item, "location"),
        Td(item.get("serviced_at") or EMPTY, cls="cell"),                  # date: filterable/sortable
        _display_cell(item, "interval_days", "number"),
        Td(item.get("due_date") or EMPTY, cls="cell"),
        _display_cell(item, "last_cost", "money"),
        Td(_status_badge(item["status"]), cls="cell", **{"data-filter-value": item["status"]}),
        Td(
            Button("Mark serviced", type="button", cls="btn btn--xs",
                   hx_post="/maintenance/mark-serviced", hx_include="this",
                   hx_target="#maint-content", hx_swap="outerHTML",
                   **{"hx-vals": f'{{"ids": ["{iid}"]}}'}),
            Button("Delete", type="button", cls="btn btn--xs btn--ghost",
                   hx_delete=f"/maintenance/{iid}", hx_target="#maint-content",
                   hx_swap="outerHTML", hx_confirm="Delete this equipment?"),
            cls="cell cell--actions"),
        cls="data-row")


def _table(items: list[dict]) -> Table:
    head = Thead(Tr(
        Th(Input(type="checkbox", cls="bulk-select-all"), cls="cell--check"),
        sortable_th("Name", 1), filter_th("Location", 2), sortable_th("Last serviced", 3),
        sortable_th("Interval (days)", 4), sortable_th("Next due", 5),
        sortable_th("Last cost", 6, right=True), sortable_th("Status", 7),
        Th("", cls="cell--actions"),
    ))
    body = Tbody(*[_row(i) for i in items])
    return Table(head, body, cls="data-table js-table", id=_TABLE_ID,
                 **{"data-page-size": "25"})


def _content(items: list[dict], q: str, status: str) -> Div:
    """The filterable content block (swapped by HTMX after add/mark/delete)."""
    shown = items
    if status in ("due", "ok"):
        shown = [i for i in shown if i["status"] == status]
    if q:
        ql = q.lower()
        shown = [i for i in shown if ql in (i["name"] or "").lower()
                 or ql in (i["location"] or "").lower()]
    due_count = sum(1 for i in items if i["status"] == "due")
    cards = [{"label": "Due", "count": due_count, "status": "due", "color": "amber"},
             {"label": "OK", "count": len(items) - due_count, "status": "ok", "color": "green"}]
    filter_bar = Div(
        Form(Input(type="search", name="q", value=q, placeholder="Search name or location",
                   cls="form-input form-input--sm"),
             (Input(type="hidden", name="status", value=status) if status else ""),
             method="get", action="/maintenance", cls="maint-search"),
        date_range_filter(_TABLE_ID, 3, "Last serviced"),
        cls="maint-filter-bar")
    if not shown:
        table_block = empty_state_cta(
            title="No equipment matches" if (q or status) else "No equipment yet",
            body="Add your first item below." if not (q or status) else "Try clearing filters.")
    else:
        table_block = Div(_table(shown), table_pager(_TABLE_ID))
    return Div(
        status_cards(cards, "/maintenance", status or None, total_override=len(items)),
        filter_bar,
        bulk_toolbar(_TABLE_ID, [
            {"value": "serviced", "label": "Mark serviced", "method": "post",
             "url": "/maintenance/mark-serviced", "target": "#maint-content", "swap": "outerHTML"},
        ]),
        table_block,
        id="maint-content")


def setup_ui_routes(app) -> None:
    """Entry point the module loader calls to register UI pages."""

    async def _fetch(request: Request) -> list[dict]:
        try:
            async with _api(request) as c:
                r = await c.get("/api/maintenance/equipment")
                if r.status_code == 200:
                    return r.json().get("items", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("maintenance: fetch failed: %s", exc)
        return []

    def _page(request: Request):
        q = request.query_params.get("q", "")
        status = request.query_params.get("status", "")
        return q, status

    @app.get("/maintenance")
    async def maintenance_page(request: Request):
        items = await _fetch(request)
        q, status = _page(request)
        add_form = Form(
            Input(name="name", placeholder="Equipment name", required=True,
                  cls="form-input form-input--sm"),
            Input(name="location", placeholder="Location", cls="form-input form-input--sm"),
            Input(name="interval_days", type="number", value="90", cls="form-input form-input--sm",
                  style="max-width:110px"),
            Button("Add", type="submit", cls="btn btn--primary"),
            hx_post="/maintenance/add", hx_target="#maint-content", hx_swap="outerHTML",
            cls="maint-add-form")
        body = Div(
            breadcrumbs([("Operations", None), ("Maintenance", None)]),
            H2("Equipment Maintenance"),
            _content(items, q, status),
            P("Add equipment", cls="section-title"),
            add_form,
            # The shared enhancers: Excel sort/pager, column+date filters, bulk toolbar.
            Script(ENHANCED_TABLE_JS), Script(COLUMN_FILTER_JS), Script(BULK_TOOLBAR_JS),
            cls="container maint-page")
        return base_shell(body, title="Maintenance - Celerp", nav_active="maintenance",
                          request=request)

    async def _refresh(request: Request):
        items = await _fetch(request)
        q, status = _page(request)
        return HTMLResponse(str(_content(items, q, status)))

    @app.post("/maintenance/add")
    async def maintenance_add(request: Request):
        form = await request.form()
        if form.get("name"):
            async with _api(request) as c:
                await c.post("/api/maintenance/equipment", json={
                    "name": form["name"], "location": form.get("location", ""),
                    "interval_days": int(form.get("interval_days") or 90)})
        return await _refresh(request)

    @app.post("/maintenance/mark-serviced")
    async def maintenance_mark(request: Request):
        form = await request.form()
        ids = form.getlist("selected") or form.getlist("ids")
        # bulk_toolbar posts `selected`; per-row button posts `ids` via hx-vals.
        if not ids:
            import json as _json
            raw = form.get("ids")
            if raw:
                try:
                    ids = _json.loads(raw)
                except Exception:
                    ids = [raw]
        if ids:
            async with _api(request) as c:
                await c.post("/api/maintenance/equipment/mark-serviced", json={"ids": list(ids)})
        return await _refresh(request)

    @app.delete("/maintenance/{equipment_id}")
    async def maintenance_delete(request: Request, equipment_id: str):
        async with _api(request) as c:
            await c.delete(f"/api/maintenance/equipment/{equipment_id}")
        return await _refresh(request)

    # ── click-to-edit fragments ──
    _CELL_TYPES = {"name": "text", "location": "text", "interval_days": "number",
                   "last_cost": "money"}

    async def _one(request: Request, equipment_id: str) -> dict | None:
        for i in await _fetch(request):
            if i["id"] == equipment_id:
                return i
        return None

    @app.get("/maintenance/{equipment_id}/field/{field}/edit")
    async def cell_edit(request: Request, equipment_id: str, field: str):
        item = await _one(request, equipment_id)
        if item is None or field not in _CELL_TYPES:
            return HTMLResponse("", status_code=404)
        raw = item.get(field)
        val = "" if raw in (None, "") else str(raw)
        return HTMLResponse(str(_edit_cell(equipment_id, field, val, _CELL_TYPES[field])))

    @app.get("/maintenance/{equipment_id}/field/{field}/display")
    async def cell_display(request: Request, equipment_id: str, field: str):
        item = await _one(request, equipment_id)
        if item is None or field not in _CELL_TYPES:
            return HTMLResponse("", status_code=404)
        return HTMLResponse(str(_display_cell(item, field, _CELL_TYPES[field])))

    @app.patch("/maintenance/{equipment_id}/field/{field}")
    async def cell_save(request: Request, equipment_id: str, field: str):
        form = await request.form()
        value = form.get("value", "")
        async with _api(request) as c:
            r = await c.patch(f"/api/maintenance/equipment/{equipment_id}/field/{field}",
                              json={"value": value})
        # On validation error, re-render the editor so the user can fix it (house
        # rule: validate at the function level, never by hiding the control).
        if r.status_code >= 400:
            return HTMLResponse(str(_edit_cell(equipment_id, field, value, _CELL_TYPES.get(field, "text"))),
                                status_code=200)
        return HTMLResponse(str(_display_cell(r.json(), field, _CELL_TYPES.get(field, "text"))))

    log.info("acme-maintenance: UI routes registered")
