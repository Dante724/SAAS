"""Build 73 — Work Schedules & timesheets.

Planned shifts / absences (WorkSchedule) that the scheduler shades and payroll
compares actual worked time (TimeEntry) against. Orderry keeps the *plan* and the
*fact* separate; payroll runs off the fact, but variance against the plan is what
managers review.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_date
from backend.models import Employee, TimeEntry, WorkSchedule
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

KINDS = ["shift", "day_off", "vacation", "sick"]


def _hhmm(mins: Optional[int]) -> Optional[str]:
    if mins is None:
        return None
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _ser(w: WorkSchedule) -> dict:
    return {"id": w.id, "employee_id": w.employee_id, "location_id": w.location_id,
            "day": w.day.isoformat(), "kind": w.kind,
            "start_min": w.start_min, "end_min": w.end_min,
            "start": _hhmm(w.start_min), "end": _hhmm(w.end_min),
            "planned_hours": q2(max(0, (w.end_min - w.start_min)) / 60) if w.kind == "shift" else 0,
            "note": w.note}


@router.get("")
async def list_schedules(
    date_from: str, date_to: str, employee_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Planned entries in a date range (inclusive), optionally for one employee."""
    try:
        df, dt = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400, "date_from / date_to must be YYYY-MM-DD")
    q = select(WorkSchedule).where(
        WorkSchedule.org_id == current.org_id,
        WorkSchedule.day >= df, WorkSchedule.day <= dt)
    if employee_id:
        q = q.where(WorkSchedule.employee_id == employee_id)
    rows = (await db.execute(q.order_by(WorkSchedule.day, WorkSchedule.start_min))).scalars().all()
    return {"items": [_ser(w) for w in rows]}


class ScheduleIn(BaseModel):
    employee_id: str
    day: str                 # YYYY-MM-DD
    kind: str = "shift"      # shift|day_off|vacation|sick
    start: Optional[str] = None   # "09:00" (shift only)
    end: Optional[str] = None     # "17:00"
    location_id: Optional[str] = None
    note: Optional[str] = None


def _to_min(hhmm: Optional[str], default: int) -> int:
    if not hhmm:
        return default
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


@router.post("", status_code=201)
async def create_schedule(
    data: ScheduleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_staff")),
):
    if data.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {KINDS}")
    start_min = _to_min(data.start, 540)
    end_min = _to_min(data.end, 1020)
    if data.kind == "shift" and end_min <= start_min:
        raise HTTPException(400, "Shift end must be after start")
    w = WorkSchedule(
        org_id=current.org_id, employee_id=data.employee_id, location_id=data.location_id,
        day=parse_date(data.day, "day"), kind=data.kind,
        start_min=start_min, end_min=end_min, note=data.note)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return _ser(w)


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_staff")),
):
    w = (await db.execute(select(WorkSchedule).where(
        WorkSchedule.id == schedule_id, WorkSchedule.org_id == current.org_id))).scalar_one_or_none()
    if w:
        await db.delete(w)
        await db.commit()
    return {"ok": True}


@router.get("/compare")
async def timesheet_compare(
    employee_id: str, date_from: str, date_to: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Planned hours (schedule) vs actual hours (completed TimeEntry) for an
    employee over a range — the variance managers review before accruing payroll."""
    try:
        df, dt = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400, "date_from / date_to must be YYYY-MM-DD")
    sched = (await db.execute(select(WorkSchedule).where(
        WorkSchedule.org_id == current.org_id, WorkSchedule.employee_id == employee_id,
        WorkSchedule.day >= df, WorkSchedule.day <= dt))).scalars().all()
    planned_hours = sum(max(0, (w.end_min - w.start_min)) / 60 for w in sched if w.kind == "shift")
    absences = [{"day": w.day.isoformat(), "kind": w.kind} for w in sched if w.kind != "shift"]

    entries = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == employee_id,
        TimeEntry.ended_at.isnot(None),
        TimeEntry.started_at >= datetime.combine(df, datetime.min.time()),
        TimeEntry.started_at <= datetime.combine(dt, datetime.max.time())))).scalars().all()
    actual_hours = 0.0
    for e in entries:
        if e.ended_at and e.started_at:
            actual_hours += (e.ended_at - e.started_at).total_seconds() / 3600
    return {
        "employee_id": employee_id, "date_from": df.isoformat(), "date_to": dt.isoformat(),
        "planned_hours": q2(planned_hours), "actual_hours": q2(actual_hours),
        "variance_hours": q2(actual_hours - planned_hours),
        "absences": absences,
    }
