"""The booking slot engine, shared by the public booking page (routers/public.py) and the API v2
booking endpoints (routers/v2_bookings.py), so both give the same answer for the same inputs.

Everything here works in the location's wall clock: appointments are stored as naive local time
and working hours are local, so "now", lead time, the booking window and past slots all use
``local_now(location.timezone)``.
"""
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Optional

from sqlalchemy import select

from backend.models import Appointment, Location, Order, Service

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Statuses that hold a slot. "booked" is the app's own; "confirmed" is what API v1 creates.
BUSY_STATUSES = ("booked", "confirmed")


def parse_hhmm(v, default_min):
    """'HH:MM' → minutes past midnight; falls back to default on junk."""
    try:
        h, m = str(v).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default_min


def day_window(location, day):
    """Open window for a weekday as (open_min, close_min), or None when the
    branch is closed. Reads Location.working_hours ({mon:{open,close,closed}}).
    A location with no configured hours falls back to 09:00-18:00 so existing
    orgs keep working until they set hours."""
    wh = getattr(location, "working_hours", None) if location else None
    if not wh:
        return (540, 1080)  # 09:00-18:00 default
    d = wh.get(WEEKDAY_KEYS[day.weekday()]) or {}
    if d.get("closed"):
        return None
    o = parse_hhmm(d.get("open"), 540)
    c = parse_hhmm(d.get("close"), 1080)
    return (o, c) if c > o else None


def local_now(tzname):
    """Build 144br: 'now' as naive local wall-clock in the location's timezone.
    Appointments are stored as naive local time and working hours are local, so all
    lead-time / window / past-slot comparisons must use the same clock — not UTC.
    Falls back to UTC on an unknown tz."""
    from datetime import timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tzname or "UTC")).replace(tzinfo=None)
    except Exception:
        return datetime.now(_tz.utc).replace(tzinfo=None)


async def resolve_location(db, org, location_id):
    q = select(Location).where(Location.org_id == org.id, Location.active == True)  # noqa: E712
    if location_id:
        q = q.where(Location.id == location_id)
    return (await db.execute(q)).scalars().first()


async def busy_windows(db, org, location, day, employee_id):
    """(start,end) datetimes already taken on `day` — appointments, and optionally
    scheduled work orders when appointment_consider_orders is on. Scoped to the
    location and, if given, one employee."""
    day_start = datetime.combine(day, dt_time(0, 0))
    day_end = day_start + timedelta(days=1)
    q = select(Appointment).where(
        Appointment.org_id == org.id, Appointment.status.in_(BUSY_STATUSES),
        Appointment.scheduled_for >= day_start, Appointment.scheduled_for < day_end)
    if location:
        q = q.where(Appointment.location_id == location.id)
    if employee_id:
        q = q.where(Appointment.employee_id == employee_id)
    rows = (await db.execute(q)).scalars().all()
    busy = [(b.scheduled_for, b.scheduled_for + timedelta(minutes=b.duration_min or 60)) for b in rows]
    if org.appointment_consider_orders:
        oq = select(Order).where(
            Order.org_id == org.id, Order.scheduled_for.isnot(None),
            Order.scheduled_for >= day_start, Order.scheduled_for < day_end)
        if location:
            oq = oq.where(Order.location_id == location.id)
        for o in (await db.execute(oq)).scalars().all():
            busy.append((o.scheduled_for, o.scheduled_for + timedelta(minutes=60)))
    return busy


def slot_is_free(cur, slot_end, busy, allow_multiple):
    """When multiple appointments per slot are allowed the window is never 'full'
    (capacity is unbounded); otherwise any overlap makes it busy."""
    if allow_multiple:
        return True
    return not any(cur < be and bs < slot_end for bs, be in busy)


def day_slots(org, location, day, busy, dur, now):
    """List of 'HH:MM' start times bookable on `day` for a job of `dur` mins."""
    win = day_window(location, day)
    if win is None:
        return None  # closed
    open_min, close_min = win
    step = org.appointment_time_slot or 60
    lead = timedelta(minutes=org.appointment_lead_min or 0)
    earliest = now + lead
    out = []
    cur = datetime.combine(day, dt_time(0, 0)) + timedelta(minutes=open_min)
    win_end = datetime.combine(day, dt_time(0, 0)) + timedelta(minutes=close_min)
    while cur + timedelta(minutes=dur) <= win_end:
        slot_end = cur + timedelta(minutes=dur)
        if cur >= earliest and slot_is_free(cur, slot_end, busy, org.appointment_allow_multiple):
            out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step)
    return out


async def service_duration(db, org, service_id):
    dur = org.appointment_time_slot or 60
    if service_id:
        svc = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
        if svc and svc.duration_min:
            dur = svc.duration_min
    return dur


async def slots_for_day(db, org, day: date, *, location_id: Optional[str] = None,
                        employee_id: Optional[str] = None, service_id: Optional[str] = None,
                        duration_min: Optional[int] = None) -> dict:
    """The booking page's answer for one day: {location, now, step, window_days, duration_min,
    slots: ['HH:MM', ...], reason: None | 'in the past' | 'beyond the N-day appointment window' |
    'closed'}. `duration_min`, when given, replaces the service's (API v2 only)."""
    location = await resolve_location(db, org, location_id)
    from backend.services.dates import location_tz
    now = local_now(location_tz(location, org))
    step = org.appointment_time_slot or 60
    window_days = org.appointment_window_days or 30
    out = {"location": location, "now": now, "step": step, "window_days": window_days,
           "duration_min": None, "slots": [], "reason": None}
    if day < now.date():
        out["reason"] = "in the past"
        return out
    if day > (now.date() + timedelta(days=window_days)):
        out["reason"] = f"beyond the {window_days}-day appointment window"
        return out
    dur = duration_min or await service_duration(db, org, service_id)
    out["duration_min"] = dur
    busy = await busy_windows(db, org, location, day, employee_id)
    slots = day_slots(org, location, day, busy, dur, now)
    if slots is None:
        out["reason"] = "closed"
        return out
    out["slots"] = slots
    return out


async def employee_busy(db, org_id: str, day: date, employee_ids) -> dict:
    """employee id → (start, end) windows that employee already holds on `day`, at any location
    (the same appointments the double-booking check looks at)."""
    ids = list(employee_ids)
    out: dict = {e: [] for e in ids}
    if not ids:
        return out
    day_start = datetime.combine(day, dt_time(0, 0))
    rows = (await db.execute(select(Appointment).where(
        Appointment.org_id == org_id, Appointment.employee_id.in_(ids),
        Appointment.status.in_(BUSY_STATUSES),
        Appointment.scheduled_for >= day_start - timedelta(days=1),
        Appointment.scheduled_for < day_start + timedelta(days=1)))).scalars().all()
    for b in rows:
        out[b.employee_id].append((b.scheduled_for, b.scheduled_for + timedelta(minutes=b.duration_min or 60)))
    return out


async def employee_clash(db, org_id: str, employee_id: Optional[str], start: datetime, end: datetime,
                         exclude_id: Optional[str] = None):
    """The appointment that `employee_id` already holds overlapping [start, end), or None."""
    if not employee_id:
        return None
    q = select(Appointment).where(
        Appointment.org_id == org_id, Appointment.employee_id == employee_id,
        Appointment.status.in_(BUSY_STATUSES),
        Appointment.scheduled_for < end, Appointment.scheduled_for >= start - timedelta(days=1))
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    for o in (await db.execute(q)).scalars().all():
        if start < o.scheduled_for + timedelta(minutes=o.duration_min or 60):
            return o
    return None
