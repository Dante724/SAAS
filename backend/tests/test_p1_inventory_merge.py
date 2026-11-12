"""Audit P1 inventory, INV-04: merging products carries everything that points at the source.

Before: reservations, open PO lines, kit components and job lines kept pointing at the archived
source. The target's reserved figure rose while the holds still named the source, so releasing
them freed nothing and the target stayed "reserved" forever. The source's units were revalued at
the target's average, and nothing was locked."""
import pytest
from sqlalchemy import select

from backend.tests import kit_helpers as K
from backend.tests.test_qa_f2_stock import _ledger_matches

pytestmark = pytest.mark.asyncio


async def _receive(client, h, pid, loc, qty, cost):
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": cost})
    assert r.status_code == 200, r.text


async def _reserved(db, pid):
    from sqlalchemy import func

    from backend.models import StockLevel
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_reserved), 0))
                                   .where(StockLevel.part_id == pid))).scalar_one())


async def test_merge_moves_holds_and_reweights_the_average(client, tenant, db):
    from backend.models import BundleComponent, OrderLine, Part, POLine, StockReservation
    loc = await K.loc(client, tenant)
    target = await K.part(client, tenant, loc, "Screen", 99, 10, qty=0)
    source = await K.part(client, tenant, loc, "Screen (dup)", 99, 20, qty=0)
    await _receive(client, tenant, target, loc, 1, 10)
    await _receive(client, tenant, source, loc, 3, 20)

    oid, _ = await K.job(client, tenant, loc)
    r = await client.post("/api/inventory/reserve", headers=tenant, json={
        "part_id": source, "location_id": loc, "qty": 1, "order_id": oid})
    assert r.status_code == 201, r.text
    hold = r.json()["reservation_id"]
    # a job line with the source on it, plus pending units still to come
    await K.add_line(client, tenant, oid, ref_id=source, qty=1, line_type="part", kind="product",
                     description="Screen (dup)", unit_price=99)
    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    r = await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sup, "location_id": loc, "lines": [{"part_id": source, "qty_ordered": 5, "unit_cost": 20}]})
    assert r.status_code == 201, r.text
    kit = await K.kit(client, tenant, [{"ref_type": "product", "ref_id": source, "qty": 1}], name="Dup kit")

    r = await client.post("/api/inventory/parts/merge", headers=tenant,
                          json={"target_id": target, "source_ids": [source]})
    assert r.status_code == 200, r.text

    db.expire_all()
    t = (await db.execute(select(Part).where(Part.id == target))).scalar_one()
    # 1 @ 10 + 2 left @ 20 (one went on the job) → 50 / 3
    assert float(t.avg_cost) == pytest.approx(50 / 3, abs=1e-3)              # was 10
    res = (await db.execute(select(StockReservation).where(StockReservation.id == hold))).scalar_one()
    assert res.part_id == target                                             # was the source
    assert (await db.execute(select(POLine.part_id))).scalars().all() == [target]
    assert (await db.execute(select(BundleComponent.ref_id).where(
        BundleComponent.bundle_id == kit["id"]))).scalars().all() == [target]
    assert set((await db.execute(select(OrderLine.ref_id).where(
        OrderLine.order_id == oid, OrderLine.kind == "product"))).scalars().all()) == {target}

    # the hold can be released, and the target's reserved figure goes back to zero
    assert await _reserved(db, target) == 1
    r = await client.post(f"/api/inventory/reservations/{hold}/release", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _reserved(db, target) == 0                                  # was stuck at 1
    assert await _reserved(db, source) == 0

    # removing the job line gives the unit back to the target, not the archived source
    line = [ln for ln in await K.lines(client, tenant, oid) if ln["kind"] == "product"][0]
    assert (await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)).status_code in (200, 204)
    assert await K.stock(client, tenant, target) == 4
    assert await K.stock(client, tenant, source) == 0
    await _ledger_matches(db, target)
    await _ledger_matches(db, source)
