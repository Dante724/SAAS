"""Audit P1 inventory: write-off and supplier-return documents.

- INV-05: a write-off line naming a serial unit can't change its qty (drawing 2 anonymous units left
  their serials in stock), the line edit locks the document and re-checks it, and a revert never
  restores a unit that is no longer written off (a stocktake that found it already put it back).
- INV-08: every movement a write-off or supplier return writes names its document.
"""
import pytest
from sqlalchemy import select

from backend.models import SerializedItem, StockLevel, StockMovement
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand, _phone
from backend.tests.test_writeoff_edit_restores_layers import _loc, _part, _receive

pytestmark = pytest.mark.asyncio


async def _unit(db, part_id, sn):
    db.expire_all()
    return (await db.execute(select(SerializedItem).where(
        SerializedItem.part_id == part_id, SerializedItem.serial_number == sn))).scalar_one()


async def test_serial_write_off_line_qty_cant_change(client, tenant, db):
    p, loc, sns = await _phone(client, tenant, n=3)
    u = await _unit(db, p["id"], sns[0])
    r = await client.post("/api/stock-docs/write-offs", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": p["id"], "qty": 1, "serialized_item_id": u.id}]})
    assert r.status_code == 201, r.text
    doc, ln = r.json()["id"], r.json()["lines"][0]["id"]
    assert await _on_hand(db, p["id"]) == 2
    r = await client.patch(f"/api/stock-docs/write-offs/{doc}/lines/{ln}", headers=tenant, json={"qty": 3})
    assert r.status_code == 422, r.text                                       # was 200: 2 anonymous units left
    assert await _on_hand(db, p["id"]) == 2
    assert {(await _unit(db, p["id"], sn)).status for sn in sns[1:]} == {"in_stock"}
    # a comment can still be edited
    r = await client.patch(f"/api/stock-docs/write-offs/{doc}/lines/{ln}", headers=tenant, json={"comment": "cracked"})
    assert r.status_code == 200, r.text
    await _ledger_matches(db, p["id"])


async def test_revert_skips_a_unit_already_back_in_stock(client, tenant, db):
    p, loc, sns = await _phone(client, tenant, n=2)
    u = await _unit(db, p["id"], sns[0])
    r = await client.post("/api/stock-docs/write-offs", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": p["id"], "qty": 1, "serialized_item_id": u.id}]})
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    # a stocktake found the phone on the shelf: the unit and one unit of stock are already back
    u = await _unit(db, p["id"], sns[0])
    u.status = "in_stock"
    row = (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().first()
    row.qty_available = (row.qty_available or 0) + 1
    db.add(StockMovement(part_id=p["id"], location_id=loc, movement_type="adjust", qty=1, ref_type="stocktake"))
    await db.commit()
    assert await _on_hand(db, p["id"]) == 2

    r = await client.delete(f"/api/stock-docs/write-offs/{doc}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, p["id"]) == 2                                   # was 3: restored twice
    await _ledger_matches(db, p["id"])


async def test_write_off_and_supplier_return_movements_name_their_document(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, loc, pid, 10, 4)).status_code == 200
    r = await client.post("/api/stock-docs/write-offs", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 3}]})
    assert r.status_code == 201, r.text
    wo, ln = r.json()["id"], r.json()["lines"][0]["id"]
    assert (await client.patch(f"/api/stock-docs/write-offs/{wo}/lines/{ln}", headers=tenant,
                               json={"qty": 2})).status_code == 200
    assert (await client.delete(f"/api/stock-docs/write-offs/{wo}", headers=tenant)).status_code == 200
    r = await client.post("/api/stock-docs/returns", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 201, r.text
    pr = r.json()["id"]
    assert (await client.delete(f"/api/stock-docs/returns/{pr}", headers=tenant)).status_code == 200

    db.expire_all()
    mv = (await db.execute(select(StockMovement).where(
        StockMovement.part_id == pid, StockMovement.movement_type != "receive"))).scalars().all()
    got = sorted((m.movement_type, m.ref_type, m.ref_id) for m in mv)
    assert got == sorted([
        ("writeoff", "write_off", wo), ("writeoff_adjust", "write_off", wo), ("writeoff_reverted", "write_off", wo),
        ("return_to_supplier", "purchase_return", pr), ("return_reverted", "purchase_return", pr)])
    await _ledger_matches(db, pid)
