"""144in — duplicate est_* statuses (record_type work_order + estimate) must not
crash status change, and ensure_seeded must self-heal the orphans."""
from sqlalchemy import select

from backend.models import OrderStatus


async def test_duplicate_estimate_status_heals_and_change_works(client, tenant, db):
    # an estimate at est_draft
    oid = (await client.post("/api/orders", json={"complaint": "x", "is_estimate": True}, headers=tenant)).json()["id"]
    est = (await client.get("/api/statuses?record_type=estimate", headers=tenant)).json()["items"]
    org_id = None
    # inject the orphan: a copy of est_sent mis-typed as work_order (the live corruption)
    src = next(s for s in est if s["key"] == "est_sent")
    row = (await db.execute(select(OrderStatus).where(OrderStatus.id == src["id"]))).scalar_one()
    org_id = row.org_id
    db.add(OrderStatus(org_id=org_id, key="est_sent", name="Sent", status_group="ready",
                       record_type="work_order", is_estimate=True))
    await db.commit()

    # before heal: two est_sent rows exist
    n = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.key == "est_sent"))).scalars().all()
    assert len(n) == 2

    # a /statuses call runs ensure_seeded → self-heal removes the work_order orphan
    await client.get("/api/statuses?record_type=estimate", headers=tenant)
    left = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.key == "est_sent"))).scalars().all()
    assert len(left) == 1 and left[0].record_type == "estimate"

    # and the status change no longer 500s
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "est_sent"}, headers=tenant)
    assert r.status_code == 200, r.text
    # work-order pipeline is clean of est_ orphans
    wo = (await client.get("/api/statuses?record_type=work_order", headers=tenant)).json()["items"]
    assert not [s for s in wo if s["key"].startswith("est_")]
