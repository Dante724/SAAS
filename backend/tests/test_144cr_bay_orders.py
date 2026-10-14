"""144cr — technician bay: /timeclock/my-work returns per-ORDER cards so a tech can
advance status and see checklist progress without opening the full drawer."""
import uuid

import pytest


async def _register(client):
    email = f"bay-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Org B", "name": "Owner B", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _job(client, h, me, desc="Brakes"):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    loc = await _loc(client, h)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=h)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": desc,
        "qty": 1, "unit_price": 100, "technician_id": me["id"], "booked_hours": 1.0}, headers=h)
    return o


@pytest.mark.asyncio
async def test_my_work_returns_order_cards_with_next_status(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    o = await _job(client, tenant, me)
    mw = (await client.get("/api/timeclock/my-work", headers=tenant)).json()
    assert "orders" in mw, "bay should group my lines into order cards"
    card = next(c for c in mw["orders"] if c["order_id"] == o["id"])
    assert card["status"] == "new"
    # forward one-tap target is the next status in the workflow
    assert card["next_status"] and card["next_status"] != "new"
    assert card["next_label"]
    assert card["checklist_total"] == 0 and card["checklist_done"] == 0
    assert card["client"]  # client name surfaced
    assert any(ln["description"] == "Brakes" for ln in card["lines"])


@pytest.mark.asyncio
async def test_advancing_status_from_bay_target_is_reachable(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    o = await _job(client, tenant, me)
    mw = (await client.get("/api/timeclock/my-work", headers=tenant)).json()
    card = next(c for c in mw["orders"] if c["order_id"] == o["id"])
    # the suggested next_status must be an accepted transition via the real endpoint
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": card["next_status"]}, headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_my_work_orders_are_org_scoped(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _job(client, tenant, me)
    # the other tenant sees none of my jobs
    hb = await _register(client)
    mw2 = (await client.get("/api/timeclock/my-work", headers=hb)).json()
    assert mw2["orders"] == []
