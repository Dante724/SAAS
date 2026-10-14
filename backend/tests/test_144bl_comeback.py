"""144bl — Comeback Shield, first slice: detect when a device is back soon after a prior visit (a
likely comeback), so the shop can double-check before promising. Gated by can_view_comeback_alerts."""
import pytest


async def _client(client, h, name="Comeback Co"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550999"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_repeat_device_is_flagged(client, tenant):
    cid = await _client(client, tenant)
    o1 = (await client.post("/api/orders", json={"client_id": cid, "complaint": "won't charge", "asset_serial": "CB-1"}, headers=tenant)).json()
    dev_id = o1["device"]["id"]
    o2 = (await client.post("/api/orders", json={"client_id": cid, "device_id": dev_id, "complaint": "still won't charge"}, headers=tenant)).json()

    r = await client.get(f"/api/orders/{o2['id']}/comeback", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_comeback"] is True
    assert body["count"] >= 1
    assert body["prior_order_number"] == o1["order_number"]


@pytest.mark.asyncio
async def test_new_device_not_flagged(client, tenant):
    cid = await _client(client, tenant, name="Fresh Co")
    o = (await client.post("/api/orders", json={"client_id": cid, "asset_serial": "FRESH-1"}, headers=tenant)).json()
    r = await client.get(f"/api/orders/{o['id']}/comeback", headers=tenant)
    assert r.status_code == 200 and r.json()["is_comeback"] is False, r.text


@pytest.mark.asyncio
async def test_comeback_gated(client, tenant):
    cid = await _client(client, tenant, name="Gate Co")
    o = (await client.post("/api/orders", json={"client_id": cid, "asset_serial": "GATE-1"}, headers=tenant)).json()
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "No Comeback", "permissions": {"can_view_comeback_alerts": False}}, headers=tenant)).json()["id"]
    await client.post("/api/staff/employees", json={
        "name": "NoCb", "email": "nocb-144bl@test.dev", "password": "pw-nocb-12345", "role_id": rid}, headers=tenant)
    tok = (await client.post("/api/auth/token", data={"username": "nocb-144bl@test.dev", "password": "pw-nocb-12345"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = await client.get(f"/api/orders/{o['id']}/comeback", headers=h)
    assert r.status_code == 403, r.text
