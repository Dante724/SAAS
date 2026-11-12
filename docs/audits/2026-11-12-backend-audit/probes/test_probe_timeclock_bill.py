"""Probe WO: /api/timeclock/order/{id}/bill re-bills the same time and edits closed jobs."""
from datetime import timedelta
from sqlalchemy import select


async def test_bill_twice_on_closed_job(client, tenant, db):
    from backend.models import Order, OrderLine, TimeEntry
    from backend.services.dates import now_utc
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    st = now_utc() - timedelta(hours=1)
    db.add(TimeEntry(org_id=me["org_id"], employee_id=me["id"], order_id=oid, started_at=st,
                     ended_at=st + timedelta(hours=1), duration_sec=3600, billable=True,
                     billable_rate=50, state="stopped"))
    await db.commit()
    for _ in range(2):
        r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)
        print("PROBE bill", r.status_code, r.text[:120])
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    print("PROBE lines", [(l.description, l.total) for l in lines], "order status", o.status, "total", o.total)
    assert len(lines) == 2
