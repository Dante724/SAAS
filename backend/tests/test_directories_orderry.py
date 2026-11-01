"""144hi — directory binding, per-location items, CSV import/export, merge."""


async def _dir(client, tenant, key="test_dir", name="Test dir"):
    d = await client.post("/api/directories", json={"key": key, "name": name}, headers=tenant)
    return d.json()["id"]


async def test_per_location_visibility(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    did = await _dir(client, tenant, "locdir", "Loc dir")
    await client.post(f"/api/directories/{did}/items", json={"value": "Global"}, headers=tenant)
    await client.post(f"/api/directories/{did}/items", json={"value": "OnlyHere", "location_id": loc}, headers=tenant)
    # a different (fake) location sees only the global item
    other = await client.get(f"/api/directories/{did}/items?location_id=nonexistent", headers=tenant)
    vals = [i["value"] for i in other.json()["items"]]
    assert "Global" in vals and "OnlyHere" not in vals, vals
    # this location sees both
    here = await client.get(f"/api/directories/{did}/items?location_id={loc}", headers=tenant)
    hv = [i["value"] for i in here.json()["items"]]
    assert "Global" in hv and "OnlyHere" in hv, hv


async def test_csv_import_export_with_parents(client, tenant):
    did = await _dir(client, tenant, "csvdir", "CSV dir")
    csv_in = "value,parent\nSamsung,\nGalaxy S24,Samsung\n"
    r = await client.post(f"/api/directories/{did}/import", json={"csv": csv_in}, headers=tenant)
    assert r.status_code == 200 and r.json()["added"] == 2, r.text
    items = (await client.get(f"/api/directories/{did}/items", headers=tenant)).json()["items"]
    child = [i for i in items if i["value"] == "Galaxy S24"][0]
    parent = [i for i in items if i["value"] == "Samsung"][0]
    assert child["parent_id"] == parent["id"]           # parent linked by value
    exp = await client.get(f"/api/directories/{did}/export", headers=tenant)
    assert exp.status_code == 200 and "Galaxy S24,Samsung" in exp.text


async def test_merge_reparents_children(client, tenant):
    did = await _dir(client, tenant, "mergedir", "Merge dir")
    a = (await client.post(f"/api/directories/{did}/items", json={"value": "Keep"}, headers=tenant)).json()["id"]
    b = (await client.post(f"/api/directories/{did}/items", json={"value": "Dupe"}, headers=tenant)).json()["id"]
    await client.post(f"/api/directories/{did}/items", json={"value": "Child", "parent_id": b}, headers=tenant)
    r = await client.post(f"/api/directories/items/{b}/merge", json={"target_id": a}, headers=tenant)
    assert r.status_code == 200 and r.json()["reparented"] == 1, r.text
    items = (await client.get(f"/api/directories/{did}/items", headers=tenant)).json()["items"]
    vals = [i["value"] for i in items]
    assert "Dupe" not in vals and "Keep" in vals            # source retired
    child = [i for i in items if i["value"] == "Child"][0]
    assert child["parent_id"] == a                          # moved to the kept element


async def test_field_directory_binding_round_trips(client, tenant):
    types = (await client.get("/api/forms/types", headers=tenant)).json()["items"]
    tid = types[0]["id"] if types else (await client.post(
        "/api/forms/types", json={"name": "Repair"}, headers=tenant)).json()["id"]
    f = await client.post(f"/api/forms/types/{tid}/fields", json={
        "field_label": "Brand", "field_type": "select", "directory_key": "manufacturer",
        "parent_field_key": "group"}, headers=tenant)
    assert f.status_code == 201, f.text
    assert f.json()["directory_key"] == "manufacturer"
    assert f.json()["parent_field_key"] == "group"       # 144hj cascade round-trips


async def test_inline_add_by_key(client, tenant):
    # any staff can extend a directory from a form dialog; dupes return existing
    r = await client.post("/api/directories/by-key/lead_source/items", json={"value": "Nextdoor"}, headers=tenant)
    assert r.status_code == 201, r.text
    again = await client.post("/api/directories/by-key/lead_source/items", json={"value": "nextdoor"}, headers=tenant)
    assert again.json().get("existing") is True


async def test_safe_delete_blocks_orphaning(client, tenant):
    did = await _dir(client, tenant, "safedir", "Safe dir")
    p = (await client.post(f"/api/directories/{did}/items", json={"value": "Parent"}, headers=tenant)).json()["id"]
    await client.post(f"/api/directories/{did}/items", json={"value": "Kid", "parent_id": p}, headers=tenant)
    blocked = await client.delete(f"/api/directories/items/{p}", headers=tenant)
    assert blocked.status_code == 409                     # would orphan a child
    forced = await client.delete(f"/api/directories/items/{p}?force=true", headers=tenant)
    assert forced.status_code == 200 and forced.json()["unparented"] == 1
    items = (await client.get(f"/api/directories/{did}/items", headers=tenant)).json()["items"]
    assert [i for i in items if i["value"] == "Kid"][0]["parent_id"] is None
