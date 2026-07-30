# SPDX-License-Identifier: MIT
"""Test harness for acme-maintenance.

A module has two halves that talk over HTTP: the API router mounted on Celerp's
FastAPI app, and the UI routes mounted on its FastHTML app. This harness stands
both up in-process against SQLite and wires the UI's outbound client straight
into the API app, so a request to `/maintenance` exercises the real render path,
the real proxy call, and the real database write with nothing mocked in between.

Copying this file into your own module is the intended use. The three things to
change are the imports at the top, `MODULE_TABLE_PREFIX`, and the stub company
payload if your pages read fields this one does not.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fasthtml.common import FastHTML
from sqlalchemy import Uuid, create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from celerp.db import get_session
from celerp.models.base import Base
from celerp.models.company import Company, Location
from celerp.services.auth import (
    get_current_company_id,
    get_current_role,
    get_current_user,
)

# Importing the models module registers this module's tables on the shared Base.
# The tables are then looked up by NAME (see `Env.table`) rather than by importing
# each class, so a test that needs a table this module has not created yet fails
# on its own line with a readable error instead of breaking collection.
import acme_maintenance.models  # noqa: F401
import acme_maintenance.ui_routes as ui_routes
from acme_maintenance.routes import setup_api_routes
from acme_maintenance.ui_routes import setup_ui_routes

MODULE_TABLE_PREFIX = "acme_"
COOKIE_NAME = "celerp_token"


# ── failure injection ─────────────────────────────────────────────────────────

class _FailInjector:
    """Wraps the API app so a test can break one upstream path on demand.

    It sits OUTSIDE FastAPI on purpose: `"raise"` has to escape as an exception
    the UI's httpx client sees, which is what a real unreachable API looks like.
    An exception raised inside FastAPI would come back as a 500 response instead.
    """

    def __init__(self, app, rules: dict) -> None:
        self._app = app
        self._rules = rules

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            for prefix, action in self._rules.items():
                if scope["path"].startswith(prefix):
                    if action == "raise":
                        raise httpx.ConnectError("injected: upstream unreachable")
                    await send({
                        "type": "http.response.start",
                        "status": int(action),
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": json.dumps({"detail": "injected failure"}).encode(),
                    })
                    return
        await self._app(scope, receive, send)


# ── the environment fixture ───────────────────────────────────────────────────

class Env:
    """Everything a test needs to drive the module: client, db, and knobs."""

    def __init__(self, client: TestClient, api_client: TestClient, sync_engine,
                 company_id, other_company_id, locations: list[dict], inject: dict) -> None:
        self.client = client
        self.api = api_client
        self.company_id = company_id
        self.other_company_id = other_company_id
        self.locations = locations
        self.inject = inject
        self._engine = sync_engine

    # -- database access, for asserting on rows the routes wrote --

    def session(self) -> Session:
        return Session(self._engine)

    def table(self, name: str):
        """The SQLAlchemy Table for *name*, by name.

        Raises with the table list when it is missing, which is what a test sees
        before the migration that adds the table has been written.
        """
        try:
            return Base.metadata.tables[name]
        except KeyError:
            known = sorted(t for t in Base.metadata.tables if t.startswith(MODULE_TABLE_PREFIX))
            raise AssertionError(f"no table {name!r} is defined; module tables: {known}") from None

    def rows(self, name: str, **where) -> list[dict]:
        table = self.table(name)
        stmt = select(table)
        for key, value in where.items():
            stmt = stmt.where(table.c[key] == _as_column_value(table, key, value))
        with self.session() as s:
            return [dict(r._mapping) for r in s.execute(stmt)]

    def insert(self, name: str, **values):
        table = self.table(name)
        values.setdefault("id", uuid.uuid4())
        values.setdefault("company_id", self.company_id)
        values = {k: _as_column_value(table, k, v) for k, v in values.items()}
        with self.session() as s:
            s.execute(table.insert().values(**values))
            s.commit()
        return values["id"]

    # -- fixtures for the module's own rows --

    def equipment(self, name: str = "Lathe", *, company_id=None, **kw):
        """Insert one equipment row directly and return its id as a string."""
        values = {
            "id": uuid.uuid4(),
            "company_id": company_id or self.company_id,
            "name": name,
            "location": kw.pop("location", ""),
            "interval_days": kw.pop("interval_days", 90),
            "last_cost": kw.pop("last_cost", 0),
            "notes": kw.pop("notes", ""),
            "created_at": kw.pop("created_at", datetime.now(timezone.utc)),
        }
        values.update(kw)
        table = self.table("acme_equipment")
        values = {k: v for k, v in values.items() if k in table.c}
        with self.session() as s:
            s.execute(table.insert().values(**values))
            s.commit()
        return str(values["id"])

    def service_log(self, equipment_id, *, serviced_at=None, cost=0, note=""):
        """Insert one service-log row directly and return its id as a string."""
        table = self.table("acme_service_log")
        values = {
            "id": uuid.uuid4(),
            "company_id": self.company_id,
            "equipment_id": uuid.UUID(str(equipment_id)),
            "serviced_at": serviced_at or date.today(),
            "cost": cost,
            "note": note,
            "created_at": datetime.now(timezone.utc),
        }
        values = {k: v for k, v in values.items() if k in table.c}
        with self.session() as s:
            s.execute(table.insert().values(**values))
            s.commit()
        return str(values["id"])

    # -- request helpers --

    def get(self, url: str, *, htmx: bool = False, **kw):
        return self.client.get(url, headers=_headers(htmx), **kw)

    def post(self, url: str, *, htmx: bool = True, **kw):
        return self.client.post(url, headers=_headers(htmx), **kw)

    def patch(self, url: str, *, htmx: bool = True, **kw):
        return self.client.patch(url, headers=_headers(htmx), **kw)

    def delete(self, url: str, *, htmx: bool = True, **kw):
        return self.client.delete(url, headers=_headers(htmx), **kw)


def _headers(htmx: bool) -> dict:
    return {"HX-Request": "true"} if htmx else {}


def _as_column_value(table, key: str, value):
    """Let a test pass an id as the string the routes speak.

    Ids cross the HTTP boundary as strings, so tests hold them as strings. A UUID
    column will not compare against one, so coerce here rather than making every
    test wrap its ids.
    """
    if isinstance(value, str) and isinstance(table.c[key].type, Uuid):
        try:
            return uuid.UUID(value)
        except ValueError:
            return value
    return value


def _fake_token(company_id, role: str) -> str:
    """An unsigned JWT-shaped token.

    `ui.config.get_role` base64-decodes the payload segment without verifying the
    signature, so this is enough for the UI layer to read a role. The API layer's
    own auth is overridden below, so no real signing key is involved.
    """
    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    payload = {"sub": str(uuid.uuid4()), "company_id": str(company_id), "role": role}
    return f"{seg({'alg': 'none'})}.{seg(payload)}.x"


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    """Factory for an Env. Call with role="viewer" to test a read-only caller."""

    def _make(role: str = "operator", locations: list[dict] | None = None) -> Env:
        company_id = uuid.uuid4()
        other_company_id = uuid.uuid4()
        locs = locations if locations is not None else [
            {"id": str(uuid.uuid4()), "name": "Main plant"},
            {"id": str(uuid.uuid4()), "name": "Warehouse"},
        ]
        inject: dict = {}

        # Attachments land under a temp data_dir. LocalBackend._root reads
        # settings.data_dir on every call, so patching the setting is enough.
        import celerp.config
        monkeypatch.setattr(celerp.config.settings, "data_dir", tmp_path, raising=False)

        # One database per Env: a test that builds a second environment (a viewer,
        # or a company with no locations) is describing a separate company, and
        # sharing one file would let its rows show up in the first one's queries.
        db_path = tmp_path / f"module-{company_id.hex[:8]}.db"
        sync_engine = create_engine(f"sqlite:///{db_path}")
        # Only this module's tables plus the two core tables it reads: companies,
        # and locations for the location dropdown. Creating the whole core schema
        # would make the module's tests depend on every core migration.
        core_tables = {"companies", "locations"}
        tables = [t for t in Base.metadata.sorted_tables
                  if t.name.startswith(MODULE_TABLE_PREFIX) or t.name in core_tables]
        Base.metadata.create_all(sync_engine, tables=tables)
        with Session(sync_engine) as s:
            for cid, name in ((company_id, "Acme Co"), (other_company_id, "Other Co")):
                s.add(Company(id=cid, name=name, slug=f"c-{cid.hex[:8]}", settings={}))
            for loc in locs:
                s.add(Location(id=uuid.UUID(str(loc["id"])), company_id=company_id,
                               name=loc["name"], type="warehouse"))
            s.commit()

        async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async_session = async_sessionmaker(async_engine, expire_on_commit=False)

        async def _session_override():
            async with async_session() as session:
                yield session

        # The API app: the module's real router, plus the two core endpoints the
        # module's UI reads through its own client.
        api = FastAPI()
        setup_api_routes(api)

        @api.get("/companies/me")
        async def _company_me():
            return {"id": str(company_id), "name": "Acme Co", "slug": "acme",
                    "currency": "THB", "settings": {}}

        @api.get("/companies/me/locations")
        async def _company_locations():
            return {"items": locs, "total": len(locs)}

        api.dependency_overrides[get_session] = _session_override
        api.dependency_overrides[get_current_company_id] = lambda: company_id
        api.dependency_overrides[get_current_role] = lambda: role
        api.dependency_overrides[get_current_user] = lambda: object()

        wrapped_api = _FailInjector(api, inject)
        transport = httpx.ASGITransport(app=wrapped_api)

        def _api_override(request):
            token = request.cookies.get(COOKIE_NAME)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return httpx.AsyncClient(transport=transport, base_url="http://api",
                                     headers=headers, timeout=5)

        monkeypatch.setattr(ui_routes, "_api", _api_override)

        # base_shell fetches company settings over the network when it is not
        # given any. The module passes its own, so this only guards the chrome
        # against a stray outbound call making the suite non-hermetic.
        import ui.api_client

        async def _get_company(_token):
            return {"id": str(company_id), "name": "Acme Co", "settings": {}}

        monkeypatch.setattr(ui.api_client, "get_company", _get_company)

        # An explicit secret keeps FastHTML from writing a `.sesskey` file into
        # whatever directory the tests were started from.
        ui_app = FastHTML(secret_key="acme-maintenance-tests")
        setup_ui_routes(ui_app)
        client = TestClient(ui_app)
        client.cookies.set(COOKIE_NAME, _fake_token(company_id, role))

        return Env(client, TestClient(wrapped_api, base_url="http://api"), sync_engine,
                   company_id, other_company_id, locs, inject)

    return _make


@pytest.fixture
def env(make_env):
    """The default environment: one operator, two locations, empty database."""
    return make_env()
