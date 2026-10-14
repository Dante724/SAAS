"""144at — reservation lock: a job's reserved stock can't be consumed by another job (or a POS
sale) unless the actor can override. Tested at the service layer for deterministic control over the
override flag (the API admin is an owner, who always overrides)."""
import pytest
from sqlalchemy import select

from backend.models import Part, StockLevel, StockMovement
from backend.services import reservations as rsv


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _seed(client, h, qty):
    loc = await _loc(client, h)
    pid = (await client.post("/api/inventory/parts", json={"name": "Coil", "sku": f"RL-{qty}"}, headers=h)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                      json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "Owner"}, headers=h)).json()["id"]
    est = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Coil", "qty": qty, "unit_price": 1}]},
        headers=h)).json()["id"]
    # reserve the whole stock to the estimate
    await client.post(f"/api/orders/{est}/reserve-stock", json={"hold_days": 7}, headers=h)
    return loc, pid, est


@pytest.mark.asyncio
async def test_block_then_override(client, tenant, db):
    loc, pid, est = await _seed(client, tenant, 5)
    org_id = (await db.execute(select(Part.org_id).where(Part.id == pid))).scalar_one()

    # another job wants 2 — all 5 are held by `est`, so 2 are reserved-blocked
    plan = await rsv.plan_consumption(db, part_id=pid, location_id=loc, order_id="other-wo", qty=2)
    assert plan["free"] == 0 and plan["reserved_block"] == 2

    # without override → blocked, nothing drawn
    g = await rsv.guard_and_draw(db, org_id=org_id, part_id=pid, location_id=loc, order_id="other-wo",
                                 qty=2, actor_id=None, allow_override=False)
    assert g["blocked"] == 2 and g["take"] == 0

    # with override → draws 2 from the estimate's hold, audited
    g2 = await rsv.guard_and_draw(db, org_id=org_id, part_id=pid, location_id=loc, order_id="other-wo",
                                  qty=2, actor_id=None, allow_override=True, taken_label="WO-OTHER")
    assert g2["blocked"] == 0 and g2["take"] == 2
    assert sum(o["qty"] for o in g2["overrides"]) == 2
    await db.commit()

    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == pid, StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_reserved == 3   # 5 held − 2 overridden
    mv = (await db.execute(select(StockMovement).where(
        StockMovement.part_id == pid, StockMovement.movement_type == "reserve_override"))).scalars().all()
    assert len(mv) == 1


@pytest.mark.asyncio
async def test_free_stock_is_consumable_without_override(client, tenant, db):
    # 5 on hand, only 2 reserved to the estimate → 3 free, another job can take 3 with no override
    loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "RL-F"}, headers=tenant)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 5, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "O"}, headers=tenant)).json()["id"]
    est = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Belt", "qty": 2, "unit_price": 1}]},
        headers=tenant)).json()["id"]
    await client.post(f"/api/orders/{est}/reserve-stock", json={"hold_days": 7}, headers=tenant)
    org_id = (await db.execute(select(Part.org_id).where(Part.id == pid))).scalar_one()

    g = await rsv.guard_and_draw(db, org_id=org_id, part_id=pid, location_id=loc, order_id="other-wo",
                                 qty=3, actor_id=None, allow_override=False)
    assert g["blocked"] == 0 and g["take"] == 3
    g2 = await rsv.guard_and_draw(db, org_id=org_id, part_id=pid, location_id=loc, order_id="other-wo",
                                  qty=4, actor_id=None, allow_override=False)
    assert g2["blocked"] == 1   # only 3 free, the 4th dips into the estimate's hold
