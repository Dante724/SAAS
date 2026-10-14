"""144bk — bulk posting: preview (fuzzy match) + commit (creates new parts) + bulk qty adjust.

The posting engine (WAC + movement) is unchanged; these add safer *front doors* to it:
a preview that classifies each row matched/unsure/new, a commit that creates the 'new' ones
and posts in one transaction, and a bulk quantity adjust across selected parts.
"""
import pytest
from sqlalchemy import func, select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, name, sku):
    return (await client.post("/api/inventory/parts", json={"name": name, "sku": sku, "barcode": sku}, headers=h)).json()


@pytest.mark.asyncio
async def test_preview_classifies_matched_unsure_new(client, tenant):
    await _part(client, tenant, "iPhone 13 Screen OLED", "SCR-IP13")
    await _part(client, tenant, "iPhone 13 Screen LCD", "SCR-IP13L")
    r = await client.post("/api/inventory/postings/preview", json={"rows": [
        {"code": "SCR-IP13", "qty": 5, "purchase_price": 60},           # exact sku -> matched
        {"name": "iPhone 13 Screen", "qty": 2},                          # ambiguous -> unsure (2 candidates)
        {"name": "Totally New Widget XYZ", "qty": 10, "purchase_price": 3},  # no match -> new
    ]}, headers=tenant)
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows[0]["match_status"] == "matched" and rows[0]["part_id"]
    assert rows[1]["match_status"] in ("unsure", "matched")  # has candidates
    assert len(rows[1].get("candidates", [])) >= 1
    assert rows[2]["match_status"] == "new" and not rows[2].get("part_id")


@pytest.mark.asyncio
async def test_commit_creates_new_part_and_posts_stock(client, tenant, db):
    from backend.models import Part, StockLevel
    loc = await _loc(client, tenant)
    existing = await _part(client, tenant, "Galaxy S22 Battery", "BAT-GS22")
    r = await client.post("/api/inventory/postings/commit", json={
        "location_id": loc,
        "rows": [
            {"part_id": existing["id"], "qty": 4, "purchase_price": 14, "markup_pct": 50},
            {"new_name": "Pixel 7 Battery", "new_sku": "BAT-PX7", "qty": 6, "purchase_price": 10, "markup_pct": 100},
        ]},
        headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lines_posted"] == 2 and body["created_parts"] == 1
    # existing part got +4 and sell price from markup
    db.expire_all()
    ex = (await db.execute(select(Part).where(Part.id == existing["id"]))).scalar_one()
    assert ex.sell_price == 21.0  # 14 * 1.5
    exqty = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                              .where(StockLevel.part_id == existing["id"]))).scalar()
    assert float(exqty) == 4.0
    # new part exists with stock 6
    newp = (await db.execute(select(Part).where(Part.org_id.isnot(None), Part.sku == "BAT-PX7"))).scalars().first()
    assert newp is not None and newp.sell_price == 20.0
    nqty = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                             .where(StockLevel.part_id == newp.id))).scalar()
    assert float(nqty) == 6.0


@pytest.mark.asyncio
async def test_commit_rejects_row_without_part_or_name(client, tenant):
    loc = await _loc(client, tenant)
    r = await client.post("/api/inventory/postings/commit", json={
        "location_id": loc, "rows": [{"qty": 3, "purchase_price": 1}]}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_adjust_set_and_delta(client, tenant, db):
    from backend.models import StockLevel
    loc = await _loc(client, tenant)
    p1 = await _part(client, tenant, "Adj Part A", "ADJ-A")
    p2 = await _part(client, tenant, "Adj Part B", "ADJ-B")
    # seed p1 to 10 via posting
    await client.post("/api/inventory/postings", json={"location_id": loc, "lines": [{"part_id": p1["id"], "qty": 10, "purchase_price": 1}]}, headers=tenant)
    r = await client.post("/api/inventory/parts/bulk-adjust", json={"location_id": loc, "rows": [
        {"part_id": p1["id"], "set_qty": 7},     # 10 -> 7  (delta -3)
        {"part_id": p2["id"], "delta": 5},        # 0 -> 5
    ]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["adjusted"] == 2
    db.expire_all()
    q1 = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(StockLevel.part_id == p1["id"]))).scalar()
    q2 = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(StockLevel.part_id == p2["id"]))).scalar()
    assert float(q1) == 7.0 and float(q2) == 5.0
