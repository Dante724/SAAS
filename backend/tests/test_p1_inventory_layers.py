"""Audit P1 inventory, INV-02: a job draws and restores cost layers at its own location.

Sales and write-offs already took the FIFO layers of the location the stock left from. Job
consumption took the oldest layer anywhere in the shop, and removing a job line refilled the newest
layer anywhere. So a job at branch A emptied branch B's layers, B's next sale was costed at average
and both branches' valuation drifted from their shelves."""
import pytest
from sqlalchemy import select

from backend.tests import kit_helpers as K
from backend.tests.test_qa_f2_stock import _branch, _ledger_matches

P = {"line_type": "part", "kind": "product", "description": "Screen", "unit_price": 99}


async def _receive(client, h, pid, loc, qty, cost):
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": cost})
    assert r.status_code == 200, r.text


async def _layers(db, pid):
    """{location_id: qty remaining} over the part's cost layers."""
    from backend.models import StockItemBatch
    db.expire_all()
    out: dict = {}
    for loc, q in (await db.execute(select(StockItemBatch.location_id, StockItemBatch.qty_remaining)
                                    .where(StockItemBatch.part_id == pid))).all():
        out[loc] = out.get(loc, 0.0) + float(q)
    return out


@pytest.mark.asyncio
async def test_job_draws_and_restores_its_own_branchs_cost_layers(client, tenant, db):
    here = await K.loc(client, tenant)
    branch = await _branch(client, tenant, db, "Branch B")
    pid = await K.part(client, tenant, here, "Screen", 99, 10, qty=0)
    # branch B's layer is the OLDEST in the shop: an org-wide FIFO draw would take it first
    await _receive(client, tenant, pid, branch, 2, 50)
    await _receive(client, tenant, pid, here, 2, 10)
    assert await _layers(db, pid) == {branch: 2, here: 2}

    oid, _ = await K.job(client, tenant, here)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P)
    assert await _layers(db, pid) == {branch: 2, here: 1}          # was {branch: 1, here: 2}
    line = [ln for ln in await K.lines(client, tenant, oid) if ln["kind"] == "product"][0]
    assert float(line["cost_price"]) == 10                           # this branch's cost, not B's 50

    # removing the line puts the unit back on this branch's layer, not on the newest one anywhere
    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert r.status_code in (200, 204), r.text
    assert await _layers(db, pid) == {branch: 2, here: 2}
    await _ledger_matches(db, pid)


@pytest.mark.asyncio
async def test_removing_a_job_line_never_refills_another_branchs_layer(client, tenant, db):
    """The newest layer is at branch B and has room (B sold one); the job at A gives its unit back
    to A's layer, so B's layers still match B's shelf."""
    here = await K.loc(client, tenant)
    branch = await _branch(client, tenant, db, "Branch C")
    pid = await K.part(client, tenant, here, "Battery", 40, 10, qty=0)
    await _receive(client, tenant, pid, here, 2, 10)
    await _receive(client, tenant, pid, branch, 2, 30)             # newest layer
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": branch, "qty": 1, "movement_type": "writeoff"})
    assert r.status_code == 200, r.text
    assert await _layers(db, pid) == {here: 2, branch: 1}

    oid, _ = await K.job(client, tenant, here)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P)
    line = [ln for ln in await K.lines(client, tenant, oid) if ln["kind"] == "product"][0]
    assert (await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)).status_code in (200, 204)
    assert await _layers(db, pid) == {here: 2, branch: 1}            # was {here: 1, branch: 2}
    await _ledger_matches(db, pid)


@pytest.mark.asyncio
async def test_write_off_document_drains_the_locations_untagged_layers(client, tenant, db):
    """A write-off document drained only layers tagged with its warehouse: layers with no warehouse
    (older data) stayed full while the shelf emptied."""
    from backend.models import Part, StockItemBatch
    from backend.tests.test_stocktake_postings_print import _wh
    here = await K.loc(client, tenant)
    w = await _wh(client, tenant)
    pid = await K.part(client, tenant, here, "Glass", 20, 5, qty=0)
    await _receive(client, tenant, pid, here, 3, 5)
    db.expire_all()
    org = (await db.execute(select(Part.org_id).where(Part.id == pid))).scalar_one()
    for b in (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == pid))).scalars():
        b.warehouse_id = None                                         # a layer from before warehouses
    await db.commit()
    r = await client.post("/api/stock-docs/write-offs", headers=tenant, json={
        "warehouse_id": w["id"], "lines": [{"part_id": pid, "qty": 2}]})
    assert r.status_code == 201, r.text
    assert await _layers(db, pid) == {here: 1}
    assert org
