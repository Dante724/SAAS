"""Inventory intelligence reports: valuation, dead stock, reorder forecast."""


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def test_reports_registered(client, tenant):
    cat = (await client.get("/api/reports/catalog", headers=tenant)).json()
    keys = {r["key"] for tab in cat["tabs"] for r in tab["reports"]}
    assert {"stock_valuation", "dead_stock", "reorder_forecast"} <= keys, keys


async def test_stock_valuation_totals(client, tenant):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/catalog/products",
         json={"name": "Widget", "cost_price": 10, "sell_price": 20}, headers=tenant)).json()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust",
        json={"location_id": loc, "movement_type": "receive", "qty": 5}, headers=tenant)
    assert r.status_code == 200, r.text
    rep = (await client.get("/api/reports/run/stock_valuation", headers=tenant)).json()
    assert rep["columns"][-1] == "Stock value (cost)"
    total = [row for row in rep["rows"] if row[0] == "TOTAL"]
    assert total, rep["rows"]
    assert total[0][-1] == 50.0                     # 5 units * cost 10


async def test_dead_stock_lists_unsold_onhand(client, tenant):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/catalog/products",
         json={"name": "Dusty", "cost_price": 4, "sell_price": 9}, headers=tenant)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
        json={"location_id": loc, "movement_type": "receive", "qty": 3}, headers=tenant)
    rep = (await client.get("/api/reports/run/dead_stock", headers=tenant)).json()
    names = [row[0] for row in rep["rows"]]
    assert "Dusty" in names                          # never sold, on hand -> dead
    row = [r for r in rep["rows"] if r[0] == "Dusty"][0]
    assert row[4] == 12.0 and row[5] == "never"      # tied-up cash 3*4


async def test_reorder_forecast_runs(client, tenant):
    rep = await client.get("/api/reports/run/reorder_forecast", headers=tenant)
    assert rep.status_code == 200
    assert "Suggest order" in rep.json()["columns"]
