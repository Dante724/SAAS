"""Build 144ao — managed product categories: create/rename/delete, counts, name-sync,
template scoping, and inventory/catalog filtering by category_id."""
import pytest


async def _mk_cat(client, tenant, name, template_key=None):
    body = {"entity_type": "product", "name": name}
    if template_key:
        body["template_key"] = template_key
    return (await client.post("/api/catalog/categories", json=body, headers=tenant)).json()


@pytest.mark.asyncio
async def test_create_assign_count_and_filter(client, tenant):
    cat = await _mk_cat(client, tenant, "Screens")
    assert cat["id"]
    # a product assigned to it
    p = (await client.post("/api/catalog/products", json={"name": "iPhone 13 screen", "sell_price": 6499, "category_id": cat["id"]}, headers=tenant)).json()
    pid = p["product"]["id"] if "product" in p else p["id"]

    # the managed tree now shows a count of 1
    tree = (await client.get("/api/catalog/categories?entity_type=product", headers=tenant)).json()["tree"]
    node = next(c for c in tree if c["id"] == cat["id"])
    assert node["count"] == 1

    # catalog filter by category_id
    lst = (await client.get(f"/api/catalog/products?category_id={cat['id']}", headers=tenant)).json()
    assert any(i["id"] == pid for i in lst["items"])

    # inventory list filter by category_id + the name mirror is synced for POS grouping
    inv = (await client.get(f"/api/inventory/parts?category_id={cat['id']}", headers=tenant)).json()
    row = next(i for i in inv["items"] if i["id"] == pid)
    assert row["category"] == "Screens" and row["category_id"] == cat["id"]


@pytest.mark.asyncio
async def test_rename_updates_product_mirror(client, tenant):
    cat = await _mk_cat(client, tenant, "Btteries")  # typo on purpose
    p = (await client.post("/api/catalog/products", json={"name": "S22 battery", "sell_price": 2199, "category_id": cat["id"]}, headers=tenant)).json()
    pid = p["product"]["id"] if "product" in p else p["id"]
    r = await client.patch(f"/api/catalog/categories/{cat['id']}", json={"name": "Batteries"}, headers=tenant)
    assert r.status_code == 200 and r.json()["name"] == "Batteries"
    inv = (await client.get(f"/api/inventory/parts?category_id={cat['id']}", headers=tenant)).json()
    assert next(i for i in inv["items"] if i["id"] == pid)["category"] == "Batteries"


@pytest.mark.asyncio
async def test_delete_blocked_when_in_use(client, tenant):
    cat = await _mk_cat(client, tenant, "Cables")
    await client.post("/api/catalog/products", json={"name": "USB-C cable", "sell_price": 499, "category_id": cat["id"]}, headers=tenant)
    busy = await client.delete(f"/api/catalog/categories/{cat['id']}", headers=tenant)
    assert busy.status_code == 409
    # an empty category deletes cleanly
    empty = await _mk_cat(client, tenant, "Empty")
    assert (await client.delete(f"/api/catalog/categories/{empty['id']}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_template_scoping(client, tenant):
    await _mk_cat(client, tenant, "Shared")
    await _mk_cat(client, tenant, "Tyres", template_key="vehicle")
    # filtering by a template shows shared + that template's categories, not others' only
    veh_tree = (await client.get("/api/catalog/categories?entity_type=product&template_key=vehicle", headers=tenant)).json()["tree"]
    names = {c["name"] for c in veh_tree}
    assert "Shared" in names and "Tyres" in names
    other_tree = (await client.get("/api/catalog/categories?entity_type=product&template_key=phone", headers=tenant)).json()["tree"]
    onames = {c["name"] for c in other_tree}
    assert "Shared" in onames and "Tyres" not in onames
