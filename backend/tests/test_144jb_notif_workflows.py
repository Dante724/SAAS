"""144jb — internal (staff) workflow notifications, scope + record_type scoping."""
from sqlalchemy import select


async def _me(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()


async def test_rule_accepts_scope_and_record_type(client, tenant):
    r = await client.post("/api/notifications/rules", json={
        "module": "order", "scope": "staff", "event": "status_changed",
        "record_type": "estimate", "channel": "email"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["scope"] == "staff" and body["record_type"] == "estimate"


async def test_staff_rule_notifies_assignee(client, tenant, db):
    """A staff-scoped order rule notifies the assigned tech (not the actor)."""
    from backend.models import NotificationLog, NotificationRule, Order
    me = await _me(client, tenant)
    oid = me.get("org_id") or me.get("orgId")
    emp_id = me.get("id") or me.get("employee_id")
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "9"}, headers=tenant)).json()["id"]
    order_id = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    # assign the order to the admin employee (has an email)
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    o.assigned_to_id = emp_id
    db.add(NotificationRule(org_id=oid, module="order", scope="staff",
                            event="status_changed", channel="email", active=True))
    await db.commit()

    from backend.services import notification_rules as nr
    # actor is someone else, so the assignee should be notified
    sent = await nr.notify_staff_order(db, org_id=oid, order_id=order_id,
                                       event="status_changed", status_key="in_repair",
                                       actor_id="someone-else")
    assert sent >= 1
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == order_id,
        NotificationLog.scope == "employee"))).scalars().first()
    assert log is not None and log.employee_id == emp_id


async def test_staff_rule_skips_the_actor(client, tenant, db):
    """You are not notified about a change you made yourself."""
    from backend.models import NotificationLog, NotificationRule, Order
    me = await _me(client, tenant)
    oid = me.get("org_id") or me.get("orgId")
    emp_id = me.get("id") or me.get("employee_id")
    cid = (await client.post("/api/clients", json={"name": "C2", "phone": "8"}, headers=tenant)).json()["id"]
    order_id = (await client.post("/api/orders", json={"complaint": "y", "client_id": cid}, headers=tenant)).json()["id"]
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    o.assigned_to_id = emp_id
    db.add(NotificationRule(org_id=oid, module="order", scope="staff",
                            event="status_changed", channel="email", active=True))
    await db.commit()

    from backend.services import notification_rules as nr
    sent = await nr.notify_staff_order(db, org_id=oid, order_id=order_id,
                                       event="status_changed", status_key="in_repair",
                                       actor_id=emp_id)   # actor IS the assignee
    assert sent == 0


async def test_record_type_scopes_client_rule(client, tenant, db):
    """An estimate-scoped client rule does not fire for a work_order."""
    from backend.models import Client, NotificationRule, Order
    me = await _me(client, tenant)
    oid = me.get("org_id") or me.get("orgId")
    cid = (await client.post("/api/clients", json={"name": "C3", "phone": "7", "email": "c3@e.com"}, headers=tenant)).json()["id"]
    cust = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    cust.consent_email = True
    order_id = (await client.post("/api/orders", json={"complaint": "z", "client_id": cid}, headers=tenant)).json()["id"]
    # Clear the org's seeded default rules (build 144ah) so this test controls the rule set and the
    # only rule present is the estimate-scoped one under test.
    from sqlalchemy import delete as _delete
    await db.execute(_delete(NotificationRule).where(NotificationRule.org_id == oid))
    # order defaults to record_type work_order; rule scoped to estimate → no match
    db.add(NotificationRule(org_id=oid, module="order", scope="client",
                            event="status_changed", record_type="estimate",
                            channel="email", active=True))
    await db.commit()

    from backend.services import notification_rules as nr
    res = await nr.evaluate(db, org_id=oid, order_id=order_id,
                            event="status_changed", status_key="in_repair")
    assert res["matched"] == 0
