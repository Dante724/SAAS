"""Build 136 — Locations CRUD + working hours."""
import pytest


@pytest.mark.asyncio
async def test_location_crud_and_hours(client, tenant):
    # a seed location already exists; the list now returns full fields
    first = (await client.get("/api/staff/locations", headers=tenant)).json()["items"]
    assert first and "timezone" in first[0] and "active" in first[0]

    wh = {"mon": {"open": "09:00", "close": "18:00"}, "sun": {"closed": True}}
    r = await client.post("/api/staff/locations", json={
        "name": "Downtown", "phone": "+15550001", "timezone": "Asia/Dubai",
        "working_hours": wh}, headers=tenant)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    assert r.json()["timezone"] == "Asia/Dubai" and r.json()["working_hours"]["sun"]["closed"] is True

    # edit
    up = await client.put(f"/api/staff/locations/{lid}", json={
        "name": "Downtown Branch", "working_hours": {"mon": {"open": "08:00", "close": "20:00"}}}, headers=tenant)
    assert up.status_code == 200
    assert up.json()["name"] == "Downtown Branch"
    assert up.json()["working_hours"]["mon"]["open"] == "08:00"

    # archive (deactivate)
    d = await client.delete(f"/api/staff/locations/{lid}", headers=tenant)
    assert d.status_code == 200
    got = next(x for x in (await client.get("/api/staff/locations", headers=tenant)).json()["items"] if x["id"] == lid)
    assert got["active"] is False


@pytest.mark.asyncio
async def test_cannot_archive_last_active_location(client, tenant):
    locs = (await client.get("/api/staff/locations", headers=tenant)).json()["items"]
    active = [ln for ln in locs if ln["active"]]
    # archive down to one, then the last must be refused
    for ln in active[1:]:
        await client.delete(f"/api/staff/locations/{ln['id']}", headers=tenant)
    last = [ln for ln in (await client.get("/api/staff/locations", headers=tenant)).json()["items"] if ln["active"]]
    assert len(last) == 1
    r = await client.delete(f"/api/staff/locations/{last[0]['id']}", headers=tenant)
    assert r.status_code == 409
