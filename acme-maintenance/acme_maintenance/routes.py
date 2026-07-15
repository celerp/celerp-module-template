# SPDX-License-Identifier: MIT
"""API routes for acme-maintenance, mounted under /api/maintenance.

The loader calls setup_api_routes(app) at startup. Everything here uses only
celerp's PUBLIC helpers:
  - get_session          an async DB session
  - get_current_user     auth dependency (rejects anonymous callers)
  - get_current_company_id  the caller's active company, for row scoping

Do NOT import celerp.session_gate, celerp.ai.*, celerp.gateway, or
celerp.connectors — the loader rejects modules that do (those are revenue-gated
internals). Run lint.py to catch that before you ever start the app.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celerp.db import get_session
from celerp.services.auth import get_current_company_id, get_current_user

from acme_maintenance.models import Equipment

log = logging.getLogger(__name__)

# Every route requires a logged-in user; get_current_company_id scopes the rows.
router = APIRouter(prefix="/api/maintenance", tags=["maintenance"],
                   dependencies=[Depends(get_current_user)])


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    interval_days: int = Field(default=90, ge=1, le=3650)
    notes: str = ""


def _serialize(e: Equipment) -> dict:
    due = e.due_date()
    return {"id": e.id, "name": e.name,
            "serviced_at": e.serviced_at.isoformat() if e.serviced_at else None,
            "interval_days": e.interval_days, "notes": e.notes,
            "due_date": due.isoformat() if due else None, "is_due": e.is_due()}


@router.get("/equipment")
async def list_equipment(company_id: str = Depends(get_current_company_id),
                         session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(
        select(Equipment).where(Equipment.company_id == company_id)
        .order_by(Equipment.name))).scalars().all()
    items = [_serialize(e) for e in rows]
    return {"items": items, "due_count": sum(1 for i in items if i["is_due"])}


@router.post("/equipment")
async def add_equipment(body: EquipmentIn,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    e = Equipment(company_id=company_id, name=body.name,
                  interval_days=body.interval_days, notes=body.notes)
    session.add(e)
    await session.commit()
    return _serialize(e)


@router.post("/equipment/{equipment_id}/serviced")
async def mark_serviced(equipment_id: str,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    from datetime import datetime, timezone
    e = (await session.execute(
        select(Equipment).where(Equipment.id == equipment_id,
                                Equipment.company_id == company_id))).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail="equipment not found")
    e.serviced_at = datetime.now(timezone.utc).date()
    await session.commit()
    return _serialize(e)


def setup_api_routes(app) -> None:
    """Entry point the module loader calls to mount these routes."""
    app.include_router(router)
    log.info("acme-maintenance: API routes registered")
