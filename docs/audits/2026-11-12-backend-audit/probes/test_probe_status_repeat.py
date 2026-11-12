"""Probe WO: repeating the same status PATCH re-runs side effects."""
from sqlalchemy import select, func


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def test_same_status_twice_requeues_customer_message(client, tenant, db):
    from backend.models import Client, NotificationLog, NotificationRule, OrderStatusLog, Order, AuditLog
    oid = await _org(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "P", "email": "p@e.com"}, headers=tenant)).json()["id"]
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.consent_email = True
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="ready", channel="email", active=True))
    await db.commit()
    order_id = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    for _ in range(3):
        r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)
        assert r.status_code == 200, r.text
    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == order_id, NotificationLog.trigger_event == "status_changed"))).scalars().all()
    sl = (await db.execute(select(func.count()).select_from(OrderStatusLog).where(
        OrderStatusLog.order_id == order_id, OrderStatusLog.to_status == "ready"))).scalar()
    au = (await db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_id == order_id, AuditLog.action == "order.status_change"))).scalar()
    print("PROBE notification rows queued:", len(logs), [l.status for l in logs])
    print("PROBE status log rows ready:", sl, "audit status_change rows:", au)
    assert len(logs) == 3   # documents the bug: 3 customer messages for one real transition


async def test_closed_twice_resets_closed_at(client, tenant, db):
    from backend.models import Order
    cid = (await client.post("/api/clients", json={"name": "Q"}, headers=tenant)).json()["id"]
    order_id = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    first = o.closed_at
    import asyncio; await asyncio.sleep(0.05)
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    await db.refresh(o)
    print("PROBE closed_at first", first, "second", o.closed_at)
    assert o.closed_at != first
