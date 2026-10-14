"""Build 144ap — bulk product actions from the Products multi-select."""
import pytest


async def _cat(client, tenant, name):
    return (await client.post("/api/catalog/categories", json={"entity_type": "product", "name": name}, headers=tenant)).json()["id"]


async def _prod(client, tenant, name, price):
    r = (await client.post("/api/catalog/products", json={"name": name, "sell_price": price}, headers=tenant)).json()
    return r["product"]["id"] if "product" in r else r["id"]


@pytest.mark.asyncio
async def test_bulk_move_price_delete(client, tenant):
    cat = await _cat(client, tenant, "Screens")
    a = await _prod(client, tenant, "Screen A", 100)
    b = await _prod(client, tenant, "Screen B", 200)

    # move both into the category (name mirror should sync)
    r = await client.post("/api/catalog/products/bulk", json={"ids": [a, b], "action": "category", "category_id": cat}, headers=tenant)
    assert r.status_code == 200 and r.json()["count"] == 2
    inv = (await client.get(f"/api/inventory/parts?category_id={cat}", headers=tenant)).json()
    assert {i["id"] for i in inv["items"]} >= {a, b}
    assert all(i["category"] == "Screens" for i in inv["items"] if i["id"] in (a, b))

    # set absolute price
    await client.post("/api/catalog/products/bulk", json={"ids": [a], "action": "price", "price_set": 149}, headers=tenant)
    # adjust by percent
    await client.post("/api/catalog/products/bulk", json={"ids": [b], "action": "price", "price_pct": 10}, headers=tenant)
    lst = (await client.get("/api/catalog/products", headers=tenant)).json()["items"]
    by = {i["id"]: i for i in lst}
    assert by[a]["sell_price"] == 149
    assert round(by[b]["sell_price"], 2) == 220.0

    # soft-delete → drops out of the catalog list
    d = await client.post("/api/catalog/products/bulk", json={"ids": [a, b], "action": "delete"}, headers=tenant)
    assert d.json()["count"] == 2
    remaining = {i["id"] for i in (await client.get("/api/catalog/products", headers=tenant)).json()["items"]}
    assert a not in remaining and b not in remaining


@pytest.mark.asyncio
async def test_bulk_guards(client, tenant):
    empty = await client.post("/api/catalog/products/bulk", json={"ids": [], "action": "category"}, headers=tenant)
    assert empty.status_code == 422
    a = await _prod(client, tenant, "Solo", 10)
    bad = await client.post("/api/catalog/products/bulk", json={"ids": [a], "action": "nope"}, headers=tenant)
    assert bad.status_code == 400
