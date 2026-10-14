"""144iz — notification hardening: consent gate, scheduler run_due, provider status."""
from datetime import datetime, timedelta

from sqlalchemy import select


async def _org_id(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


async def test_provider_status_defaults_simulated(client, tenant):
    r = await client.get("/api/notifications/providers", headers=tenant)
    assert r.status_code == 200, r.text
    ch = r.json()["channels"]
    # no creds configured in tests → everything simulated
    assert ch["email"] == "simulated" and ch["sms"] == "simulated"


async def test_consent_blocks_send(client, tenant, db):
    """A client opted out on phone must not receive an SMS; it's logged skipped."""
    from backend.models import Client, NotificationLog, NotificationRule, Order
    oid = await _org_id(client, tenant)
    # create client + order via API (avoids model NOT-NULL pitfalls), then opt out
    cid = (await client.post("/api/clients", json={"name": "No SMS", "phone": "9990001111"}, headers=tenant)).json()["id"]
    oid_order = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    cust = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    cust.consent_phone = False
    # Clear the default rules seeded on org creation (build 144ah) so only our SMS rule fires —
    # otherwise a seeded email rule also matches status_changed and logs a "no email" skip, and
    # the assertion below could pick that log instead of the consent one.
    from sqlalchemy import delete as _delete
    await db.execute(_delete(NotificationRule).where(NotificationRule.org_id == oid))
    db.add(NotificationRule(org_id=oid, module="order", event="status_changed",
                            channel="sms", active=True))
    await db.commit()

    from backend.services import notification_rules as nr
    await nr.evaluate(db, org_id=oid, order_id=oid_order, event="status_changed", status_key="in_repair")
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == oid_order, NotificationLog.channel == "sms"))).scalars().first()
    assert log is not None and log.status == "skipped"
    assert "consent" in (log.error or "").lower()


async def test_run_due_dispatches_scheduled(client, tenant, db):
    """A scheduled NotificationLog whose send_at has passed is dispatched by run_due."""
    from backend.models import Client, NotificationLog
    oid = await _org_id(client, tenant)
    cust = Client(org_id=oid, name="Due Client", phone="9995556666", consent_phone=True)
    db.add(cust)
    await db.flush()
    db.add(NotificationLog(
        org_id=oid, scope="client", channel="sms", trigger_event="reminder",
        client_id=cust.id, recipient=cust.phone, subject=None, body="Reminder!",
        status="scheduled", send_at=datetime.utcnow() - timedelta(minutes=1)))
    await db.commit()

    from backend.services import notification_rules as nr
    res = await nr.run_due(db, org_id=oid)
    assert res["due"] >= 1 and res["sent"] >= 1
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.client_id == cust.id))).scalars().first()
    assert log.status == "sent"
