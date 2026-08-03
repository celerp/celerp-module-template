# SPDX-License-Identifier: MIT
"""Create the acme_equipment table.

Celerp runs a module's migrations at startup, in filename order, and keeps no
version state, so every migration has to be safe to apply again: it checks for
what it creates before creating it. There is no revision graph and no downgrade
here - recovery from a bad upgrade is a restore from backup, which is why the
steps only ever go forward. maint_001 makes the equipment table; the next file
evolves it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    if "acme_equipment" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "acme_equipment",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("company_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location", sa.String(200), nullable=False, server_default=""),
        sa.Column("serviced_at", sa.Date(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("last_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
