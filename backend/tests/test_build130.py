"""Build 130 — product Batches tab endpoint."""
import pytest

from backend.services.dates import now_utc


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_batches_endpoint_shape(client, tenant):
    # a brand-new product has no batches yet
    pid = (await client.post("/api/inventory/parts",
                             json={"name": "Widget", "sku": "W-1"}, headers=tenant)).json()["id"]
    d = (await client.get(f"/api/inventory/parts/{pid}/batches", headers=tenant)).json()
    assert d["items"] == []


@pytest.mark.asyncio
async def test_batch_layer_surfaces_via_endpoint(client, tenant, db):
    from datetime import datetime

    from backend.models import StockItemBatch
    loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts",
                             json={"name": "Cell", "sku": "C-1"}, headers=tenant)).json()["id"]
    # seed a costed stock layer directly (mirrors what a posting / PO receive writes)
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    db.add(StockItemBatch(org_id=org_id, part_id=pid, location_id=loc,
                          unit_cost=20, qty_received=5, qty_remaining=3,
                          received_at=now_utc()))
    await db.commit()

    d = (await client.get(f"/api/inventory/parts/{pid}/batches", headers=tenant)).json()["items"]
    assert len(d) == 1
    b = d[0]
    assert b["in_stock"] is True
    assert b["qty_received"] == 5 and b["qty_remaining"] == 3
    assert b["unit_cost"] == 20 and b["amount"] == pytest.approx(60)   # 20 * 3 remaining
