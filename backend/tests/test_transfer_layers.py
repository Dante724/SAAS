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
    assert r.status_code == 200 and r.json()["status"] == "received", r.text   # verifying moves it on
    await _move(client, tenant, tid, "completed")
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


@pytest.mark.asyncio
async def test_a_phone_carries_its_own_layer_to_the_branch(client, tenant, db):
    from backend.models import SerializedItem
    from backend.tests.test_inventory_bins_passport_stocktake import SN, _product
    from backend.tests.test_inventory_bins_passport_stocktake import _receive as _post
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    p = (await _product(client, tenant, name="Phone", serialized=True))["id"]
    await _post(client, tenant, a, p, 1, cost=5, serials=[SN[0]])
    await _post(client, tenant, a, p, 1, cost=9, serials=[SN[1]])      # the newer, dearer phone moves
    t = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": p, "qty": 1, "serials": [SN[1]]}]}, headers=tenant)
    assert t.status_code == 201, t.text
    await _move(client, tenant, t.json()["id"], "ready", "in_transit", "received", "completed")
    assert await _layers(db, p, a) == [(1, 5)], await _layers(db, p, a)     # FIFO would have taken the 5
    assert await _layers(db, p, b) == [(1, 9)], await _layers(db, p, b)
    db.expire_all()
    u = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == p,
                                                       SerializedItem.location_id == b))).scalar_one()
    layer = await db.get(StockItemBatch, u.batch_id)
    assert (layer.location_id, float(layer.unit_cost)) == (b, 9), "the unit must point at its layer at the branch"


@pytest.mark.asyncio
async def test_short_phone_delivery_lands_the_phone_that_arrived_at_its_cost(client, tenant, db):
    from backend.tests.test_inventory_bins_passport_stocktake import SN, _product
    from backend.tests.test_inventory_bins_passport_stocktake import _receive as _post
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    p = (await _product(client, tenant, name="Phone", serialized=True))["id"]
    await _post(client, tenant, a, p, 1, cost=5, serials=[SN[2]])
    await _post(client, tenant, a, p, 1, cost=9, serials=[SN[3]])
    t = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": p, "qty": 2, "serials": [SN[2], SN[3]]}]},
                          headers=tenant)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    await _move(client, tenant, tid, "ready", "in_transit")
    r = await client.post(f"/api/transfers/{tid}/receive", headers=tenant,
                          json={"lines": [{"part_id": p, "qty_received": 1, "serials": [SN[3]]}]})
    assert r.status_code == 200, r.text
    await _move(client, tenant, tid, "completed")
    assert await _layers(db, p, b) == [(1, 9)], f"the 5 phone went missing, the 9 arrived: {await _layers(db, p, b)}"
    assert await _layers(db, p, a) == []


_URL = __import__("os").environ.get("TEST_DATABASE_URL") or __import__("os").environ.get("DATABASE_URL", "")


@pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks need Postgres")
@pytest.mark.asyncio
async def test_boxing_and_selling_the_same_units_at_once_keeps_layers_and_shelf_equal(client, tenant, db):
    """Two transfers and a sale grab the same 10 units at once. Each takes the stock rows first
    (sorted), then the layers, so nothing deadlocks, nothing is taken twice, and the source's
    layers still hold exactly what is on its shelf."""
    import asyncio

    from sqlalchemy import func

    from backend.models import StockLevel
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 6, 10)
    await _receive(client, tenant, a, pid, 4, 30)
    t1 = await _transfer(client, tenant, a, b, pid, 6)
    t2 = await _transfer(client, tenant, a, b, pid, 6)

    async def ready(tid):
        return await client.patch(f"/api/transfers/{tid}/status", json={"status": "ready"}, headers=tenant)

    async def sell():
        return await client.post("/api/sales", json={"lines": [
            {"kind": "product", "ref_id": pid, "description": "x", "qty": 6, "unit_price": 50}]}, headers=tenant)

    r1, r2, s = await asyncio.gather(ready(t1), ready(t2), sell())
    codes = [r1.status_code, r2.status_code, s.status_code]
    assert all(c in (200, 201, 409) for c in codes), [r1.text, r2.text, s.text]
    assert sum(c in (200, 201) for c in codes) == 1, codes          # 10 units: only one 6 fits
    db.expire_all()
    shelf = float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid, StockLevel.location_id == a))).scalar())
    assert shelf == 4
    assert sum(q for q, _ in await _layers(db, pid, a)) == 4, await _layers(db, pid, a)
    assert await _layers(db, pid, a) == [(4, 30)]                 # FIFO: the 6 @ 10 went, whoever won
