"""Build 144bl — an approved recommendation's order line is attributed to the
recommender, so the existing per-line commission engine pays them."""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bl Co", "name": "Advisor", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def test_approved_rec_line_attributed_to_recommender(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    my_id = me["id"]
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "Coolant flush", "kind": "service", "unit_price": 45}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", json={}, headers=H)
    await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                      json={"approve_ids": [rec["id"]], "decline_ids": []})
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    line = [ln for ln in got["lines"] if ln["description"] == "Coolant flush"][0]
    assert line["technician_id"] == my_id   # commission attribution follows the recommender
