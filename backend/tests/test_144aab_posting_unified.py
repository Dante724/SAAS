"""144aab — one posting engine. Proves the accuracy fixes:
  * moving-average cost is weighted on GLOBAL on-hand (all locations), so a posting at
    a second branch blends with the first instead of ignoring it, and
  * a manual posting now births a FIFO cost layer (StockItemBatch), same as PO receipt.
"""
import pytest
from sqlalchemy import func, select

from backend.models import Location, Part, StockItemBatch, StockLevel


@pytest.mark.asyncio
async def test_posting_uses_global_moving_average_across_locations(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc_a = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    loc_b = Location(org_id=me["org_id"], name="Branch B")
    db.add(loc_b)
    await db.commit()
    await db.refresh(loc_b)

    pid = (await client.post("/api/inventory/parts", json={"name": "Cell", "sku": "CELL-1"}, headers=tenant)).json()["id"]

    # 10 @ 10 into A  → avg 10
    await client.post("/api/inventory/postings", json={
        "location_id": loc_a, "lines": [{"part_id": pid, "qty": 10, "purchase_price": 10}]}, headers=tenant)
    # 10 @ 20 into B  → global avg = (10*10 + 10*20)/20 = 15  (would be 20 under the old per-location bug)
    await client.post("/api/inventory/postings", json={
        "location_id": loc_b.id, "lines": [{"part_id": pid, "qty": 10, "purchase_price": 20}]}, headers=tenant)

    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    assert round(part.avg_cost, 2) == 15.0, part.avg_cost

    # cost layers exist for BOTH postings (exact-COGS parity with PO receipt)
    layers = (await db.execute(select(func.count()).select_from(StockItemBatch)
              .where(StockItemBatch.part_id == pid))).scalar()
    assert layers == 2, layers

    # quantities land per-location
    a = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid, StockLevel.location_id == loc_a))).scalar_one()
    b = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid, StockLevel.location_id == loc_b.id))).scalar_one()
    assert a.qty_available == 10 and b.qty_available == 10
