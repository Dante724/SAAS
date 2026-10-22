"""Customer messages name the status the way the shop does ("Waiting parts"), never the
internal key ("waiting_parts") — both the built-in wording and the {Order Status} variable."""
from sqlalchemy import select


async def _org(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


async def _order(client, tenant, db, oid):
    from backend.models import Client
    cid = (await client.post("/api/clients", json={"name": "Sam", "email": "sam@e.com"}, headers=tenant)).json()["id"]
    (await db.execute(select(Client).where(Client.id == cid))).scalar_one().consent_email = True
    await db.commit()
    return (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]


async def test_default_wording_uses_status_name(client, tenant, db):
    from backend.models import NotificationLog, NotificationRule
    oid = await _org(client, tenant)
    order_id = await _order(client, tenant, db, oid)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="waiting_parts", channel="email", active=True))
    await db.commit()
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "waiting_parts"}, headers=tenant)
    assert r.status_code == 200, r.text
    log = (await db.execute(select(NotificationLog).where(NotificationLog.order_id == order_id,
           NotificationLog.trigger_event == "status_changed"))).scalars().first()
    assert log is not None and "Waiting parts" in log.body and "waiting_parts" not in log.body


async def test_status_variable_uses_status_name(client, tenant, db):
    from backend.models import NotificationLog, NotificationRule, NotificationTemplate
    oid = await _org(client, tenant)
    order_id = await _order(client, tenant, db, oid)
    t = NotificationTemplate(org_id=oid, name="t", channel="email", trigger_event="status_change",
                             body_template="Now: {Order Status}", active=True)
    db.add(t)
    await db.flush()
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="in_repair", channel="email", template_id=t.id, active=True))
    await db.commit()
    await client.patch(f"/api/orders/{order_id}/status", json={"status": "in_repair"}, headers=tenant)
    log = (await db.execute(select(NotificationLog).where(NotificationLog.order_id == order_id,
           NotificationLog.template_id == t.id))).scalars().first()
    assert log is not None and log.body == "Now: In repair"
