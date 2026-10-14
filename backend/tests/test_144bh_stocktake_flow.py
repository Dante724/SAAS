"""144bh — stocktake run-flow works against location-level stock.

Full count seeds from real on-hand, scan-to-count increments, and posting the variance adjusts
the SAME location StockLevel row (no warehouse-keyed duplicate / double-count).
"""
import pytest
from sqlalchemy import func, select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part_with_stock(client, h, loc, sku, qty):
    p = (await client.post("/api/inventory/parts", json={"name": f"P {sku}", "sku": sku, "barcode": sku}, headers=h)).json()
    await client.post("/api/inventory/postings", json={
        "location_id": loc, "lines": [{"part_id": p["id"], "qty": qty, "purchase_price": 5}]}, headers=h)
    return p["id"]


@pytest.mark.asyncio
async def test_full_count_seeds_real_onhand(client, tenant):
    loc = await _loc(client, tenant)
    await _part_with_stock(client, tenant, loc, "ST-A", 10)
    st = (await client.post("/api/stocktake", json={"location_id": loc, "kind": "full"}, headers=tenant)).json()
    assert st["lines"] >= 1, "full count should seed lines from location stock"
    detail = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()
    line = next(l for l in detail["lines"] if l["system_qty"] == 10)
    assert line["system_qty"] == 10


@pytest.mark.asyncio
async def test_scan_to_count_and_post_adjusts_location_row(client, tenant, db):
    from backend.models import StockLevel
    loc = await _loc(client, tenant)
    pid = await _part_with_stock(client, tenant, loc, "ST-B", 10)
    st = (await client.post("/api/stocktake", json={"location_id": loc, "kind": "partial"}, headers=tenant)).json()
    stid = st["id"]
    # scan the part 8 times-worth (one call, qty 8) — counted 8 vs system 10 → variance -2
    scan = await client.post(f"/api/stocktake/{stid}/scan", json={"code": "ST-B", "qty": 8}, headers=tenant)
    assert scan.status_code == 200, scan.text
    assert scan.json()["counted_qty"] == 8 and scan.json()["variance"] == -2
    # post
    posted = await client.post(f"/api/stocktake/{stid}/post", headers=tenant)
    assert posted.status_code == 200 and posted.json()["adjustments"] == 1
    db.expire_all()
    # exactly one StockLevel row for this part, now reading 8 (not a duplicate warehouse row)
    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid))).scalars().all()
    assert len(rows) == 1, "post must adjust the existing row, not create a warehouse duplicate"
    assert float(rows[0].qty_available) == 8.0


@pytest.mark.asyncio
async def test_unseeded_scan_needs_register_unknown(client, tenant):
    loc = await _loc(client, tenant)
    st = (await client.post("/api/stocktake", json={"location_id": loc, "kind": "partial"}, headers=tenant)).json()
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "NOPE-XYZ", "qty": 1}, headers=tenant)
    assert r.status_code == 404   # unknown code rejected unless register_unknown
