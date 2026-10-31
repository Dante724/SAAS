"""QA fix E5 — role, category, warehouse and status names are unique (case-insensitive) → 409.

Found in the 5-shop QA run (31 Oct 2026): all four accepted a second row with the same name,
which then can't be told apart in pickers and reports.
"""
import pytest


@pytest.mark.asyncio
async def test_role_names_unique(client, tenant):
    assert (await client.post("/api/settings/rbac/roles", json={"name": "Front desk", "permissions": {}},
                              headers=tenant)).status_code == 201
    r = await client.post("/api/settings/rbac/roles", json={"name": " front DESK ", "permissions": {}}, headers=tenant)
    assert r.status_code == 409, r.text
    other = (await client.post("/api/settings/rbac/roles", json={"name": "Tech", "permissions": {}}, headers=tenant)).json()
    r = await client.put(f"/api/settings/rbac/roles/{other['id']}", json={"name": "Front Desk", "permissions": {}},
                         headers=tenant)
    assert r.status_code == 409, r.text
    # saving a role under its own name is fine
    r = await client.put(f"/api/settings/rbac/roles/{other['id']}", json={"name": "TECH", "permissions": {}},
                         headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_sibling_category_names_unique(client, tenant):
    a = (await client.post("/api/catalog/categories", json={"name": "Screens"}, headers=tenant)).json()
    r = await client.post("/api/catalog/categories", json={"name": "screens"}, headers=tenant)
    assert r.status_code == 409, r.text
    # same name under a different parent, or for services, is a different category
    assert (await client.post("/api/catalog/categories", json={"name": "Screens", "parent_id": a["id"]},
                              headers=tenant)).status_code == 201
    assert (await client.post("/api/catalog/categories", json={"name": "Screens", "entity_type": "service"},
                              headers=tenant)).status_code == 201
    b = (await client.post("/api/catalog/categories", json={"name": "Batteries"}, headers=tenant)).json()
    r = await client.patch(f"/api/catalog/categories/{b['id']}", json={"name": "SCREENS"}, headers=tenant)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_warehouse_names_unique(client, tenant):
    locs = (await client.get("/api/staff/locations", headers=tenant)).json()
    loc = (locs["items"] if isinstance(locs, dict) else locs)[0]["id"]
    assert (await client.post("/api/settings/warehouses", json={"name": "Back room", "location_id": loc},
                              headers=tenant)).status_code == 201
    r = await client.post("/api/settings/warehouses", json={"name": "back room", "location_id": loc},
                          headers=tenant)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_status_names_unique_per_record_type(client, tenant):
    body = {"name": "Waiting on parts QA", "status_group": "in_progress"}
    r = await client.post("/api/statuses", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post("/api/statuses", json={**body, "name": "waiting on PARTS qa"}, headers=tenant)
    assert r.status_code == 409, r.text
