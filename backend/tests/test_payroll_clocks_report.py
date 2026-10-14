"""Build 144i — payroll report + the two clocks (labour hours / shop attendance)."""
import pytest


async def _emp(client, h, name, email, **extra):
    body = {"name": name, "email": email, "password": "pw-emp-12345"}
    body.update(extra)
    r = await client.post("/api/staff/employees", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_hourly_basis_roundtrips_and_defaults(client, tenant):
    # default is labour
    eid = await _emp(client, tenant, "Def", "def-clk@test.dev")
    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["hourly_basis"] == "labour"

    # can be set to shop attendance
    eid2 = await _emp(client, tenant, "Att", "att-clk@test.dev",
                      wage_type="hourly", wage_value=20, hourly_basis="attendance")
    row2 = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
                if e["id"] == eid2)
    assert row2["hourly_basis"] == "attendance"

    # patch back to labour; a bogus value is coerced to labour
    await client.patch(f"/api/staff/employees/{eid2}", json={"hourly_basis": "labour"}, headers=tenant)
    row3 = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
                if e["id"] == eid2)
    assert row3["hourly_basis"] == "labour"
    await client.patch(f"/api/staff/employees/{eid2}", json={"hourly_basis": "nonsense"}, headers=tenant)
    row4 = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
                if e["id"] == eid2)
    assert row4["hourly_basis"] == "labour"


@pytest.mark.asyncio
async def test_attendance_clock_pays_from_shifts(client, tenant, db):
    """The shop-attendance clock posts hourly wages from completed shifts, which
    then show up in the payroll calculation's hourly total."""
    from datetime import datetime

    from sqlalchemy import select

    from backend.models import Employee, Location, Shift

    eid = await _emp(client, tenant, "Shift Sam", "sam-att@test.dev",
                     wage_type="hourly", wage_value=20, hourly_basis="attendance")
    # give the employee two completed shifts in June (8h total) via the DB
    loc = (await db.execute(select(Location))).scalars().first()
    db.add(Shift(employee_id=eid, location_id=loc.id,
                 started_at=datetime(2026, 6, 3, 9, 0), ended_at=datetime(2026, 6, 3, 14, 0),
                 hours_worked=5.0))
    db.add(Shift(employee_id=eid, location_id=loc.id,
                 started_at=datetime(2026, 6, 4, 9, 0), ended_at=datetime(2026, 6, 4, 12, 0),
                 hours_worked=3.0))
    await db.commit()

    r = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": eid, "date_from": "2026-06-01", "date_to": "2026-06-30"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["hours"] == 8.0
    assert body["wage_posted"] == 160.0        # 8h × $20
    assert body["ledger_posted"] is True

    # reposting the same window is idempotent (no double pay)
    again = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": eid, "date_from": "2026-06-01", "date_to": "2026-06-30"}, headers=tenant)
    assert again.json()["ledger_posted"] is False

    # it flows into the month's payroll calculation as hourly
    row = next(i for i in (await client.get(
        "/api/payroll/calculation?month=2026-06", headers=tenant)).json()["items"]
        if i["employee_id"] == eid)
    assert row["hourly_total"] == 160.0
    assert row["computed_total"] == 160.0


@pytest.mark.asyncio
async def test_payroll_report_lists_employee_totals(client, tenant):
    eid = await _emp(client, tenant, "Rep Rita", "rita-rep@test.dev", wage_type="fixed", wage_value=0)
    await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 300, "month": "2026-06"}, headers=tenant)

    # the report is in the catalog under Finance
    cat = (await client.get("/api/reports/catalog", headers=tenant)).json()
    finance = next(t for t in cat["tabs"] if t["name"] == "Finance")
    assert any(r["key"] == "payroll" and r["title"] == "Payroll summary" for r in finance["reports"])

    run = await client.get(
        "/api/reports/run/payroll?date_from=2026-06-01&date_to=2026-06-30", headers=tenant)
    assert run.status_code == 200, run.text
    data = run.json()
    assert data["columns"][:2] == ["Employee", "Position"]
    assert "Bonuses" in data["columns"] and "Total" in data["columns"]
    rita = next(r for r in data["rows"] if r[0] == "Rep Rita")
    bonus_idx = data["columns"].index("Bonuses")
    total_idx = data["columns"].index("Total")
    assert rita[bonus_idx] == 300
    assert rita[total_idx] == 300
