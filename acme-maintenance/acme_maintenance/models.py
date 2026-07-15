# SPDX-License-Identifier: MIT
"""The one table this module owns: a piece of company equipment.

Every module table inherits from celerp's shared Base (so it lives in the same
database and Alembic sees it) and is scoped by company_id - Celerp is
multi-company, and rows must never leak across companies. That scoping is
enforced in routes.py, where every query filters on the current company.

The fields are chosen to exercise the standard list patterns the UI demonstrates:
a text column (name, location), a date (serviced_at), a number (interval_days),
and a money amount (last_cost) so the table shows currency formatting.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

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
    serviced_at: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    interval_days: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="90")
    last_cost: Mapped[float] = mapped_column(sa.Numeric(14, 2), nullable=False, server_default="0")
    notes: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # id/company_id are UUID objects; str() them when serializing to JSON.

    def due_date(self) -> date | None:
        """When the next service is due, or None if never serviced."""
        if self.serviced_at is None:
            return None
        return self.serviced_at + timedelta(days=self.interval_days)

    def is_due(self, today: date | None = None) -> bool:
        """True when service is overdue (or was never recorded)."""
        due = self.due_date()
        if due is None:
            return True
        return due <= (today or datetime.now(timezone.utc).date())

    def status(self) -> str:
        """'due' or 'ok' - drives the status filter cards and the status column."""
        return "due" if self.is_due() else "ok"
