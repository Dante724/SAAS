"""Build 144dn — clock privacy + manager roster.

Every employee only sees their OWN clock; a manager (can_manage_payroll) gets a
live roster of who is clocked in and where. A non-manager is denied the roster
and cannot read another employee's timesheet.
"""
import uuid

import pytest


async def _emp_headers(client, H, perms=None):
    role_id = None
    if perms is not None:
        role_id = (await client.post("/api/staff/roles", json={"name": "R", "permissions": perms}, headers=H)).json()["id"]
    email = f"e-{uuid.uuid4().hex[:6]}@t.dev"
    body = {"name": "Emp", "email": email, "password": "pw-test-12345"}
    if role_id:
        body["role_id"] = role_id
    eid = (await client.post("/api/staff/employees", json=body, headers=H)).json()["id"]
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}, eid


@pytest.mark.asyncio
async def test_roster_is_manager_only_and_lists_who_is_clocked_in(client, tenant):
    H = tenant  # owner/manager

    # a plain worker clocks in
    worker, wid = await _emp_headers(client, H, perms={"can_manage_payroll": False})
    ci = await client.post("/api/timeclock/shift/clock-in", headers=worker)
    assert ci.status_code == 201, ci.text

    # manager sees the worker on the roster, with a location
    r = await client.get("/api/timeclock/roster", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["on_shift"] >= 1
    assert any(row["employee_id"] == wid and row["location_name"] for row in body["on_shift"])

    # the worker themselves is NOT allowed to see the org roster
    denied = await client.get("/api/timeclock/roster", headers=worker)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_worker_only_sees_own_clock(client, tenant):
    H = tenant
    a_hdr, a_id = await _emp_headers(client, H, perms={"can_manage_payroll": False})
    b_hdr, b_id = await _emp_headers(client, H, perms={"can_manage_payroll": False})

    # worker A's own active shift/timer endpoints are self-scoped
    act = (await client.get("/api/timeclock/active", headers=a_hdr)).json()
    assert "active" in act  # None until a timer runs — the point is it's A's own view

    # A cannot read B's timesheet
    denied = await client.get(
        f"/api/timeclock/timesheet?employee_id={b_id}&date_from=2024-01-01&date_to=2024-01-02",
        headers=a_hdr)
    assert denied.status_code == 403

    # A's /entries never leaks B, even if A passes B's id
    ent = (await client.get(f"/api/timeclock/entries?employee_id={b_id}", headers=a_hdr)).json()
    items = ent.get("items", []) if isinstance(ent, dict) else ent
    assert all(e.get("employee_id") == a_id for e in items)


@pytest.mark.asyncio
async def test_manager_can_read_any_timesheet(client, tenant):
    H = tenant
    _, wid = await _emp_headers(client, H, perms={"can_manage_payroll": False})
    r = await client.get(
        f"/api/timeclock/timesheet?employee_id={wid}&date_from=2024-01-01&date_to=2024-01-02",
        headers=H)
    assert r.status_code == 200
    assert r.json()["employee_id"] == wid
