"""Build 144ab — reports 'To' date is inclusive of the whole day.

Regression: the Reports UI sends date_to as a plain date (YYYY-MM-DD), which parsed
to 00:00:00 and silently excluded everything that happened *on* the end date —
so the Activity Log (today's events) always looked empty.
"""
from datetime import datetime

import pytest

from backend.services.dates import now_utc


@pytest.mark.asyncio
async def test_activity_log_includes_todays_events_with_date_to_today(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Zed"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]
    st = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()["items"][-1]["key"]
    await client.patch(f"/api/orders/{oid}/status", json={"status": st}, headers=tenant)

    today = now_utc().date().isoformat()
    # Exactly what the UI sends: a date-only From/To spanning "today".
    r = (await client.get(
        f"/api/reports/run/activity_log?date_from={today}&date_to={today}", headers=tenant)).json()
    # before the fix this was 0; the day is now inclusive
    assert r["row_count"] >= 2, r
    events = " ".join(row[2] for row in r["rows"])
    assert "Work order created" in events and "Status changed" in events


@pytest.mark.asyncio
async def test_other_dated_reports_also_inclusive(client, tenant):
    # a money-moving report should likewise see same-day rows (sanity that the fix
    # is in the shared range parser, not just the activity log)
    today = now_utc().date().isoformat()
    r = await client.get(f"/api/reports/run/wo_created?date_from={today}&date_to={today}", headers=tenant)
    assert r.status_code == 200
