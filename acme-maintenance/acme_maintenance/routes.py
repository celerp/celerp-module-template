# SPDX-License-Identifier: MIT
"""API routes for acme-maintenance, mounted under /api/maintenance.

The loader calls setup_api_routes(app) at startup. This layer owns the data:
list, create, edit-one-field, mark-serviced (single or bulk), and delete. The
UI layer (ui_routes.py) renders HTML and calls these.

Everything here uses only celerp's PUBLIC helpers:
  - get_session          an async DB session
  - get_current_user     auth dependency (rejects anonymous callers)
  - get_current_company_id  the caller's active company, for row scoping

Do NOT import celerp.session_gate, celerp.ai.*, celerp.gateway, or
celerp.connectors - the loader rejects modules that do.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from celerp.db import get_session
from celerp.services.auth import get_current_company_id, get_current_user

from acme_maintenance.models import Equipment

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"],
                   dependencies=[Depends(get_current_user)])

# Fields a user may edit inline (click-to-edit). Everything else is derived.
EDITABLE_FIELDS = {"name", "location", "interval_days", "last_cost", "notes"}


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    interval_days: int = Field(default=90, ge=1, le=3650)


def serialize(e: Equipment) -> dict:
    due = e.due_date()
    return {"id": str(e.id), "name": e.name, "location": e.location,
            "serviced_at": e.serviced_at.isoformat() if e.serviced_at else None,
            "interval_days": e.interval_days, "last_cost": float(e.last_cost or 0),
            "notes": e.notes, "due_date": due.isoformat() if due else None,
            "status": e.status()}


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="equipment not found")


async def _get(session: AsyncSession, company_id, equipment_id: str) -> Equipment:
    e = (await session.execute(
        select(Equipment).where(Equipment.id == _as_uuid(equipment_id),
                                Equipment.company_id == company_id))).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail="equipment not found")
    return e


@router.get("/equipment")
async def list_equipment(company_id: str = Depends(get_current_company_id),
                         session: AsyncSession = Depends(get_session)) -> dict:
    # Newest first (global UX rule: newer rows matter more). Client-side sort/
    # filter/paginate take over from there, so no server-side query params here.
    rows = (await session.execute(
        select(Equipment).where(Equipment.company_id == company_id)
        .order_by(Equipment.created_at.desc()))).scalars().all()
    items = [serialize(e) for e in rows]
    return {"items": items, "due_count": sum(1 for i in items if i["status"] == "due")}


@router.post("/equipment")
async def add_equipment(body: EquipmentIn,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    e = Equipment(company_id=company_id, name=body.name, location=body.location,
                  interval_days=body.interval_days)
    session.add(e)
    await session.commit()
    return serialize(e)


@router.patch("/equipment/{equipment_id}/field/{field}")
async def edit_field(equipment_id: str, field: str, value: str = Body("", embed=True),
                     company_id: str = Depends(get_current_company_id),
                     session: AsyncSession = Depends(get_session)) -> dict:
    # Function-level validation (global UX rule: validate here, never by hiding
    # the control). An invalid field/value returns a clear error the UI shows.
    if field not in EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"{field} is not editable")
    e = await _get(session, company_id, equipment_id)
    if field == "interval_days":
        try:
            n = int(value)
        except ValueError:
            raise HTTPException(status_code=422, detail="interval must be a whole number of days")
        if not (1 <= n <= 3650):
            raise HTTPException(status_code=422, detail="interval must be 1-3650 days")
        e.interval_days = n
    elif field == "last_cost":
        try:
            e.last_cost = Decimal(value or "0")
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="cost must be a number")
    else:  # name, location, notes
        if field == "name" and not value.strip():
            raise HTTPException(status_code=422, detail="name cannot be empty")
        setattr(e, field, value)
    await session.commit()
    return serialize(e)


@router.post("/equipment/mark-serviced")
async def mark_serviced(ids: list[str] = Body(..., embed=True),
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Mark one or many items serviced today. Powers both the per-row button and
    the bulk toolbar (the same endpoint, one or many ids)."""
    today = datetime.now(timezone.utc).date()
    uuids = [_as_uuid(i) for i in ids]
    rows = (await session.execute(
        select(Equipment).where(Equipment.company_id == company_id,
                                Equipment.id.in_(uuids)))).scalars().all()
    for e in rows:
        e.serviced_at = today
    await session.commit()
    return {"updated": len(rows)}


@router.delete("/equipment/{equipment_id}")
async def delete_equipment(equipment_id: str,
                           company_id: str = Depends(get_current_company_id),
                           session: AsyncSession = Depends(get_session)) -> dict:
    # Reversibility (global UX rule a): deletion is user-initiated and confirmed
    # in the UI. A production module would keep a soft-delete/undo; kept hard
    # here to stay minimal, which is the kind of deviation the rules say to note.
    await session.execute(sa_delete(Equipment).where(
        Equipment.id == _as_uuid(equipment_id), Equipment.company_id == company_id))
    await session.commit()
    return {"deleted": equipment_id}


def setup_api_routes(app) -> None:
    """Entry point the module loader calls to mount these routes."""
    app.include_router(router)
    log.info("acme-maintenance: API routes registered")
