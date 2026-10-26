"""API v2: Booking-site API: free slots, appointment list/get/create, reschedule and cancel.

Routes register on backend.routers.v2.router, so they share its auth, limits and OpenAPI.
Scope: ``appointments:read`` / ``appointments:write`` (or broad read / write).

Free slots come from the same engine as the public booking page (services/booking_slots.py), so
both answer the same for the same inputs. Scheduled times are shop wall-clock time everywhere:
a time sent with an offset is converted to the location's clock before it is stored.

Events: appointment.created comes from the ORM hook in services/webhooks.py (once, whatever the
source); appointment.updated and appointment.cancelled are emitted here. Client notifications
follow the app: rescheduling fires the "changed" rules and cancelling the "status_changed" ones.
"""
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Appointment, Client, Employee, Location, Organisation, Service
from backend.routers.v1 import (
    _ERRORS,
    _NOT_FOUND,
    _WRITE_ERRORS,
    ErrorOut,
    _audit,
    _bounds,
    _doc,
    _idem_h,
    _idempotent,
    _In,
    _many,
    _one,
    _page,
    _page_q,
    _size_q,
)
from backend.routers.v2 import R, W, router
from backend.services import booking_slots as bs
from backend.services import leads as lsvc
from backend.services.apikeys import ApiPrincipal
from backend.services.dates import parse_dt, parse_wall_dt
from backend.services.public_name import public_staff_name

_ID = Field(None, min_length=1, max_length=64)
_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
_STATUS = Literal["booked", "confirmed", "done", "canceled", "no_show"]
CANCELED = "canceled"          # the status the app uses (routers/appointments.py)
NEW_STATUS = "booked"          # the app's own status: the slot engine and the race guard know it

_VALIDATION = {422: {"model": ErrorOut, "description": "Invalid input, an unbookable service, or a date "
                                                       "outside today … the booking window."}}


# ── documented shapes (OpenAPI only; handlers return plain dicts) ────────────
class SlotEmployeeOut(BaseModel):
    id: str
    name: str = Field(..., description="The name the shop shows customers (Settings → Public pages).")


class SlotOut(BaseModel):
    start: str = Field(..., description="Shop wall-clock start, ISO 8601 without offset.")
    end: str = Field(..., description="Shop wall-clock end (start + duration_min).")
    time: str = Field(..., description="HH:MM, the same value the public booking page lists.")
    employees: Optional[list[SlotEmployeeOut]] = Field(
        None, description="Staff free for the whole slot who may perform the service. Only when "
                          "employee_id was left out; null otherwise.")


class SlotsOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "date": "2026-10-28", "location_id": "2b9e…", "timezone": "Europe/London",
        "service_id": "5c7d…", "employee_id": None, "duration_min": 60, "step_min": 30,
        "reason": None, "slots": [
            {"start": "2026-10-28T09:00:00", "end": "2026-10-28T10:00:00", "time": "09:00",
             "employees": [{"id": "9d0a…", "name": "Sam K."}]},
            {"start": "2026-10-28T09:30:00", "end": "2026-10-28T10:30:00", "time": "09:30",
             "employees": [{"id": "9d0a…", "name": "Sam K."}, {"id": "41be…", "name": "Ola"}]}]}]})
    date: str
    location_id: Optional[str] = None
    timezone: str = Field(..., description="The location's timezone: the clock every time here is in.")
    service_id: Optional[str] = None
    employee_id: Optional[str] = None
    duration_min: Optional[int] = None
    step_min: int = Field(..., description="Minutes between start times (the shop's slot size).")
    reason: Optional[Literal["closed"]] = Field(None, description="'closed' when the location is closed that day.")
    slots: list[SlotOut]


class BookingOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "e02f…", "status": "booked", "scheduled_for": "2026-10-28T10:30:00",
        "ends_at": "2026-10-28T11:30:00", "duration_min": 60, "location_id": "2b9e…",
        "location_name": "High Street", "service_id": "5c7d…", "service_name": "Screen repair",
        "employee_id": "9d0a…", "employee_name": "Sam Kerr", "client_id": "6f1c…",
        "client_name": "Dana Whitfield", "client_phone": "+1 555 0100", "note": "Cracked screen",
        "order_id": None, "created_at": "2026-10-25T18:06:36", "updated_at": "2026-10-25T18:06:36"}]})
    id: str
    status: str = Field(..., description="booked, confirmed, done, canceled or no_show.")
    scheduled_for: str = Field(..., description="Shop wall-clock time, ISO 8601 without offset.")
    ends_at: str = Field(..., description="Shop wall-clock time.")
    duration_min: int
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None
    client_id: Optional[str] = Field(None, description="null for keys without contacts:read.")
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    note: Optional[str] = None
    order_id: Optional[str] = Field(None, description="The work order or estimate it became, if any.")
    created_at: Optional[str] = Field(None, description="ISO 8601, UTC.")
    updated_at: Optional[str] = Field(None, description="ISO 8601, UTC.")


# ── input models ─────────────────────────────────────────────────────────────
class BookingIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "scheduled_for": "2026-10-28T10:30:00", "service_id": "5c7d…", "employee_id": "9d0a…",
        "location_id": "2b9e…", "client_name": "Dana Whitfield", "client_phone": "+1 555 0100",
        "note": "Cracked screen"}]})
    scheduled_for: str = Field(..., min_length=1, max_length=64,
                               description="ISO 8601. Without an offset it is the shop's wall-clock time; "
                                           "with one (`Z`, `+05:30`) it is converted to the location's clock.")
    location_id: Optional[str] = Field(None, min_length=1, max_length=64,
                                       description="Default: the shop's first active location.")
    service_id: Optional[str] = _ID
    employee_id: Optional[str] = Field(None, min_length=1, max_length=64,
                                       description="Leave out for an unassigned appointment.")
    client_id: Optional[str] = Field(None, min_length=1, max_length=64, description="Needs contacts:read.")
    client_name: Optional[str] = Field(None, min_length=1, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    duration_min: Optional[int] = Field(None, ge=5, le=1440,
                                        description="Default: the service's duration, else the shop's slot size.")
    note: Optional[str] = Field(None, max_length=5000)
    check_slot: bool = Field(True, description="Refuse with 409 unless the time is one of the free starts "
                                               "GET /appointments/slots gives for the same inputs. "
                                               "false skips it (the employee clash check still applies).")
    notify_client: bool = Field(True, description="Run the shop's appointment confirmation rules, "
                                                  "as a booking from the public page does.")


class BookingPatch(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "scheduled_for": "2026-10-29T14:00:00", "employee_id": "41be…"}]})
    scheduled_for: Optional[str] = Field(None, min_length=1, max_length=64,
                                         description="New start, read like scheduled_for on create.")
    duration_min: Optional[int] = Field(None, ge=5, le=1440)
    employee_id: Optional[str] = Field(None, min_length=1, max_length=64, description="Move to another employee.")
    note: Optional[str] = Field(None, max_length=5000, description="null clears it.")


class CancelIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"reason": "Customer called to cancel"}]})
    reason: Optional[str] = Field(None, max_length=500, description="Kept on the appointment's note and in the event.")


# ── helpers ──────────────────────────────────────────────────────────────────
def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat() if v else None


async def _names(db, org_id: str, rows) -> dict:
    """(kind, id) → name for the services, employees, locations and contacts on these rows."""
    out: dict = {}
    for kind, model, attr in (("s", Service, "service_id"), ("e", Employee, "employee_id"),
                              ("l", Location, "location_id"), ("c", Client, "client_id")):
        ids = {getattr(r, attr) for r in rows if getattr(r, attr)}
        if ids:
            for rid, name in (await db.execute(select(model.id, model.name).where(
                    model.org_id == org_id, model.id.in_(ids)))).all():
                out[(kind, rid)] = name
    return out


def _ser(b: Appointment, names: dict, p: ApiPrincipal) -> dict:
    dur = b.duration_min or 60
    contacts = p.allows("read", "contacts")
    return {"id": b.id, "status": b.status,
            "scheduled_for": _iso(b.scheduled_for), "ends_at": _iso(b.scheduled_for + timedelta(minutes=dur)),
            "duration_min": dur, "location_id": b.location_id, "location_name": names.get(("l", b.location_id)),
            "service_id": b.service_id, "service_name": names.get(("s", b.service_id)),
            "employee_id": b.employee_id, "employee_name": names.get(("e", b.employee_id)),
            "client_id": b.client_id if contacts else None,
            "client_name": b.client_name or names.get(("c", b.client_id)),
            "client_phone": b.client_phone, "note": b.note, "order_id": b.order_id,
            "created_at": _iso(b.created_at), "updated_at": _iso(b.updated_at or b.created_at)}


async def _one_out(db, b: Appointment, p: ApiPrincipal) -> dict:
    return {"data": _ser(b, await _names(db, p.org_id, [b]), p)}


async def _org(db, org_id: str) -> Organisation:
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()


def _bookable(svc: Optional[Service]) -> None:
    if svc is not None and not (svc.active and svc.bookable):
        raise HTTPException(422, f"Service '{svc.name}' can't be booked: it is "
                                 f"{'inactive' if not svc.active else 'not marked bookable'}. "
                                 "Turn on Bookable for it in Settings → Services, or pick another service.")


def _active(row, what: str) -> None:
    if row is not None and not row.active:
        raise HTTPException(422, f"{what} '{row.name}' is not active")


def _may_perform(svc: Optional[Service], emp: Optional[Employee]) -> None:
    allowed = (getattr(svc, "allowed_employee_ids", None) or []) if svc else []
    if emp is not None and allowed and emp.id not in allowed:
        raise HTTPException(422, f"{emp.name} can't perform the service '{svc.name}'. "
                                 "Pick one of the employees allowed for it, or leave employee_id out.")


def _clash_409(o: Appointment) -> HTTPException:
    o_end = o.scheduled_for + timedelta(minutes=o.duration_min or 60)
    return HTTPException(409, "That employee already has an appointment "
                              f"{o.scheduled_for.strftime('%H:%M')}-{o_end.strftime('%H:%M')}")


async def _save(db) -> None:
    """Flush and commit; the unique slot index (an employee's booked start) is a clash, 409."""
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "That time slot was just taken. Please pick another")


async def _notify(db, org_id: str, appointment_id: str, event: str) -> None:
    """The shop's module="appointment" rules, as the app runs them. Never fails the request."""
    try:
        from backend.services import notification_rules as _nr
        await _nr.evaluate_appointment(db, org_id=org_id, appointment_id=appointment_id, event=event)
    except Exception:  # noqa: BLE001
        pass


def _date_q(desc: str, required: bool = False):
    return Query(... if required else None, pattern=_DATE_RE, description=desc, examples=["2026-10-28"])


def _day(value: str, field: str) -> _date:
    try:
        return _date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{field} is not a valid date (YYYY-MM-DD)")


def _id_q(desc: str):
    return Query(None, min_length=1, max_length=64, description=desc)


# ── slots ────────────────────────────────────────────────────────────────────
@router.get("/appointments/slots", summary="Free appointment slots for a day",
            responses=_doc(_one("SlotsResponse", SlotsOut), {**_ERRORS, **_NOT_FOUND, **_VALIDATION}),
            description="The free start times on one day, in the location's wall-clock time: the same "
                        "answer the shop's public booking page gives for the same inputs (working hours, "
                        "slot size, lead time, booking window, existing appointments and, when the shop "
                        "counts them, scheduled work orders). When employee_id is left out each slot lists "
                        "the staff free for all of it who may perform the service. `date` must be from today "
                        "(at the location) to the end of the shop's booking window, else 422. Needs "
                        "appointments:read.")
async def appointment_slots(
        date: str = _date_q("The day, YYYY-MM-DD, at the location.", required=True),
        service_id: Optional[str] = _id_q("A bookable service: its duration sets the slot length."),
        employee_id: Optional[str] = _id_q("Only this employee's free times."),
        location_id: Optional[str] = _id_q("Default: the shop's first active location."),
        duration_min: Optional[int] = Query(None, ge=5, le=1440,
                                            description="Override the length (5–1440 minutes)."),
        db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("appointments")):
    day = _day(date, "date")
    org = await _org(db, p.org_id)
    svc = await lsvc.own(db, Service, service_id, p.org_id, "Service")
    emp = await lsvc.own(db, Employee, employee_id, p.org_id, "Employee")
    loc = await lsvc.own(db, Location, location_id, p.org_id, "Location")
    _bookable(svc)
    _active(emp, "Employee")
    _active(loc, "Location")
    _may_perform(svc, emp)

    ans = await bs.slots_for_day(db, org, day, location_id=location_id, employee_id=employee_id,
                                 service_id=service_id, duration_min=duration_min)
    location = ans["location"]
    if ans["reason"] == "in the past":
        raise HTTPException(422, f"date is in the past: today at this location is {ans['now'].date().isoformat()}")
    if ans["reason"] and ans["reason"] != "closed":
        last = ans["now"].date() + timedelta(days=ans["window_days"])
        raise HTTPException(422, f"date is beyond the shop's {ans['window_days']}-day booking window "
                                 f"(the last bookable day is {last.isoformat()})")
    dur = ans["duration_min"]
    starts = [datetime.combine(day, datetime.strptime(t, "%H:%M").time()) for t in ans["slots"]]

    per_slot: dict = {}
    if emp is None and starts:
        cands = (await db.execute(select(Employee).where(
            Employee.org_id == p.org_id, Employee.active == True)  # noqa: E712
            .order_by(Employee.name, Employee.id))).scalars().all()
        allowed = (svc.allowed_employee_ids or []) if svc else []
        if allowed:
            cands = [e for e in cands if e.id in allowed]
        held = await bs.employee_busy(db, p.org_id, day, [e.id for e in cands])
        mode = getattr(org, "public_employee_name", None)
        for s in starts:
            e_ = s + timedelta(minutes=dur)
            per_slot[s] = [{"id": e.id, "name": public_staff_name(e, mode)} for e in cands
                           if not any(s < be and bs_ < e_ for bs_, be in held[e.id])]

    return {"data": {
        "date": day.isoformat(), "location_id": location.id if location else None,
        "timezone": (location.timezone if location and location.timezone else "UTC"),
        "service_id": service_id, "employee_id": employee_id, "duration_min": dur,
        "step_min": ans["step"], "reason": ans["reason"],
        "slots": [{"start": s.isoformat(), "end": (s + timedelta(minutes=dur)).isoformat(),
                   "time": s.strftime("%H:%M"), "employees": per_slot.get(s) if emp is None else None}
                  for s in starts]}}


# ── list / get ───────────────────────────────────────────────────────────────
@router.get("/appointments", summary="List appointments",
            responses=_doc(_many("BookingList", BookingOut), {**_ERRORS, **_NOT_FOUND}),
            description="Soonest first. Dates are the shop's wall-clock days; date_to is inclusive. "
                        "updated_since takes an ISO 8601 instant (no offset means UTC) and returns what "
                        "was created or changed since, for syncing. Filtering by client_id needs "
                        "contacts:read. Needs appointments:read.")
async def list_appointments(
        date_from: Optional[str] = _date_q("From this day (inclusive)."),
        date_to: Optional[str] = _date_q("To this day (inclusive)."),
        employee_id: Optional[str] = _id_q("Only this employee's."),
        location_id: Optional[str] = _id_q("Only this location's."),
        client_id: Optional[str] = _id_q("Only this contact's. Needs contacts:read."),
        status: Optional[_STATUS] = Query(None, description="booked, confirmed, done, canceled or no_show."),
        updated_since: Optional[str] = Query(None, min_length=1, max_length=64,
                                             description="ISO 8601 instant, e.g. 2026-10-25T18:00:00Z."),
        page: int = _page_q(), page_size: int = _size_q(),
        db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("appointments")):
    org = p.org_id
    conds = [Appointment.org_id == org]
    if date_from:
        conds.append(Appointment.scheduled_for >= datetime.combine(_day(date_from, "date_from"), datetime.min.time()))
    if date_to:
        conds.append(Appointment.scheduled_for < datetime.combine(_day(date_to, "date_to") + timedelta(days=1),
                                                                  datetime.min.time()))
    if employee_id:
        await lsvc.own(db, Employee, employee_id, org, "Employee")
        conds.append(Appointment.employee_id == employee_id)
    if location_id:
        await lsvc.own(db, Location, location_id, org, "Location")
        conds.append(Appointment.location_id == location_id)
    if client_id:
        p.require("read", "contacts")
        await lsvc.own(db, Client, client_id, org, "Contact")
        conds.append(Appointment.client_id == client_id)
    if status:
        conds.append(Appointment.status == status)
    if updated_since:
        since = parse_dt(updated_since, "updated_since")
        if since.tzinfo:
            since = since.astimezone(timezone.utc).replace(tzinfo=None)
        conds.append(func.coalesce(Appointment.updated_at, Appointment.created_at) >= since)
    page, size = _bounds(page, page_size)
    total = (await db.execute(select(func.count()).select_from(Appointment).where(*conds))).scalar() or 0
    rows = (await db.execute(select(Appointment).where(*conds)
                             .order_by(Appointment.scheduled_for, Appointment.id)
                             .limit(size).offset((page - 1) * size))).scalars().all()
    names = await _names(db, org, rows)
    return _page([_ser(b, names, p) for b in rows], total, page, size)


@router.get("/appointments/{aid}", summary="Get an appointment",
            responses=_doc(_one("BookingResponse", BookingOut), {**_ERRORS, **_NOT_FOUND}),
            description="Needs appointments:read.")
async def get_appointment(aid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("appointments")):
    return await _one_out(db, await lsvc.own(db, Appointment, aid, p.org_id, "Appointment"), p)


# ── create ───────────────────────────────────────────────────────────────────
@router.post("/appointments", status_code=201, summary="Book an appointment",
             responses=_doc(_one("BookingCreated", BookingOut), _WRITE_ERRORS, 201),
             description="Needs appointments:write. Creates a `booked` appointment the same way as v1: "
                         "the time is shop wall-clock (an offset is converted to the location's clock) and "
                         "an employee can't be double-booked (409). With check_slot (the default) the time "
                         "must also be a free start from GET /appointments/slots for the same inputs, else "
                         "409. The employee must be allowed to perform the service (422). Send an "
                         "Idempotency-Key so a retried request can't book twice. Fires appointment.created.")
async def create_appointment(data: BookingIn, db: AsyncSession = Depends(get_db),
                             p: ApiPrincipal = W("appointments"), idempotency_key: Optional[str] = _idem_h()):
    if data.client_id:
        p.require("read", "contacts")
    if not (data.client_id or (data.client_name and data.client_name.strip())):
        raise HTTPException(422, "Send client_name, or client_id for an existing contact")

    async def run():
        org_id = p.org_id
        org = await _org(db, org_id)
        loc = await lsvc.own(db, Location, data.location_id, org_id, "Location")
        svc = await lsvc.own(db, Service, data.service_id, org_id, "Service")
        emp = await lsvc.own(db, Employee, data.employee_id, org_id, "Employee")
        await lsvc.own(db, Client, data.client_id, org_id, "Contact")
        _active(loc, "Location")
        _bookable(svc)
        _active(emp, "Employee")
        _may_perform(svc, emp)
        if loc is None:
            loc = await bs.resolve_location(db, org, None)
        start = await parse_wall_dt(db, org_id, loc.id if loc else None, data.scheduled_for, "scheduled_for")
        dur = data.duration_min or await bs.service_duration(db, org, data.service_id)
        end = start + timedelta(minutes=dur)
        if data.check_slot:
            # The employee's free times and, when the shop takes one appointment per slot, the
            # location's too (the capacity check the public booking page makes on submit).
            views = [data.employee_id] + ([None] if data.employee_id and not org.appointment_allow_multiple else [])
            for emp_view in views:
                ans = await bs.slots_for_day(db, org, start.date(), location_id=loc.id if loc else None,
                                             employee_id=emp_view, service_id=data.service_id, duration_min=dur)
                if (ans["reason"] or start.second or start.microsecond
                        or start.strftime("%H:%M") not in ans["slots"]):
                    raise HTTPException(409, f"{start.strftime('%Y-%m-%d %H:%M')} isn't a free slot"
                                             f"{' (' + ans['reason'] + ')' if ans['reason'] else ''}. "
                                             "GET /api/v2/appointments/slots lists the free ones")
        clash = await bs.employee_clash(db, org_id, data.employee_id, start, end)
        if clash:
            raise _clash_409(clash)
        b = Appointment(org_id=org_id, location_id=loc.id if loc else None, scheduled_for=start,
                        duration_min=dur, status=NEW_STATUS, client_id=data.client_id,
                        client_name=data.client_name.strip() if data.client_name else None,
                        client_phone=data.client_phone, service_id=data.service_id,
                        employee_id=data.employee_id, note=data.note)
        db.add(b)
        try:
            await db.flush()      # appointment.created is queued here by the ORM hook (once)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "That time slot was just taken. Please pick another")
        await _audit(db, p, "appointment.create", "appointment", b.id, scheduled_for=start.isoformat())
        await _save(db)
        if data.notify_client:
            await _notify(db, org_id, b.id, "created")
        await db.refresh(b)
        return await _one_out(db, b, p)
    return await _idempotent(db, p, idempotency_key, "POST /v2/appointments", data, run)


# ── reschedule ───────────────────────────────────────────────────────────────
@router.patch("/appointments/{aid}", summary="Reschedule an appointment",
              responses=_doc(_one("BookingUpdated", BookingOut), _WRITE_ERRORS),
              description="Needs appointments:write. Change the start, length, employee or note of a "
                          "`booked` or `confirmed` appointment (409 for done, cancelled or no-show). The "
                          "same clash rule as the app's reschedule: the employee can't hold an overlapping "
                          "appointment (409). Fields left out or null keep their value, except note, which "
                          "null clears. Fires appointment.updated with changed_fields, and the shop's "
                          "\"appointment changed\" notification rules when the time or employee moved.")
async def reschedule_appointment(aid: str, data: BookingPatch, db: AsyncSession = Depends(get_db),
                                 p: ApiPrincipal = W("appointments")):
    org_id = p.org_id
    b = await lsvc.own(db, Appointment, aid, org_id, "Appointment")
    if b.status not in bs.BUSY_STATUSES:
        raise HTTPException(409, f"Only a booked or confirmed appointment can be changed; this one is {b.status}")
    sent = data.model_fields_set
    if not sent:
        raise HTTPException(422, "Nothing to change: send scheduled_for, duration_min, employee_id or note")
    emp = await lsvc.own(db, Employee, data.employee_id, org_id, "Employee")
    _active(emp, "Employee")
    if emp is not None and b.service_id:
        _may_perform(await db.get(Service, b.service_id), emp)
    new_start = (await parse_wall_dt(db, org_id, b.location_id, data.scheduled_for, "scheduled_for")
                 if data.scheduled_for else b.scheduled_for)
    new_dur = data.duration_min or b.duration_min or 60
    new_emp = data.employee_id or b.employee_id
    clash = await bs.employee_clash(db, org_id, new_emp, new_start, new_start + timedelta(minutes=new_dur),
                                    exclude_id=b.id)
    if clash:
        raise HTTPException(409, "That slot clashes with an appointment "
                                 f"{clash.scheduled_for.strftime('%H:%M')}-"
                                 f"{(clash.scheduled_for + timedelta(minutes=clash.duration_min or 60)).strftime('%H:%M')}")
    new = {"scheduled_for": new_start, "duration_min": new_dur, "employee_id": new_emp}
    if "note" in sent:
        new["note"] = data.note
    changed = [k for k, v in new.items() if getattr(b, k) != v]
    if changed:
        for k in changed:
            setattr(b, k, new[k])
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "That time slot was just taken. Please pick another")
        from backend.services.webhooks import appointment_data, emit_event
        await emit_event(db, org_id, "appointment.updated", {**appointment_data(b), "changed_fields": changed})
        await _audit(db, p, "appointment.update", "appointment", b.id, fields=changed,
                     scheduled_for=b.scheduled_for.isoformat())
        await _save(db)
        if set(changed) & {"scheduled_for", "duration_min", "employee_id"}:
            await _notify(db, org_id, b.id, "changed")   # as the app's reschedule does
        await db.refresh(b)
    return await _one_out(db, b, p)


# ── cancel ───────────────────────────────────────────────────────────────────
@router.post("/appointments/{aid}/cancel", summary="Cancel an appointment",
             responses=_doc(_one("BookingCancelled", BookingOut), _WRITE_ERRORS),
             description="Needs appointments:write. Sets the status to `canceled`, which frees the slot, and "
                          "fires appointment.cancelled and the shop's cancellation notification rules. "
                          "Cancelling an already cancelled appointment returns 200 with the same record and "
                          "fires nothing. A done or no-show appointment can't be cancelled (409).")
async def cancel_appointment(aid: str, data: Optional[CancelIn] = None, db: AsyncSession = Depends(get_db),
                             p: ApiPrincipal = W("appointments"), idempotency_key: Optional[str] = _idem_h()):
    org_id = p.org_id
    body = data or CancelIn()
    b = await lsvc.own(db, Appointment, aid, org_id, "Appointment")

    async def run():
        if b.status == CANCELED:
            return await _one_out(db, b, p)
        if b.status not in bs.BUSY_STATUSES:
            raise HTTPException(409, f"A {b.status} appointment can't be cancelled")
        b.status = CANCELED
        if body.reason and body.reason.strip():
            b.note = "\n".join(x for x in (b.note, f"Cancelled: {body.reason.strip()}") if x)
        await db.flush()
        from backend.services.webhooks import appointment_data, emit_event
        await emit_event(db, org_id, "appointment.cancelled", {**appointment_data(b), "reason": body.reason})
        await _audit(db, p, "appointment.cancel", "appointment", b.id, reason=body.reason)
        await _save(db)
        await _notify(db, org_id, b.id, "status_changed")   # as the app's cancel does
        if b.employee_id:                                    # and tell the booked technician
            try:
                from backend.services.push import notify
                when = b.scheduled_for.strftime("%b %d %H:%M") if b.scheduled_for else ""
                await notify(db, org_id=org_id, employee_id=b.employee_id, kind="appointment.cancelled",
                             title="Appointment cancelled",
                             body=f"{b.client_name or 'Client'} · {when}".strip(" ·"),
                             deep_link="/tech#/dashboard")
            except Exception:  # noqa: BLE001
                pass
        await db.refresh(b)
        return await _one_out(db, b, p)
    return await _idempotent(db, p, idempotency_key, f"POST /v2/appointments/{aid}/cancel", body, run, status=200)
