"""Build 144bk — store price vs repair price on products."""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bk Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_repair_price_persists_and_search_exposes_it(client):
    H = await _tenant(client)
    p = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": f"S-{uuid.uuid4().hex[:6]}",
        "sell_price": 100, "repair_price": 130, "cost_price": 40}, headers=H)).json()
    got = (await client.get(f"/api/inventory/parts/{p['id']}", headers=H)).json()
    assert got["sell_price"] == 100 and got["repair_price"] == 130
    # catalog search exposes the WO price
    res = (await client.get("/api/catalog/search?q=Screen&type=product", headers=H)).json()["items"]
    row = [x for x in res if x["ref_id"] == p["id"]][0]
    assert row["price"] == 100          # store price
    assert row["repair_price"] == 130   # WO price


async def test_repair_price_falls_back_to_store(client):
    H = await _tenant(client)
    p = (await client.post("/api/inventory/parts", json={
        "name": "Cable", "sku": f"C-{uuid.uuid4().hex[:6]}", "sell_price": 60}, headers=H)).json()
    res = (await client.get("/api/catalog/search?q=Cable&type=product", headers=H)).json()["items"]
    row = [x for x in res if x["ref_id"] == p["id"]][0]
    assert row["repair_price"] == 60    # no repair price set → falls back to store
