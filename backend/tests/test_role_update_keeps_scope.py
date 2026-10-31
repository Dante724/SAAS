"""Updating a role must not widen it: fields the client leaves out keep their current value
(the old role editor never sent location or account limits, so saving reset them to "all")."""
import pytest


@pytest.mark.asyncio
async def test_update_without_scope_fields_keeps_them(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Branch tech", "location_scope": "selected", "location_ids": [loc],
        "order_scope": "own", "account_ids": ["acc-1"], "hidden_status_keys": ["dropped"]}, headers=tenant)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    # A client that only knows about name + permissions (like the old editor)
    u = await client.put(f"/api/settings/rbac/roles/{rid}", json={"name": "Branch technician", "permissions": {}}, headers=tenant)
    assert u.status_code == 200, u.text
    d = u.json()
    assert d["name"] == "Branch technician"
    assert d["location_scope"] == "selected" and d["location_ids"] == [loc]
    assert d["order_scope"] == "own" and d["account_ids"] == ["acc-1"] and d["hidden_status_keys"] == ["dropped"]

    # Sending a field still changes it
    w = await client.put(f"/api/settings/rbac/roles/{rid}", json={"name": "Branch technician", "location_scope": "all", "location_ids": []}, headers=tenant)
    assert w.json()["location_scope"] == "all" and w.json()["location_ids"] == []
    assert w.json()["order_scope"] == "own"

    bad = await client.put(f"/api/settings/rbac/roles/{rid}", json={"name": "x", "location_scope": "everywhere"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_role_in_use_cannot_be_deleted(client, tenant):
    rid = (await client.post("/api/settings/rbac/roles", json={"name": "Front desk"}, headers=tenant)).json()["id"]
    emp = await client.post("/api/staff/employees", json={"name": "Dana", "email": "dana@shop.test", "role_id": rid}, headers=tenant)
    assert emp.status_code in (200, 201), emp.text
    r = await client.delete(f"/api/settings/rbac/roles/{rid}", headers=tenant)
    assert r.status_code == 409 and "another role" in r.json()["detail"]

    await client.patch(f"/api/staff/employees/{emp.json()['id']}", json={"name": "Dana", "email": "dana@shop.test", "role_id": None}, headers=tenant)
    assert (await client.delete(f"/api/settings/rbac/roles/{rid}", headers=tenant)).status_code == 200
