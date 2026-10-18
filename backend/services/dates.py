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
