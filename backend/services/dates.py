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
