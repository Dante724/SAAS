"""Hardening council (High): a double tap on "Clock in" opened two attendance shifts.

routers/timeclock.py shift_clock_in checked _open_shift() and then inserted with no lock, so two
requests in the same window both passed the check (201, 201). _open_shift returns only the
newest open shift, so clock-out closed one and the other stayed open forever — payroll hours
kept growing. Fix: clock-in locks the employee row before the open-shift check (the second
request waits, then sees the first shift and gets 409); clock-out closes every open shift.
"""
import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.models import Employee, Location, Shift
from backend.routers import timeclock
from backend.services.dates import now_utc

pytestmark = pytest.mark.asyncio

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks need Postgres")


def _slow(monkeypatch, module, name):
    """Widen the check-then-insert window so two clock-ins interleave deterministically."""
    orig = getattr(module, name)

    async def slowed(*a, **kw):
        out = await orig(*a, **kw)
        await asyncio.sleep(0.2)
        return out

    monkeypatch.setattr(module, name, slowed)


async def _open_shifts(db):
    db.expire_all()
    return (await db.execute(select(Shift).where(Shift.ended_at.is_(None)))).scalars().all()


@pg_only
async def test_double_tap_clock_in_opens_one_shift(client, tenant, db, monkeypatch):
    _slow(monkeypatch, timeclock, "_open_shift")
    a, b = await asyncio.gather(
        client.post("/api/timeclock/shift/clock-in", headers=tenant),
        client.post("/api/timeclock/shift/clock-in", headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)
    assert len(await _open_shifts(db)) == 1


@pg_only
async def test_clock_out_after_double_tap_closes_everything(client, tenant, db, monkeypatch):
    _slow(monkeypatch, timeclock, "_open_shift")
    await asyncio.gather(
        client.post("/api/timeclock/shift/clock-in", headers=tenant),
        client.post("/api/timeclock/shift/clock-in", headers=tenant))
    r = await client.post("/api/timeclock/shift/clock-out", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _open_shifts(db) == []


async def test_leaked_duplicate_shift_is_not_paid_twice(client, tenant, db):
    """Two shifts open at once (a double tap before the clock-in lock) can't exist any more: the
    database refuses the second (w4s01 uq_shifts_open_employee; the migration closed old leaks).
    Clock-out pays the time once and a second tap is refused."""
    from sqlalchemy.exc import IntegrityError
    emp = (await db.execute(select(Employee))).scalars().first()
    loc = (await db.execute(select(Location).where(Location.org_id == emp.org_id))).scalars().first()
    emp_id, loc_id = emp.id, loc.id
    start = now_utc() - timedelta(hours=8)
    db.add(Shift(employee_id=emp_id, location_id=loc_id, started_at=start))
    await db.commit()
    db.add(Shift(employee_id=emp_id, location_id=loc_id, started_at=start))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    r = await client.post("/api/timeclock/shift/clock-out", headers=tenant)
    assert r.status_code == 200, r.text
    again = await client.post("/api/timeclock/shift/clock-out", headers=tenant)
    assert again.status_code == 409, again.text
    db.expire_all()
    shifts = (await db.execute(select(Shift).where(Shift.employee_id == emp_id))).scalars().all()
    paid = sum(float(s.hours_worked or 0) for s in shifts if s.ended_at is not None)
    assert len(shifts) == 1 and 7.9 < paid < 8.1, [(s.ended_at, s.hours_worked) for s in shifts]
    # What payroll and the employee's history read: closed shifts' hours.
    hist = await client.get("/api/timeclock/shift/history", headers=tenant)
    assert 7.9 < float(hist.json()["total_hours"]) < 8.1, hist.json()


async def test_clock_out_closes_every_open_shift(client, tenant, db):
    """An employee has at most one open shift (the database says so since w4s01); clock-out
    closes it, so nothing keeps accruing hours."""
    emp = (await db.execute(select(Employee))).scalars().first()
    loc = (await db.execute(select(Location).where(Location.org_id == emp.org_id))).scalars().first()
    db.add(Shift(employee_id=emp.id, location_id=loc.id, started_at=now_utc()))
    await db.commit()
    r = await client.post("/api/timeclock/shift/clock-out", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _open_shifts(db) == []
    r = await client.get("/api/timeclock/shift/active", headers=tenant)
    assert r.json()["shift"] is None
