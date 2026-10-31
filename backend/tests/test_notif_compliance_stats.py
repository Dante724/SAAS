"""Customer texting: STOP / START replies, the opt-out line on a customer's first text,
'Send me a sample' filled from a real job, per-rule send stats and shop-wide quiet hours."""
import pytest
from sqlalchemy import select

from backend.config import settings


async def _org(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


@pytest.fixture
def unsigned_ok(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "", raising=False)


async def _client(client, tenant, phone="+15550101"):
    return (await client.post("/api/clients", json={"name": "Sam Rivera", "phone": phone, "email": "sam@e.com"}, headers=tenant)).json()["id"]


@pytest.mark.asyncio
async def test_stop_and_start_replies_change_consent(client, tenant, db, unsigned_ok):
    from backend.models import Client
    org = await _org(client, tenant)
    cid = await _client(client, tenant)
    r = await client.post(f"/api/chats/webhook/twilio?org_id={org}", data={"From": "+15550101", "Body": "Stop"})
    assert r.status_code == 200, r.text
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    await db.refresh(c)
    assert c.consent_phone is False
    await client.post(f"/api/chats/webhook/twilio?org_id={org}", data={"From": "+15550101", "Body": "START"})
    await db.refresh(c)
    assert c.consent_phone is True
    # ordinary replies don't touch consent
    await client.post(f"/api/chats/webhook/twilio?org_id={org}", data={"From": "+15550101", "Body": "stop by at 5?"})
    await db.refresh(c)
    assert c.consent_phone is True


@pytest.mark.asyncio
async def test_first_text_says_how_to_stop_later_ones_dont(client, tenant, db):
    from backend.models import NotificationLog, NotificationRule
    from backend.services import notification_rules as nr
    org = await _org(client, tenant)
    cid = await _client(client, tenant)
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    db.add(NotificationRule(org_id=org, module="order", scope="client", event="comment_added", channel="sms", active=True))
    await db.commit()
    await nr.evaluate(db, org_id=org, order_id=oid, event="comment_added")
    await nr.evaluate(db, org_id=org, order_id=oid, event="comment_added")
    logs = (await db.execute(select(NotificationLog).where(NotificationLog.order_id == oid, NotificationLog.channel == "sms")
                             .order_by(NotificationLog.created_at))).scalars().all()
    assert len(logs) == 2
    assert logs[0].body.endswith(nr.OPT_OUT_FOOTER) and nr.OPT_OUT_FOOTER not in logs[1].body


@pytest.mark.asyncio
async def test_sample_send_fills_placeholders(client, tenant):
    cid = await _client(client, tenant)
    await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)
    r = await client.post("/api/notifications/test", json={"channel": "email", "subject": "Hi {Client Name}",
                                                          "body": "Hello {Client Name}", "sample": True}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "Hello Sam Rivera"


@pytest.mark.asyncio
async def test_rule_stats_and_texting_reach(client, tenant, db):
    from backend.models import Client, NotificationRule
    from backend.services import notification_rules as nr
    org = await _org(client, tenant)
    cid = await _client(client, tenant)
    await _client(client, tenant, "+15550202")
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    rule = NotificationRule(org_id=org, module="order", scope="client", event="comment_added", channel="email", active=True)
    db.add(rule)
    await db.commit()
    await nr.evaluate(db, org_id=org, order_id=oid, event="comment_added")
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.consent_phone = False
    await db.commit()
    st = (await client.get("/api/notifications/rules/stats", headers=tenant)).json()
    assert st["rules"][rule.id]["sent_7d"] == 1 and st["rules"][rule.id]["last_sent_at"]
    assert st["texting"] == {"with_phone": 2, "opted_out": 1}


@pytest.mark.asyncio
async def test_quiet_hours_switch_applies_to_customer_messages(client, tenant, db):
    from backend.models import NotificationRule
    org = await _org(client, tenant)
    db.add_all([NotificationRule(org_id=org, module="order", scope="client", event="created", channel="email", active=True),
                NotificationRule(org_id=org, module="order", scope="client", event="reminder", channel="sms", active=True),
                NotificationRule(org_id=org, module="order", scope="staff", event="created", channel="push", active=True)])
    await db.commit()
    assert (await client.put("/api/notifications/quiet-hours", json={"on": True}, headers=tenant)).status_code == 200
    rows = (await db.execute(select(NotificationRule).where(NotificationRule.org_id == org))).scalars().all()
    for r in rows:
        await db.refresh(r)
    by = {(r.scope, r.event): r.working_hours_only for r in rows}
    assert by[("client", "created")] is True and not by[("client", "reminder")] and not by[("staff", "created")]
