"""144ag — receipt timeline + supplier performance scorecard."""
import pytest


async def _sup(client, tenant):
    return (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_receipt_timeline_records_each_delivery(client, tenant):
    sid = await _sup(client, tenant); loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts", json={"name": "Hose", "sku": "H-1"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 10, "unit_cost": 3}]})).json()["po_id"]
    # two partial receipts
    ln = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant, json={"lines": [{"po_line_id": ln, "qty": 4}]})
    await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant, json={"lines": [{"po_line_id": ln, "qty": 6}]})
    r = await client.get(f"/api/inventory/po/{po_id}/receipts", headers=tenant)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2 and {i["qty"] for i in items} == {4, 6}


@pytest.mark.asyncio
async def test_supplier_performance_scorecard(client, tenant):
    sid = await _sup(client, tenant); loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts", json={"name": "Filter", "sku": "F-1"}, headers=tenant)).json()["id"]
    # PO 1 @ cost 5, received
    po1 = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "on_credit": True, "lines": [{"part_id": pid, "qty_ordered": 2, "unit_cost": 5}]})).json()["po_id"]
    await client.post(f"/api/inventory/po/{po1}/receive", headers=tenant, json={})
    # PO 2 @ cost 8 (price went up), received
    po2 = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "on_credit": True, "lines": [{"part_id": pid, "qty_ordered": 2, "unit_cost": 8}]})).json()["po_id"]
    await client.post(f"/api/inventory/po/{po2}/receive", headers=tenant, json={})

    p = (await client.get(f"/api/suppliers/{sid}/performance", headers=tenant)).json()
    assert p["total_pos"] == 2 and p["received_pos"] == 2
    assert p["total_spend"] == pytest.approx(26.0)   # 2*5 + 2*8
    trend = {t["part"]: t for t in p["price_trend"]}
    assert "Filter" in trend and trend["Filter"]["first"] == 5 and trend["Filter"]["latest"] == 8
