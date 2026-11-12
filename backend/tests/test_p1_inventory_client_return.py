"""Audit P1 inventory, INV-06: a client return with no job.

Before: anyone allowed to take returns could "return" any quantity of any product with no job and
no sale behind it. Stock appeared out of nothing, with no cost layer and no cost, which could cover
a shortage. Now:
- a free-standing return of a non-serialized product needs `can_manage_inventory` (the front desk
  takes returns against a job, or of serialized units this shop sold);
- the quantity is bounded and whole for products counted in whole units;
- the restock is a receipt of the return document: a cost layer at the product's cost (a serial
  unit's own cost), with the movement naming the return."""
import pytest
from sqlalchemy import select

from backend.models import StockItemBatch, StockMovement
from backend.tests.test_qa_f1_jobs import _staff
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand, _phone
from backend.tests.test_stocktake_postings_print import _part, _stock

pytestmark = pytest.mark.asyncio


async def test_front_desk_cant_create_stock_with_a_free_standing_return(client, tenant, db):
    p = await _part(client, tenant, sku="RET-FS", barcode="RET-FS", cost=5)
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={"lines": [{"part_id": p["id"], "qty": 10}]})
    assert r.status_code == 403, r.text                                      # was 201: +10 from nothing
    assert await _on_hand(db, p["id"]) == 0
    # not restocked (e.g. a faulty item kept aside) moves no stock, so it is fine for the desk
    r = await client.post("/api/returns", headers=desk, json={"lines": [{"part_id": p["id"], "qty": 1, "restock": False}]})
    assert r.status_code == 201, r.text
    assert await _on_hand(db, p["id"]) == 0


async def test_free_standing_return_is_bounded_and_whole(client, tenant, db):
    p = await _part(client, tenant, sku="RET-B", barcode="RET-B", cost=5)
    r = await client.post("/api/returns", headers=tenant, json={"lines": [{"part_id": p["id"], "qty": 1e12}]})
    assert r.status_code == 422, r.text
    r = await client.post("/api/returns", headers=tenant, json={"lines": [{"part_id": p["id"], "qty": 1.5}]})
    assert r.status_code == 422, r.text
    assert await _on_hand(db, p["id"]) == 0


async def test_free_standing_return_gets_a_cost_layer_and_names_its_document(client, tenant, db):
    p = await _part(client, tenant, sku="RET-L", barcode="RET-L", cost=5)
    await _stock(client, tenant, p["id"], 2)
    r = await client.post("/api/returns", headers=tenant, json={"reason": "Unopened",
                                                                "lines": [{"part_id": p["id"], "qty": 3}]})
    assert r.status_code == 201, r.text
    ret = r.json()["id"]
    assert await _on_hand(db, p["id"]) == 5
    db.expire_all()
    layers = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == p["id"]))).scalars().all()
    assert sum(float(b.qty_remaining) for b in layers) == 5                  # was 2: 3 units with no layer
    mv = (await db.execute(select(StockMovement).where(
        StockMovement.part_id == p["id"], StockMovement.ref_type == "client_return"))).scalars().one()
    assert (mv.ref_id, mv.movement_type, mv.qty) == (ret, "return", 3)
    assert float(mv.unit_cost or 0) == 5
    await _ledger_matches(db, p["id"])


async def test_returned_phone_goes_back_on_a_layer_at_its_own_cost(client, tenant, db):
    from backend.models import SerializedItem
    p, here, sns = await _phone(client, tenant, n=2)
    line = {"kind": "product", "ref_id": p["id"], "description": "iPhone 13", "qty": 1, "unit_price": 400,
            "serials": [sns[0]]}
    assert (await client.post("/api/sales", json={"lines": [line]}, headers=tenant)).status_code == 201
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={
        "lines": [{"part_id": p["id"], "qty": 1, "serials": [sns[0]]}]})
    assert r.status_code == 201, r.text                     # a unit this shop sold: bounded, desk may
    db.expire_all()
    u = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == p["id"],
                                                       SerializedItem.serial_number == sns[0]))).scalar_one()
    layer = (await db.execute(select(StockItemBatch).where(StockItemBatch.id == u.batch_id))).scalar_one()
    assert u.status == "in_stock" and float(layer.qty_remaining) >= 1
    layered = sum(float(b.qty_remaining) for b in (await db.execute(
        select(StockItemBatch).where(StockItemBatch.part_id == p["id"]))).scalars())
    assert layered == await _on_hand(db, p["id"]) == 2
    await _ledger_matches(db, p["id"])
