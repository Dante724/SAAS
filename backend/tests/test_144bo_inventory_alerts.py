"""144bo — in-app inventory alerts: low-stock (min-stock crossing, deduped) + job-unblocked.

Pure decision helpers are tested in isolation; the async service functions are tested against
the real models + StaffNotification feed.
"""
import pytest
from sqlalchemy import select

from backend.models import Order, OrderLine, Part, StaffNotification, StockLevel
from backend.services import inventory_alerts as A


# ── pure helpers ────────────────────────────────────────────────────────────────
def test_crossed_low_edge():
    assert A.crossed_low(6, 4, 5) is True        # fell through the line
    assert A.crossed_low(6, 5, 5) is True         # landing exactly on the line counts
    assert A.crossed_low(4, 3, 5) is False        # already below before → not a new edge
    assert A.crossed_low(10, 8, 5) is False       # still above
    assert A.crossed_low(6, 4, 0) is False        # min_stock 0 = not tracked


def test_newly_covered_fifo():
    needs = [("A", 3), ("B", 2), ("C", 4)]  # cumulative 3, 5, 9
    # receiving lifts on-hand 0 -> 5: A (cum3) and B (cum5) covered, C (cum9) not
    assert A.newly_covered(0, 5, needs) == ["A", "B"]
    # already had 3 (A covered), receive to 9: B and C newly covered, A not re-reported
    assert A.newly_covered(3, 9, needs) == ["B", "C"]
    # nothing new
    assert A.newly_covered(9, 9, needs) == []


async def _part(client, h, name, sku, min_stock):
    p = (await client.post("/api/inventory/parts",
         json={"name": name, "sku": sku, "barcode": sku, "min_stock": min_stock}, headers=h)).json()
    return p


async def _org_loc(client, tenant, db):
    from backend.models import Part as _P
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    return loc


# ── low-stock on consume (min-stock crossing + dedupe) ───────────────────────────
@pytest.mark.asyncio
async def test_low_stock_fires_once_then_resets_on_receive(client, tenant, db):
    from backend.models import Part as P
    p = await _part(client, tenant, "Screen", "SCR-LOW", 5)
    org_id = (await db.execute(select(P.org_id).where(P.id == p["id"]))).scalar_one()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    # on-hand 3 (below the min of 5)
    db.add(StockLevel(part_id=p["id"], location_id=loc, qty_available=3, qty_reserved=0))
    await db.commit()
    part = (await db.execute(select(P).where(P.id == p["id"]))).scalar_one()

    fired = await A.maybe_low_stock(db, org_id, part)
    await db.commit()
    assert fired is True
    n1 = (await db.execute(select(StaffNotification).where(
        StaffNotification.org_id == org_id, StaffNotification.kind == A.KIND_LOW))).scalars().all()
    assert len(n1) >= 1  # at least the owner notified

    # dedupe: calling again does nothing while still low
    db.expire_all()
    part = (await db.execute(select(P).where(P.id == p["id"]))).scalar_one()
    assert part.low_stock_alerted is True
    assert await A.maybe_low_stock(db, org_id, part) is False

    # receive back above the line resets the flag
    await A.on_received(db, org_id, part, before_on_hand=3, after_on_hand=10)
    assert part.low_stock_alerted is False


# ── job-unblocked on receive ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_job_unblocked_notifies_on_receive(client, tenant, db):
    from backend.models import Part as P
    p = await _part(client, tenant, "Need Part", "NEED-UB", 0)
    org_id = (await db.execute(select(P.org_id).where(P.id == p["id"]))).scalar_one()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = Order(org_id=org_id, order_number="WO-UB-1", status="new", location_id=loc)
    db.add(o); await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", ref_id=p["id"], description="Need", qty=3))
    await db.commit()
    part = (await db.execute(select(P).where(P.id == p["id"]))).scalar_one()

    # before 0, after 3 → the job is now coverable
    nums = await A.on_received(db, org_id, part, before_on_hand=0, after_on_hand=3)
    await db.commit()
    assert "WO-UB-1" in nums
    n = (await db.execute(select(StaffNotification).where(
        StaffNotification.org_id == org_id, StaffNotification.kind == A.KIND_UNBLOCKED))).scalars().all()
    assert len(n) >= 1


@pytest.mark.asyncio
async def test_no_unblock_when_still_short(client, tenant, db):
    from backend.models import Part as P
    p = await _part(client, tenant, "Still Short", "SHORT-UB", 0)
    org_id = (await db.execute(select(P.org_id).where(P.id == p["id"]))).scalar_one()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = Order(org_id=org_id, order_number="WO-UB-2", status="new", location_id=loc)
    db.add(o); await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", ref_id=p["id"], description="Need", qty=10))
    await db.commit()
    part = (await db.execute(select(P).where(P.id == p["id"]))).scalar_one()
    # received only 3 of 10 needed → not covered, no notification
    nums = await A.on_received(db, org_id, part, before_on_hand=0, after_on_hand=3)
    assert nums == []
