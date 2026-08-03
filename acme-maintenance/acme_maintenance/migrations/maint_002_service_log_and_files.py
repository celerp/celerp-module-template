# SPDX-License-Identifier: MIT
"""Service log, files, instructions and archiving

Moves "last serviced" out of a column on acme_equipment and into acme_service_log,
so one service is one row and undoing one cannot leave two fields disagreeing.

Every step is guarded by an inspector check, including the backfill: a re-run after
the insert succeeded but a later step failed must not insert a second log row per
equipment and fabricate duplicate service history.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    columns = _columns("acme_equipment")
    if "instructions" not in columns:
        op.add_column("acme_equipment",
                      sa.Column("instructions", sa.Text(), nullable=False,
                                server_default=""))
    if "archived_at" not in columns:
        op.add_column("acme_equipment",
                      sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    tables = _tables()
    if "acme_service_log" not in tables:
        op.create_table(
            "acme_service_log",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("company_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("equipment_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey("acme_equipment.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("serviced_at", sa.Date(), nullable=False),
            sa.Column("cost", sa.Numeric(14, 2), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
        op.create_index("ix_acme_service_log_lookup", "acme_service_log",
                        ["company_id", "equipment_id", sa.text("serviced_at DESC")])

    if "acme_equipment_file" not in tables:
        op.create_table(
            "acme_equipment_file",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("company_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("equipment_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey("acme_equipment.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("content_type", sa.Text(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("storage_key", sa.Text(), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
        op.create_index("ix_acme_equipment_file_lookup", "acme_equipment_file",
                        ["company_id", "equipment_id"])

    # The date every existing row already holds becomes its first log entry. Skip any
    # equipment that already has one, so a re-run adds nothing.
    if "serviced_at" in _columns("acme_equipment"):
        bind = op.get_bind()
        pending = bind.execute(sa.text(
            "SELECT e.id, e.company_id, e.serviced_at FROM acme_equipment e"
            " WHERE e.serviced_at IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM acme_service_log l"
            "                 WHERE l.equipment_id = e.id)"
        )).fetchall()
        for equipment_id, company_id, serviced_at in pending:
            bind.execute(sa.text(
                "INSERT INTO acme_service_log"
                " (id, company_id, equipment_id, serviced_at, note, created_at)"
                " VALUES (:id, :company_id, :equipment_id, :serviced_at,"
                "         'Recorded before the service log existed', CURRENT_TIMESTAMP)"
            ), {"id": str(uuid.uuid4()), "company_id": str(company_id),
                "equipment_id": str(equipment_id), "serviced_at": serviced_at})
        op.drop_column("acme_equipment", "serviced_at")
