# SPDX-License-Identifier: MIT
"""The one table this module owns: a piece of company equipment.

Every module table inherits from celerp's shared Base (so it lives in the same
database and Alembic sees it) and is scoped by company_id — Celerp is
multi-company, and rows must never leak across companies. That scoping is
enforced in routes.py, where every query filters on the current company.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from celerp.models.base import Base


class Equipment(Base):
    """A serviceable piece of company equipment."""

    __tablename__ = "acme_equipment"          # prefix with your module name to avoid clashes

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    company_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    serviced_at: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    interval_days: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="90")
    notes: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def due_date(self) -> date | None:
        """When the next service is due, or None if never serviced."""
        if self.serviced_at is None:
            return None
        from datetime import timedelta
        return self.serviced_at + timedelta(days=self.interval_days)

    def is_due(self, today: date | None = None) -> bool:
        """True when service is overdue (or was never recorded)."""
        due = self.due_date()
        if due is None:
            return True
        return due <= (today or datetime.now(timezone.utc).date())
