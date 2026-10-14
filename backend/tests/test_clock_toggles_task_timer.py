"""Build 144x — org clock master switches + work-order timer on tasks."""
import pytest


async def _emp_headers(client, tenant):
    """The tenant fixture's default employee headers already authenticate."""
    return tenant


@pytest.mark.asyncio
async def test_org_clock_flags_default_on_and_persist(client, tenant):
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c["clock_wo_enabled"] is True and c["clock_attendance_enabled"] is True
    # also surfaced on /auth/me for the UI to gate the widget
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["clock_wo_enabled"] is True and me["clock_attendance_enabled"] is True

    await client.put("/api/staff/company", json={
        "name": "Shop", "clock_wo_enabled": False, "clock_attendance_enabled": False}, headers=tenant)
    c2 = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c2["clock_wo_enabled"] is False and c2["clock_attendance_enabled"] is False


@pytest.mark.asyncio
async def test_disabled_clocks_block_clock_in(client, tenant):
    await client.put("/api/staff/company", json={
        "name": "Shop", "clock_wo_enabled": False, "clock_attendance_enabled": False}, headers=tenant)
    # attendance clock-in refused
    r = await client.post("/api/timeclock/shift/clock-in", headers=tenant)
    assert r.status_code == 403
    # WO/job timer refused
    r2 = await client.post("/api/timeclock/start", json={"category": "job"}, headers=tenant)
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_task_timer_records_entry_and_flows_to_payroll(client, tenant):
    # a task (standalone, no order)
    tid = (await client.post("/api/tasks", json={"title": "Rebuild carb"}, headers=tenant)).json()["id"]

    # start the WO timer against the task
    start = await client.post("/api/timeclock/start?switch=true",
                              json={"task_id": tid, "category": "task"}, headers=tenant)
    assert start.status_code == 201, start.text
    entry = start.json()
    assert entry["task_id"] == tid and entry["category"] == "task" and entry["running"] is True

    # stop it
    stop = await client.post("/api/timeclock/stop", headers=tenant)
    assert stop.status_code == 200

    # the stopped entry shows up in the timesheet/entries feed with its task link
    entries = (await client.get("/api/timeclock/entries", headers=tenant)).json()
    items = entries.get("items", entries) if isinstance(entries, dict) else entries
    assert any(e.get("task_id") == tid for e in items)


@pytest.mark.asyncio
async def test_task_timer_blocked_when_wo_clock_off(client, tenant):
    tid = (await client.post("/api/tasks", json={"title": "x"}, headers=tenant)).json()["id"]
    await client.put("/api/staff/company", json={"name": "Shop", "clock_wo_enabled": False}, headers=tenant)
    r = await client.post("/api/timeclock/start", json={"task_id": tid, "category": "task"}, headers=tenant)
    assert r.status_code == 403
