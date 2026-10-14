"""Build 118 — product lifecycle detail: stock, movements/history, purchase orders."""
import pytest


async def _wh(client, H):
    items = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"]
    return next(w for w in items if w["is_default"])


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _part(client, H, name="Widget", sku="PD-1"):
    return (await client.post("/api/inventory/parts", json={
        "name": name, "sku": sku, "cost_price": 5, "sell_price": 10}, headers=H)).json()


@pytest.mark.asyncio
async def test_part_stock_endpoint(client, tenant):
    p = await _part(client, tenant)
    loc = await _loc(client, tenant)
    await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 7, "movement_type": "receive"}, headers=tenant)
    d = (await client.get(f"/api/inventory/parts/{p['id']}/stock", headers=tenant)).json()
    assert d["total_available"] == 7
    assert len(d["items"]) >= 1


@pytest.mark.asyncio
async def test_part_movements_history(client, tenant):
    p = await _part(client, tenant, sku="PD-2")
    loc = await _loc(client, tenant)
    await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 5, "movement_type": "receive"}, headers=tenant)
    rows = (await client.get(f"/api/inventory/parts/{p['id']}/movements", headers=tenant)).json()
    assert isinstance(rows, list) and len(rows) >= 1
    # enriched fields present
    m = rows[0]
    assert "qty" in m and "at" in m and "location" in m and "unit_cost" in m


@pytest.mark.asyncio
async def test_part_stock_404_for_unknown(client, tenant):
    r = await client.get("/api/inventory/parts/nope/stock", headers=tenant)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_part_purchase_orders_endpoint(client, tenant):
    p = await _part(client, tenant, sku="PD-3")
    d = (await client.get(f"/api/inventory/parts/{p['id']}/purchase-orders", headers=tenant)).json()
    assert "items" in d and isinstance(d["items"], list)   # empty is fine
