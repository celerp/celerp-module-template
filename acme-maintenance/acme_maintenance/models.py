# SPDX-License-Identifier: MIT
"""The three tables this module owns: equipment, its service log, and its files.

Every module table inherits from celerp's shared Base (so it lives in the same
database and Alembic sees it) and is scoped by company_id - Celerp is
multi-company, and rows must never leak across companies. That scoping is
enforced in routes.py, where every query filters on the current company.

The fields are chosen to exercise the standard list patterns the UI demonstrates:
text columns (name, location), a date (the service log's serviced_at), a number
(interval_days), and a money amount (last_cost) so the table shows currency
formatting.

One design point worth copying: "last serviced" is not a column on Equipment. It
is the newest service-log row, so there is exactly one record of a service and
taking one back cannot leave two fields disagreeing.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from celerp.models.base import Base


class Equipment(Base):
    """A serviceable piece of company equipment."""

    __tablename__ = "acme_equipment"          # prefix with your module name to avoid clashes

    # Match the house convention: real UUID columns, company_id a FK to companies.
    # (A String(36) column fails against the UUID that get_current_company_id
    # provides - "operator does not exist: character varying = uuid".)
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("companies.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    location: Mapped[str] = mapped_column(sa.String(200), nullable=False, server_default="")
    interval_days: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="90")
    last_cost: Mapped[float] = mapped_column(sa.Numeric(14, 2), nullable=False, server_default="0")
    notes: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default="")
    # The standing procedure for servicing this item, shown in its own block on the
    # detail page. `notes` stays what it is: a free-form remark about the item.
    instructions: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default="")
    # Archived rows leave the list and come back with restore. Nothing is deleted.
    archived_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    logs: Mapped[list["ServiceLog"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True)
    files: Mapped[list["EquipmentFile"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True)

    # id/company_id are UUID objects; str() them when serializing to JSON.
    # The three methods below take the last-serviced date rather than reading a
    # column, because that date lives in the service log (see ServiceLog).

    def due_date(self, last_serviced: date | None) -> date | None:
        """When the next service is due, or None if it has never been serviced."""
        if last_serviced is None:
            return None
        return last_serviced + timedelta(days=self.interval_days)

    def is_due(self, last_serviced: date | None, today: date | None = None) -> bool:
        """True when service is overdue (or was never recorded)."""
        due = self.due_date(last_serviced)
        if due is None:
            return True
        return due <= (today or datetime.now(timezone.utc).date())

    def status(self, last_serviced: date | None, today: date | None = None) -> str:
        """'due' or 'ok' - drives the status filter cards and the status column."""
        return "due" if self.is_due(last_serviced, today) else "ok"


class ServiceLog(Base):
    """One recorded service of one piece of equipment.

    The newest row for an equipment IS its last-serviced date. Marking an item
    serviced adds a row; undoing removes the newest one, and the date falls back to
    the row before it with no second field to keep in step.
    """

    __tablename__ = "acme_service_log"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("companies.id"), nullable=False, index=True)
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("acme_equipment.id", ondelete="CASCADE"), nullable=False)
    serviced_at: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    cost: Mapped[float | None] = mapped_column(sa.Numeric(14, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc))

    equipment: Mapped[Equipment] = relationship(back_populates="logs")

    __table_args__ = (
        sa.Index("ix_acme_service_log_lookup", "company_id", "equipment_id",
                 sa.text("serviced_at DESC")),
    )


class EquipmentFile(Base):
    """A file attached to a piece of equipment.

    The bytes live in celerp's attachment storage; this table holds the metadata and
    the storage key. The key never reaches the browser: downloads stream through the
    module's own company-scoped route.
    """

    __tablename__ = "acme_equipment_file"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("companies.id"), nullable=False, index=True)
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("acme_equipment.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    content_type: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    size_bytes: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc))

    equipment: Mapped[Equipment] = relationship(back_populates="files")

    __table_args__ = (
        sa.Index("ix_acme_equipment_file_lookup", "company_id", "equipment_id"),
    )
