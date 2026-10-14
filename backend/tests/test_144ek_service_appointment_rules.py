"""Build 144ek — service appointment rules: prices-by-type (reuses /api/pricing),
allowed employees (enforced on public appointment), required resource types (stored)."""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_service_round_trips_appointment_rules(client):
    reg = await _register(client)
    H = {"Authorization": f"Bearer {reg['access_token']}"}
    s = (await client.post("/api/catalog/services", json={
        "name": "Alignment", "allowed_employee_ids": ["e1", "e2"],
        "required_resource_types": ["Lift", "Bay 2"]}, headers=H)).json()
    assert s["allowed_employee_ids"] == ["e1", "e2"]
    assert s["required_resource_types"] == ["Lift", "Bay 2"]
    # update clears them
    u = (await client.put(f"/api/catalog/services/{s['id']}", json={
        "name": "Alignment", "allowed_employee_ids": [], "required_resource_types": []}, headers=H)).json()
    assert u["allowed_employee_ids"] == [] and u["required_resource_types"] == []


@pytest.mark.asyncio
async def test_price_type_value_persists_for_service(client):
    reg = await _register(client)
    H = {"Authorization": f"Bearer {reg['access_token']}"}
    s = (await client.post("/api/catalog/services", json={"name": "Diag"}, headers=H)).json()
    pt = (await client.post("/api/pricing/types", json={"name": "VIP", "markup_pct": 0}, headers=H)).json()
    r = await client.put(f"/api/pricing/types/{pt['id']}/items",
                         json={"item_type": "service", "item_id": s["id"], "price": 99}, headers=H)
    assert r.status_code == 200, r.text
    items = (await client.get(f"/api/pricing/types/{pt['id']}/items", headers=H)).json()["items"]
    hit = [i for i in items if i["item_id"] == s["id"]]
    assert hit and hit[0]["price"] == 99
