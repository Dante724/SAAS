"""Build 144 — workplace attendance shift (clock in/out) + employee weekly hours."""
import pytest


@pytest.mark.asyncio
async def test_workplace_shift_clock_in_out(client, tenant):
    # need at least one location to clock in to
    loc = await client.post("/api/staff/locations", json={"name": "Main shop"}, headers=tenant)
    assert loc.status_code == 201, loc.text

    # not clocked in initially
    a0 = await client.get("/api/timeclock/shift/active", headers=tenant)
    assert a0.status_code == 200
    assert a0.json()["shift"] is None

    # clock in
    ci = await client.post("/api/timeclock/shift/clock-in", json={}, headers=tenant)
    assert ci.status_code == 201, ci.text
    assert ci.json()["open"] is True

    # double clock-in is rejected
    dup = await client.post("/api/timeclock/shift/clock-in", json={}, headers=tenant)
    assert dup.status_code == 409

    # active shows the open shift
    a1 = await client.get("/api/timeclock/shift/active", headers=tenant)
    assert a1.json()["shift"] is not None
    assert a1.json()["shift"]["open"] is True

    # clock out closes it and logs hours
    co = await client.post("/api/timeclock/shift/clock-out", json={}, headers=tenant)
    assert co.status_code == 200, co.text
    assert co.json()["open"] is False
    assert co.json()["hours_worked"] >= 0

    # active is null again
    a2 = await client.get("/api/timeclock/shift/active", headers=tenant)
    assert a2.json()["shift"] is None

    # clock-out with no open shift is rejected
    co2 = await client.post("/api/timeclock/shift/clock-out", json={}, headers=tenant)
    assert co2.status_code == 409

    # history includes the closed shift
    hist = await client.get("/api/timeclock/shift/history", headers=tenant)
    assert hist.status_code == 200
    assert len(hist.json()["items"]) == 1
    assert hist.json()["total_hours"] >= 0


@pytest.mark.asyncio
async def test_clock_in_falls_back_to_org_location(client, tenant):
    # a fresh org has a default location, so clock-in works without picking one
    ci = await client.post("/api/timeclock/shift/clock-in", json={}, headers=tenant)
    assert ci.status_code == 201, ci.text
    assert ci.json()["location_id"]


@pytest.mark.asyncio
async def test_employee_weekly_hours_roundtrips(client, tenant):
    r = await client.post("/api/staff/employees", json={
        "name": "Sam Tech", "email": "sam-b144@test.dev", "weekly_hours": 37.5}, headers=tenant)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]

    emps = (await client.get("/api/staff/employees", headers=tenant)).json()
    row = next(e for e in emps if e["id"] == eid)
    assert row["weekly_hours"] == 37.5

    upd = await client.patch(f"/api/staff/employees/{eid}", json={"weekly_hours": 40}, headers=tenant)
    assert upd.status_code == 200
    emps2 = (await client.get("/api/staff/employees", headers=tenant)).json()
    assert next(e for e in emps2 if e["id"] == eid)["weekly_hours"] == 40
