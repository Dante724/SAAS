"""Hardening council M3 (transfer half): a transfer moved `StockLevel` quantities but never the
FIFO cost layers. The units' layers stayed at the source (so a later sale there drew COGS from
stock that had left) and the destination had none (so its sales were valued at the average).
Now "ready" consumes the source layers and records each layer's cost on the transfer_out
movement, "completed" creates destination layers at those costs for what arrived, and a cancel
puts layers back where the goods return.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import StockItemBatch


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _loc2(client, h):
    r = await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _part(client, h):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _receive(client, h, loc, pid, qty, unit_cost):
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": unit_cost}, headers=h)
    assert r.status_code == 200, r.text


async def _transfer(client, h, a, b, pid, qty):
    t = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": pid, "qty": qty}]}, headers=h)
    assert t.status_code == 201, t.text
    return t.json()["id"]


async def _move(client, h, tid, *statuses, **extra):
    for st in statuses:
        r = await client.patch(f"/api/transfers/{tid}/status", json={"status": st, **extra}, headers=h)
        assert r.status_code == 200, f"{st}: {r.status_code} {r.text}"


async def _layers(db, pid, loc):
    """[(qty_remaining, unit_cost)] at the location, oldest first, empty layers dropped."""
    rows = (await db.execute(select(StockItemBatch).where(
        StockItemBatch.part_id == pid, StockItemBatch.location_id == loc)
        .order_by(StockItemBatch.received_at, StockItemBatch.id))).scalars().all()
    return [(float(b.qty_remaining), float(b.unit_cost)) for b in rows if float(b.qty_remaining) > 0]


@pytest.mark.asyncio
async def test_completed_transfer_moves_the_layers_at_their_costs(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 10, 10)
    await _receive(client, tenant, a, pid, 10, 30)
    tid = await _transfer(client, tenant, a, b, pid, 15)
    await _move(client, tenant, tid, "ready", "in_transit", "received", "completed")
    assert await _layers(db, pid, a) == [(5, 30)], f"source layers {await _layers(db, pid, a)}"
    assert await _layers(db, pid, b) == [(10, 10), (5, 30)], f"destination layers {await _layers(db, pid, b)}"


@pytest.mark.asyncio
async def test_ready_takes_the_layers_off_the_source(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 10, 10)
    tid = await _transfer(client, tenant, a, b, pid, 10)
    await _move(client, tenant, tid, "ready")
    assert await _layers(db, pid, a) == [], f"boxed units still on the source layers: {await _layers(db, pid, a)}"
    assert await _layers(db, pid, b) == []


@pytest.mark.asyncio
async def test_short_receipt_lands_only_what_arrived(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 10, 10)
    tid = await _transfer(client, tenant, a, b, pid, 10)
    await _move(client, tenant, tid, "ready", "in_transit")
    r = await client.post(f"/api/transfers/{tid}/receive", json={"lines": [{"part_id": pid, "qty_received": 7}]}, headers=tenant)
    assert r.status_code == 200, r.text
    await _move(client, tenant, tid, "received", "completed")
    assert await _layers(db, pid, b) == [(7, 10)], await _layers(db, pid, b)
    assert await _layers(db, pid, a) == []


@pytest.mark.asyncio
async def test_cancel_from_ready_restores_the_source_layers(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 10, 10)
    await _receive(client, tenant, a, pid, 10, 30)
    tid = await _transfer(client, tenant, a, b, pid, 15)
    await _move(client, tenant, tid, "ready")
    await _move(client, tenant, tid, "canceled")
    layers = await _layers(db, pid, a)
    assert sorted(layers, key=lambda x: x[1]) == [(10, 10), (10, 30)], layers


@pytest.mark.asyncio
async def test_cancel_in_transit_puts_the_layers_where_the_goods_go_back(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 10, 10)
    tid = await _transfer(client, tenant, a, b, pid, 10)
    await _move(client, tenant, tid, "ready", "in_transit")
    await _move(client, tenant, tid, "canceled", return_location_id=b)
    assert await _layers(db, pid, a) == []
    assert await _layers(db, pid, b) == [(10, 10)], await _layers(db, pid, b)
