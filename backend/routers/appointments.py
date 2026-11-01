"""Appointments — appointments. Service duration drives the slot; an employee
cannot hold two overlapping appointments (the schedule-gate intersection test)."""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.services.dates import parse_dt, parse_wall_dt
from backend.models import Appointment, Client, Service, Employee
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/appointments", tags=["appointments"])


def _ser(b: Appointment, names: dict) -> dict:
    end = b.scheduled_for + timedelta(minutes=b.duration_min or 60)
    return {"id": b.id, "client_id": b.client_id,
            "client_name": b.client_name or names.get(("c", b.client_id)),
            "service_id": b.service_id, "service_name": names.get(("s", b.service_id)),
            "employee_id": b.employee_id, "employee_name": names.get(("e", b.employee_id)),
            "scheduled_for": b.scheduled_for.isoformat(), "ends_at": end.isoformat(),
            "duration_min": b.duration_min, "status": b.status, "note": b.note,
            "order_id": b.order_id}   # set once converted to a WO/estimate — lets the UI show "converted"


async def _names(db, org_id):
    names = {}
    for c in (await db.execute(select(Client).where(Client.org_id == org_id))).scalars().all():
        names[("c", c.id)] = c.name
    for s in (await db.execute(select(Service).where(Service.org_id == org_id))).scalars().all():
        names[("s", s.id)] = s.name
    for e in (await db.execute(select(Employee).where(Employee.org_id == org_id))).scalars().all():
        names[("e", e.id)] = e.name
    return names


@router.get("")
async def list_appointments(
    date_str: Optional[str] = None,
    unassigned: bool = False,          # 144dr: only appointments with no assignee
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Appointment).where(Appointment.org_id == current.org_id)
    if unassigned:
        q = q.where(Appointment.employee_id.is_(None))
    if date_str:
        day = parse_dt(date_str, "date_str")
        q = q.where(Appointment.scheduled_for >= day,
                    Appointment.scheduled_for < day + timedelta(days=1))
    rows = (await db.execute(q.order_by(Appointment.scheduled_for))).scalars().all()
    names = await _names(db, current.org_id)
    return {"items": [_ser(b, names) for b in rows]}


class AppointmentIn(BaseModel):
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    service_id: Optional[str] = None
    employee_id: Optional[str] = None   # 144dr: leave empty to create an unassigned appointment
    scheduled_for: str  # ISO datetime
    duration_min: Optional[int] = None  # default: the service's duration, else 60
    note: Optional[str] = None


@router.post("", status_code=201)
async def create_appointment(
    data: AppointmentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    start = await parse_wall_dt(db, current.org_id, current.location_id, data.scheduled_for, "scheduled_for")
    dur = data.duration_min
    if not dur and data.service_id:
        svc = (await db.execute(select(Service).where(Service.id == data.service_id))).scalar_one_or_none()
        dur = getattr(svc, "duration_min", None) if svc else None
    dur = dur or 60
    end = start + timedelta(minutes=dur)
    # Overlap guard only applies once an appointment has an assignee (unassigned
    # appointments are just requests parked for later distribution — 144dr).
    if data.employee_id:
        others = (await db.execute(select(Appointment).where(and_(
            Appointment.org_id == current.org_id, Appointment.employee_id == data.employee_id,
            Appointment.status == "booked")))).scalars().all()
        for o in others:
            o_end = o.scheduled_for + timedelta(minutes=o.duration_min or 60)
            if start < o_end and o.scheduled_for < end:
                raise HTTPException(409,
                    f"That employee already has an appointment {o.scheduled_for.strftime('%H:%M')}-{o_end.strftime('%H:%M')}")
    b = Appointment(org_id=current.org_id, location_id=current.location_id,
                client_id=data.client_id, client_name=data.client_name,
                service_id=data.service_id, employee_id=data.employee_id,
                scheduled_for=start, duration_min=dur, note=data.note)
    db.add(b)
    from backend.routers.feed import log_system_event
    from sqlalchemy.exc import IntegrityError
    try:
        await db.flush()
        await log_system_event(db, current.org_id, "appointment", b.id,
                               f"Appointment at {start.strftime('%d %b %H:%M')} ({dur} min)")
        await db.commit()
    except IntegrityError:   # Build 144bz: race guard — that slot was just taken
        await db.rollback()
        raise HTTPException(409, "That time slot was just taken — please pick another")
    # Build 85: appointment confirmation notifications (module="appointment")
    try:
        from backend.services import notification_rules as _nr
        await _nr.evaluate_appointment(db, org_id=current.org_id, appointment_id=b.id, event="created")
    except Exception:
        pass
    names = await _names(db, current.org_id)
    return _ser(b, names)


class AppointmentStatusIn(BaseModel):
    status: Optional[str] = None            # booked|done|canceled|no_show
    # Build 144bq: drag-reschedule on the scheduler timeline
    scheduled_for: Optional[str] = None     # new ISO start
    employee_id: Optional[str] = None       # move to another technician
    duration_min: Optional[int] = None


@router.patch("/{bid}")
async def update_appointment(
    bid: str, data: AppointmentStatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    b = (await db.execute(select(Appointment).where(
        Appointment.id == bid, Appointment.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Appointment not found")

    # ── Build 144bq: reschedule (move in time and/or to another tech) ──
    if data.scheduled_for is not None or data.employee_id is not None or data.duration_min is not None:
        try:
            new_start = (await parse_wall_dt(db, current.org_id, b.location_id, data.scheduled_for, "scheduled_for")
                         if data.scheduled_for else b.scheduled_for)
        except ValueError:
            raise HTTPException(400, "Bad scheduled_for")
        new_emp = data.employee_id or b.employee_id
        new_dur = data.duration_min or b.duration_min or 60
        new_end = new_start + timedelta(minutes=new_dur)
        # the target technician can't be double-booked (ignore this same appointment)
        others = (await db.execute(select(Appointment).where(
            Appointment.org_id == current.org_id, Appointment.employee_id == new_emp,
            Appointment.status == "booked", Appointment.id != b.id))).scalars().all()
        for o in others:
            o_end = o.scheduled_for + timedelta(minutes=o.duration_min or 60)
            if new_start < o_end and o.scheduled_for < new_end:
                raise HTTPException(409,
                    f"That slot clashes with an appointment {o.scheduled_for.strftime('%H:%M')}-{o_end.strftime('%H:%M')}")
        b.scheduled_for = new_start
        b.employee_id = new_emp
        if data.duration_min:
            b.duration_min = new_dur
        await db.commit()
        # Build 144bs: tell the client their appointment moved (module="appointment", event="changed")
        try:
            from backend.services import notification_rules as _nr
            await _nr.evaluate_appointment(db, org_id=current.org_id, appointment_id=b.id, event="changed")
        except Exception:
            pass
        return {"ok": True, "status": b.status,
                "scheduled_for": b.scheduled_for.isoformat(), "employee_id": b.employee_id}

    if data.status is None:
        raise HTTPException(400, "Nothing to update")
    if data.status not in ("booked", "done", "canceled", "no_show"):
        raise HTTPException(400, "Invalid status")
    was = b.status
    b.status = data.status
    await db.commit()
    # Build 144bs: fire client-facing appointment rules on status change (cancel / no-show / done)
    if was != b.status:
        try:
            from backend.services import notification_rules as _nr
            await _nr.evaluate_appointment(db, org_id=current.org_id, appointment_id=b.id, event="status_changed")
        except Exception:
            pass
    # notify the booked technician when an appointment is cancelled
    if data.status == "canceled" and was != "canceled" and b.employee_id and b.employee_id != current.id:
        try:
            from backend.services.push import notify
            when = b.scheduled_for.strftime("%b %d %H:%M") if b.scheduled_for else ""
            await notify(db, org_id=current.org_id, employee_id=b.employee_id,
                         kind="appointment.cancelled", title="Appointment cancelled",
                         body=f"{b.client_name or 'Client'} · {when}".strip(" ·"),
                         deep_link="/tech#/dashboard")
        except Exception:
            pass
    return {"ok": True, "status": b.status}


# ── Build 85: availability, appointment → work order ─────────────────────────────
@router.get("/availability")
async def availability(
    date_str: str,
    duration_min: int = 60,
    employee_id: Optional[str] = None,
    service_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Free slots for an appointment on a day.

    Availability is driven by the employee's **work schedule** (build 73) — a day off
    means no slots at all — and both existing **appointments and scheduled work orders**
    count as busy, so the shop can't be double-booked from two different screens.
    """
    from backend.models import Order, WorkSchedule
    day = parse_dt(date_str, "date_str").date()

    if service_id:
        svc = (await db.execute(select(Service).where(
            Service.id == service_id, Service.org_id == current.org_id))).scalar_one_or_none()
        if svc and getattr(svc, "duration_min", None):
            duration_min = svc.duration_min
    if duration_min <= 0:
        raise HTTPException(400, "duration_min must be positive")

    win_start, win_end = 540, 1020          # 09:00–17:00 default
    if employee_id:
        sched = (await db.execute(select(WorkSchedule).where(
            WorkSchedule.org_id == current.org_id, WorkSchedule.employee_id == employee_id,
            WorkSchedule.day == day))).scalars().all()
        shift = next((s for s in sched if s.kind == "shift"), None)
        if any(s.kind in ("day_off", "vacation", "sick") for s in sched) and not shift:
            return {"date": date_str, "slots": [], "reason": "employee off this day"}
        if shift:
            win_start, win_end = shift.start_min, shift.end_min

    start = datetime(day.year, day.month, day.day, 0, 0)
    end = datetime(day.year, day.month, day.day, 23, 59, 59)

    def _m(dt):
        return dt.hour * 60 + dt.minute

    busy = []
    bq = select(Appointment).where(
        Appointment.org_id == current.org_id, Appointment.scheduled_for >= start,
        Appointment.scheduled_for <= end, Appointment.status.in_(["booked", "done"]))
    if employee_id:
        bq = bq.where(Appointment.employee_id == employee_id)
    for b in (await db.execute(bq)).scalars().all():
        s = _m(b.scheduled_for)
        busy.append((s, s + (b.duration_min or 60)))

    oq = select(Order).where(
        Order.org_id == current.org_id, Order.scheduled_for.isnot(None),
        Order.scheduled_for >= start, Order.scheduled_for <= end,
        Order.archived == False)  # noqa: E712
    if employee_id:
        oq = oq.where(Order.assigned_to_id == employee_id)
    for o in (await db.execute(oq)).scalars().all():
        s = _m(o.scheduled_for)
        e = _m(o.scheduled_to) if o.scheduled_to else s + (o.duration_min or 60)
        busy.append((s, e))

    busy.sort()
    slots, cursor = [], win_start
    for bs, be in busy:
        if bs > cursor and bs - cursor >= duration_min:
            slots.append((cursor, bs))
        cursor = max(cursor, be)
    if win_end - cursor >= duration_min:
        slots.append((cursor, win_end))

    def _hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    return {"date": date_str, "duration_min": duration_min,
            "slots": [{"start": _hhmm(s), "end": _hhmm(e),
                       "start_iso": datetime(day.year, day.month, day.day,
                                             s // 60, s % 60).isoformat()}
                      for s, e in slots]}


@router.post("/{bid}/to-order", status_code=201)
async def appointment_to_order(
    bid: str,
    record: str = "work_order",   # work_order | estimate — what the appointment becomes
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Turn an appointment into a work order or estimate — the client, the time and
    the service all carry across, and the appointment is marked done and linked to the job."""
    from backend.models import Order, OrderStatus
    from backend.services.numbering import next_number
    is_estimate = record == "estimate"
    rec_type = "estimate" if is_estimate else "work_order"
    if not is_estimate:
        from backend.services.billing import assert_can_add_job
        await assert_can_add_job(db, current.org_id)

    b = (await db.execute(select(Appointment).where(
        Appointment.id == bid, Appointment.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Appointment not found")
    if b.order_id:
        raise HTTPException(409, "This appointment already has a linked document")

    client_id = b.client_id
    if not client_id:
        existing = None
        if b.client_phone:
            existing = (await db.execute(select(Client).where(
                Client.org_id == current.org_id,
                Client.phone == b.client_phone))).scalars().first()
        if existing:
            client_id = existing.id
        else:
            c = Client(org_id=current.org_id, name=b.client_name or "Appointment",
                       phone=b.client_phone, source="appointment")
            db.add(c)
            await db.flush()
            client_id = c.id
        b.client_id = client_id

    initial = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.record_type == rec_type,
        OrderStatus.is_default == True, OrderStatus.active == True))).scalars().first()  # noqa: E712

    order = Order(
        org_id=current.org_id, location_id=b.location_id or current.location_id,
        order_number=await next_number(db, current.org_id, "order"),
        client_id=client_id, assigned_to_id=b.employee_id or current.id,
        status=initial.key if initial else ("est_draft" if is_estimate else "new"),
        is_estimate=is_estimate,
        complaint=b.note or "Booked appointment",
        scheduled_for=b.scheduled_for,
        scheduled_to=b.scheduled_for + timedelta(minutes=b.duration_min or 60),
    )
    db.add(order)
    await db.flush()
    b.order_id = order.id
    b.status = "done"
    await db.commit()
    return {"order_id": order.id, "order_number": order.order_number, "client_id": client_id, "is_estimate": is_estimate}
