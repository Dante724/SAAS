"""Hardening council M10 — only one cash drawer opens per location. /shifts/open did a plain
check-then-insert with no lock, so three concurrent opens at one location all answered 200 and
/shifts/current then raised (scalar_one_or_none over several open rows → 500)."""
import asyncio
import os

import pytest
from sqlalchemy import select

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks race on Postgres only")


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _open_count(client, h, loc):
    shifts = (await client.get("/api/finance/shifts", params={"location_id": loc}, headers=h)).json()["items"]
    return sum(1 for s in shifts if s["open"])


@pg_only
@pytest.mark.asyncio
async def test_only_one_drawer_opens_per_location_under_concurrency(client, tenant):
    loc = await _loc(client, tenant)
    rs = await asyncio.gather(*[client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 50},
                                            headers=tenant) for _ in range(3)])
    codes = sorted(r.status_code for r in rs)
    assert codes == [200, 409, 409], codes
    assert await _open_count(client, tenant, loc) == 1
    cur = await client.get("/api/finance/shifts/current", params={"location_id": loc}, headers=tenant)
    assert cur.status_code == 200, cur.text
    assert cur.json()["open"] is True


@pytest.mark.asyncio
async def test_current_shift_tolerates_two_open_rows(client, tenant, db):
    """Drawers doubled before this fix exist in real data: /shifts/current must still answer with
    the newest open one, not 500, so the shop can close them."""
    from backend.models import CashShift, Employee
    loc = await _loc(client, tenant)
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 10}, headers=tenant)
    assert r.status_code == 200, r.text
    first = r.json()["id"]
    me = (await client.get("/api/auth/me", headers=tenant)).json()["id"]
    emp = (await db.execute(select(Employee).where(Employee.id == me))).scalar_one()
    # a second open row, as the unlocked open left behind
    db.add(CashShift(location_id=loc, opened_by_id=emp.id, opening_float=20))
    await db.commit()
    cur = await client.get("/api/finance/shifts/current", params={"location_id": loc}, headers=tenant)
    assert cur.status_code == 200, cur.text
    assert cur.json()["open"] is True
    assert cur.json()["id"] != first                 # the newest open drawer
    assert cur.json()["opening_float"] == 20
