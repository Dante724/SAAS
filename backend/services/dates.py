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
    """The clock scheduled times are kept in: the location's timezone, else the shop's."""
    from sqlalchemy import select

    from backend.models import Location, Organisation
    if location_id:
        tz = (await db.execute(select(Location.timezone).where(
            Location.id == location_id, Location.org_id == org_id))).scalar()
        if tz:
            return tz
    return (await db.execute(select(Organisation.timezone).where(Organisation.id == org_id))).scalar() or "UTC"


async def parse_wall_dt(db, org_id: str, location_id: str | None, value, field: str = "date") -> datetime:
    """parse_dt for a scheduled time: the result is naive shop wall-clock time."""
    dt = parse_dt(value, field)
    return to_wall_clock(dt, await shop_tzname(db, org_id, location_id)) if dt.tzinfo else dt
