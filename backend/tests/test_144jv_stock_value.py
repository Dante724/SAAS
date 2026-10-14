"""144jv — product Stock tab: /parts/{id}/stock returns per-location value + totals (Orderry parity)."""
from backend.models import StockLevel


async def _location(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def test_stock_endpoint_returns_value_and_totals(client, tenant, db):
    loc = await _location(client, tenant)
    pid = (await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Valued widget", "sell_price": 30, "cost_price": 4})).json()["id"]
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=10, qty_reserved=2))
    await db.commit()

    r = await client.get(f"/api/inventory/parts/{pid}/stock", headers=tenant)
    assert r.status_code == 200
    d = r.json()
    assert d["total_available"] == 10
    assert d["total_reserved"] == 2
    assert d["total_value"] == 40.0                    # 10 × cost 4 (avg_cost 0 → falls back to cost_price)
    row = d["items"][0]
    assert row["available"] == 10 and row["reserved"] == 2 and row["value"] == 40.0
