"""Build 144aa — min order qty, bulk create-PO, and the conversions-history fix."""
import pytest


async def _supplier(client, tenant):
    r = await client.post("/api/suppliers", json={"name": "Acme Parts"}, headers=tenant)
    return r.json()["id"]


async def _location(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_min_order_qty_persists(client, tenant):
    pid = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": "SC-1", "cost_price": 10, "sell_price": 25,
        "min_stock": 3, "min_order_qty": 12}, headers=tenant)).json()["id"]
    part = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert part["min_order_qty"] == 12
    # editable
    await client.put(f"/api/inventory/parts/{pid}", json={
        "name": "Screen", "sku": "SC-1", "min_order_qty": 20}, headers=tenant)
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["min_order_qty"] == 20


@pytest.mark.asyncio
async def test_create_po_from_selected_products(client, tenant):
    sup = await _supplier(client, tenant)
    loc = await _location(client, tenant)
    p1 = (await client.post("/api/inventory/parts", json={"name": "Cable", "cost_price": 2}, headers=tenant)).json()["id"]
    p2 = (await client.post("/api/inventory/parts", json={"name": "Fuse", "cost_price": 1}, headers=tenant)).json()["id"]
    r = await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": p1, "qty_ordered": 10, "unit_cost": 2},
                  {"part_id": p2, "qty_ordered": 5, "unit_cost": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    po_id = r.json()["po_id"]
    pos = (await client.get("/api/inventory/po", headers=tenant)).json()["items"]
    po = next(p for p in pos if p["id"] == po_id)
    assert len(po["lines"]) == 2 and po["total"] == 25   # 10*2 + 5*1


@pytest.mark.asyncio
async def test_create_po_rejects_unknown_part_and_supplier(client, tenant):
    sup = await _supplier(client, tenant)
    loc = await _location(client, tenant)
    pid = (await client.post("/api/inventory/parts", json={"name": "Real", "cost_price": 1}, headers=tenant)).json()["id"]
    # unknown part_id → 422 (QA hardening: PO lines must be the org's own parts)
    r = await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": "not-mine", "qty_ordered": 1, "unit_cost": 1}]}, headers=tenant)
    assert r.status_code == 422
    # unknown supplier → 404
    r2 = await client.post("/api/inventory/po", json={
        "supplier_id": "nope", "location_id": loc,
        "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 1}]}, headers=tenant)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_convert_asset_to_product_shows_in_conversions_history(client, tenant):
    loc = await _location(client, tenant)
    # a company-owned asset
    aid = (await client.post("/api/assets", json={
        "owner_type": "company", "asset_type": "Device", "brand": "Acme",
        "model": "X1", "serial": "SN-777", "location_id": loc}, headers=tenant)).json()["id"]
    # convert it to a product (the path the inventory UI uses)
    r = await client.post(f"/api/assets/{aid}/convert-to-product", json={"sale_price": 50}, headers=tenant)
    assert r.status_code == 201, r.text

    # the conversion now appears in the history the Conversions page reads
    convs = (await client.get("/api/conversions", headers=tenant)).json()["items"]
    assert any(c["direction"] == "asset_to_product" and c["device_id"] == aid for c in convs), convs
    mine = next(c for c in convs if c["device_id"] == aid)
    assert mine["serial_identifier"] == "SN-777" and mine["status"] == "completed"
