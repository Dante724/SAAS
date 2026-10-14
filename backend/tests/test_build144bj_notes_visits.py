"""Build 144bj — tech notes + conclusion persist; device prior-visit count."""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bj Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def test_tech_notes_and_conclusion_persist(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={
        "tech_notes": "Replaced ribbon cable", "conclusion": "Advised screen protector"}, headers=H)
    assert r.status_code == 200
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert got["tech_notes"] == "Replaced ribbon cable"
    assert got["conclusion"] == "Advised screen protector"


async def test_device_visit_count(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=H)).json()["id"]
    dev = (await client.post("/api/assets", json={
        "owner_type": "client", "client_id": cid, "asset_type": "Phone",
        "brand": "Apple", "model": "iPhone", "serial": f"SN{uuid.uuid4().hex[:6]}"}, headers=H)).json()
    did = dev["id"]
    # two prior orders on this device
    for _ in range(2):
        await client.post("/api/orders", json={
            "client_id": cid, "location_id": loc, "device_id": did, "complaint": "x"}, headers=H)
    o3 = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "device_id": did, "complaint": "y"}, headers=H)).json()
    got = (await client.get(f"/api/orders/{o3['id']}", headers=H)).json()
    assert got["device"]["visit_count"] == 2   # the two earlier orders, excluding this one
