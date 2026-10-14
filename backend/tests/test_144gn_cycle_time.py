"""144gn — cycle-time intelligence: dwell stats, stall detection, predicted ready.

Uses the direct `db` fixture to plant status-transition logs with a controlled
clock, so we can assert the derived stats deterministically.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import Order, OrderStatusLog
from backend.services.cycle_time import compute_dwell_stats, get_order_timing


async def _loc(client, H):
    r = await client.get("/api/staff/locations", headers=H)
    body = r.json()
    return (body.get("items") or body)[0]["id"]


@pytest.mark.asyncio
async def test_dwell_stats_and_stall(client, tenant, db):
    H = tenant
    loc_id = await _loc(client, H)

    # 5 orders that each sat 30 min in "new" then moved on → a solid bucket.
    # Create everything via the API FIRST (no open db-session txn), then plant the
    # controlled clock in one db block — interleaving the two locks SQLite.
    ids = []
    for i in range(5):
        o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": f"x{i}"}, headers=H)).json()
        ids.append(o["id"])
    stuck = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "stuck"}, headers=H)).json()

    base = datetime.utcnow() - timedelta(days=1)
    org_id = None
    for oid in ids:
        row = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
        org_id = row.org_id
        logs = (await db.execute(select(OrderStatusLog).where(OrderStatusLog.order_id == oid))).scalars().all()
        for lg in logs:
            lg.changed_at = base
        db.add(OrderStatusLog(order_id=oid, from_status="new", to_status="in_repair",
                              changed_at=base + timedelta(minutes=30)))
    stuck_row = (await db.execute(select(Order).where(Order.id == stuck["id"]))).scalar_one()
    stuck_row.status_entered_at = datetime.utcnow() - timedelta(hours=3)
    await db.commit()

    stats = await compute_dwell_stats(db, org_id, is_estimate=False)
    bucket = stats.get(("new", None))
    assert bucket is not None and bucket["n"] == 5, stats
    assert 29.0 <= bucket["avg"] <= 31.0, bucket

    # The order stuck in "new" for 3 hours → well past p80 → stalled.
    timing = await get_order_timing(db, stuck_row, stats)
    assert timing["stalled"] is True, timing
    assert timing["typical_min"] and timing["typical_min"] < timing["elapsed_min"]


@pytest.mark.asyncio
async def test_timing_endpoint_shape(client, tenant):
    H = tenant
    loc_id = await _loc(client, H)
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "y"}, headers=H)).json()
    r = await client.get(f"/api/orders/{o['id']}/timing", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("status", "elapsed_min", "stalled", "confidence", "predicted_ready_at"):
        assert k in body, body
    # Fresh order, no history → not stalled, no fictional prediction.
    assert body["stalled"] is False
    assert body["confidence"] == "none"


@pytest.mark.asyncio
async def test_timing_flags_batch(client, tenant):
    H = tenant
    loc_id = await _loc(client, H)
    await client.post("/api/orders", json={"location_id": loc_id, "complaint": "z"}, headers=H)
    r = await client.get("/api/orders/timing/flags", headers=H)
    assert r.status_code == 200, r.text
    assert "items" in r.json()
