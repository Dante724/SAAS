"""Build 144bb — Settings → Public pages toggles gate the public message + approval features."""
import pytest


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order(client, H):
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Bo"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()


async def test_toggles_roundtrip_and_default_on(client, tenant):
    pp = (await client.get("/api/staff/public-pages", headers=tenant)).json()
    assert pp["oe_allow_approval"] is True and pp["oe_allow_messages"] is True
    r = await client.put("/api/staff/public-pages", json={"oe_allow_messages": False, "oe_allow_approval": False}, headers=tenant)
    assert r.status_code == 200
    pp = (await client.get("/api/staff/public-pages", headers=tenant)).json()
    assert pp["oe_allow_messages"] is False and pp["oe_allow_approval"] is False


async def test_messages_disabled_blocks_and_hides(client, tenant):
    o = await _order(client, tenant)
    # off
    await client.put("/api/staff/public-pages", json={"oe_allow_messages": False}, headers=tenant)
    assert (await client.post(f"/api/public/track/{o['public_token']}/message", json={"body": "hi"})).status_code == 403
    t = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    assert t["allow_messages"] is False
    # back on
    await client.put("/api/staff/public-pages", json={"oe_allow_messages": True}, headers=tenant)
    assert (await client.post(f"/api/public/track/{o['public_token']}/message", json={"body": "hi"})).status_code == 200


async def test_approval_disabled_blocks_and_hides(client, tenant):
    o = await _order(client, tenant)
    # move to diagnosed so it would normally await approval
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "diagnosed"}, headers=tenant)
    await client.put("/api/staff/public-pages", json={"oe_allow_approval": False}, headers=tenant)
    t = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    assert t["awaiting_approval"] is False
    assert (await client.post(f"/api/public/track/{o['public_token']}/respond", json={"decision": "accept"})).status_code == 403
