"""Audit INV-01: a stocktake never drives stock negative, and the moving average never weights a
negative on-hand.

A count's variance is taken against the book when the line is counted (W3-D) and applied to the
book as it is when the count posts. When more stock has left since the count than the count says
is still there (counted 3 of 5, then 4 written off), applying the shortage would take the shelf
below zero: that count no longer describes the shelf, so posting is refused for those lines and
they are recounted. Nothing is posted until every line fits."""
import pytest
from sqlalchemy import select

from backend.tests.test_inventory_bins_passport_stocktake import _bin, _receive
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand
from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh


async def _line(client, H, st_id, part_id, bin_id=None):
    lines = (await client.get(f"/api/stocktake/{st_id}", headers=H)).json()["lines"]
    return next(ln for ln in lines if ln["part_id"] == part_id and ln["bin_id"] == bin_id)


async def _count(client, H, st_id, part_id, qty, bin_id=None):
    ln = await _line(client, H, st_id, part_id, bin_id)
    r = await client.patch(f"/api/stocktake/{st_id}/lines/{ln['id']}", json={"counted_qty": qty}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _writeoff(client, H, part_id, loc, qty):
    body = {"location_id": loc, "qty": qty, "movement_type": "writeoff"}
    r = await client.post(f"/api/inventory/parts/{part_id}/adjust", json=body, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_count_overtaken_by_stock_leaving_is_refused_not_driven_negative(client, tenant, db):
    """Counted 3 of 5, then 4 written off (book 1): the −2 shortage can't come off 1 → 409, nothing
    posted; a recount against the book now posts and stock stays at or above zero."""
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="INV01-A", barcode="INV01-A")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    assert (await _count(client, tenant, st["id"], p["id"], 3))["variance"] == -2
    await _writeoff(client, tenant, p["id"], loc, 4)
    assert await _on_hand(db, p["id"]) == 1

    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 409, r.text
    assert "INV01-A" in r.text or p["name"] in r.text
    assert await _on_hand(db, p["id"]) == 1                      # nothing was applied
    assert (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["status"] != "posted"

    # recount the line: the shelf now holds 1, as the book says
    assert (await _count(client, tenant, st["id"], p["id"], 1))["variance"] == 0
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, p["id"]) == 1
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_shortage_that_still_fits_posts_against_the_book_now(client, tenant, db):
    """Counted 3 of 5, then 1 written off (book 4): the −2 still fits → 2 on hand, never below 0."""
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="INV01-B", barcode="INV01-B")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    await _count(client, tenant, st["id"], p["id"], 3)
    await _writeoff(client, tenant, p["id"], loc, 1)
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, p["id"]) == 2
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_bin_count_overtaken_by_stock_leaving_the_bin_is_refused(client, tenant, db):
    """A bin's shortage goes straight onto the bin row: it is refused too when the bin no longer
    holds it, instead of taking the bin negative."""
    loc = await _loc(client, tenant)
    b = await _bin(client, tenant, loc, "INV01 shelf")
    p = await _part(client, tenant, sku="INV01-C", barcode="INV01-C")
    await _receive(client, tenant, loc, p["id"], 5, bin_id=b["id"])
    st = (await client.post("/api/stocktake", json={"location_id": loc, "bin_id": b["id"], "kind": "full"},
                            headers=tenant)).json()
    await _count(client, tenant, st["id"], p["id"], 3, bin_id=b["id"])
    # 4 leave that bin after the count (a job taking from the bin)
    from backend.services import stock_rows
    await stock_rows.draw(db, p["id"], loc, 4, bin_id=b["id"])
    await db.commit()
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 409, r.text
    assert await _on_hand(db, p["id"]) == 1


@pytest.mark.asyncio
async def test_negative_on_hand_can_no_longer_exist(client, tenant, db):
    """Legacy negative stock used to skew the moving average. Migration w2b01 set it to 0 (with an
    adjust movement) and a CHECK (audit DB-07) now refuses it; receiving onto 1 @ 10 averages normally."""
    from sqlalchemy.exc import IntegrityError

    from backend.models import Part, StockLevel
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="INV01-D", barcode="INV01-D", cost=10)
    await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 1, "movement_type": "receive", "unit_cost": 10}, headers=tenant)
    row = (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().first()
    row.qty_available = -1
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 2, "movement_type": "receive", "unit_cost": 20}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    part = (await db.execute(select(Part).where(Part.id == p["id"]))).scalar_one()
    assert float(part.avg_cost) == pytest.approx(50 / 3, abs=1e-3)     # (1 × 10 + 2 × 20) / 3, stored to 4 dp
