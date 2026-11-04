"""Hardening council: the /api/bins cost-layer routes must not bypass stock.

`POST /api/bins/batches` used to write a StockItemBatch (+ serialized units) with no StockLevel
and no StockMovement, so units sat "in stock" that no shelf count knew about. `/bins/consume`
drained FIFO layers (open to any employee) and `/bins/allocate-serial` flipped a unit to
allocated, neither moving a single unit of stock. Nothing in the app calls the last two.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import SerializedItem, StockItemBatch, StockLevel, StockMovement


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, serialized=False):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100, "serialized": serialized}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _sum(db, col, pid):
    return float((await db.execute(select(func.coalesce(func.sum(col), 0)).where(
        col.class_.part_id == pid))).scalar())


@pytest.mark.asyncio
async def test_bins_batches_moves_stock_with_the_layer(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, serialized=True)
    r = await client.post("/api/bins/batches", json={
        "part_id": pid, "location_id": loc, "unit_cost": 50, "qty": 2,
        "serials": ["IMEI-A1", "IMEI-A2"]}, headers=tenant)
    assert r.status_code == 201, r.text
    on_hand = await _sum(db, StockLevel.qty_available, pid)
    layers = await _sum(db, StockItemBatch.qty_remaining, pid)
    moves = await _sum(db, StockMovement.qty, pid)
    units = (await db.execute(select(func.count()).select_from(SerializedItem).where(
        SerializedItem.part_id == pid, SerializedItem.status == "in_stock"))).scalar()
    # invariant: on-hand == in-stock units == cost layers == sum of movements
    assert (on_hand, units, layers, moves) == (2, 2, 2, 2), \
        f"on_hand={on_hand} units={units} layers={layers} movements={moves}"
    prod = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert prod["qty_available"] == 2
    assert float(prod.get("avg_cost") or 0) == 50


@pytest.mark.asyncio
async def test_bins_batches_refuses_another_shops_location_and_bad_quantities(client, tenant, db):
    pid = await _part(client, tenant)
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    other_loc = await _loc(client, {"Authorization": f"Bearer {r.json()['access_token']}"})
    r = await client.post("/api/bins/batches", json={
        "part_id": pid, "location_id": other_loc, "unit_cost": 5, "qty": 1}, headers=tenant)
    assert r.status_code == 404, r.text
    loc = await _loc(client, tenant)
    for bad in ({"qty": 0}, {"qty": -2}, {"unit_cost": -1}):
        r = await client.post("/api/bins/batches", json={"part_id": pid, "location_id": loc, "unit_cost": 5,
                                                         "qty": 1, **bad}, headers=tenant)
        assert r.status_code == 422, (bad, r.text)
    assert await _sum(db, StockLevel.qty_available, pid) == 0


@pytest.mark.asyncio
async def test_bins_consume_and_allocate_serial_no_longer_exist(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 5, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text
    r = await client.post("/api/bins/consume", json={"part_id": pid, "qty": 5}, headers=tenant)
    assert r.status_code in (404, 405), r.text
    assert (await _sum(db, StockLevel.qty_available, pid), await _sum(db, StockItemBatch.qty_remaining, pid)) == (5, 5)
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post("/api/bins/allocate-serial", json={"part_id": pid, "order_id": oid}, headers=tenant)
    assert r.status_code in (404, 405), r.text
