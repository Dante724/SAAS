"""Hardening council M5: editing a write-off line *down* gave the units back to the shelf
(`_give_stock`) but never put them back on the FIFO cost layers (`_restore_batches`), so
on-hand and the layers drifted apart and the next sale drew COGS from the wrong layer.
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
    return await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": unit_cost}, headers=h)


async def _on_hand(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid))).scalar())


async def _layers(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockItemBatch.qty_remaining), 0)).where(
        StockItemBatch.part_id == pid))).scalar())


@pytest.mark.asyncio
async def test_writeoff_line_edited_down_restores_cost_layers(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, 10, 10)).status_code == 200
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 5}]}, headers=tenant)
    assert r.status_code == 201, r.text
    doc, ln = r.json()["id"], r.json()["lines"][0]["id"]
    assert await _layers(db, pid) == 5

    r = await client.patch(f"/api/stock-docs/write-offs/{doc}/lines/{ln}", json={"qty": 2}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, pid) == 8
    assert await _layers(db, pid) == 8, f"on_hand=8 but the cost layers hold {await _layers(db, pid)}"


@pytest.mark.asyncio
async def test_writeoff_line_edited_up_still_drains_layers(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, 10, 10)).status_code == 200
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 2}]}, headers=tenant)
    assert r.status_code == 201, r.text
    doc, ln = r.json()["id"], r.json()["lines"][0]["id"]

    r = await client.patch(f"/api/stock-docs/write-offs/{doc}/lines/{ln}", json={"qty": 6}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, pid) == 4
    assert await _layers(db, pid) == 4


@pytest.mark.asyncio
async def test_writeoff_edit_down_then_sale_books_fifo_cogs_from_the_restored_layer(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, 10, 10)).status_code == 200
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 10}]}, headers=tenant)
    assert r.status_code == 201, r.text
    doc, ln = r.json()["id"], r.json()["lines"][0]["id"]
    # the count was wrong: only 6 were actually scrapped, 4 go back on the shelf
    assert (await client.patch(f"/api/stock-docs/write-offs/{doc}/lines/{ln}", json={"qty": 6},
                               headers=tenant)).status_code == 200
    assert (await _receive(client, tenant, loc, pid, 10, 30)).status_code == 200
    # 4 @ 10 came back on the layer, then 10 @ 30: selling 5 is FIFO 4×10 + 1×30 = 70
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "product", "ref_id": pid, "description": "x", "qty": 5, "unit_price": 50}]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert abs(r.json()["total_cost"] - 70) < 0.01, f"COGS={r.json()['total_cost']}, expected 70"
