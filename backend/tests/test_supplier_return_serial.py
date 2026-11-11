"""Hardening council M8: a supplier return of a serialized product took the aggregate stock but
never required the unit, so the IMEI stayed `in_stock` with on-hand 0 (and could be sold or
written off later). Returns now follow the write-off rule: one unit per line, naming its serial,
which must be in stock at that location.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import SerializedItem, StockLevel


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _loc2(client, h):
    r = await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _part(client, h):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100, "serialized": True}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _receive(client, h, loc, pid, serials):
    return await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": len(serials), "movement_type": "receive", "serials": serials}, headers=h)


async def _supplier(client, h):
    r = await client.post("/api/suppliers", json={"name": f"Sup {uuid.uuid4().hex[:4]}"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _unit(db, pid, serial):
    return (await db.execute(select(SerializedItem).where(
        SerializedItem.part_id == pid, SerializedItem.serial_number == serial))).scalar_one()


async def _on_hand(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid))).scalar())


async def _live(db, pid):
    return (await db.execute(select(func.count()).select_from(SerializedItem).where(
        SerializedItem.part_id == pid, SerializedItem.status == "in_stock"))).scalar()


@pytest.mark.asyncio
async def test_return_of_serialized_product_needs_the_unit(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, ["SN-K1"])).status_code == 200
    sup = await _supplier(client, tenant)
    r = await client.post("/api/stock-docs/returns", json={
        "supplier_id": sup, "location_id": loc, "lines": [{"part_id": pid, "qty": 1}]}, headers=tenant)
    assert r.status_code == 422, f"a serialized return without its unit was accepted: {r.status_code} {r.text}"
    # nothing moved: the unit is still on the shelf and so is the stock
    assert (await _on_hand(db, pid), await _live(db, pid)) == (1, 1)


@pytest.mark.asyncio
async def test_return_of_serialized_unit_takes_it_out_of_stock(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, ["SN-K2"])).status_code == 200
    unit = await _unit(db, pid, "SN-K2")
    sup = await _supplier(client, tenant)
    r = await client.post("/api/stock-docs/returns", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": pid, "qty": 1, "serialized_item_id": unit.id}]}, headers=tenant)
    assert r.status_code == 201, r.text
    db.expire_all()
    assert (await _on_hand(db, pid), await _live(db, pid)) == (0, 0)
    unit = await _unit(db, pid, "SN-K2")
    assert unit.status != "in_stock"

    # the same unit can't go back twice
    r = await client.post("/api/stock-docs/returns", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": pid, "qty": 1, "serialized_item_id": unit.id}]}, headers=tenant)
    assert r.status_code == 409, r.text

    # deleting the return puts the unit back on the shelf
    doc = (await client.get("/api/stock-docs/returns", headers=tenant)).json()["items"][0]["id"]
    assert (await client.delete(f"/api/stock-docs/returns/{doc}", headers=tenant)).status_code == 200
    db.expire_all()
    assert (await _on_hand(db, pid), await _live(db, pid)) == (1, 1)


@pytest.mark.asyncio
async def test_return_of_serialized_unit_at_another_location_is_refused(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, a, pid, ["SN-K3"])).status_code == 200
    assert (await _receive(client, tenant, b, pid, ["SN-K4"])).status_code == 200
    unit_b = await _unit(db, pid, "SN-K4")
    sup = await _supplier(client, tenant)
    r = await client.post("/api/stock-docs/returns", json={
        "supplier_id": sup, "location_id": a,
        "lines": [{"part_id": pid, "qty": 1, "serialized_item_id": unit_b.id}]}, headers=tenant)
    assert r.status_code == 409, f"unit at B returned from A: {r.status_code} {r.text}"
    assert (await _on_hand(db, pid), await _live(db, pid)) == (2, 2)


@pytest.mark.asyncio
async def test_return_of_two_serialized_units_on_one_line_is_refused(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, ["SN-K5", "SN-K6"])).status_code == 200
    unit = await _unit(db, pid, "SN-K5")
    sup = await _supplier(client, tenant)
    r = await client.post("/api/stock-docs/returns", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": pid, "qty": 2, "serialized_item_id": unit.id}]}, headers=tenant)
    assert r.status_code == 422, r.text
    assert (await _on_hand(db, pid), await _live(db, pid)) == (2, 2)
