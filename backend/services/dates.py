"""Parse ISO date/datetime from request input, returning a clean 400 instead of a 500
when the client sends something malformed. Mirrors the guarded pattern already used in
public.py. Use for request-body/query values; internal/stored values don't need it."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException


# Timestamps are stored as naive UTC (DateTime without a zone) on SQLite and Postgres alike;
# see docs/ADR-tz-timestamps.md for why they are not timestamptz. now_utc() is the one source
# of "now" for those columns: computed from an aware clock (datetime.utcnow() is deprecated) and
# returned naive so it compares with and stores into the existing columns unchanged.
def _clock() -> datetime:
    """The aware current instant. The single seam tests patch to freeze time."""
    return datetime.now(timezone.utc)


def now_utc() -> datetime:
    return _clock().replace(tzinfo=None)


def zone(tzname: str | None):
    """ZoneInfo for a shop's timezone name; UTC when it is empty or unknown."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzname or "UTC")
    except Exception:
        return timezone.utc


def today_in(tzname: str | None) -> date:
    """The calendar date right now on the shop's clock (not the server's)."""
    return _clock().astimezone(zone(tzname)).date()


def org_today(org) -> date:
    """today_in() for a loaded Organisation (or anything with a .timezone)."""
    return today_in(getattr(org, "timezone", None))


async def shop_today(db, org_id: str, location_id: str | None = None) -> date:
    return today_in(await shop_tzname(db, org_id, location_id))


def day_start_utc(day: date, tzname: str | None) -> datetime:
    """Naive-UTC instant at which `day` begins on the shop's clock — the floor for filtering
    stored timestamps by the shop's day."""
    local = datetime.combine(day, time.min, tzinfo=zone(tzname))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def day_end_utc(day: date, tzname: str | None) -> datetime:
    return day_start_utc(day + timedelta(days=1), tzname)


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
    return dt.astimezone(zone(tzname)).replace(tzinfo=None)


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
