"""144jc — order created / comment / payment now fire the rules engine; send-test;
per-location working-hours window."""
from sqlalchemy import select


async def _org(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


async def test_order_created_fires_rule(client, tenant, db):
    """A client 'Order created' rule now fires on order creation (was dead)."""
    from backend.models import Client, NotificationLog, NotificationRule
    oid = await _org(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "New Client", "email": "n@e.com"}, headers=tenant)).json()["id"]
    cust = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    cust.consent_email = True
    db.add(NotificationRule(org_id=oid, module="order", scope="client",
                            event="created", channel="email", active=True))
    await db.commit()

    order_id = (await client.post("/api/orders", json={"complaint": "boom", "client_id": cid}, headers=tenant)).json()["id"]
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == order_id,
        NotificationLog.trigger_event == "created"))).scalars().first()
    assert log is not None


async def test_payment_and_comment_events_are_valid_rules(client, tenant):
    for ev in ("payment_received", "comment_added"):
        r = await client.post("/api/notifications/rules",
                              json={"module": "order", "event": ev, "channel": "email"},
                              headers=tenant)
        assert r.status_code in (200, 201), r.text


async def test_comment_added_evaluates(client, tenant, db):
    from backend.models import Client, NotificationLog, NotificationRule
    oid = await _org(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Cc", "email": "cc@e.com"}, headers=tenant)).json()["id"]
    cust = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    cust.consent_email = True
    order_id = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    db.add(NotificationRule(org_id=oid, module="order", scope="client",
                            event="comment_added", channel="email", active=True))
    await db.commit()
    from backend.services import notification_rules as nr
    res = await nr.evaluate(db, org_id=oid, order_id=order_id, event="comment_added")
    assert res["matched"] == 1
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == order_id,
        NotificationLog.trigger_event == "comment_added"))).scalars().first()
    assert log is not None


async def test_send_test_endpoint(client, tenant):
    """Send test delivers to the signed-in user (simulated adapter → sent)."""
    r = await client.post("/api/notifications/test",
                          json={"channel": "email", "subject": "Test", "body": "Hello {Client Name}"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("sent", "queued", "simulated")


def test_working_window_shift():
    from datetime import datetime

    from backend.services.notification_rules import shift_into_working_hours
    late = datetime(2026, 9, 20, 20, 0)          # 20:00
    # default 9–18 → pushes to next day 09:00
    nxt = shift_into_working_hours(late)
    assert nxt.hour == 9 and nxt.day == 21
    # wider per-location window 8–21 → 20:00 stays same day
    same = shift_into_working_hours(late, 8 * 60, 21 * 60)
    assert same.hour == 20 and same.day == 20
