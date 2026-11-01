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


@pytest.mark.asyncio
async def test_rows_that_already_share_a_name_can_still_be_saved(client, tenant, db):
    """Council/review: shops with two "Tech" roles or warehouses from before the check (or made by
    the app itself) must still be able to edit them; only a change of name is checked."""
    from backend.models import OrderStatus, Role, Warehouse
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = me["org_id"]
    r1, r2 = Role(org_id=org, name="Tech", permissions={}), Role(org_id=org, name="Tech", permissions={})
    whs = (await client.get("/api/settings/warehouses", headers=tenant)).json()["items"]
    main = next(w for w in whs if w.get("location_id"))
    dup_wh = Warehouse(org_id=org, name=main["name"], kind="product", scope="location",
                       location_id=main["location_id"], allowed_actions=[])
    st = (await client.get("/api/statuses", headers=tenant)).json()
    st = st.get("items", st) if isinstance(st, dict) else st
    some = st[0]
    dup_st = OrderStatus(org_id=org, key="dup_" + some["key"], name=some["name"],
                         status_group=some["status_group"], record_type=some.get("record_type", "work_order"))
    db.add_all([r1, r2, dup_wh, dup_st])
    await db.commit()
    r = await client.put(f"/api/settings/rbac/roles/{r1.id}", json={"name": "Tech", "permissions": {}}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.put(f"/api/settings/warehouses/{main['id']}", json={
        "name": main["name"], "location_id": main["location_id"], "active": True}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.put(f"/api/statuses/{dup_st.id}", json={
        "name": some["name"], "status_group": some["status_group"], "color": "#123456"}, headers=tenant)
    assert r.status_code == 200, r.text
    # renaming onto a taken name is still refused, and the error says so plainly
    r = await client.put(f"/api/settings/rbac/roles/{r1.id}", json={"name": "admin", "permissions": {}},
                         headers=tenant)
    assert r.status_code == 409 and r.json()["detail"]["message"].startswith("There's already a role"), r.text


@pytest.mark.asyncio
async def test_warehouse_names_are_per_location(client, tenant):
    a = (await client.post("/api/staff/locations", json={"name": "Second shop"}, headers=tenant)).json()
    locs = (await client.get("/api/staff/locations", headers=tenant)).json()
    locs = locs["items"] if isinstance(locs, dict) else locs
    first = next(loc["id"] for loc in locs if loc["id"] != a["id"])
    for loc in (first, a["id"]):
        r = await client.post("/api/settings/warehouses", json={"name": "Back room", "location_id": loc},
                              headers=tenant)
        assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_moving_a_category_next_to_a_namesake_is_refused(client, tenant):
    parent = (await client.post("/api/catalog/categories", json={"name": "Phones"}, headers=tenant)).json()
    await client.post("/api/catalog/categories", json={"name": "Screens", "parent_id": parent["id"]}, headers=tenant)
    top = (await client.post("/api/catalog/categories", json={"name": "Screens"}, headers=tenant)).json()
    r = await client.patch(f"/api/catalog/categories/{top['id']}", json={"parent_id": parent["id"]}, headers=tenant)
    assert r.status_code == 409, r.text
    # a PATCH that changes nothing about name or place is fine
    r = await client.patch(f"/api/catalog/categories/{top['id']}", json={"template_key": None}, headers=tenant)
    assert r.status_code == 200, r.text
