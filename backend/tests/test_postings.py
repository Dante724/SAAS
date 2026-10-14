"""Fix for the postings 405 — bulk stock inflow endpoint."""
import pytest


@pytest.mark.asyncio
async def test_bulk_posting_creates_stock_and_markup(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Part, StockLevel
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    p = (await client.post("/api/inventory/parts", json={"name": "Charging Port", "sku": "PORT-IPH"}, headers=tenant)).json()
    pid = p["id"]

    r = await client.post("/api/inventory/postings", json={
        "location_id": loc, "note": "7613278",
        "lines": [{"part_id": pid, "qty": 3, "purchase_price": 300, "markup_pct": 50}]}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lines_posted"] == 1 and body["total_cost"] == pytest.approx(900.0)

    db.expire_all()
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid, StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_available == pytest.approx(3.0)
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    assert part.cost_price == pytest.approx(300.0)
    assert part.sell_price == pytest.approx(450.0)   # 300 * 1.5 markup


@pytest.mark.asyncio
async def test_posting_validation(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    # no lines -> 422 (not 405)
    r = await client.post("/api/inventory/postings", json={"location_id": loc, "lines": []}, headers=tenant)
    assert r.status_code == 422
