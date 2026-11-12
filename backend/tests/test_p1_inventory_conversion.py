"""Audit P1 inventory, INV-07 and Q1: converting between assets and products moves stock through
documents. Each conversion writes a movement and a cost layer, and the shelf matches the movements.

Before:
- `/api/conversions/asset-to-product` added a unit to stock with no movement and no cost layer;
- `/api/assets/{id}/convert-to-product` wrote a movement but no cost layer;
- product → asset took the unit off the shelf with no movement, and left its cost layer full."""
import pytest
from sqlalchemy import select

from backend.models import Device, SerializedItem, StockItemBatch, StockMovement
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand, _phone

pytestmark = pytest.mark.asyncio


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _asset(client, tenant, db, serial, price=300):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    d = Device(org_id=me["org_id"], owner_type="company", brand="Apple", model="iPad", serial=serial,
               purchase_price=price, sale_price=500)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d.id


async def _layers(db, pid):
    db.expire_all()
    return [(float(b.qty_remaining), float(b.unit_cost)) for b in (await db.execute(
        select(StockItemBatch).where(StockItemBatch.part_id == pid))).scalars()]


async def test_asset_to_product_stock_unit_is_received_on_a_layer(client, tenant, db):
    did = await _asset(client, tenant, db, "CONV-1")
    loc = await _loc(client, tenant)
    r = await client.post("/api/conversions/asset-to-product", headers=tenant, json={
        "device_id": did, "create_stock_unit": True, "location_id": loc, "cost_price": 250})
    assert r.status_code == 201, r.text
    pid = r.json()["part_id"]
    assert await _on_hand(db, pid) == 1
    await _ledger_matches(db, pid)                                           # was: no movement at all
    assert await _layers(db, pid) == [(1.0, 250.0)]
    unit = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == pid))).scalar_one()
    assert unit.batch_id is not None and unit.serial_number == "CONV-1" and unit.status == "in_stock"
    mv = (await db.execute(select(StockMovement).where(StockMovement.part_id == pid))).scalar_one()
    assert (mv.ref_type, mv.ref_id, mv.qty) == ("asset", did, 1)


async def test_asset_convert_to_product_endpoint_creates_a_layer(client, tenant, db):
    did = await _asset(client, tenant, db, "CONV-2", price=300)
    r = await client.post(f"/api/assets/{did}/convert-to-product", headers=tenant, json={"sale_price": 450})
    assert r.status_code == 201, r.text
    pid = r.json()["part_id"]
    assert await _on_hand(db, pid) == 1
    await _ledger_matches(db, pid)
    assert await _layers(db, pid) == [(1.0, 300.0)]                          # was: no layer


async def test_product_to_asset_takes_the_unit_off_with_a_movement(client, tenant, db):
    p, loc, sns = await _phone(client, tenant, n=2)
    u = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == p["id"],
                                                       SerializedItem.serial_number == sns[0]))).scalar_one()
    r = await client.post("/api/conversions/product-to-asset", headers=tenant, json={
        "serialized_item_id": u.id, "owner_type": "company"})
    assert r.status_code == 201, r.text
    assert await _on_hand(db, p["id"]) == 1
    await _ledger_matches(db, p["id"])                                       # was: on hand 1, movements 2
    assert sum(q for q, _c in await _layers(db, p["id"])) == 1                # the unit's layer was drawn
