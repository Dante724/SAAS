"""Work-order time clock.

Primary unit is time tracked against a work order (order_id). Non-job time is an
entry with order_id = null. Exactly one running entry per employee; starting a new
timer auto-stops the previous (a 'switch'). Stopped entries roll up onto the order
(scheduled vs tracked vs billed) and, once a timesheet is approved, post hourly
wages into the payroll ledger so the existing payroll run picks them up.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Order, TimeEntry, TimeEntryAudit
from backend.services.dates import (
    day_end_utc,
    day_start_utc,
    now_utc,
    parse_dt,
    shop_timezone,
    shop_tzname,
    today_in,
    utc_iso,
    utc_to_wall,
)


def _udt(value, field: str):
    """Parse a user-supplied ISO datetime (400 on bad input) and normalize any
    timezone-aware value to naive UTC, so comparisons against naive
    now_utc()/stored values don't raise TypeError (500)."""
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
    end = s.ended_at or now_utc()
    return max(int((end - s.started_at).total_seconds()), 0)


def _ser_shift(s) -> dict:
    return {"id": s.id, "location_id": s.location_id,
            "started_at": utc_iso(s.started_at),
            "ended_at": utc_iso(s.ended_at),
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
    # Lock the employee row for this transaction so a double tap serialises: the second
    # request waits here, then sees the first shift and gets 409 instead of a second open shift.
    # FOR NO KEY UPDATE: serialises clock-ins without blocking rows that only FK to employees.
    await db.execute(select(Employee.id).where(Employee.id == current.id)
                     .with_for_update(key_share=True))
    if await _open_shift(db, current.id):
        raise HTTPException(409, "You are already clocked in")
    loc_id = current.location_id
    if not loc_id:
        loc_id = (await db.execute(select(Location.id).where(
            Location.org_id == current.org_id, Location.active == True)  # noqa: E712
            .order_by(Location.id))).scalars().first()
    if not loc_id:
        raise HTTPException(400, "No location available to clock in to")
    s = Shift(employee_id=current.id, location_id=loc_id, started_at=now_utc())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _ser_shift(s)


@router.post("/shift/clock-out")
async def shift_clock_out(db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    from backend.models import Shift
    s = await _open_shift(db, current.id)
    if not s:
        raise HTTPException(409, "You are not clocked in")
    # Close every open shift, not just the newest: one leaked open (e.g. by an older double
    # tap) would otherwise accrue hours forever. Only the newest is worked time; a leaked
    # duplicate closes at its own start with 0 hours, so payroll doesn't pay the time twice.
    for o in (await db.execute(select(Shift).where(
            Shift.employee_id == current.id, Shift.ended_at.is_(None)))).scalars().all():
        if o is s:
            o.ended_at = now_utc()
            o.hours_worked = q2(_shift_elapsed(o) / 3600.0)
        else:
            o.ended_at = o.started_at
            o.hours_worked = 0
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
        "started_at": utc_iso(s.started_at),
        "elapsed_sec": _shift_elapsed(s),
    } for s in open_shifts]
    # running job/task timers across the org
    running = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.state == "running",
        TimeEntry.ended_at.is_(None)).order_by(TimeEntry.started_at))).scalars().all()
    order_nos = await _order_nos(db, current.org_id, [e.order_id for e in running])
    timers = []
    for e in running:
        timers.append({
            "employee_id": e.employee_id, "employee_name": names.get(e.employee_id, "—"),
            "order_number": order_nos.get(e.order_id),
            "category": e.category, "location_name": locs.get(e.location_id, "—"),
            "started_at": utc_iso(e.started_at),
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
    now = now or now_utc()
    return max(0, int((now - e.started_at).total_seconds()))


def _ser(e: TimeEntry, order_no: Optional[str] = None, emp_name: Optional[str] = None, task_title: Optional[str] = None,
         *, show_pay: bool = True) -> dict:
    sec = _elapsed_sec(e)
    out = {
        "id": e.id, "employee_id": e.employee_id, "employee_name": emp_name,
        "order_id": e.order_id, "order_line_id": getattr(e, "order_line_id", None), "task_id": e.task_id, "order_number": order_no, "task_title": task_title, "category": e.category,
        "started_at": utc_iso(e.started_at),
        "ended_at": utc_iso(e.ended_at),
        "duration_sec": sec, "duration_min": round(sec / 60, 1),
        "running": e.ended_at is None and e.state == "running",
        "billable": e.billable, "cost_rate": e.cost_rate, "billable_rate": e.billable_rate,
        "labor_cost": q2(sec / 3600 * (e.cost_rate or 0)),
        "labor_billable": q2(sec / 3600 * (e.billable_rate or 0)) if e.billable else 0.0,
        "note": e.note, "source": e.source, "state": e.state, "needs_review": e.needs_review,
        "disputed": getattr(e, "disputed", False), "dispute_note": getattr(e, "dispute_note", None),
    }
    if not show_pay:                 # someone else's pay rate the caller may not see
        out["cost_rate"] = out["labor_cost"] = None
    return out


async def _emp_map(db: AsyncSession, org_id: str) -> dict:
    return {e.id: e.name for e in (await db.execute(
        select(Employee).where(Employee.org_id == org_id))).scalars().all()}


async def _order_no(db: AsyncSession, order_id: Optional[str]) -> Optional[str]:
    if not order_id:
        return None
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    return o.order_number if o else None


async def _order_nos(db: AsyncSession, org_id: str, order_ids) -> dict:
    """{order_id: order_number} for a whole list in one query (lists used _order_no per row)."""
    from backend.services.batching import names_by_id
    return await names_by_id(db, Order.id, Order.order_number, order_ids,
                             org_col=Order.org_id, org_id=org_id)


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
    when = when or now_utc()
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
        from backend.services.task_visibility import visibility_conds
        # tk02: a task you can't see is a 404 here too (its title would show on the time entry)
        task = (await db.execute(select(Task).where(
            Task.id == task_id, Task.org_id == current.org_id,
            *await visibility_conds(db, current)))).scalar_one_or_none()
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
        started_at=now_utc(), billable=is_labour,
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
    if not await _can_manage_emp(db, current, e.employee_id):
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
        category="job", started_at=now_utc(), billable=True,
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
        async with db.begin_nested():     # a refused invoice change undoes the line change too
            if not e.order_id:
                return
            from backend.models import Order, OrderLine
            line = (await db.execute(select(OrderLine).where(
                OrderLine.order_id == e.order_id, OrderLine.line_type == "labour",
                OrderLine.ref_id == e.id))).scalar_one_or_none()

            if line:
                from backend.services.invoice_money import locking_invoice
                if await locking_invoice(db, line.id):
                    return       # billed on a sent or paid invoice: the line stays as it was invoiced
            hours = q2((e.duration_sec or 0) / 3600.0)
            enabled = await _clock_labour_billing_on(db, e.org_id)
            # remove the line when: caller asked, entry is void, billing is off, or no time
            if remove or e.state == "void" or not enabled or hours <= 0 or not e.billable:
                if line:
                    from backend.services.invoice_money import release_line
                    await release_line(db, line.id)   # off any draft invoice that lists it
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
    else:
        from backend.services.payroll_views import payroll_scope_ids
        scope = await payroll_scope_ids(db, current)
        if scope is not None:           # E3: only the people the role's payroll scope covers
            q = q.where(TimeEntry.employee_id.in_(scope | {current.id}))
        if employee_id:
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


async def _can_manage_emp(db: AsyncSession, current: Employee, employee_id: Optional[str]) -> bool:
    """May the caller manage this person's time (and see its cost)? Their own always; anyone
    else's only with payroll rights AND inside the role's payroll scope (E3)."""
    if employee_id == current.id:
        return True
    if not await _can_manage(db, current):
        return False
    from backend.services.payroll_views import in_scope, payroll_scope_ids
    return in_scope(await payroll_scope_ids(db, current), employee_id)


async def _assert_manage_emp(db: AsyncSession, current: Employee, employee_id: Optional[str]) -> None:
    if not await _can_manage_emp(db, current, employee_id):
        raise HTTPException(403, "This person's time and pay are outside what your role can manage")


async def _pay_visible(db: AsyncSession, current: Employee):
    """Predicate: may the caller see this employee's pay rate? Own always; others with
    can_view_payroll inside the payroll scope."""
    from backend.services.auth import has_perm
    from backend.services.payroll_views import in_scope, payroll_scope_ids
    see = await has_perm(db, current, "can_view_payroll")
    scope = await payroll_scope_ids(db, current)
    return lambda emp_id: emp_id == current.id or (see and in_scope(scope, emp_id))


async def _lock_employee(db: AsyncSession, employee_id: str) -> None:
    """Serialise changes to one person's time and wages: a second request waits here, then sees
    what the first one saved. FOR NO KEY UPDATE, as in shift_clock_in (rows that only reference the
    employee are not blocked). SQLite ignores it and serialises writes anyway."""
    await db.execute(select(Employee.id).where(Employee.id == employee_id)
                     .with_for_update(key_share=True))


async def _assert_no_overlap(db: AsyncSession, org_id: str, employee_id: str, start: datetime,
                             end: datetime, *, exclude_id: Optional[str] = None) -> None:
    """409 when [start, end) overlaps another of this person's entries (a running timer counts up
    to now). Every way of writing time goes through here (manual create, edit, the v1 API), after
    the employee lock, so two writes can't both pass the check: overlapping hours are paid twice."""
    await _lock_employee(db, employee_id)
    q = select(TimeEntry).where(
        TimeEntry.org_id == org_id, TimeEntry.employee_id == employee_id,
        TimeEntry.state != "void", TimeEntry.started_at < end)
    if exclude_id:
        q = q.where(TimeEntry.id != exclude_id)
    for o in (await db.execute(q)).scalars().all():
        if start < (o.ended_at or now_utc()):
            raise HTTPException(409, "That overlaps an existing time entry")


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
    if not await _can_manage_emp(db, current, emp_id):
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
    await _assert_no_overlap(db, current.org_id, emp_id, start, end)
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
    if not await _can_manage_emp(db, current, e.employee_id):
        raise HTTPException(403, "Not allowed")
    if e.state == "approved":
        raise HTTPException(409, "Entry is approved and locked; reopen it first")
    if e.state == "running":
        raise HTTPException(409, "Stop the timer before editing")
    # 144ay — record each field change to the append-only audit trail (old → new).
    times_changed = False
    if data.started_at:
        nv = _udt(data.started_at, "started_at")
        if nv != e.started_at:
            _audit(db, e, "edit", current.id, field="started_at", old=e.started_at, new=nv)
            e.started_at = nv
            times_changed = True
    if data.ended_at:
        nv = _udt(data.ended_at, "ended_at")
        if nv != e.ended_at:
            _audit(db, e, "edit", current.id, field="ended_at", old=e.ended_at, new=nv)
            e.ended_at = nv
            times_changed = True
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
    if times_changed and e.ended_at:
        await _assert_no_overlap(db, current.org_id, e.employee_id, e.started_at, e.ended_at, exclude_id=e.id)
        # LAB-04: someone without payroll rights may still fix the hours of their own entry (a
        # forgotten clock-out), but the entry is flagged so whoever approves it looks at it.
        if e.employee_id == current.id and not await _can_manage(db, current):
            e.needs_review = True
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
    if not await _can_manage_emp(db, current, e.employee_id):
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
    await _assert_manage_emp(db, current, e.employee_id)
    if e.state == "running":
        raise HTTPException(409, "Stop the timer before approving")
    if e.state == "void":
        raise HTTPException(409, "Cannot approve a voided entry")
    if e.state == "approved":
        return {"ok": True, "state": e.state, "wage_posted": 0.0}
    await assert_not_self(db, current, e.employee_id, "approve your own time")
    await _lock_employee(db, e.employee_id)
    await db.refresh(e)
    if e.state != "stopped":
        raise HTTPException(409, "This entry changed meanwhile; reload and try again")
    _audit(db, e, "approve", current.id, field="state", old=e.state, new="approved")
    e.state = "approved"
    e.approved_by_id = current.id
    await db.flush()
    # LAB-03: approving one entry pays it, the same way a timesheet approval does (it used to only
    # lock it, and the next timesheet approval then counted it as already paid).
    wage = Decimal("0")
    emp = (await db.execute(select(Employee).where(
        Employee.id == e.employee_id, Employee.org_id == current.org_id))).scalar_one()
    if emp.hourly_basis != "attendance":          # one clock pays: attendance staff are paid from shifts
        tz = await shop_timezone(db, emp)
        wage, _ = await _sync_day_wages(db, current.org_id, emp.id, _shop_day(e, tz), tz,
                                        fresh=frozenset({e.id}))
    await db.commit()
    return {"ok": True, "state": "approved", "wage_posted": float(wage)}


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
    if not await _can_manage_emp(db, current, e.employee_id):
        raise HTTPException(403, "Not allowed")
    names = await _emp_map(db, current.org_id)
    rows = (await db.execute(select(TimeEntryAudit).where(
        TimeEntryAudit.time_entry_id == eid).order_by(TimeEntryAudit.at.asc()))).scalars().all()
    return {"items": [{"action": a.action, "field": a.field, "old": a.old_value, "new": a.new_value,
                       "actor": names.get(a.actor_id, "—"), "at": utc_iso(a.at)} for a in rows]}


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
    await _assert_manage_emp(db, current, e.employee_id)
    await _lock_employee(db, e.employee_id)
    await db.refresh(e)
    if e.state != "approved":
        raise HTTPException(409, "Only approved entries can be reopened")
    # LAB-01: reopening takes the entry's wage back with a reversing ledger row (the ledger is
    # append-only), and re-prices the rest of that day, whose overtime may move. Approving the
    # corrected entry then pays it once.
    tz = await _emp_tz(db, current.org_id, e.employee_id)
    reversed_ = await _reverse_legacy_wage(db, e, tz)
    _audit(db, e, "reopen", current.id, field="state", old="approved", new="stopped")
    e.state = "stopped"
    e.approved_by_id = None
    await db.flush()
    amt, _ = await _sync_day_wages(db, current.org_id, e.employee_id, _shop_day(e, tz), tz)
    await db.commit()
    return {"ok": True, "state": "stopped", "wage_reversed": float(reversed_ - amt)}


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
    if not await _can_manage_emp(db, current, e.employee_id):
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
    await _assert_manage_emp(db, current, e.employee_id)
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
    tz = await shop_tzname(db, current.org_id)   # "today"/"this week" on the shop's clock
    today = today_in(tz)
    week_start = day_start_utc(today - timedelta(days=today.weekday()), tz)
    day_start = day_start_utc(today, tz)
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

    # open service/labour lines assigned to me, with booked + measured actual. Only the tech's
    # own lines on open jobs are read, and the follow-up queries join on that set instead of
    # binding an id per order: this used to put every open order in the shop into one IN list,
    # which failed with "too many SQL variables" at 33,208 orders.
    my_line_q = (select(OrderLine).join(Order, Order.id == OrderLine.order_id).where(
        OrderLine.technician_id == current.id, Order.org_id == current.org_id,
        Order.is_estimate == False, Order.archived == False,   # noqa: E712
        Order.closed_at.is_(None)))
    lines = (await db.execute(my_line_q)).scalars().all()
    my_line_ids = select(my_line_q.subquery().c.id)
    open_orders = (await db.execute(select(Order).where(
        Order.id.in_(select(my_line_q.subquery().c.order_id))))).scalars().all()
    open_ids = {o.id: o.order_number for o in open_orders}
    # measured actual seconds per line (job time, any tech) via shared helper source
    line_secs: dict = {}
    for e in (await db.execute(select(TimeEntry).where(
            TimeEntry.org_id == current.org_id,
            TimeEntry.order_line_id.in_(my_line_ids)))).scalars().all():
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
        from sqlalchemy import case
        from backend.models import OrderChecklistItem, OrderStatus, Client, Device
        from backend.routers.statuses import ensure_seeded, is_transition_allowed
        await ensure_seeded(db, current.org_id)
        wf = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == current.org_id, OrderStatus.record_type == "work_order",
            OrderStatus.active == True).order_by(OrderStatus.sort_order))).scalars().all()  # noqa: E712
        by_key = {s.key: s for s in wf}
        order_by_id = {o.id: o for o in open_orders}
        mine_orders = select(my_line_q.subquery().c.order_id)
        # checklist progress per order (done/total)
        prog = {oid: (int(d or 0), int(t)) for oid, d, t in (await db.execute(
            select(OrderChecklistItem.order_id,
                   func.sum(case((OrderChecklistItem.done == True, 1), else_=0)), func.count())  # noqa: E712
            .where(OrderChecklistItem.order_id.in_(mine_orders))
            .group_by(OrderChecklistItem.order_id))).all()}
        from backend.services.batching import names_by_id
        mine = [order_by_id[oid] for oid in my_order_ids if oid in order_by_id]
        clients = await names_by_id(db, Client.id, Client.name, [o.client_id for o in mine],
                                    org_col=Client.org_id, org_id=current.org_id)
        dev_ids = {o.device_id for o in mine if o.device_id}
        devices = {d.id: d for d in (await db.execute(select(Device).where(
            Device.org_id == current.org_id, Device.id.in_(dev_ids)))).scalars().all()} if dev_ids else {}
        for oid in my_order_ids:
            o = order_by_id.get(oid)
            if not o:
                continue
            cur = by_key.get(o.status)
            nxt = None
            if cur and not cur.is_terminal:
                # B11: the next step is the next main-path status this role may move to. A
                # "pending" status (Waiting parts…) is a detour, not the next step for every job.
                later = [s for s in wf if s.sort_order > cur.sort_order]
                ok = [s for s in later if await is_transition_allowed(
                    db, current.org_id, current.role_id, o.status, s.key)]
                nxt = next((s for s in ok if not s.is_terminal and s.status_group != "pending"), None) \
                    or next((s for s in ok if not s.is_terminal), None) or next(iter(ok), None)
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
def _shop_day(e: TimeEntry, tz: Optional[str]) -> date:
    """The day an entry counts on: the shop's calendar day it started on, not UTC's (RPT-01)."""
    return utc_to_wall(e.started_at, tz).date()


def _window(date_from: str, date_to: str, tz: Optional[str]) -> tuple[datetime, datetime]:
    """A timesheet window as naive UTC [start, end). A date-only value (what the screens send) is a
    whole day on the shop's clock, as in the reports (PR #200); a value with a time is taken as given."""
    start, end = _udt(date_from, "date_from"), _udt(date_to, "date_to")
    if len(date_from.strip()) == 10:
        start = day_start_utc(start.date(), tz)
    if len(date_to.strip()) == 10:
        end = day_end_utc(end.date(), tz)
    return start, end


async def _emp_tz(db: AsyncSession, org_id: str, employee_id: str) -> str:
    """The clock this person's days are counted on: their branch's zone, else the shop's."""
    emp = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.org_id == org_id))).scalar_one_or_none()
    return await shop_timezone(db, emp) if emp else await shop_tzname(db, org_id)


def _overtime_split(entries: list[TimeEntry], tz: Optional[str] = None) -> tuple[float, float]:
    """Return (straight_hours, overtime_hours) by daily threshold, per shop day."""
    by_day: dict[date, float] = {}
    for e in entries:
        if e.state == "void" or not e.duration_sec:
            continue
        d = _shop_day(e, tz)
        by_day[d] = by_day.get(d, 0) + e.duration_sec / 3600
    straight = overtime = 0.0
    for _, hrs in by_day.items():
        ot = max(0.0, hrs - OT_DAILY_THRESHOLD_H)
        straight += hrs - ot
        overtime += ot
    return q4(straight), q4(overtime)


def _day_shares(entries: list[TimeEntry]) -> dict[str, Decimal]:
    """Each entry's pay within one day: entries in time order, each at its own snapshotted rate;
    hours past the daily threshold at the overtime multiplier. The shares add up to the day's pay."""
    hours_so_far = 0.0
    out: dict[str, Decimal] = {}
    for e in sorted(entries, key=lambda x: (x.started_at, x.id)):
        if e.state == "void" or not e.duration_sec:
            continue
        h = e.duration_sec / 3600
        straight = max(0.0, min(h, OT_DAILY_THRESHOLD_H - hours_so_far))
        rate = e.cost_rate or 0
        out[e.id] = Decimal(str(q2(straight * rate + (h - straight) * rate * OT_MULTIPLIER)))
        hours_so_far += h
    return out


def _wage_key(entry_id: str) -> str:
    from backend.models import EmployeeBalanceLedger
    return EmployeeBalanceLedger.normalize_key(f"hourly:{entry_id}")


async def _sync_day_wages(db: AsyncSession, org_id: str, employee_id: str, day: date, tz: Optional[str],
                          *, fresh: frozenset = frozenset()) -> tuple[Decimal, int]:
    """Make the wage ledger for one person's shop day match what their approved entries earn.

    Wages are kept per time entry: every ledger row for an entry carries the accrual key
    `hourly:<entry>`, so the group's sum is what that entry has been paid. An approved entry should
    hold its share of the day (_day_shares, which places overtime); a reopened one nothing. The
    difference is posted as a new row, never by changing old ones (the ledger is append-only), so a
    reopen reverses the wage and approving again pays it once. A first payment is dated to the day
    worked; a correction to today, so a correction to a period already run reaches the next run.
    Entries approved before wages were kept per entry have no rows and are left alone unless they are
    being approved now (`fresh`). Call with the employee row locked. Returns (amount, rows) posted."""
    from backend.models import EmployeeBalanceLedger as L
    from backend.services.payroll import CommissionEngine, _money
    entries = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org_id, TimeEntry.employee_id == employee_id, TimeEntry.state != "void",
        TimeEntry.started_at >= day_start_utc(day, tz),
        TimeEntry.started_at < day_end_utc(day, tz)))).scalars().all()
    shares = _day_shares([e for e in entries if e.state == "approved"])
    keys = {e.id: _wage_key(e.id) for e in entries}
    groups = {k: (_money(net), int(n)) for k, net, n in (await db.execute(
        select(L.accrual_key, func.sum(L.amount), func.count(L.id)).where(
            L.org_id == org_id, L.accrual_key.in_(list(keys.values())))
        .group_by(L.accrual_key))).all()} if keys else {}
    eng = CommissionEngine(db, org_id)
    today = today_in(tz)
    amount, rows = Decimal("0"), 0
    for e in sorted(entries, key=lambda x: (x.started_at, x.id)):
        key = keys[e.id]
        net, n = groups.get(key, (Decimal("0"), 0))
        if n == 0 and e.id not in fresh:
            continue
        delta = _money(shares.get(e.id, Decimal("0")) - net)
        if delta == 0:
            continue
        on = day if n == 0 else today
        note = (f"Hourly wages {day} ({q2((e.duration_sec or 0) / 3600)}h)" if n == 0 else
                f"Hourly wages {day}: " + ("entry reopened" if e.state != "approved" else "day recalculated"))
        if await eng._post(employee_id=employee_id, entry_type="hourly_wage", amount=delta, event=None,
                           rule_id=None, ref_type="time_entry", ref_id=f"{e.id}:{on}:{on}",
                           idempotency_key=key if n == 0 else f"{key}@{n}", accrual_key=key, note=note):
            amount += delta
            rows += 1
    return amount, rows


async def _reverse_legacy_wage(db: AsyncSession, e: TimeEntry, tz: Optional[str]) -> Decimal:
    """Before wages were kept per entry, a timesheet approval paid one row per employee and UTC day
    (ref_id `<employee>:<day>:<day>`, no accrual key). Reopening an entry that such a row paid takes
    its share off that day's pay, so approving it again pays the corrected wage once, not on top.
    Never takes more than is still standing for that day. Returns the amount reversed (positive).

    Only an entry the row actually paid is reversed. Under the old code the two ways to approve left
    different traces: approve_entry wrote an "approve" row to the entry's audit trail (144ay) and
    paid nothing (LAB-03), while a timesheet approval wrote no per-entry audit row and paid the day.
    So an "approve" audit row since the entry's last reopen means it was approved on its own and the
    day's row paid only the other entries: nothing is reversed, and approving it again pays it for
    the first time (which also fixes that old underpay)."""
    from sqlalchemy import and_, or_

    from backend.models import EmployeeBalanceLedger as L
    from backend.services.payroll import CommissionEngine, _money
    if (await db.execute(select(L.id).where(
            L.org_id == e.org_id, L.accrual_key == _wage_key(e.id)).limit(1))).first():
        return Decimal("0")          # paid per entry: _sync_day_wages reverses it
    trail = (await db.execute(select(TimeEntryAudit.action).where(
        TimeEntryAudit.org_id == e.org_id, TimeEntryAudit.time_entry_id == e.id,
        TimeEntryAudit.action.in_(("approve", "reopen")))
        .order_by(TimeEntryAudit.at, TimeEntryAudit.id))).scalars().all()
    since_reopen = trail[len(trail) - trail[::-1].index("reopen"):] if "reopen" in trail else trail
    if "approve" in since_reopen:
        return Decimal("0")          # approved on its own: no timesheet row paid it
    uday = e.started_at.date()
    lkey = L.normalize_key(f"hourly-legacy:{e.employee_id}:{uday}")
    standing = _money((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == e.org_id, L.employee_id == e.employee_id, L.entry_type == "hourly_wage",
        or_(and_(L.ref_type == "timesheet", L.ref_id == f"{e.employee_id}:{uday}:{uday}",
                 L.accrual_key.is_(None)),
            L.accrual_key == lkey)))).scalar())
    if standing <= 0:
        return Decimal("0")          # never paid (approved on its own before LAB-03): nothing to take back
    lo = datetime.combine(uday, datetime.min.time())
    same_day = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == e.org_id, TimeEntry.employee_id == e.employee_id,
        TimeEntry.state == "approved", TimeEntry.started_at >= lo,
        TimeEntry.started_at < lo + timedelta(days=1)))).scalars().all()
    amt = min(_day_shares(same_day).get(e.id, Decimal("0")), standing)
    if amt <= 0:
        return Decimal("0")
    today = today_in(tz)
    posted = await CommissionEngine(db, e.org_id)._post(
        employee_id=e.employee_id, entry_type="hourly_wage", amount=-amt, event=None, rule_id=None,
        ref_type="timesheet", ref_id=f"{e.employee_id}:{today}:{today}",
        idempotency_key=f"hourly-legacy-reopen:{e.id}", accrual_key=lkey,
        note=f"Hourly wages {uday}: entry reopened")
    return amt if posted else Decimal("0")


@router.get("/timesheet")
async def timesheet(
    employee_id: Optional[str] = None,
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    emp_id = employee_id or current.id
    if not await _can_manage_emp(db, current, emp_id):
        raise HTTPException(403, "Not allowed")
    tz = await _emp_tz(db, current.org_id, emp_id)
    start, end = _window(date_from, date_to, tz)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == emp_id,
        TimeEntry.state != "void", TimeEntry.started_at >= start,
        TimeEntry.started_at < end).order_by(TimeEntry.started_at))).scalars().all()
    total = sum(e.duration_sec for e in rows)
    job = sum(e.duration_sec for e in rows if e.order_id)
    billable = sum(e.duration_sec for e in rows if e.billable)
    cost = sum(e.duration_sec / 3600 * (e.cost_rate or 0) for e in rows)
    bill = sum(e.duration_sec / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    straight_h, ot_h = _overtime_split(rows, tz)
    approved = [e for e in rows if e.state == "approved"]
    nos = await _order_nos(db, current.org_id, [e.order_id for e in rows])
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
    """Approve (lock) every stopped entry in the window and pay each one into the payroll ledger
    (_sync_day_wages). Days are the shop's days. Nothing is paid twice: an entry already paid is
    no longer 'stopped', and each entry's wage has its own key."""
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await assert_not_self(db, current, data.employee_id, "approve your own timesheet")
    await _assert_manage_emp(db, current, data.employee_id)
    tz = await shop_timezone(db, emp)
    start, end = _window(data.date_from, data.date_to, tz)
    await _lock_employee(db, emp.id)
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == data.employee_id,
        TimeEntry.state == "stopped", TimeEntry.started_at >= start,
        TimeEntry.started_at < end))).scalars().all()
    if not rows:
        raise HTTPException(404, "No stopped entries to approve in that window")
    days = sorted({_shop_day(e, tz) for e in rows})
    already = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.employee_id == data.employee_id,
        TimeEntry.state == "approved", TimeEntry.started_at >= day_start_utc(days[0], tz),
        TimeEntry.started_at < day_end_utc(days[-1], tz)))).scalars().all()
    s_before, o_before = _overtime_split(already, tz)
    s_after, o_after = _overtime_split(list(already) + list(rows), tz)
    for e in rows:
        e.state = "approved"
        e.approved_by_id = current.id
    from backend.services import audit
    if emp.hourly_basis == "attendance":
        # One clock pays. This employee's hours are paid from shop attendance, so approving
        # the labour timesheet locks it for job costing but posts no wage (else both pay).
        await audit.record(db, org_id=current.org_id, actor_id=current.id, action="payroll.timesheet_approve",
                           entity_type="employee", entity_id=data.employee_id,
                           diff={"window": [data.date_from, data.date_to], "entries": len(rows),
                                 "amount": 0, "paid_from": "attendance"})
        await db.commit()
        return {"ok": True, "approved": len(rows), "straight_hours": 0.0, "overtime_hours": 0.0,
                "wage_posted": 0.0, "ledger_posted": False, "paid_from": "attendance"}
    await db.flush()
    fresh = frozenset(e.id for e in rows)
    wage, posted = Decimal("0"), 0
    for day in days:
        amt, n = await _sync_day_wages(db, current.org_id, emp.id, day, tz, fresh=fresh)
        wage, posted = wage + amt, posted + n
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="payroll.timesheet_approve",
                       entity_type="employee", entity_id=data.employee_id,
                       diff={"window": [data.date_from, data.date_to], "entries": len(rows),
                             "amount": float(wage), "paid_from": "labour"})
    await db.commit()
    return {"ok": True, "approved": len(rows), "straight_hours": q4(s_after - s_before),
            "overtime_hours": q4(o_after - o_before), "wage_posted": float(wage), "ledger_posted": posted > 0}


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
    sees = await _pay_visible(db, current)
    tracked = sum(_elapsed_sec(e) for e in rows)
    billable = sum(_elapsed_sec(e) for e in rows if e.billable)
    cost = sum(_elapsed_sec(e) / 3600 * (e.cost_rate or 0) for e in rows)
    all_visible = all(sees(e.employee_id) for e in rows)
    bill = sum(_elapsed_sec(e) / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    return {
        "order_id": order_id, "order_number": o.order_number,
        "scheduled_min": o.duration_min or 0,
        "tracked_sec": tracked, "tracked_min": round(tracked / 60, 1),
        "billable_sec": billable, "billable_min": round(billable / 60, 1),
        "billed_labour_total": o.labour_total or 0,
        # the total reveals pay rates, so it shows only when every entry's rate is visible
        "labor_cost": q2(cost) if all_visible else None, "labor_billable": q2(bill),
        "entries": [_ser(e, o.order_number, names.get(e.employee_id), show_pay=sees(e.employee_id)) for e in rows],
    }


class BillIn(BaseModel):
    rate: Optional[float] = None       # ignored: the rate is the one snapshotted on each time entry (WO-02)
    round_to_min: int = Field(0, ge=0, le=240)   # 0 = no rounding, e.g. 15 = round up to 15-min


@router.post("/order/{order_id}/bill")
async def bill_tracked(
    order_id: str, data: BillIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Bill the job's tracked billable time that isn't on the job yet as one labour line.

    Gated like adding any line (WO-02): the job's line lock, the closed/locked-job guard and the
    custom-service right. The price is each entry's snapshotted billable rate, never one sent by the
    caller. Billed entries point at the new line (order_line_id), so a second call, a retry or a
    double tap bills nothing again. Time already on a line (attributed to one, or billed by the
    shop's automatic labour billing) is skipped. Running timers are left for later."""
    from backend.models import OrderLine
    from backend.routers.orders import (
        LineIn,
        _assert_line_rights,
        _audit_line,
        _auto_apply_tax,
        _calc_line,
        _guard_editable,
        _lock_for_lines,
        _log_amount_change,
        _order_perms,
        _recalc_order,
    )
    o = await _resolve_order(db, current.org_id, order_id)
    await _lock_for_lines(db, o)
    await _guard_editable(db, o, current)
    if await _clock_labour_billing_on(db, current.org_id):
        raise HTTPException(409, "This shop bills tracked time onto the job automatically")
    billable = (TimeEntry.org_id == current.org_id) & (TimeEntry.order_id == order_id) & \
               (TimeEntry.state != "void") & (TimeEntry.billable == True)  # noqa: E712
    auto_billed = select(OrderLine.ref_id).where(
        OrderLine.order_id == order_id, OrderLine.line_type == "labour", OrderLine.ref_id.isnot(None))
    rows = (await db.execute(select(TimeEntry).where(
        billable, TimeEntry.state.in_(("stopped", "approved")), TimeEntry.ended_at.isnot(None),
        TimeEntry.order_line_id.is_(None), TimeEntry.id.notin_(auto_billed))
        .execution_options(populate_existing=True))).scalars().all()
    rows = [e for e in rows if (e.duration_sec or 0) > 0]
    if not rows:
        if (await db.execute(select(TimeEntry.id).where(billable).limit(1))).first():
            raise HTTPException(409, "All tracked time on this job is already billed")
        raise HTTPException(404, "No billable tracked time on this order")
    total_sec = sum(e.duration_sec for e in rows)
    minutes = total_sec / 60
    if data.round_to_min:
        import math
        minutes = math.ceil(minutes / data.round_to_min) * data.round_to_min
    hours = q2(minutes / 60)
    if hours <= 0:
        # nothing is billed and the entries stay unbilled, so their time counts once there is more
        raise HTTPException(409, "Not enough tracked time to bill yet")
    # one line for every entry: the time-weighted average of their own billable and cost rates
    rate = q2(sum(e.duration_sec * float(e.billable_rate or 0) for e in rows) / total_sec)
    cost = q2(sum(e.duration_sec * float(e.cost_rate or 0) for e in rows) / total_sec)
    li = LineIn(line_type="labour", kind="service", description=f"Tracked labor ({hours}h)",
                qty=hours, unit_price=rate)
    await _assert_line_rights(db, current, li)          # a line from outside the catalog
    if (await _order_perms(db, current)).get("can_add_services") is False:
        raise HTTPException(403, "You don't have permission to add services")
    await _auto_apply_tax(db, current.org_id, li, o.location_id)
    prev_total = float(o.total or 0)
    line = OrderLine(order_id=order_id, line_type="labour", kind="service", description=li.description,
                     qty=hours, unit_price=rate, cost_price=cost, tax_id=li.tax_id, tax_rate=li.tax_rate,
                     tax_mode=li.tax_mode, total=_calc_line(li))
    db.add(line)
    await db.flush()
    for e in rows:
        e.order_line_id = line.id
    await _audit_line(db, current, order_id, line, "order.line_add")
    await _recalc_order(db, o)
    await _log_amount_change(db, current.org_id, o, prev_total)
    await db.commit()
    return {"ok": True, "line_id": line.id, "entries": len(rows), "hours": hours, "rate": rate,
            "amount": line.total, "labour_total": o.labour_total}
