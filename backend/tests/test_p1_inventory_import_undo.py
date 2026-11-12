"""Audit P1 inventory, INV-03: undoing an import reverses its opening stock with compensating
movements. It no longer deletes history, never takes a stock row below zero, and still refuses once
the imported stock has been used.

Before: the undo DELETEd the import's movements, cost layers and serial units, then subtracted the
qty from whichever stock row came first for (product, location). Stock imported into a bin was
taken off the unbinned row, which went negative while the bin kept the units."""
import pytest
from sqlalchemy import select

from backend.models import SerializedItem, StockItemBatch, StockLevel, StockMovement
from backend.tests.test_import_centre import _import, _me, _seed_part
from backend.tests.test_inventory_bins_passport_stocktake import _bin
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand

pytestmark = pytest.mark.asyncio


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _rows(db, pid):
    db.expire_all()
    return {r.bin_id: float(r.qty_available) for r in (await db.execute(
        select(StockLevel).where(StockLevel.part_id == pid))).scalars()}


async def test_undo_takes_the_stock_back_from_the_bin_it_went_into(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    b1 = await _bin(client, tenant, loc, "B1")
    p = await _seed_part(db, me["org_id"], name="Hinge", sku="H-1")
    pid = p.id
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 2, "movement_type": "receive", "unit_cost": 3})
    assert r.status_code == 200, r.text
    out = await _import(client, tenant, "products", [["SKU", "Opening stock", "Cost price", "Bin"],
                                                     ["H-1", "5", "3", "B1"]], options={"location_id": loc})
    assert await _rows(db, pid) == {None: 2, b1["id"]: 5}

    r = await client.post(f"/api/imports/batches/{out['batch_id']}/undo", headers=tenant)
    assert r.status_code == 200, r.text
    rows = await _rows(db, pid)
    assert rows == {None: 2, b1["id"]: 0}                 # was {None: -3, B1: 5}
    assert min(rows.values()) >= 0
    assert await _on_hand(db, pid) == 2

    # the history stays: the import's receipt plus a compensating movement out of the same bin
    mv = (await db.execute(select(StockMovement).where(
        StockMovement.part_id == pid, StockMovement.ref_type == "import",
        StockMovement.ref_id == out["batch_id"]))).scalars().all()
    assert sorted((m.movement_type, m.qty, m.bin_id) for m in mv) == \
        sorted([("receive", 5.0, b1["id"]), ("import_undo", -5.0, b1["id"])])
    await _ledger_matches(db, pid)
    # the import's cost layer is kept, emptied
    layers = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == pid))).scalars().all()
    assert sorted(float(b.qty_remaining) for b in layers) == [0.0, 2.0]


async def test_undo_retires_imported_serial_units_instead_of_deleting_them(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    p = await _seed_part(db, me["org_id"], name="Phone X", sku="PX-1", serialized=True)
    pid = p.id
    out = await _import(client, tenant, "products", [["SKU", "Opening stock", "Cost price", "Serial numbers"],
                                                     ["PX-1", "2", "100", "IMP-SN-1; IMP-SN-2"]],
                        options={"location_id": loc})
    units = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == pid))).scalars().all()
    assert len(units) == 2, [u.serial_number for u in units]
    r = await client.post(f"/api/imports/batches/{out['batch_id']}/undo", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    units = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == pid))).scalars().all()
    assert len(units) == 2 and {u.status for u in units} == {"written_off"}
    assert await _on_hand(db, pid) == 0
    await _ledger_matches(db, pid)
    # the serials are free to be imported again
    again = await _import(client, tenant, "products", [["SKU", "Opening stock", "Cost price", "Serial numbers"],
                                                       ["PX-1", "2", "100", "IMP-SN-1; IMP-SN-2"]],
                          options={"location_id": loc})
    assert again["batch_id"] != out["batch_id"]


async def test_undo_is_refused_once_imported_stock_was_used(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    p = await _seed_part(db, me["org_id"], name="Strap", sku="ST-1")
    pid = p.id
    out = await _import(client, tenant, "products", [["SKU", "Opening stock", "Cost price"], ["ST-1", "3", "2"]],
                        options={"location_id": loc})
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 2, "movement_type": "writeoff"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/imports/batches/{out['batch_id']}/undo", headers=tenant)
    assert r.status_code == 409, r.text
    assert await _on_hand(db, pid) == 1
    await _ledger_matches(db, pid)
