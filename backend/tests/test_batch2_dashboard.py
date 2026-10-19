"""Batch 2 — the reports dashboard counts low stock in one aggregate and caches per shop."""
import pytest

from backend.routers import reports


async def _part(client, h, loc, name, min_stock, qty):
    p = (await client.post("/api/inventory/parts", json={"name": name, "sku": name, "min_stock": min_stock},
                           headers=h)).json()
    if qty:
        r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
            "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=h)
        assert r.status_code in (200, 201), r.text
    return p


@pytest.mark.asyncio
async def test_low_stock_aggregate_and_short_cache(client, tenant):
    reports._dash_cache.clear()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    await _part(client, tenant, loc, "LOW1", 5, 2)       # under minimum
    await _part(client, tenant, loc, "OK1", 1, 10)       # fine
    b = await _part(client, tenant, loc, "NONE1", 3, 0)  # nothing on hand
    d = (await client.get("/api/reports/dashboard", headers=tenant)).json()
    assert d["stats"]["low_stock_parts"] == 2

    # cached for a few seconds: a change shows after the entry expires
    await client.post(f"/api/inventory/parts/{b['id']}/adjust", json={
        "location_id": loc, "qty": 9, "movement_type": "receive"}, headers=tenant)
    assert (await client.get("/api/reports/dashboard", headers=tenant)).json()["stats"]["low_stock_parts"] == 2
    reports._dash_cache.clear()
    assert (await client.get("/api/reports/dashboard", headers=tenant)).json()["stats"]["low_stock_parts"] == 1
