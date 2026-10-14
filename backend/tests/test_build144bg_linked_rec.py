"""Build 144bg — a catalog-linked recommendation keeps its ref_id, and on approval
the created order line carries that ref_id (so it behaves like a picked catalog line)."""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bg Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def test_linked_recommendation_carries_ref_id_to_line(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    # a catalog product to link to
    part = (await client.post("/api/inventory/parts", json={
        "name": "Battery", "sku": f"B-{uuid.uuid4().hex[:6]}", "sell_price": 120, "cost_price": 40},
        headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    # recommendation linked to the part (kind product, ref_id)
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "Battery", "kind": "product", "ref_id": part["id"],
        "unit_price": 120, "qty": 1}, headers=H)).json()
    assert rec["ref_id"] == part["id"]
    await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", json={}, headers=H)
    # customer approves → order line links the same catalog part
    await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                      json={"approve_ids": [rec["id"]], "decline_ids": []})
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    line = [ln for ln in got["lines"] if ln["description"] == "Battery"][0]
    assert line["ref_id"] == part["id"]
    assert line["kind"] == "product"
