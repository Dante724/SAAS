"""Data-integrity regression: after a work order is transferred to another location, a
later stock reversal (here: deleting the product line) must return stock to the location it
was DEDUCTED from, not the order's new location. Otherwise per-location on-hand corrupts:
the original location stays short and the new one gains phantom units."""
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models import StockLevel


@pytest.mark.asyncio
async def test_transfer_then_reverse_returns_to_original_location(client, tenant):
    locA = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    locB = (await client.post("/api/staff/locations", headers=tenant, json={"name": "Branch B"})).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "TP", "sku": "TP-1"}, headers=tenant)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": locA, "qty": 10, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    # real WO at A with a product line qty 3 -> deducts 3 from A
    oid = (await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": locA, "complaint": "fix",
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "P", "qty": 3, "unit_price": 20}]})).json()["id"]
    # sanity: the deduction actually happened (total on-hand 10 -> 7)
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"] == 7

    od = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    line_id = [l for l in od["lines"] if l.get("ref_id") == pid][0]["id"]

    assert (await client.post(f"/api/orders/{oid}/transfer", headers=tenant,
                              json={"location_id": locB})).status_code == 200
    # delete the line -> reverses the 3 units
    assert (await client.delete(f"/api/orders/{oid}/lines/{line_id}", headers=tenant)).status_code in (200, 204)

    eng = create_async_engine(os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"])
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            slA = (await s.execute(select(StockLevel).where(
                StockLevel.part_id == pid, StockLevel.location_id == locA))).scalar_one_or_none()
            slB = (await s.execute(select(StockLevel).where(
                StockLevel.part_id == pid, StockLevel.location_id == locB))).scalar_one_or_none()
    finally:
        await eng.dispose()

    # stock returned to A (original), not B
    assert slA is not None and slA.qty_available == 10, (slA and slA.qty_available)
    assert slB is None or (slB.qty_available or 0) == 0, (slB and slB.qty_available)
