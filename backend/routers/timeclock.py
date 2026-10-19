"""Work-order time clock.

Primary unit is time tracked against a work order (order_id). Non-job time is an
entry with order_id = null. Exactly one running entry per employee; starting a new
timer auto-stops the previous (a 'switch'). Stopped entries roll up onto the order
(scheduled vs tracked vs billed) and, once a timesheet is approved, post hourly
wages into the payroll ledger so the existing payroll run picks them up.
"""
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Order, TimeEntry, TimeEntryAudit
from backend.services.dates import parse_dt


def _udt(value, field: str):
    """Parse a user-supplied ISO datetime (400 on bad input) and normalize any
    timezone-aware value to naive UTC, so comparisons against naive
    datetime.utcnow()/stored values don't raise TypeError (500)."""
    dt = parse_dt(value, field)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _audit(db, e, action, actor_id, *, field=None, old=None, new=None):
    """144ay — append an immutable audit row for a time-entry change (ADR 144ax Option B)."""
    db.add(TimeEntryAudit(org_id=e.org_id, time_entry_id=e.id, action=action, field=field,
                          old_value=None if old is None else str(old),
                          new_value=None if new is None else str(new), actor_id=actor_id))
from backend.services.auth import assert_not_self, get_current_employee, require_perm, record_scope
from backend.core import paging as _paging
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/timeclock", tags=["timeclock"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

CATEGORIES = ("job", "task", "break", "admin", "other")


async def _clock_enabled(db: AsyncSession, org_id: str, which: str) -> bool:
    """Org master switch for a clock. which = 'wo' | 'attendance'. Defaults on."""
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not org:
        return True
    val = org.clock_wo_enabled if which == "wo" else org.clock_attendance_enabled
    return True if val is None else bool(val)


# ══ Build 144: workplace attendance shift (clock in/out of the workplace) ══════
# Separate from per-order timers: this is "I'm at work now" attendance, used to
# track daily hours against the employee's expected weekly_hours.

def _shift_elapsed(s) -> int:
    end = s.ended_at or datetime.utcnow()
    return max(int((end - s.started_at).total_seconds()), 0)


def _ser_shift(s) -> dict:
    return {"id": s.id, "location_id": s.location_id,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "elapsed_sec": _shift_elapsed(s),
            "hours_worked": q2(s.hours_worked or 0), "open": s.ended_at is None}


async def _open_shift(db, employee_id):
    from backend.models import Shift
    return (await db.execute(select(Shift).where(
        Shift.employee_id == employee_id, Shift.ended_at.is_(None))
        .order_by(Shift.started_at.desc()))).scalars().first()


@router.get("/shift/active")
async def active_shift(db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(get_current_employee)):
    s = await _open_shift(db, current.id)
    return {"shift": _ser_shift(s) if s else None}


@router.post("/shift/clock-in", status_code=201)
async def shift_clock_in(db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(get_current_employee)):
    from backend.models import Location, Shift
    if not await _clock_enabled(db, current.org_id, "attendance"):
        raise HTTPException(403, "Shop attendance clock is turned off for this company")
    if await _open_shift(db, current.id):
        raise HTTPException(409, "You are already clocked in")
    loc_id = current.location_id
    if not loc_id:
        loc_id = (await db.execute(select(Location.id).where(
            Location.org_id == current.org_id, Location.active == True)  # noqa: E712
            .order_by(Location.id))).scalars().first()
    if not loc_id:
        raise HTTPException(400, "No location available to clock in to")
    s = Shift(employee_id=current.id, location_id=loc_id, started_at=datetime.utcnow())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _ser_shift(s)


@router.post("/shift/clock-out")
async def shift_clock_out(db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    s = await _open_shift(db, current.id)
    if not s:
        raise HTTPException(409, "You are not clocked in")
    s.ended_at = datetime.utcnow()
    s.hours_worked = q2(_shift_elapsed(s) / 3600.0)
    await db.commit()
    await db.refresh(s)
    return _ser_shift(s)


@router.get("/shift/history")
async def shift_history(db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    from backend.models import Shift
    rows = (await db.execute(select(Shift).where(Shift.employee_id == current.id)
            .order_by(Shift.started_at.desc()).limit(60))).scalars().all()
    total = q2(sum(s.hours_worked or 0 for s in rows if s.ended_at))
    return {"items": [_ser_shift(s) for s in rows], "total_hours": total}


# Build 144dn — manager-only live roster: who is clocked in right now and where.
# Regular employees never see anyone else's clock; this is gated to managers.
@router.get("/roster")
async def roster(db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(get_current_employee)):
    if not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    from backend.models import Shift, Location
    names = await _emp_map(db, current.org_id)
    locs = {l.id: l.name for l in (await db.execute(
        select(Location).where(Location.org_id == current.org_id))).scalars().all()}
    # open attendance shifts (scoped to this org via its locations)
    open_shifts = (await db.execute(select(Shift).where(
        Shift.ended_at.is_(None), Shift.location_id.in_(list(locs.keys()) or [""]))
        .order_by(Shift.started_at))).scalars().all()
    on_shift = [{
        "employee_id": s.employee_id, "employee_name": names.get(s.employee_id, "—"),
        "location_id": s.location_id, "location_name": locs.get(s.location_id, "—"),
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "elapsed_sec": _shift_elapsed(s),
    } for s in open_shifts]
    # running job/task timers across the org
    running = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.state == "running",
        TimeEntry.ended_at.is_(None)).order_by(TimeEntry.started_at))).scalars().all()
    timers = []
    for e in running:
        timers.append({
            "employee_id": e.employee_id, "employee_name": names.get(e.employee_id, "—"),
            "order_number": await _order_no(db, e.order_id),
            "category": e.category, "location_name": locs.get(e.location_id, "—"),
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "elapsed_sec": _elapsed_sec(e),
        })
    return {"on_shift": on_shift, "timers": timers,
            "counts": {"on_shift": len(on_shift), "running_timers": len(timers)}}


AUTO_STOP_HOURS = 12          # a timer left running longer than this is force-stopped + flagged
OT_DAILY_THRESHOLD_H = 8      # hours/day beyond which time is overtime
OT_MULTIPLIER = 1.5


# ── helpers ──────────────────────────────────────────────────────────────────
def _rates(emp: Employee) -> tuple[float, float]:
    """(cost_rate, billable_rate) per hour, snapshotted onto each entry at start."""
    cost = emp.hourly_cost_rate or (emp.wage_value if emp.wage_type == "hourly" else 0.0) or 0.0
    billable = emp.billable_rate or cost
    return float(cost), float(billable)


def _elapsed_sec(e: TimeEntry, now: Optional[datetime] = None) -> int:
    if e.ended_at:
        return int(e.duration_sec or 0)
    now = now or datetime.utcnow()
    return max(0, int((now - e.started_at).total_seconds()))


def _ser(e: TimeEntry, order_no: Optional[str] = None, emp_name: Optional[str] = None, task_title: Optional[str] = None) -> dict:
    sec = _elapsed_sec(e)
    return {
        "id": e.id, "employee_id": e.employee_id, "employee_name": emp_name,
        "order_id": e.order_id, "order_line_id": getattr(e, "order_line_id", None), "task_id": e.task_id, "order_number": order_no, "task_title": task_title, "category": e.category,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "ended_at": e.ended_at.isoformat() if e.ended_at else None,
        "duration_sec": sec, "duration_min": round(sec / 60, 1),
        "running": e.ended_at is None and e.state == "running",
        "billable": e.billable, "cost_rate": e.cost_rate, "billable_rate": e.billable_rate,
        "labor_cost": q2(sec / 3600 * (e.cost_rate or 0)),
        "labor_billable": q2(sec / 3600 * (e.billable_rate or 0)) if e.billable else 0.0,
        "note": e.note, "source": e.source, "state": e.state, "needs_review": e.needs_review,
        "disputed": getattr(e, "disputed", False), "dispute_note": getattr(e, "dispute_note", None),
    }


async def _emp_map(db: AsyncSession, org_id: str) -> dict:
    return {e.id: e.name for e in (await db.execute(
        select(Employee).where(Employee.org_id == org_id))).scalars().all()}


async def _order_no(db: AsyncSession, order_id: Optional[str]) -> Optional[str]:
    if not order_id:
        return None
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    return o.order_number if o else None


async def _task_title(db: AsyncSession, task_id: Optional[str]) -> Optional[str]:
    if not task_id:
        return None
    from backend.models import Task
    t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    return (t.title if t else None)


async def _running_for(db: AsyncSession, org_id: str, employee_id: str) -> Optional[TimeEntry]:
    return (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org_id, TimeEntry.employee_id == employee_id,
        TimeEntry.state == "running", TimeEntry.ended_at.is_(None)))).scalars().first()


def _stop(e: TimeEntry, when: Optional[datetime] = None, needs_review: bool = False) -> None:
    when = when or datetime.utcnow()
    # guard against a timer left running for days
    cap = e.started_at + timedelta(hours=AUTO_STOP_HOURS)
    if when > cap:
        when = cap
        needs_review = True
    e.ended_at = when
    e.duration_sec = max(0, int((when - e.started_at).total_seconds()))
    e.state = "stopped"
    if needs_review:
        e.needs_review = True


async def _resolve_order(db: AsyncSession, org_id: str, order_id: Optional[str]) -> Optional[Order]:
    if not order_id:
        return None
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Work order not found")
    return o


# ── timer control (acts on the calling employee) ─────────────────────────────
class StartIn(BaseModel):
    order_id: Optional[str] = None
    order_line_id: Optional[str] = None  # 144v: attribute to a specific labour/service line
    task_id: Optional[str] = None      # build 144x: time a standalone task
    category: str = "job"
    note: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


@router.post("/start", status_code=201)
async def start_timer(
    data: StartIn,
    switch: bool = Query(False, description="stop any running timer first"),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {CATEGORIES}")
    if not await _clock_enabled(db, current.org_id, "wo"):
        raise HTTPException(403, "Work-order / job timer is turned off for this company")
    order = await _resolve_order(db, current.org_id, data.order_id)

    # 144v: a line timer validates the labour/service line and derives its order,
    # so multiple techs on one WO each attribute time to their own line.
    if data.order_line_id:
        from backend.models import OrderLine
        line = (await db.execute(select(OrderLine).where(OrderLine.id == data.order_line_id))).scalar_one_or_none()
        if not line:
            raise HTTPException(404, "Order line not found")
        if order is None:
            order = await _resolve_order(db, current.org_id, line.order_id)
        if not order or line.order_id != order.id:
            raise HTTPException(400, "That line doesn't belong to this work order")

    # build 144x: a task timer resolves the task (and inherits its order/location for
    # context) so its hours flow through the same approve → payroll pipeline as jobs.
    task = None
    task_id = data.task_id
    if task_id:
        from backend.models import Task
        task = (await db.execute(select(Task).where(
            Task.id == task_id, Task.org_id == current.org_id))).scalar_one_or_none()
        if not task:
            raise HTTPException(404, "Task not found")
        if order is None and task.order_id:
            order = await _resolve_order(db, current.org_id, task.order_id)

    running = await _running_for(db, current.org_id, current.id)
    if running:
        if not switch:
            raise HTTPException(409, "You already have a running timer. Stop it or use switch.")
        _stop(running)
    cost, billable = _rates(current)
    # billable/paid labour = a job (order) or a task; break/admin/other are unpaid
    is_labour = bool(data.order_id) or bool(task_id) or data.category == "job"
    resolved_category = "job" if data.order_id else ("task" if task_id else data.category)
    e = TimeEntry(
        org_id=current.org_id, employee_id=current.id, order_id=(order.id if order else data.order_id),
        order_line_id=data.order_line_id,
        task_id=task_id,
        location_id=(order.location_id if order else (task.location_id if task else current.location_id)),
        category=resolved_category,
        started_at=datetime.utcnow(), billable=is_labour,
        cost_rate=cost, billable_rate=billable, note=data.note, source="timer", state="running",
        gps_lat=getattr(data, "gps_lat", None), gps_lng=getattr(data, "gps_lng", None))
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _ser(e, await _order_no(db, e.order_id), current.name)


@router.post("/stop")
async def stop_timer(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    running = await _running_for(db, current.org_id, current.id)
    if not running:
        raise HTTPException(404, "No running timer")
    _stop(running)
    await _maybe_emit_stop(db, running)
    await _apply_labour_billing(db, running)   # optional time→labour billing
    await db.commit()
    await db.refresh(running)
    return _ser(running, await _order_no(db, running.order_id), current.name)


@router.post("/entries/{entry_id}/stop")
async def stop_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144v: stop a specific running entry. Anyone can stop their own; stopping
    another technician's timer (e.g. from the WO 'Time & people' panel) requires
    manager rights — same gate as the roster."""
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == entry_id, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Time entry not found")
    if e.employee_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Only a manager can stop another technician's timer")
    if e.ended_at is not None or e.state != "running":
        raise HTTPException(400, "That timer isn't running")
    _stop(e)
    await _maybe_emit_stop(db, e)
    await _apply_labour_billing(db, e)
    await db.commit()
    await db.refresh(e)
    return _ser(e, await _order_no(db, e.order_id), (await _emp_map(db, current.org_id)).get(e.employee_id))


class SwitchIn(BaseModel):
    order_id: str
    note: Optional[str] = None


@router.post("/switch", status_code=201)
async def switch_timer(
    data: SwitchIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    order = await _resolve_order(db, current.org_id, data.order_id)
    running = await _running_for(db, current.org_id, current.id)
    if running:
        _stop(running)
        await _maybe_emit_stop(db, running)
        await _apply_labour_billing(db, running)   # bill the timer we just stopped
    cost, billable = _rates(current)
    e = TimeEntry(
        org_id=current.org_id, employee_id=current.id, order_id=data.order_id,
        location_id=(order.location_id if order else current.location_id),
        category="job", started_at=datetime.utcnow(), billable=True,
        cost_rate=cost, billable_rate=billable, note=data.note, source="timer", state="running",
        gps_lat=getattr(data, "gps_lat", None), gps_lng=getattr(data, "gps_lng", None))
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _ser(e, await _order_no(db, e.order_id), current.name)


@router.get("/active")
async def active_timer(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    running = await _running_for(db, current.org_id, current.id)
    if not running:
        return {"active": None}
    return {"active": _ser(running, await _order_no(db, running.order_id), current.name,
                           await _task_title(db, running.task_id)) if running else None}


async def _maybe_emit_stop(db: AsyncSession, e: TimeEntry) -> None:
    try:
        from backend.services.webhooks import emit_event
        await emit_event(db, e.org_id, "time_entry.stopped", {
            "id": e.id, "employee_id": e.employee_id, "order_id": e.order_id,
            "duration_sec": e.duration_sec, "billable": e.billable})
    except Exception:
        pass


async def _apply_labour_billing(db: AsyncSession, e: TimeEntry, *, remove: bool = False) -> None:
    """Optional (org `clock_labour_billing`): turn tracked job time on a work order
    into a priced labour line. Idempotent — the line is keyed to this time entry's
    id, so re-editing time updates the same line instead of stacking new ones.
    Best-effort: never breaks the timer action if anything goes wrong.
    """
    try:
        if not e.order_id:
            return
        from backend.models import Order, OrderLine
        line = (await db.execute(select(OrderLine).where(
            OrderLine.order_id == e.order_id, OrderLine.line_type == "labour",
            OrderLine.ref_id == e.id))).scalar_one_or_none()

        hours = q2((e.duration_sec or 0) / 3600.0)
        enabled = await _clock_labour_billing_on(db, e.org_id)
        # remove the line when: caller asked, entry is void, billing is off, or no time
        if remove or e.state == "void" or not enabled or hours <= 0 or not e.billable:
            if line:
                await db.delete(line)
                order = (await db.execute(select(Order).where(Order.id == e.order_id))).scalar_one_or_none()
                if order:
                    from backend.routers.orders import _recalc_order
                    await _recalc_order(db, order)
            return

        rate = float(e.billable_rate or 0)
        total = q2(hours * rate)
        desc = (e.note or "Labour (tracked time)")
        if line:
            line.qty = hours
            line.unit_price = rate
            line.total = total
            line.description = desc
        else:
            db.add(OrderLine(
                order_id=e.order_id, line_type="labour", kind="service", ref_id=e.id,
                description=desc, qty=hours, unit_price=rate,
                cost_price=float(e.cost_rate or 0), technician_id=e.employee_id, total=total))
        order = (await db.execute(select(Order).where(Order.id == e.order_id))).scalar_one_or_none()
        if order:
            from backend.routers.orders import _recalc_order
            await _recalc_order(db, order)
    except Exception:
        pass


async def _clock_labour_billing_on(db: AsyncSession, org_id: str) -> bool:
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    return bool(org and getattr(org, "clock_labour_billing", False))


# ── entries: list / manual create / edit / void / approve ────────────────────
@router.get("/entries")
async def list_entries(
    employee_id: Optional[str] = None,
    order_id: Optional[str] = None,
    state: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = _paging.skip_query(),
    limit: int = _paging.limit_query(_paging.PAGE_MAX),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    limit = _paging.clamp(limit)
    q = select(TimeEntry).where(TimeEntry.org_id == current.org_id)
    # non-managers can only see their own time
    if not await _can_manage(db, current):
        q = q.where(TimeEntry.employee_id == current.id)
    elif employee_id:
        q = q.where(TimeEntry.employee_id == employee_id)
    if order_id:
        q = q.where(TimeEntry.order_id == order_id)
    if state:
        q = q.where(TimeEntry.state == state)
    if date_from:
        q = q.where(TimeEntry.started_at >= _udt(date_from, "date_from"))
    if date_to:
        q = q.where(TimeEntry.started_at < _udt(date_to, "date_to") + timedelta(days=1))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(q.order_by(TimeEntry.started_at.desc(), TimeEntry.id.desc())
                             .offset(skip).limit(limit))).scalars().all()
    names = await _emp_map(db, current.org_id)
    oids = {e.order_id for e in rows if e.order_id}
    nos = dict((await db.execute(select(Order.id, Order.order_number).where(
        Order.org_id == current.org_id, Order.id.in_(oids)))).all()) if oids else {}
    return {"items": [_ser(e, nos.get(e.order_id), names.get(e.employee_id)) for e in rows],
            "total": total, "skip": skip, "limit": limit}


async def _can_manage(db: AsyncSession, emp: Employee) -> bool:
    """Owners (no role) and roles not explicitly denied can manage all timesheets."""
    if not emp.role_id:
        return True
    from backend.models import Role
    from backend.services.permissions import role_allows
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one_or_none()
    return role is not None and role_allows(role, "can_manage_payroll")


class ManualIn(BaseModel):
    employee_id: Optional[str] = None
    order_id: Optional[str] = None
    category: str = "job"
    started_at: str
    ended_at: Optional[str] = None
    duration_min: Optional[float] = None
    billable: Optional[bool] = None
    note: Optional[str] = None


@router.post("/entries", status_code=201)
async def create_manual(
    data: ManualIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    emp_id = data.employee_id or current.id
    if emp_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "You can only log your own time")
    if data.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {CATEGORIES}")
    await _resolve_order(db, current.org_id, data.order_id)
    start = _udt(data.started_at, "started_at")
    if data.ended_at:
        end = _udt(data.ended_at, "ended_at")
    elif data.duration_min is not None:
        end = start + timedelta(minutes=data.duration_min)
    else:
        raise HTTPException(400, "Provide ended_at or duration_min")
    if end <= start:
        raise HTTPException(400, "End must be after start")
    # overlap guard against that employee's other entries
    others = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == emp_id,
        TimeEntry.state != "void"))).scalars().all()
    for o in others:
        o_end = o.ended_at or datetime.utcnow()
        if start < o_end and o.started_at < end:
            raise HTTPException(409, "That overlaps an existing time entry")
    # 144cw — scope to the caller's org: a manager must not log time for a foreign-org
    # employee (would leak that employee's pay rates + pollute this org's payroll).
    emp = (await db.execute(select(Employee).where(
        Employee.id == emp_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    cost, billable_rate = _rates(emp)
    billable = data.billable if data.billable is not None else (data.category == "job")
    e = TimeEntry(
        org_id=current.org_id, employee_id=emp_id, order_id=data.order_id,
        location_id=emp.location_id, category=data.category if not data.order_id else "job",
        started_at=start, ended_at=end, duration_sec=int((end - start).total_seconds()),
        billable=billable, cost_rate=cost, billable_rate=billable_rate,
        note=data.note, source="manual", state="stopped")
    db.add(e)
    await db.flush()
    _audit(db, e, "create", current.id, new=f"{start.isoformat()}→{end.isoformat()}")  # 144ay
    await _apply_labour_billing(db, e)   # a manual order-linked entry bills too
    await db.commit()
    await db.refresh(e)
    return _ser(e, await _order_no(db, e.order_id), emp.name)


class EntryPatch(BaseModel):
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    billable: Optional[bool] = None
    category: Optional[str] = None
    note: Optional[str] = None


@router.patch("/entries/{eid}")
async def edit_entry(
    eid: str, data: EntryPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.employee_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    if e.state == "approved":
        raise HTTPException(409, "Entry is approved and locked; reopen it first")
    if e.state == "running":
        raise HTTPException(409, "Stop the timer before editing")
    # 144ay — record each field change to the append-only audit trail (old → new).
    if data.started_at:
        nv = _udt(data.started_at, "started_at")
        if nv != e.started_at:
            _audit(db, e, "edit", current.id, field="started_at", old=e.started_at, new=nv)
            e.started_at = nv
    if data.ended_at:
        nv = _udt(data.ended_at, "ended_at")
        if nv != e.ended_at:
            _audit(db, e, "edit", current.id, field="ended_at", old=e.ended_at, new=nv)
            e.ended_at = nv
    if data.category and data.category in CATEGORIES and data.category != e.category:
        _audit(db, e, "edit", current.id, field="category", old=e.category, new=data.category)
        e.category = data.category
    if data.billable is not None and data.billable != e.billable:
        _audit(db, e, "edit", current.id, field="billable", old=e.billable, new=data.billable)
        e.billable = data.billable
    if data.note is not None and data.note != e.note:
        _audit(db, e, "edit", current.id, field="note", old=e.note, new=data.note)
        e.note = data.note
    if e.ended_at and e.ended_at <= e.started_at:
        raise HTTPException(400, "End must be after start")
    if e.ended_at:
        e.duration_sec = int((e.ended_at - e.started_at).total_seconds())
    await _apply_labour_billing(db, e)   # reprice the labour line if time changed
    await db.commit()
    await db.refresh(e)
    return _ser(e, await _order_no(db, e.order_id), None)


@router.delete("/entries/{eid}")
async def void_entry(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.employee_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    if e.state == "approved":
        raise HTTPException(409, "Approved entry is locked; reopen it first")
    _audit(db, e, "void", current.id, field="state", old=e.state, new="void")
    e.state = "void"
    await _apply_labour_billing(db, e, remove=True)   # drop any labour line it created
    await db.commit()
    return {"ok": True}


@router.post("/entries/{eid}/approve")
async def approve_entry(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.state == "running":
        raise HTTPException(409, "Stop the timer before approving")
    if e.state == "void":
        raise HTTPException(409, "Cannot approve a voided entry")
    _audit(db, e, "approve", current.id, field="state", old=e.state, new="approved")
    e.state = "approved"
    e.approved_by_id = current.id
    await db.commit()
    return {"ok": True, "state": e.state}


@router.get("/entries/{eid}/audit")
async def entry_audit(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144ay — the append-only edit history for one time entry (managers, or the owner)."""
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.employee_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    names = await _emp_map(db, current.org_id)
    rows = (await db.execute(select(TimeEntryAudit).where(
        TimeEntryAudit.time_entry_id == eid).order_by(TimeEntryAudit.at.asc()))).scalars().all()
    return {"items": [{"action": a.action, "field": a.field, "old": a.old_value, "new": a.new_value,
                       "actor": names.get(a.actor_id, "—"), "at": a.at.isoformat()} for a in rows]}


@router.post("/entries/{eid}/reopen")
async def reopen_entry(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.state != "approved":
        raise HTTPException(409, "Only approved entries can be reopened")
    _audit(db, e, "reopen", current.id, field="state", old="approved", new="stopped")
    e.state = "stopped"
    e.approved_by_id = None
    await db.commit()
    return {"ok": True, "state": e.state}


class DisputeIn(BaseModel):
    note: Optional[str] = None


@router.post("/entries/{eid}/dispute")
async def dispute_entry(
    eid: str, data: DisputeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144az — a tech flags their own entry as disputed (hours look wrong). Audited."""
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    if e.employee_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    e.disputed = True
    e.dispute_note = data.note
    _audit(db, e, "dispute", current.id, field="disputed", old=False, new=(data.note or "disputed"))
    await db.commit()
    return {"ok": True, "disputed": True}


@router.post("/entries/{eid}/resolve-dispute")
async def resolve_dispute(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """144az — a manager clears a dispute (after correcting or confirming the entry). Audited."""
    e = (await db.execute(select(TimeEntry).where(
        TimeEntry.id == eid, TimeEntry.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entry not found")
    e.disputed = False
    _audit(db, e, "resolve_dispute", current.id, field="disputed", old=True, new=False)
    await db.commit()
    return {"ok": True, "disputed": False}


@router.get("/my-work")
async def my_work(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144az — the technician's own shop-floor view: live timer, today's + this week's clocked
    job hours, and the open service lines assigned to them with booked-vs-actual."""
    from backend.models import OrderLine
    from backend.services.labour import entry_seconds, is_job_actual
    now = datetime.utcnow()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mine = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == current.id,
        TimeEntry.started_at >= week_start))).scalars().all()
    today_s = week_s = 0
    for e in mine:
        if not is_job_actual(e):
            continue
        s = entry_seconds(e)
        week_s += s
        if e.started_at >= day_start:
            today_s += s
    active = await _running_for(db, current.org_id, current.id)

    # open service/labour lines assigned to me, with booked + measured actual
    open_orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.is_estimate == False,   # noqa: E712
        Order.archived == False))).scalars().all()   # noqa: E712
    open_ids = {o.id: o.order_number for o in open_orders}
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.technician_id == current.id,
        OrderLine.order_id.in_(list(open_ids.keys()) or ["__none__"])))).scalars().all()
    # measured actual seconds per line (job time, any tech) via shared helper source
    line_secs: dict = {}
    for e in (await db.execute(select(TimeEntry).where(
            TimeEntry.org_id == current.org_id,
            TimeEntry.order_line_id.in_([l.id for l in lines] or ["__none__"])))).scalars().all():
        if is_job_actual(e):
            line_secs[e.order_line_id] = line_secs.get(e.order_line_id, 0) + entry_seconds(e)
    my_lines = [{
        "order_id": l.order_id, "order_number": open_ids.get(l.order_id),
        "line_id": l.id, "description": l.description,
        "booked_hours": getattr(l, "booked_hours", 0) or 0,
        "actual_hours": q2(line_secs.get(l.id, 0) / 3600),
        "running": any(e.order_line_id == l.id and e.ended_at is None
                       for e in mine),
    } for l in lines if (l.kind or l.line_type or "").lower() in ("service", "labour", "labor")]

    # 144cr — group my lines into per-order BAY CARDS so a tech can advance status and
    # see checklist progress without opening the drawer. Status/next-status come from the
    # org's configurable work_order workflow; checklist progress from OrderChecklistItem.
    orders = []
    my_order_ids = list(dict.fromkeys(l["order_id"] for l in my_lines))
    if my_order_ids:
        from backend.models import OrderChecklistItem, OrderStatus, Client, Device
        from backend.routers.statuses import ensure_seeded
        await ensure_seeded(db, current.org_id)
        wf = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == current.org_id, OrderStatus.record_type == "work_order",
            OrderStatus.active == True).order_by(OrderStatus.sort_order))).scalars().all()  # noqa: E712
        by_key = {s.key: s for s in wf}
        order_by_id = {o.id: o for o in open_orders}
        # checklist progress per order (done/total)
        prog: dict = {}
        for it in (await db.execute(select(OrderChecklistItem).where(
                OrderChecklistItem.order_id.in_(my_order_ids)))).scalars().all():
            d, t = prog.get(it.order_id, (0, 0))
            prog[it.order_id] = (d + (1 if it.done else 0), t + 1)
        clients = {c.id: c.name for c in (await db.execute(select(Client).where(
            Client.org_id == current.org_id))).scalars().all()}
        devices = {d.id: d for d in (await db.execute(select(Device).where(
            Device.org_id == current.org_id))).scalars().all()}
        for oid in my_order_ids:
            o = order_by_id.get(oid)
            if not o:
                continue
            cur = by_key.get(o.status)
            nxt = None
            if cur and not cur.is_terminal:
                nxt = next((s for s in wf if s.sort_order > cur.sort_order and not s.is_terminal), None) \
                    or next((s for s in wf if s.sort_order > cur.sort_order), None)
            dv = devices.get(o.device_id)
            done, total = prog.get(oid, (0, 0))
            orders.append({
                "order_id": oid, "order_number": o.order_number,
                "status": o.status, "status_label": cur.name if cur else o.status,
                "status_color": cur.color if cur else None,
                "next_status": nxt.key if nxt else None,
                "next_label": nxt.name if nxt else None,
                "checklist_done": done, "checklist_total": total,
                "client": clients.get(o.client_id),
                "device": (" ".join(x for x in [dv.brand, dv.model] if x) or dv.serial) if dv else None,
                "lines": [l for l in my_lines if l["order_id"] == oid],
            })

    return {"today_seconds": today_s, "week_seconds": week_s,
            "active": _ser(active, await _order_no(db, active.order_id), current.name) if active else None,
            "lines": my_lines, "orders": orders}


# ── timesheet aggregation + approval (payroll bridge) ────────────────────────
def _overtime_split(entries: list[TimeEntry]) -> tuple[float, float]:
    """Return (straight_hours, overtime_hours) by daily threshold."""
    by_day: dict[date, float] = {}
    for e in entries:
        if e.state == "void" or not e.duration_sec:
            continue
        by_day[e.started_at.date()] = by_day.get(e.started_at.date(), 0) + e.duration_sec / 3600
    straight = overtime = 0.0
    for _, hrs in by_day.items():
        ot = max(0.0, hrs - OT_DAILY_THRESHOLD_H)
        straight += hrs - ot
        overtime += ot
    return q4(straight), q4(overtime)


def _day_wage(entries: list[TimeEntry]) -> Decimal:
    """One day's pay: each entry at its own snapshotted rate, in time order; hours past the
    daily threshold at the overtime multiplier."""
    hours_so_far = 0.0
    pay = 0.0
    for e in sorted(entries, key=lambda x: x.started_at):
        if e.state == "void" or not e.duration_sec:
            continue
        h = e.duration_sec / 3600
        straight = max(0.0, min(h, OT_DAILY_THRESHOLD_H - hours_so_far))
        rate = e.cost_rate or 0
        pay += straight * rate + (h - straight) * rate * OT_MULTIPLIER
        hours_so_far += h
    return Decimal(str(q2(pay)))


@router.get("/timesheet")
async def timesheet(
    employee_id: Optional[str] = None,
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    emp_id = employee_id or current.id
    if emp_id != current.id and not await _can_manage(db, current):
        raise HTTPException(403, "Not allowed")
    start = _udt(date_from, "date_from")
    end = _udt(date_to, "date_to") + timedelta(days=1)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == emp_id,
        TimeEntry.state != "void", TimeEntry.started_at >= start,
        TimeEntry.started_at < end).order_by(TimeEntry.started_at))).scalars().all()
    total = sum(e.duration_sec for e in rows)
    job = sum(e.duration_sec for e in rows if e.order_id)
    billable = sum(e.duration_sec for e in rows if e.billable)
    cost = sum(e.duration_sec / 3600 * (e.cost_rate or 0) for e in rows)
    bill = sum(e.duration_sec / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    straight_h, ot_h = _overtime_split(rows)
    approved = [e for e in rows if e.state == "approved"]
    nos = {}
    for e in rows:
        if e.order_id and e.order_id not in nos:
            nos[e.order_id] = await _order_no(db, e.order_id)
    return {
        "employee_id": emp_id, "date_from": date_from, "date_to": date_to,
        "total_sec": total, "total_hours": q2(total / 3600),
        "job_sec": job, "nonjob_sec": total - job,
        "billable_sec": billable, "nonbillable_sec": total - billable,
        "straight_hours": straight_h, "overtime_hours": ot_h,
        "labor_cost": q2(cost), "labor_billable": q2(bill),
        "approved_count": len(approved), "entry_count": len(rows),
        "utilization": round(billable / total, 3) if total else 0,
        "entries": [_ser(e, nos.get(e.order_id), None) for e in rows],
    }


class TimesheetApproveIn(BaseModel):
    employee_id: str
    date_from: str
    date_to: str


@router.post("/timesheet/approve")
async def approve_timesheet(
    data: TimesheetApproveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Approve (lock) every stopped entry in the window and post one hourly-wage
    ledger entry so the payroll run picks it up. Idempotent per employee+window."""
    start = _udt(data.date_from, "date_from")
    end = _udt(data.date_to, "date_to") + timedelta(days=1)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == data.employee_id,
        TimeEntry.state == "stopped", TimeEntry.started_at >= start,
        TimeEntry.started_at < end))).scalars().all()
    if not rows:
        raise HTTPException(404, "No stopped entries to approve in that window")
    await assert_not_self(db, current, data.employee_id, "approve your own timesheet")
    # Price only the entries approved now, one ledger row per day worked. Re-pricing every
    # approved entry in the window paid hours again whenever a wider window was approved
    # later, and a window spanning two months was counted in both months' runs.
    days = sorted({e.started_at.date() for e in rows})
    already = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == data.employee_id,
        TimeEntry.state == "approved", TimeEntry.started_at >= start,
        TimeEntry.started_at < end))).scalars().all()
    for e in rows:
        e.state = "approved"
        e.approved_by_id = current.id
    from backend.services.payroll import CommissionEngine
    eng = CommissionEngine(db, current.org_id)
    batch = hashlib.sha256(",".join(sorted(e.id for e in rows)).encode()).hexdigest()[:16]
    straight_h = ot_h = 0.0
    total = Decimal("0")
    posted = False
    for day in days:
        before = [e for e in already if e.started_at.date() == day]
        after = before + [e for e in rows if e.started_at.date() == day]
        s_before, o_before = _overtime_split(before)
        s_after, o_after = _overtime_split(after)
        straight_h += s_after - s_before
        ot_h += o_after - o_before
        wage = _day_wage(after) - _day_wage(before)
        if wage <= 0:
            continue
        total += wage
        posted = await eng._post(
            employee_id=data.employee_id, entry_type="hourly_wage", amount=wage, event=None,
            rule_id=None, ref_type="timesheet", ref_id=f"{data.employee_id}:{day}:{day}",
            idempotency_key=f"hourly:{data.employee_id}:{day}:{batch}",
            note=f"Hourly wages {day} ({q4(s_after - s_before)}h + {q4(o_after - o_before)}h OT)") or posted
    straight_h, ot_h, wage = q4(straight_h), q4(ot_h), total
    await db.commit()
    return {"ok": True, "approved": len(rows), "straight_hours": straight_h,
            "overtime_hours": ot_h, "wage_posted": float(wage), "ledger_posted": posted}


# ── work-order rollup + billing ──────────────────────────────────────────────
@router.get("/order/{order_id}")
async def order_time(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    o = await _resolve_order(db, current.org_id, order_id)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.order_id == order_id,
        TimeEntry.state != "void").order_by(TimeEntry.started_at))).scalars().all()
    names = await _emp_map(db, current.org_id)
    tracked = sum(_elapsed_sec(e) for e in rows)
    billable = sum(_elapsed_sec(e) for e in rows if e.billable)
    cost = sum(_elapsed_sec(e) / 3600 * (e.cost_rate or 0) for e in rows)
    bill = sum(_elapsed_sec(e) / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    return {
        "order_id": order_id, "order_number": o.order_number,
        "scheduled_min": o.duration_min or 0,
        "tracked_sec": tracked, "tracked_min": round(tracked / 60, 1),
        "billable_sec": billable, "billable_min": round(billable / 60, 1),
        "billed_labour_total": o.labour_total or 0,
        "labor_cost": q2(cost), "labor_billable": q2(bill),
        "entries": [_ser(e, o.order_number, names.get(e.employee_id)) for e in rows],
    }


class BillIn(BaseModel):
    rate: Optional[float] = None       # override billable rate
    round_to_min: int = 0              # 0 = no rounding, e.g. 15 = round up to 15-min


@router.post("/order/{order_id}/bill")
async def bill_tracked(
    order_id: str, data: BillIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Convert tracked billable time into a labor line on the order."""
    from backend.models import OrderLine
    o = await _resolve_order(db, current.org_id, order_id)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.order_id == order_id,
        TimeEntry.state != "void", TimeEntry.billable == True))).scalars().all()  # noqa: E712
    if not rows:
        raise HTTPException(404, "No billable tracked time on this order")
    total_sec = sum(_elapsed_sec(e) for e in rows)
    minutes = total_sec / 60
    if data.round_to_min and data.round_to_min > 0:
        import math
        minutes = math.ceil(minutes / data.round_to_min) * data.round_to_min
    hours = q2(minutes / 60)
    rate = data.rate if data.rate is not None else (rows[0].billable_rate or 0)
    amount = q2(hours * rate)
    line = OrderLine(order_id=order_id, line_type="labour", kind="service",
                     description=f"Tracked labor ({hours}h)", qty=hours,
                     unit_price=rate, total=amount)
    db.add(line)
    o.labour_total = (o.labour_total or 0) + amount
    o.total = (o.total or 0) + amount
    await db.commit()
    return {"ok": True, "hours": hours, "rate": rate, "amount": amount,
            "labour_total": o.labour_total}
