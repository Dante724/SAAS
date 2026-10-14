"""144jq — custom report builder API.

Sources metadata for the builder UI, CRUD for saved report definitions, and two
run paths (ad-hoc by config, or by saved id). Everything is org-scoped and the
config is validated against the whitelist in services.custom_reports before it
runs, so a spec can never reach data outside the caller's org.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import CustomReport, Employee
from backend.services import custom_reports as cr
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/custom-reports", tags=["reports"])


def _ser(r: CustomReport) -> dict:
    return {"id": r.id, "name": r.name, "config": r.config,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("/sources")
async def sources(current: Employee = Depends(get_current_employee)):
    return cr.sources_meta()


@router.get("")
async def list_reports(db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(CustomReport).where(CustomReport.org_id == current.org_id)
            .order_by(CustomReport.created_at.desc()))).scalars().all()
    return {"items": [_ser(r) for r in rows]}


class ReportIn(BaseModel):
    name: str
    config: dict


class RunIn(BaseModel):
    config: dict
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    location_id: Optional[str] = None


@router.post("/run")
async def run_adhoc(data: RunIn, db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    try:
        return await cr.build_and_run(db, current.org_id, data.config, data.date_from, data.date_to, data.location_id)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("")
async def create_report(data: ReportIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    if (data.config or {}).get("source") not in cr.SOURCES:
        raise HTTPException(422, "Unknown or missing source")
    r = CustomReport(org_id=current.org_id, name=data.name.strip() or "Untitled report",
                     config=data.config, created_by_id=current.id)
    db.add(r)
    await db.commit()
    return _ser(r)


async def _owned(db, org_id, rid) -> CustomReport:
    r = (await db.execute(select(CustomReport).where(
        CustomReport.id == rid, CustomReport.org_id == org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Report not found")
    return r


@router.put("/{rid}")
async def update_report(rid: str, data: ReportIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    r = await _owned(db, current.org_id, rid)
    if (data.config or {}).get("source") not in cr.SOURCES:
        raise HTTPException(422, "Unknown or missing source")
    r.name = data.name.strip() or r.name
    r.config = data.config
    await db.commit()
    return _ser(r)


@router.delete("/{rid}")
async def delete_report(rid: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    r = await _owned(db, current.org_id, rid)
    await db.delete(r)
    await db.commit()
    return {"ok": True}


@router.get("/{rid}/run")
async def run_saved(rid: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    location_id: Optional[str] = None, db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    r = await _owned(db, current.org_id, rid)
    try:
        out = await cr.build_and_run(db, current.org_id, r.config, date_from, date_to, location_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    out["title"] = r.name
    return out
