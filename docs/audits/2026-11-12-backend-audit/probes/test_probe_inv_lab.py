"""Audit probes (04-inventory-labour). Read-only against the tree; runs on throwaway SQLite
through the repo's own conftest (loaded with -p backend.tests.conftest)."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _register(client):
    email = f"pr-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _admin_role_id(db, org_id):
    from backend.models import Role
    return (await db.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "admin"))).scalar_one()


async def _teammate(client, H, role_id):
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _hourly_total(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type == "hourly_wage"))).scalar())


async def _entry(db, org, emp, when, hours, rate=20.0):
    from backend.models import TimeEntry
    start = datetime.fromisoformat(when)
    e = TimeEntry(org_id=org, employee_id=emp, started_at=start, ended_at=start + timedelta(hours=hours),
                  duration_sec=int(hours * 3600), cost_rate=rate, state="stopped", source="manual")
    db.add(e)
    await db.commit()
    return e.id


async def test_LAB_reopen_reapprove_pays_entry_twice(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    eid = await _entry(db, org, emp, "2026-08-03T09:00:00", 8)
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-03", "date_to": "2026-08-03"}, headers=H)
    assert r.status_code == 200, r.text
    first = await _hourly_total(db, org, emp)
    assert first == 160
    assert (await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)).status_code == 200
    # a correction: 8h -> 9h
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"ended_at": "2026-08-03T18:00:00"}, headers=H)
    assert r.status_code == 200, r.text
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    print("re-approve:", r.status_code, r.text[:200])
    total = await _hourly_total(db, org, emp)
    print("hourly ledger after reopen+fix+re-approve:", total, "(correct would be 180)")
    assert total == 180, f"ledger={total}"


async def test_LAB_single_entry_approve_is_never_paid(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    eid = await _entry(db, org, emp, "2026-08-04T09:00:00", 8)
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)).status_code == 200
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    print("timesheet approve after single approve:", r.status_code, r.text[:200])
    total = await _hourly_total(db, org, emp)
    print("hourly ledger:", total, "(8h x 20 = 160 expected)")
    assert total == 160, f"ledger={total}"


async def test_LAB_attendance_overlapping_windows_double_pay(client, db):
    from backend.models import Employee, Location, Shift
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
    e.hourly_basis = "attendance"
    e.hourly_cost_rate = 20
    loc = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    s0 = datetime(2026, 8, 3, 9)
    db.add(Shift(employee_id=emp, location_id=loc, started_at=s0, ended_at=s0 + timedelta(hours=8), hours_worked=8))
    await db.commit()
    a = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-15"}, headers=H)
    b = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    print("attendance:", a.status_code, a.text[:150], "|", b.status_code, b.text[:150])
    total = await _hourly_total(db, org, emp)
    print("hourly ledger:", total, "(one 8h shift x 20 = 160 expected)")
    assert total == 160, f"ledger={total}"


async def test_LAB_tech_extends_own_entry_overlapping(client, db):
    H, org, _ = await _register(client)
    # technician-like role: any non-owner; the own-entry rule applies to everyone
    emp, TH = await _teammate(client, H, await _admin_role_id(db, org))
    e1 = await _entry(db, org, emp, "2026-08-05T09:00:00", 2)
    await _entry(db, org, emp, "2026-08-05T12:00:00", 2)
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"ended_at": "2026-08-05T20:00:00"}, headers=TH)
    print("own edit making entries overlap:", r.status_code)
    assert r.status_code == 409, "edit accepted an overlap"


async def test_INV_stocktake_post_drives_stock_negative_and_wac(client, db):
    from backend.models import Location, Part
    H, org, _ = await _register(client)
    loc = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    r = await client.post("/api/inventory/parts", json={"name": "Screen", "cost_price": 10}, headers=H)
    assert r.status_code == 201, r.text
    pid = r.json().get("id") or r.json().get("part_id")
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": 5, "movement_type": "receive", "unit_cost": 10}, headers=H)
    assert r.status_code == 200, r.text
    r = await client.post("/api/stocktake", json={"location_id": loc, "kind": "full"}, headers=H)
    assert r.status_code == 201, r.text
    st = r.json()["id"]
    lines = (await client.get(f"/api/stocktake/{st}", headers=H)).json()["lines"]
    line = next(l for l in lines if l["part_id"] == pid)
    r = await client.patch(f"/api/stocktake/{st}/lines/{line['id']}", json={"counted_qty": 3}, headers=H)
    assert r.status_code == 200, r.text
    # after the count, 4 units are written off (sold/used elsewhere)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": 4, "movement_type": "writeoff"}, headers=H)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)
    assert r.status_code == 200, r.text
    from backend.services import stock_rows
    db.expire_all()
    avail, _ = await stock_rows.totals(db, pid, loc)
    print("on hand after stocktake post:", avail)
    # now receive 2 at cost 20: moving average weighted against negative on-hand
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": 2, "movement_type": "receive", "unit_cost": 20}, headers=H)
    assert r.status_code == 200, r.text
    db.expire_all()
    p = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    print("avg_cost after receiving 2 @20 onto on-hand", avail, "->", p.avg_cost)
    assert avail >= 0, f"stock went negative: {avail}"


async def test_LAB_reopen_one_of_two_pays_it_again(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    a = await _entry(db, org, emp, "2026-08-06T08:00:00", 3)
    await _entry(db, org, emp, "2026-08-06T12:00:00", 3)
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-06", "date_to": "2026-08-06"}, headers=H)
    assert r.status_code == 200, r.text
    assert await _hourly_total(db, org, emp) == 120
    assert (await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)).status_code == 200
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-06", "date_to": "2026-08-06"}, headers=H)
    total = await _hourly_total(db, org, emp)
    print("reopen-one-of-two re-approve:", r.text[:160], "ledger:", total, "(correct 120)")
    assert total == 120, f"ledger={total}"
