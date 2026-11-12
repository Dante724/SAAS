"""Audit probe: timesheet overtime grouped by UTC day vs the shop's day (America/New_York)."""
import json, os
import pytest


@pytest.mark.asyncio
async def test_timesheet_overtime_day(client, tenant):
    r = await client.put("/api/staff/company", json={"timezone": "America/New_York"}, headers=tenant)
    assert r.status_code == 200, r.text
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    # One local working day, 3 Nov 2026 (EST = UTC-5): 09:00-17:00 local, then 19:30-22:00 local.
    # In UTC: 14:00-22:00 on 3 Nov, then 00:30-03:00 on 4 Nov.
    for s, e in (("2026-11-03T14:00:00Z", "2026-11-03T22:00:00Z"), ("2026-11-04T00:30:00Z", "2026-11-04T03:00:00Z")):
        r = await client.post("/api/timeclock/entries", headers=tenant,
                              json={"started_at": s, "ended_at": e, "category": "job"})
        assert r.status_code == 201, r.text
    ts = await client.get(f"/api/timeclock/timesheet?employee_id={me['id']}&date_from=2026-11-03&date_to=2026-11-03",
                          headers=tenant)
    rep = await client.get("/api/reports/run/labor_cost?date_from=2026-11-03&date_to=2026-11-03", headers=tenant)
    json.dump({"timesheet_status": ts.status_code, "timesheet": {k: ts.json().get(k) for k in
               ("total_hours", "straight_hours", "overtime_hours", "entry_count")} if ts.status_code == 200 else ts.text,
               "labor_cost": rep.json() if rep.status_code == 200 else rep.text},
              open(os.environ["PROBE_OUT3"], "w"), indent=1)
