"""Data-integrity regression: converting an estimate to a work order deducts stock, and
the StockMovement it logs must be negative and equal to what was actually deducted (every
other consumption path logs qty=-deducted). Before the fix it logged qty=+l.qty (positive,
full line qty), corrupting any report that sums StockMovement.qty.

Uses a late one-shot session (not the `db` fixture) to read the movement, so the assertion
session isn't held open across the client writes (SQLite lock contention)."""
import os
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.models import StockMovement


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_convert_logs_negative_deducted_movement(client, tenant):
    loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts", json={"name": "ConvPart", "sku": "CV-1"}, headers=tenant)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 10, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "Est"}, headers=tenant)).json()["id"]
    est = (await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": loc, "complaint": "quote", "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Part", "qty": 3, "unit_price": 20}]})).json()
    oid = est["id"]

    before = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    r = await client.post(f"/api/orders/{oid}/convert", headers=tenant)
    assert r.status_code in (200, 201), r.text
    after = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    deducted = before - after
    assert deducted == 3, (before, after)

    eng = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            mv = (await s.execute(select(StockMovement).where(
                StockMovement.ref_id == oid, StockMovement.part_id == pid,
                StockMovement.movement_type == "use"))).scalars().all()
    finally:
        await eng.dispose()

    assert len(mv) == 1, mv
    # must be NEGATIVE and equal to -deducted (not +l.qty)
    assert mv[0].qty == -deducted, mv[0].qty
