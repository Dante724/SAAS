"""QA fix F2 (5-shop run, 31 Oct 2026): POS returns, stocktake counts, transfer scan/receive and
serialized (IMEI) items move stock exactly once and only when they should."""
import pytest
from sqlalchemy import func, select

from backend.models import StockLevel, StockMovement
from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh


async def _on_hand(db, part_id):
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                   .where(StockLevel.part_id == part_id))).scalar_one())


async def _moved(db, part_id):
    return float((await db.execute(select(func.coalesce(func.sum(StockMovement.qty), 0))
                                   .where(StockMovement.part_id == part_id))).scalar_one())


async def _ledger_matches(db, part_id):
    """Every unit on the shelf is explained by a movement."""
    assert await _on_hand(db, part_id) == pytest.approx(await _moved(db, part_id))


# ── 2. stocktake: the count is what's on the shelf when it posts ───────────────────────────────
@pytest.mark.asyncio
async def test_stock_moved_during_the_count_isnt_subtracted_again(client, tenant, db):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PADS-1", barcode="PADS-1")
    await _stock(client, tenant, p["id"], 31)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    # two pads written off while the count is under way: the shelf now really holds 29
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", headers=tenant, json={
        "location_id": await _loc(client, tenant), "qty": -2, "movement_type": "adjust"})
    assert r.status_code in (200, 201), r.text
    line = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    assert (await client.patch(f"/api/stocktake/{st['id']}/lines/{line['id']}",
                               json={"counted_qty": 29}, headers=tenant)).status_code == 200
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"]) == 29            # was 27: the write-off was taken twice
    posted = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    assert posted["system_qty"] == 29 and posted["variance"] == 0
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_posted_count_cant_be_edited_and_negative_is_refused(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PADS-2", barcode="PADS-2")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    line = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    url = f"/api/stocktake/{st['id']}/lines/{line['id']}"
    assert (await client.patch(url, json={"counted_qty": -3}, headers=tenant)).status_code == 422
    assert (await client.patch(url, json={"counted_qty": 5}, headers=tenant)).status_code == 200
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert (await client.patch(url, json={"counted_qty": 99}, headers=tenant)).status_code == 409

