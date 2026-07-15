# SPDX-License-Identifier: MIT
"""UI route for acme-maintenance: the /maintenance page the sidebar links to.

The loader calls setup_ui_routes(app) at startup. The page renders inside
Celerp's shell (base_shell) so it gets the sidebar, header, and styling for
free, then calls this module's own API for its data — the same split the
built-in modules use (UI process renders, API process owns the DB).
"""
from __future__ import annotations

import logging

import httpx
from fasthtml.common import Button, Div, Form, H2, Input, Li, P, Span, Ul
from starlette.requests import Request
from starlette.responses import RedirectResponse

log = logging.getLogger(__name__)

# NOTE: Request and the fasthtml element helpers are imported at MODULE level,
# not inside setup_ui_routes. The route handlers below annotate `request: Request`,
# and the framework resolves that annotation at request time against this module's
# globals — a function-local import would raise NameError there (found the hard
# way by booting the app; the loader logs "ui_routes failed").


def setup_ui_routes(app) -> None:
    """Entry point the module loader calls to register UI pages."""
    from ui.components.shell import base_shell
    from ui.config import API_BASE, COOKIE_NAME

    async def _fetch(request: Request) -> dict:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return {"items": [], "due_count": 0}
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{API_BASE}/api/maintenance/equipment",
                                headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("maintenance: could not fetch equipment: %s", exc)
        return {"items": [], "due_count": 0}

    def _row(item: dict):
        due = " · due for service" if item["is_due"] else f" · next: {item['due_date'] or '—'}"
        return Li(Span(item["name"], style="font-weight:600"),
                  Span(due, style="color:#888" if not item["is_due"] else "color:#c93838"),
                  style="padding:6px 0;border-bottom:1px solid #eee")

    @app.get("/maintenance")
    async def maintenance_page(request: Request):
        data = await _fetch(request)
        items = data["items"]
        banner = (P(f"{data['due_count']} item(s) due for service",
                    style="color:#c93838;font-weight:600")
                  if data["due_count"] else P("Everything is up to date.", style="color:#888"))
        add_form = Form(
            Input(name="name", placeholder="Equipment name", required=True, cls="form-input"),
            Input(name="interval_days", type="number", value="90", cls="form-input",
                  style="max-width:120px"),
            Button("Add", type="submit", cls="btn btn-primary"),
            method="post", action="/maintenance/add",
            style="display:flex;gap:8px;margin:12px 0")
        body = Div(
            H2("Equipment Maintenance"),
            banner,
            add_form,
            Ul(*[_row(i) for i in items], style="list-style:none;padding:0")
            if items else P("No equipment yet — add your first above."),
            cls="container", style="max-width:640px")
        return base_shell(body, title="Maintenance - Celerp", nav_active="maintenance",
                          request=request)

    @app.post("/maintenance/add")
    async def maintenance_add(request: Request):
        form = await request.form()
        token = request.cookies.get(COOKIE_NAME)
        if token and form.get("name"):
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.post(f"{API_BASE}/api/maintenance/equipment",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"name": form["name"],
                                       "interval_days": int(form.get("interval_days") or 90)})
            except Exception as exc:  # noqa: BLE001
                log.warning("maintenance: add failed: %s", exc)
        return RedirectResponse("/maintenance", status_code=303)

    log.info("acme-maintenance: UI routes registered")
