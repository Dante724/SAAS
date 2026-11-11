"""Hardening council M3 (stocktake half): posting a count adjusted `StockLevel` and wrote a
movement but never touched the FIFO cost layers. A shortage left the layers full, so the next
sale drew COGS from units that were no longer on the shelf; a surplus had no layer at all.
Now a shortage consumes layers (FIFO/FEFO, like a write-off) and a surplus gets a layer at the
average cost, so the layers always hold exactly what the count said is there.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import StockItemBatch, StockLevel


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


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


async def _count(client, h, loc, pid, counted):
    st = await client.post("/api/stocktake", json={"location_id": loc, "kind": "full"}, headers=h)
    assert st.status_code == 201, st.text
    sid = st.json()["id"]
    line = next(ln for ln in (await client.get(f"/api/stocktake/{sid}", headers=h)).json()["lines"] if ln["part_id"] == pid)
    r = await client.patch(f"/api/stocktake/{sid}/lines/{line['id']}", json={"counted_qty": counted}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/stocktake/{sid}/post", json={}, headers=h)
    assert r.status_code == 200, r.text


async def _on_hand(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid))).scalar())


async def _layers(db, pid):
    return [(float(b.qty_remaining), float(b.unit_cost)) for b in (await db.execute(
        select(StockItemBatch).where(StockItemBatch.part_id == pid).order_by(StockItemBatch.received_at))).scalars()]


async def _sell(client, h, pid, qty):
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "product", "ref_id": pid, "description": "x", "qty": qty, "unit_price": 50}]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["total_cost"]


@pytest.mark.asyncio
async def test_shortage_consumes_the_cost_layers(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 10, 10)
    await _count(client, tenant, loc, pid, 4)
    assert await _on_hand(db, pid) == 4
    layers = await _layers(db, pid)
    assert sum(q for q, _ in layers) == 4, f"on_hand=4 but cost layers hold {layers}"


@pytest.mark.asyncio
async def test_shortage_drains_oldest_layer_first(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 10, 10)
    await _receive(client, tenant, loc, pid, 10, 30)
    await _count(client, tenant, loc, pid, 12)       # 8 missing: FIFO takes 8 of the 10 @ 10
    assert await _layers(db, pid) == [(2, 10), (10, 30)]


@pytest.mark.asyncio
async def test_shortage_then_fifo_cogs_is_right(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 10, 10)
    await _count(client, tenant, loc, pid, 0)
    await _receive(client, tenant, loc, pid, 10, 30)
    # every unit on the shelf cost 30; selling 5 must book COGS 150
    cogs = await _sell(client, tenant, pid, 5)
    assert abs(cogs - 150) < 0.01, f"COGS={cogs}, expected 150 (sold from the phantom 10@10 layer)"


@pytest.mark.asyncio
async def test_surplus_gets_a_layer_at_average_cost(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 10, 10)
    await _receive(client, tenant, loc, pid, 10, 30)      # avg 20
    await _count(client, tenant, loc, pid, 25)            # 5 found
    assert await _on_hand(db, pid) == 25
    layers = await _layers(db, pid)
    assert sum(q for q, _ in layers) == 25, f"on_hand=25 but cost layers hold {layers}"
    assert layers[-1] == (5, 20), layers
    # selling all 25 books 100 + 300 + 100 = 500, no unit valued twice or at zero
    cogs = await _sell(client, tenant, pid, 25)
    assert abs(cogs - 500) < 0.01, cogs
