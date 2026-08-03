# SPDX-License-Identifier: MIT
"""The forward migration that moves the last-serviced date into the service log.

maint_002 is the interesting one: it does not just add tables, it carries existing
data across a schema change. A migration that loses that date loses the only record
of when anything was serviced, so it gets tested like any other code.

The migrations run against SQLite here. They are written with the inspector guards
that make a re-run a no-op, which is what these tests exercise.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from acme_maintenance.migrations import maint_001_create_equipment as maint_001


def _maint_002():
    """Import maint_002 at call time, so a missing migration fails per test."""
    import importlib
    return importlib.import_module(
        "acme_maintenance.migrations.maint_002_service_log_and_files")


def _run(connection, module) -> None:
    """Execute a migration module's upgrade() against an open connection."""
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


@pytest.fixture
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migrate.db'}")
    with engine.begin() as connection:
        _run(connection, maint_001)
        yield connection


def _seed(connection, *rows) -> list[uuid.UUID]:
    ids = []
    for name, serviced_at in rows:
        eq_id = uuid.uuid4()
        connection.execute(sa.text(
            "INSERT INTO acme_equipment (id, company_id, name, location, serviced_at,"
            " interval_days, last_cost, notes)"
            " VALUES (:id, :cid, :name, '', :serviced, 90, 0, '')"
        ), {"id": str(eq_id), "cid": str(uuid.uuid4()), "name": name,
            "serviced": serviced_at.isoformat() if serviced_at else None})
        ids.append(eq_id)
    return ids


def _log_rows(connection) -> list[dict]:
    return [dict(r._mapping) for r in connection.execute(
        sa.text("SELECT equipment_id, serviced_at, note FROM acme_service_log"))]


def test_maint_002_backfills_log(conn):
    """A23: the date every existing row already held becomes its first log entry."""
    lathe, press, unused = _seed(conn, ("Lathe", date(2026, 5, 1)),
                                 ("Press", date(2026, 6, 15)), ("Spare", None))
    _run(conn, _maint_002())

    by_equipment = {str(r["equipment_id"]): r for r in _log_rows(conn)}
    assert str(unused) not in by_equipment, "an unserviced item was given a service record"
    assert set(by_equipment) == {str(lathe), str(press)}
    assert str(by_equipment[str(lathe)]["serviced_at"]).startswith("2026-05-01")
    assert str(by_equipment[str(press)]["serviced_at"]).startswith("2026-06-15")

    columns = {c["name"] for c in sa.inspect(conn).get_columns("acme_equipment")}
    assert "serviced_at" not in columns, "the old column survived the migration"
    assert {"instructions", "archived_at"} <= columns
    assert "acme_equipment_file" in sa.inspect(conn).get_table_names()


def test_maint_002_backfill_is_idempotent(conn):
    """A43: a re-run adds no tables it already made and no logs it already wrote."""
    lathe, = _seed(conn, ("Lathe", date(2026, 5, 1)))
    _run(conn, _maint_002())
    first = _log_rows(conn)
    _run(conn, _maint_002())
    assert _log_rows(conn) == first, "the second run duplicated the backfilled entry"
    assert len(first) == 1


def test_maint_001_create_is_idempotent(conn):
    """A re-run of the first migration is a no-op, not a duplicate-table error.

    Celerp runs a module's migrations on every startup, so the first one has to
    survive being applied to a database that already has its table.
    """
    before = set(sa.inspect(conn).get_table_names())
    assert "acme_equipment" in before
    _run(conn, maint_001)
    after = set(sa.inspect(conn).get_table_names())
    assert after == before, "the second run of maint_001 changed the schema"
