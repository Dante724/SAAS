"""Costs stay with people allowed to see them: responses drop cost/margin fields for roles
without "View the purchase price", and their saves can't change (or wipe) a cost."""
import uuid

import pytest

from backend.services.redact import redact


async def _owner(client):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _tech(client, H):
    role = (await client.post("/api/settings/rbac/roles", json={"name": "Tech", "permissions": {"can_view_purchase_price": False}},
                              headers=H)).json()
    email = f"t-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", json={"name": "Tech", "email": email, "password": "pw-test-12345",
                                                         "role_id": role["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def test_redact_is_recursive():
    assert redact({"a": 1, "cost_price": 5, "lines": [{"margin": 3, "qty": 1}]}) == {"a": 1, "lines": [{"qty": 1}]}


@pytest.mark.asyncio
async def test_tech_never_sees_costs_and_cannot_change_them(client):
    H = await _owner(client)
    T = await _tech(client, H)
    svc = (await client.post("/api/catalog/services", json={"name": "Screen fit", "price": 100, "cost_price": 40}, headers=H)).json()
    assert svc["cost_price"] == 40                                   # owner sees it

    seen = (await client.get("/api/catalog/services", headers=T)).text
    assert "cost_price" not in seen and "Screen fit" in seen

    # the tech saves the service without a cost: the stored cost survives
    r = await client.put(f"/api/catalog/services/{svc['id']}", json={"name": "Screen fit", "price": 110, "cost_price": 0}, headers=T)
    assert r.status_code == 200, r.text
    assert "cost_price" not in r.text
    owner_view = (await client.get("/api/catalog/services", headers=H)).json()
    items = owner_view["items"] if isinstance(owner_view, dict) else owner_view
    assert next(s for s in items if s["id"] == svc["id"])["cost_price"] == 40

    # a line the tech adds takes the catalog cost, whatever they send
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={"line_type": "labour", "kind": "service", "ref_id": svc["id"],
                                                               "description": "Screen fit", "qty": 1, "unit_price": 110,
                                                               "cost_price": 1}, headers=T)
    assert r.status_code in (200, 201), r.text
    order = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert order["lines"][0]["cost_price"] == 40
    assert "cost_price" not in (await client.get(f"/api/orders/{o['id']}", headers=T)).text
