# SPDX-License-Identifier: MIT
"""API routes for acme-maintenance, mounted under /api/maintenance.

The loader calls setup_api_routes(app) at startup. This layer owns the data: the
list, one record with its service log and files, the click-to-edit save, mark
serviced and its undo, archive and restore, and the four file operations. The UI
layer (ui_routes.py) renders HTML and calls these over HTTP.

Two things every module should copy from this file:

  - Authorization lives HERE, not in the UI layer. The router requires
    `view_inventory` for reads and every write route adds `edit_inventory`, so a
    hand-made request cannot skip a check that a hidden button implies. Permission
    keys are a closed registry, so a module reuses core keys rather than inventing
    one.
  - Every query is scoped to the caller's company. A row from another company is
    not a 403, it is a 404: the caller has no way to learn the row exists.

Everything here uses celerp's PUBLIC helpers: get_session, get_current_user,
get_current_company_id, require_permission, the attachments service, and the
Location model for the company's own location list. Do NOT import
celerp.session_gate, celerp.ai.*, celerp.gateway, or celerp.connectors - the
loader rejects modules that do.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse, RedirectResponse

from celerp.db import get_session
from celerp.models.company import Location
from celerp.services import attachments
from celerp.services.auth import get_current_company_id, get_current_user
from celerp.services.permissions import require_permission

from acme_maintenance.models import Equipment, EquipmentFile, ServiceLog

log = logging.getLogger(__name__)

# Reads need view_inventory; each write route below adds edit_inventory on top.
router = APIRouter(prefix="/api/maintenance", tags=["maintenance"],
                   dependencies=[Depends(get_current_user),
                                 require_permission("view_inventory")])

# One Depends object, used by every write route: the same gate, stated per route.
CAN_EDIT = require_permission("edit_inventory")

# Fields a user may edit inline (click-to-edit). Everything else is derived.
EDITABLE_FIELDS = {"name", "location", "interval_days", "last_cost", "notes",
                   "instructions", "serviced_at"}

INSTRUCTIONS_LIMIT = 4000
FILENAME_LIMIT = 255


class EquipmentIn(BaseModel):
    """Create payload. Every field has a default, so the UI's "Add equipment"
    button can post an empty body and land the user on a real record to fill in."""

    name: str = Field(default="New equipment", min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    interval_days: int = Field(default=90, ge=1, le=3650)


# ── serialization ─────────────────────────────────────────────────────────────

def serialize(e: Equipment, last_serviced: date | None) -> dict:
    """One equipment row as JSON. `last_serviced` comes from the service log,
    which is the single source of that date (there is no column to read)."""
    due = e.due_date(last_serviced)
    return {"id": str(e.id), "name": e.name, "location": e.location,
            "serviced_at": last_serviced.isoformat() if last_serviced else None,
            "interval_days": e.interval_days, "last_cost": float(e.last_cost or 0),
            "notes": e.notes, "instructions": e.instructions,
            "due_date": due.isoformat() if due else None,
            "status": e.status(last_serviced), "archived": e.archived_at is not None}


def serialize_log(row: ServiceLog) -> dict:
    return {"id": str(row.id), "serviced_at": row.serviced_at.isoformat(),
            "cost": float(row.cost) if row.cost is not None else None,
            "note": row.note or ""}


def serialize_file(row: EquipmentFile) -> dict:
    """Metadata only. `storage_key` is never serialized: the bytes are served by
    this module's own download route, so the raw storage path stays server side."""
    return {"id": str(row.id), "filename": row.filename, "size": row.size_bytes,
            "mime": row.content_type or "application/octet-stream",
            "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else "",
            "document_tag": "", "description": ""}


# ── lookups ───────────────────────────────────────────────────────────────────

def _as_uuid(value: str, detail: str) -> uuid.UUID:
    """A malformed id is a 404 with *detail*, never a database error."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=detail) from None


async def _get(session: AsyncSession, company_id, equipment_id: str) -> Equipment:
    e = (await session.execute(
        select(Equipment).where(Equipment.id == _as_uuid(equipment_id, "Equipment not found"),
                                Equipment.company_id == company_id))).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return e


async def _last_serviced(session: AsyncSession, company_id,
                         equipment_ids: list[uuid.UUID]) -> dict[uuid.UUID, date]:
    """The newest service date per equipment, in one query rather than per row."""
    if not equipment_ids:
        return {}
    rows = await session.execute(
        select(ServiceLog.equipment_id, func.max(ServiceLog.serviced_at))
        .where(ServiceLog.company_id == company_id,
               ServiceLog.equipment_id.in_(equipment_ids))
        .group_by(ServiceLog.equipment_id))
    return {equipment_id: serviced_at for equipment_id, serviced_at in rows}


async def _logs(session: AsyncSession, company_id, equipment_id) -> list[ServiceLog]:
    """Service history, newest first (house rule: newer rows matter more)."""
    return list((await session.execute(
        select(ServiceLog).where(ServiceLog.company_id == company_id,
                                 ServiceLog.equipment_id == equipment_id)
        .order_by(ServiceLog.serviced_at.desc(), ServiceLog.created_at.desc())
    )).scalars().all())


async def _files(session: AsyncSession, company_id, equipment_id) -> list[EquipmentFile]:
    return list((await session.execute(
        select(EquipmentFile).where(EquipmentFile.company_id == company_id,
                                    EquipmentFile.equipment_id == equipment_id)
        .order_by(EquipmentFile.uploaded_at.desc()))).scalars().all())


async def _location_names(session: AsyncSession, company_id) -> list[str]:
    """The company's own locations, read through core's public model.

    The first-party inventory module reads the same model the same way
    (default_modules/celerp-inventory/celerp_inventory/routes.py). A module may
    read core's public models with the shared session; what it must never touch
    is another MODULE's tables.
    """
    return list((await session.execute(
        select(Location.name).where(Location.company_id == company_id)
        .order_by(Location.name))).scalars().all())


# ── list, create, read one ────────────────────────────────────────────────────

@router.get("/equipment")
async def list_equipment(show: str = "",
                         company_id: str = Depends(get_current_company_id),
                         session: AsyncSession = Depends(get_session)) -> dict:
    """Active equipment, newest first. `?show=archived` lists the archived rows
    instead, which is what makes archiving reversible from the UI."""
    stmt = select(Equipment).where(Equipment.company_id == company_id)
    stmt = (stmt.where(Equipment.archived_at.is_not(None)) if show == "archived"
            else stmt.where(Equipment.archived_at.is_(None)))
    rows = (await session.execute(stmt.order_by(Equipment.created_at.desc()))).scalars().all()
    last = await _last_serviced(session, company_id, [e.id for e in rows])
    items = [serialize(e, last.get(e.id)) for e in rows]
    return {"items": items, "due_count": sum(1 for i in items if i["status"] == "due")}


@router.post("/equipment")
async def add_equipment(body: EquipmentIn,
                        _: None = CAN_EDIT,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    if body.location:
        await _validate_location(session, company_id, body.location)
    e = Equipment(company_id=company_id, name=body.name, location=body.location,
                  interval_days=body.interval_days)
    session.add(e)
    await session.commit()
    return serialize(e, None)


@router.get("/equipment/{equipment_id}")
async def get_equipment(equipment_id: str,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """One record with everything its detail page draws: the item, its service
    history and its files, in one round trip."""
    e = await _get(session, company_id, equipment_id)
    logs = await _logs(session, company_id, e.id)
    files = await _files(session, company_id, e.id)
    return {"item": serialize(e, logs[0].serviced_at if logs else None),
            "logs": [serialize_log(row) for row in logs],
            "files": [serialize_file(row) for row in files]}


# ── click-to-edit save ────────────────────────────────────────────────────────

async def _validate_location(session: AsyncSession, company_id, value: str) -> None:
    """A location must be one of the company's, when the company has any.

    A company with none configured has nothing to check against, so the field
    stays free text rather than rejecting everything the user can type.
    """
    names = await _location_names(session, company_id)
    if names and value not in names:
        raise HTTPException(
            status_code=422,
            detail=f"Location must be one of: {', '.join(names)}")


def _parse_serviced_at(value: str) -> date:
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=422,
                            detail="Enter the date as YYYY-MM-DD") from None
    if parsed > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=422,
                            detail="The last serviced date cannot be in the future")
    return parsed


@router.patch("/equipment/{equipment_id}/field/{field}")
async def edit_field(equipment_id: str, field: str, value: str = Body("", embed=True),
                     _: None = CAN_EDIT,
                     company_id: str = Depends(get_current_company_id),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """Save one field. Validation happens here, at the function level, and the
    message it returns is what the user reads: the UI never hides a control to
    prevent an error it could explain instead."""
    if field not in EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"{field} is not editable")
    e = await _get(session, company_id, equipment_id)
    logs = await _logs(session, company_id, e.id)
    if field == "interval_days":
        try:
            n = int(value)
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="Interval must be a whole number of days") from None
        if not (1 <= n <= 3650):
            raise HTTPException(status_code=422, detail="Interval must be 1-3650 days")
        e.interval_days = n
    elif field == "last_cost":
        try:
            amount = Decimal(value or "0")
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="Cost must be a number") from None
        if amount < 0:
            raise HTTPException(status_code=422, detail="Cost cannot be negative")
        e.last_cost = amount
    elif field == "serviced_at":
        # The date lives in the service log, so editing it edits the newest entry,
        # or opens one when the item has never been serviced. One fact, one row.
        parsed = _parse_serviced_at(value)
        if logs:
            logs[0].serviced_at = parsed
        else:
            session.add(ServiceLog(company_id=company_id, equipment_id=e.id,
                                   serviced_at=parsed, note="Recorded by hand"))
    elif field == "instructions":
        if len(value) > INSTRUCTIONS_LIMIT:
            raise HTTPException(
                status_code=422,
                detail=f"Instructions cannot be longer than {INSTRUCTIONS_LIMIT} characters")
        e.instructions = value
    elif field == "location":
        if value:
            await _validate_location(session, company_id, value)
        e.location = value
    else:  # name, notes
        if field == "name" and not value.strip():
            raise HTTPException(status_code=422, detail="Name cannot be empty")
        setattr(e, field, value)
    await session.commit()
    logs = await _logs(session, company_id, e.id)
    return serialize(e, logs[0].serviced_at if logs else None)


# ── mark serviced, and taking it back ─────────────────────────────────────────

@router.post("/equipment/mark-serviced")
async def mark_serviced(ids: list[str] = Body(..., embed=True),
                        _: None = CAN_EDIT,
                        company_id: str = Depends(get_current_company_id),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Record a service against one or many items, today.

    Nothing in a batch fails the batch: a malformed id, or one belonging to
    another company, is counted as skipped and reported back so the UI can say
    how many were done and how many were not.
    """
    if not ids:
        raise HTTPException(status_code=422,
                            detail="Select at least one item to mark serviced")
    wanted: list[uuid.UUID] = []
    skipped = 0
    for raw in ids:
        try:
            wanted.append(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            skipped += 1
    rows = (await session.execute(
        select(Equipment).where(Equipment.company_id == company_id,
                                Equipment.id.in_(wanted)))).scalars().all()
    skipped += len(set(wanted)) - len(rows)

    today = datetime.now(timezone.utc).date()
    already = set((await session.execute(
        select(ServiceLog.equipment_id).where(
            ServiceLog.company_id == company_id,
            ServiceLog.equipment_id.in_([e.id for e in rows]),
            ServiceLog.serviced_at == today))).scalars().all())
    # Idempotent: a second run on the same day records nothing new, so re-running
    # the bulk action cannot fabricate a second service.
    for e in rows:
        if e.id not in already:
            session.add(ServiceLog(company_id=company_id, equipment_id=e.id,
                                   serviced_at=today, cost=e.last_cost))
    await session.commit()
    return {"updated": len(rows), "skipped": skipped}


@router.delete("/equipment/{equipment_id}/service-log/{log_id}")
async def undo_service(equipment_id: str, log_id: str,
                       _: None = CAN_EDIT,
                       company_id: str = Depends(get_current_company_id),
                       session: AsyncSession = Depends(get_session)) -> dict:
    """Take back the newest service entry, so the last-serviced date falls back to
    the one before it. Older entries stay: history is a log, not a spreadsheet."""
    e = await _get(session, company_id, equipment_id)
    logs = await _logs(session, company_id, e.id)
    wanted = _as_uuid(log_id, "That service entry no longer exists")
    match = next((row for row in logs if row.id == wanted), None)
    if match is None:
        # Already removed, by this user in another tab or by someone else. The
        # request wanted it gone and it is gone, so this is not an error.
        return {"removed": False}
    if match.id != logs[0].id:
        raise HTTPException(status_code=422,
                            detail="Only the most recent service entry can be removed")
    await session.delete(match)
    await session.commit()
    remaining = await _logs(session, company_id, e.id)
    return {"removed": True,
            "item": serialize(e, remaining[0].serviced_at if remaining else None)}


# ── archive and restore, in place of delete ───────────────────────────────────

@router.post("/equipment/{equipment_id}/archive")
async def archive_equipment(equipment_id: str,
                            _: None = CAN_EDIT,
                            company_id: str = Depends(get_current_company_id),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """Leave the list without leaving the database. Archiving an archived row is a
    no-op returning the same record, so a double click changes nothing."""
    e = await _get(session, company_id, equipment_id)
    if e.archived_at is None:
        e.archived_at = datetime.now(timezone.utc)
        await session.commit()
    logs = await _logs(session, company_id, e.id)
    return serialize(e, logs[0].serviced_at if logs else None)


@router.post("/equipment/{equipment_id}/restore")
async def restore_equipment(equipment_id: str,
                            _: None = CAN_EDIT,
                            company_id: str = Depends(get_current_company_id),
                            session: AsyncSession = Depends(get_session)) -> dict:
    e = await _get(session, company_id, equipment_id)
    if e.archived_at is not None:
        e.archived_at = None
        await session.commit()
    logs = await _logs(session, company_id, e.id)
    return serialize(e, logs[0].serviced_at if logs else None)


# ── files ─────────────────────────────────────────────────────────────────────

def _check_filename(filename: str) -> str:
    """Reject a filename that cannot be stored or safely echoed in a header.

    store_upload builds the stored name itself, so a user filename never becomes
    a path. It does reach the Content-Disposition header on download, which is
    where a control character would become a second header.
    """
    name = (filename or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="The file needs a name")
    if len(name) > FILENAME_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"File name cannot be longer than {FILENAME_LIMIT} characters")
    if any(ord(ch) < 32 or ch in '"\\' for ch in name):
        raise HTTPException(status_code=422,
                            detail="File name cannot contain control characters")
    return name


@router.post("/equipment/{equipment_id}/files")
async def upload_file(equipment_id: str, file: UploadFile = File(...),
                      _: None = CAN_EDIT,
                      company_id: str = Depends(get_current_company_id),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Store the bytes through celerp's attachment service, then record the
    metadata in this module's own table. The row is written only after the bytes
    land, so a storage failure never leaves a file the user cannot download."""
    e = await _get(session, company_id, equipment_id)
    _check_filename(file.filename or "")
    if not await file.read():
        raise HTTPException(status_code=422, detail="The file is empty")
    await file.seek(0)
    try:
        # The size limit and the MIME allowlist are core's, checked in one place.
        meta = await attachments.store_upload(str(company_id), file)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError:
        log.exception("acme-maintenance: storing %s failed", file.filename)
        raise HTTPException(status_code=500,
                            detail="Upload failed, the file was not saved") from None
    row = EquipmentFile(company_id=company_id, equipment_id=e.id,
                        filename=meta["filename"], content_type=meta.get("mime"),
                        size_bytes=meta.get("size") or 0, storage_key=meta.get("url") or "")
    session.add(row)
    await session.commit()
    return serialize_file(row)


@router.get("/equipment/{equipment_id}/files")
async def list_equipment_files(equipment_id: str,
                               company_id: str = Depends(get_current_company_id),
                               session: AsyncSession = Depends(get_session)) -> dict:
    e = await _get(session, company_id, equipment_id)
    return {"items": [serialize_file(row)
                      for row in await _files(session, company_id, e.id)]}


@router.delete("/equipment/{equipment_id}/files/{file_id}")
async def delete_equipment_file(equipment_id: str, file_id: str,
                                _: None = CAN_EDIT,
                                company_id: str = Depends(get_current_company_id),
                                session: AsyncSession = Depends(get_session)) -> dict:
    e = await _get(session, company_id, equipment_id)
    row = (await session.execute(select(EquipmentFile).where(
        EquipmentFile.id == _as_uuid(file_id, "That file no longer exists"),
        EquipmentFile.company_id == company_id,
        EquipmentFile.equipment_id == e.id))).scalar_one_or_none()
    if row is None:
        return {"deleted": False}
    await session.delete(row)
    await session.commit()
    return {"deleted": True}


@router.get("/equipment/{equipment_id}/files/{file_id}/download")
async def download_equipment_file(equipment_id: str, file_id: str,
                                  company_id: str = Depends(get_current_company_id),
                                  session: AsyncSession = Depends(get_session)):
    """Serve the bytes through this route rather than handing out the storage URL.

    Core's /static/attachments proxy checks only that a session cookie is present:
    it does not compare the company segment of the path to the caller's company. So
    the stored path never reaches a browser, and every download passes the same
    company scope and permission gate as the rest of this module.
    """
    e = await _get(session, company_id, equipment_id)
    row = (await session.execute(select(EquipmentFile).where(
        EquipmentFile.id == _as_uuid(file_id, "That file no longer exists"),
        EquipmentFile.company_id == company_id,
        EquipmentFile.equipment_id == e.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="That file no longer exists")

    key = row.storage_key or ""
    if key.startswith("http"):
        # A remote backend (S3 and friends) returns an absolute URL that is its own
        # public contract, and there is nothing local to stream.
        return RedirectResponse(key)

    from celerp.config import settings  # lazy: settings are not ready at import time
    dest = Path(settings.data_dir) / key.lstrip("/")
    if not dest.is_file():
        raise HTTPException(status_code=404, detail="File missing from storage")
    return FileResponse(path=str(dest), media_type=row.content_type or
                        "application/octet-stream",
                        headers={"Content-Disposition": _disposition(row.filename)})


def _disposition(filename: str) -> str:
    """A Content-Disposition value a filename cannot break out of."""
    safe = re.sub(r'[\r\n";\\]', "", filename)[:FILENAME_LIMIT] or "download"
    ascii_name = safe.encode("ascii", "replace").decode("ascii")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe)}"


def setup_api_routes(app) -> None:
    """Entry point the module loader calls to mount these routes."""
    app.include_router(router)
    log.info("acme-maintenance: API routes registered")
