# SPDX-License-Identifier: MIT
"""Create acme_equipment table

The module's migrations live on their OWN Alembic branch (branch_labels below,
matching the module name) with down_revision = None, so a module's schema is
independent of core's migration chain and of other modules. The loader points
Alembic at this directory; the table is created on the next launch.

Revision ID: maint_001
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "maint_001"
down_revision = None
branch_labels = ("acme-maintenance",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acme_equipment",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("serviced_at", sa.Date(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("acme_equipment")
