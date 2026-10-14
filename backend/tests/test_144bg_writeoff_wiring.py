"""144bg — Write-offs tab wiring fix.

Two bugs: the FE list read a non-existent /inventory/write-offs (404), and create 400'd because
the location had no is_default product warehouse. Backend now (a) falls back to any product/any
warehouse at the location, and (b) returns part_name + qty summary fields for the list view.
"""
import pytest
from sqlalchemy import select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _ensure_warehouse(client, h, db, loc):
    whs = (await client.get("/api/settings/warehouses", headers=h)).json().get("items", [])
    at_loc = [w for w in whs if w.get("location_id") == loc]
    if at_loc:
        return
    # create a non-default product warehouse → exercises the fallback (no is_default anywhere)
    await client.post("/api/settings/warehouses", json={"name": "Shelf", "location_id": loc, "kind": "product"}, headers=h)


async def _part_with_stock(client, h, loc, sku="WO-1", qty=10):
    p = (await client.post("/api/inventory/parts", json={"name": "WO Part", "sku": sku}, headers=h)).json()
    # receive stock so there's something to write off
    await client.post("/api/inventory/postings", json={
        "location_id": loc, "lines": [{"part_id": p["id"], "qty": qty, "purchase_price": 5}]}, headers=h)
    return p["id"]


@pytest.mark.asyncio
async def test_write_off_resolves_warehouse_from_location(client, tenant, db):
    loc = await _loc(client, tenant)
    await _ensure_warehouse(client, tenant, db, loc)
    pid = await _part_with_stock(client, tenant, loc, sku="WO-A")
    # create with ONLY location_id (what the FE sends) — must resolve a warehouse, not 400
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "reason": "damaged", "lines": [{"part_id": pid, "qty": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["warehouse_id"]


@pytest.mark.asyncio
async def test_write_off_list_has_summary_fields(client, tenant, db):
    loc = await _loc(client, tenant)
    await _ensure_warehouse(client, tenant, db, loc)
    pid = await _part_with_stock(client, tenant, loc, sku="WO-B")
    await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "reason": "lost", "lines": [{"part_id": pid, "qty": 3}]}, headers=tenant)
    lst = (await client.get("/api/stock-docs/write-offs", headers=tenant)).json()["items"]
    assert lst, "write-off should appear in the list"
    row = lst[0]
    assert row["reason"] == "lost"
    assert row["qty"] == 3                 # summary qty for the FE column
    assert row["part_name"] and row["part_name"] != "—"   # readable part name


@pytest.mark.asyncio
async def test_inventory_write_offs_path_is_gone(client, tenant):
    """The old FE URL must not exist — guards against anyone re-pointing to it."""
    r = await client.get("/api/inventory/write-offs", headers=tenant)
    assert r.status_code == 404
