"""Parse ISO date/datetime from request input, returning a clean 400 instead of a 500
when the client sends something malformed. Mirrors the guarded pattern already used in
public.py. Use for request-body/query values; internal/stored values don't need it."""
from datetime import date, datetime

from fastapi import HTTPException


def parse_dt(value, field: str = "date") -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid {field}: expected an ISO datetime, got {value!r}")


def parse_date(value, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid {field}: expected YYYY-MM-DD, got {value!r}")


# Scheduled times (Order.scheduled_for/_to, Appointment.scheduled_for) are stored as naive *shop
# wall-clock* time: the public booking page, working hours and slot maths all use the location's
# local clock (public._local_now). A client that sends an instant with a zone ("…Z", "+05:30") is
# converted into that clock before it is stored, so every screen and message reads the same time.
def to_wall_clock(dt: datetime, tzname: str | None) -> datetime:
    if dt.tzinfo is None:
        return dt
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tzname or "UTC")
    except Exception:
        from datetime import timezone
        zone = timezone.utc
    return dt.astimezone(zone).replace(tzinfo=None)


async def shop_tzname(db, org_id: str, location_id: str | None = None) -> str:
    """The clock scheduled times are kept in: the location's timezone, else the shop's. A location
    left on the default "UTC" counts as unset (launch B6: every location used to be created on UTC),
    the same rule as shop_timezone() below, so both helpers always agree."""
    from sqlalchemy import select

    from backend.models import Location, Organisation
    if location_id:
        tz = (await db.execute(select(Location.timezone).where(
            Location.id == location_id, Location.org_id == org_id))).scalar()
        if tz and tz != "UTC":
            return tz
    return (await db.execute(select(Organisation.timezone).where(Organisation.id == org_id))).scalar() or "UTC"


def location_tz(location, org) -> str:
    """shop_tzname() for rows already loaded: the location's zone unless it is unset or the old
    "UTC" default, else the shop's, else UTC."""
    tz = getattr(location, "timezone", None) if location is not None else None
    if tz and tz != "UTC":
        return tz
    return getattr(org, "timezone", None) or "UTC"


def _zone(tzname):
    from datetime import timezone
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(tzname or "UTC")
    except Exception:
        return timezone.utc


def wall_to_utc(dt: datetime, tzname: str | None) -> datetime:
    """Naive shop wall-clock time → naive UTC, the clock send_at / created_at are kept in."""
    from datetime import timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tzname))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_wall(dt: datetime, tzname: str | None) -> datetime:
    """Naive UTC → naive shop wall-clock time."""
    from datetime import timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_zone(tzname)).replace(tzinfo=None)


def utc_iso(dt: datetime | None) -> str | None:
    """A stored instant (naive UTC: created_at, started_at, …) as ISO 8601 with a "Z", so a browser
    reads it as UTC instead of guessing it is local time. Never use this for scheduled times: those
    are shop wall-clock and must stay without a zone."""
    if dt is None:
        return None
    from datetime import timezone
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


async def parse_wall_dt(db, org_id: str, location_id: str | None, value, field: str = "date") -> datetime:
    """parse_dt for a scheduled time: the result is naive shop wall-clock time."""
    dt = parse_dt(value, field)
    return to_wall_clock(dt, await shop_tzname(db, org_id, location_id)) if dt.tzinfo else dt


def local_now(tzname) -> datetime:
    """'Now' as naive wall-clock time in `tzname`. Scheduled and due times are stored as naive
    shop-local time (the web app sends datetime-local values), so "today" and "overdue" must be
    measured on the same clock, not UTC. Unknown or empty zones fall back to UTC."""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    try:
        return datetime.now(ZoneInfo(tzname or "UTC")).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


async def shop_timezone(db, employee) -> str:
    """The zone the employee's shop runs on: their branch's zone when one is set (a branch left
    on the default "UTC" counts as unset), else the company's, else UTC."""
    from sqlalchemy import select

    from backend.models import Location, Organisation
    if getattr(employee, "location_id", None):
        tz = (await db.execute(select(Location.timezone).where(
            Location.id == employee.location_id, Location.org_id == employee.org_id))).scalar()
        if tz and tz != "UTC":
            return tz
    tz = (await db.execute(select(Organisation.timezone).where(
        Organisation.id == employee.org_id))).scalar()
    return tz or "UTC"
