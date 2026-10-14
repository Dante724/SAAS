"""144az — technician "My Work" endpoint + dispute flow (ADR 144ax dispute surface)."""
from datetime import datetime, timedelta

import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_my_work_lists_my_assigned_lines(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Brakes",
        "qty": 1, "unit_price": 100, "technician_id": me["id"], "booked_hours": 1.0}, headers=tenant)
    mw = (await client.get("/api/timeclock/my-work", headers=tenant)).json()
    assert "today_seconds" in mw and "week_seconds" in mw
    assert any(ln["description"] == "Brakes" and ln["booked_hours"] == 1.0 for ln in mw["lines"])


@pytest.mark.asyncio
async def test_dispute_then_resolve_is_audited(client, tenant):
    start = datetime(2026, 9, 3, 9, 0, 0)
    e = (await client.post("/api/timeclock/entries", json={
        "category": "admin", "started_at": start.isoformat(),
        "ended_at": (start + timedelta(hours=1)).isoformat()}, headers=tenant)).json()
    eid = e["id"]
    # tech flags it
    r = await client.post(f"/api/timeclock/entries/{eid}/dispute", json={"note": "only worked 30m"}, headers=tenant)
    assert r.status_code == 200 and r.json()["disputed"] is True
    detail = next(x for x in (await client.get("/api/timeclock/entries", headers=tenant)).json()["items"] if x["id"] == eid)
    assert detail["disputed"] is True and detail["dispute_note"] == "only worked 30m"
    # manager resolves
    r2 = await client.post(f"/api/timeclock/entries/{eid}/resolve-dispute", headers=tenant)
    assert r2.status_code == 200 and r2.json()["disputed"] is False
    # both actions in the audit trail
    trail = (await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"]
    actions = [a["action"] for a in trail]
    assert "dispute" in actions and "resolve_dispute" in actions
