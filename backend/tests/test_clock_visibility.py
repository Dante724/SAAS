"""Build 144j — per-employee time-clock visibility (job hours / attendance)."""
import pytest


@pytest.mark.asyncio
async def test_defaults_both_clocks_on(client, tenant):
    r = await client.post("/api/staff/employees", json={
        "name": "Def", "email": "def-clk2@test.dev", "password": "pw-emp-12345"}, headers=tenant)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["track_job_hours"] is True
    assert row["track_attendance"] is True


@pytest.mark.asyncio
async def test_can_choose_only_one_clock(client, tenant):
    # attendance only
    r = await client.post("/api/staff/employees", json={
        "name": "AttOnly", "email": "attonly@test.dev", "password": "pw-emp-12345",
        "track_job_hours": False, "track_attendance": True}, headers=tenant)
    eid = r.json()["id"]
    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["track_job_hours"] is False
    assert row["track_attendance"] is True

    # flip to job-only via patch
    await client.patch(f"/api/staff/employees/{eid}", json={
        "track_job_hours": True, "track_attendance": False}, headers=tenant)
    row2 = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
                if e["id"] == eid)
    assert row2["track_job_hours"] is True
    assert row2["track_attendance"] is False


@pytest.mark.asyncio
async def test_me_exposes_clock_flags(client, tenant):
    # the admin who registered — /me carries the flags so the widget can gate
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert "track_job_hours" in me and "track_attendance" in me
    assert me["track_job_hours"] is True and me["track_attendance"] is True
