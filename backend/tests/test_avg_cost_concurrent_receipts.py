"""Two receipts of one product at the same moment must both move the moving-average cost.

apply_receipt_line read Part.avg_cost and global on-hand without a lock and wrote the new
average back, so two concurrent receipts each weighted against the old average and the later
write won (10 @10 on hand, then 10 @20 and 10 @40 at once gave 15 or 25 instead of 23.3333).
Postgres only: SQLite serialises writers, so the race can't happen there.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from backend.models import Part

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="needs concurrent writers (Postgres)")


async def _receive(client, h, loc, pid, qty, unit_cost):
    return await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": unit_cost})


@pytest.mark.asyncio
async def test_concurrent_receipts_both_move_avg_cost(client, tenant, db):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    for _ in range(3):      # a race doesn't lose every time; three products make a miss very unlikely
        r = await client.post("/api/catalog/products", headers=tenant, json={
            "name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert (await _receive(client, tenant, loc, pid, 10, 10)).status_code == 200
        r1, r2 = await asyncio.gather(_receive(client, tenant, loc, pid, 10, 20),
                                      _receive(client, tenant, loc, pid, 10, 40))
        assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
        part = (await db.execute(select(Part).where(Part.id == pid)
                                 .execution_options(populate_existing=True))).scalar_one()
        # 10 @10 + 10 @20 + 10 @40 -> 700 / 30, whichever receipt lands first
        assert abs(float(part.avg_cost) - 23.3333) < 0.01, part.avg_cost


@pytest.mark.asyncio
async def test_two_pos_with_the_same_products_in_opposite_order_both_receive(client, tenant, db):
    """Receiving locks each product before moving its average. Two POs holding the same products
    in opposite line order must not deadlock (one would roll back with a 500)."""
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    for _ in range(3):
        p1, p2 = [(await client.post("/api/inventory/parts", headers=tenant, json={
            "name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}"})).json()["id"]
            for _ in range(2)]
        pos = []
        for a, b in ((p1, p2), (p2, p1)):
            r = await client.post("/api/inventory/po", headers=tenant, json={
                "supplier_id": sup, "location_id": loc,
                "lines": [{"part_id": a, "qty_ordered": 5, "unit_cost": 10},
                          {"part_id": b, "qty_ordered": 5, "unit_cost": 30}]})
            assert r.status_code == 201, r.text
            pos.append(r.json()["po_id"])
        r1, r2 = await asyncio.gather(*[client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={})
                                        for po in pos])
        assert (r1.status_code, r2.status_code) == (200, 200), (r1.text[:300], r2.text[:300])
        for pid in (p1, p2):
            part = (await db.execute(select(Part).where(Part.id == pid)
                                     .execution_options(populate_existing=True))).scalar_one()
            assert abs(float(part.avg_cost) - 20) < 0.01, part.avg_cost      # 5 @10 + 5 @30
