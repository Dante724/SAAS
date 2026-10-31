"""144ja — notification parity: module-scoped variables, Contacts birthday module."""
from datetime import date, datetime

from sqlalchemy import select


async def _org_id(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


async def test_variables_scoped_by_module(client, tenant):
    """N-P3 — the task (internal) tab hides client-facing groups; full catalog otherwise."""
    full = (await client.get("/api/notifications/variables", headers=tenant)).json()["groups"]
    task = (await client.get("/api/notifications/variables?module=task", headers=tenant)).json()["groups"]
    assert "Client" in full                     # unscoped keeps everything
    assert "Client" not in task                 # staff tab drops client vars
    assert "Company" in task and "Date" in task  # keeps staff-relevant groups


async def test_contact_module_rule_accepted(client, tenant):
    """N-P4 — a contact/birthday rule is a valid rule; a bogus module is rejected."""
    ok = await client.post("/api/notifications/rules",
                           json={"module": "contact", "event": "birthday", "channel": "email"},
                           headers=tenant)
    assert ok.status_code in (200, 201), ok.text
    bad = await client.post("/api/notifications/rules",
                            json={"module": "nope", "event": "birthday", "channel": "email"},
                            headers=tenant)
    assert bad.status_code == 400


async def test_birthday_sweep_sends_once(client, tenant, db):
    """A client whose birthday is today gets one greeting; a re-run sends none."""
    from backend.models import Client, NotificationLog, NotificationRule
    oid = await _org_id(client, tenant)
    from backend.services.dates import shop_today
    today = await shop_today(db, oid)   # the shop's calendar day, which is what the sweep uses
    bday = f"1990-{today.month:02d}-{today.day:02d}"
    cid = (await client.post("/api/clients",
                             json={"name": "Birthday Person", "email": "bday@example.com",
                                   "birthday": bday, "consent_email": True},
                             headers=tenant)).json()["id"]
    db.add(NotificationRule(org_id=oid, module="contact", event="birthday",
                            channel="email", active=True))
    await db.commit()

    from backend.services import notification_rules as nr
    n1 = await nr.notify_contact_birthdays(db, org_id=oid)
    assert n1 >= 1
    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.client_id == cid,
        NotificationLog.trigger_event == "birthday"))).scalars().all()
    assert len(logs) == 1 and logs[0].status == "sent"

    # idempotent within the same year
    n2 = await nr.notify_contact_birthdays(db, org_id=oid)
    assert n2 == 0
    logs2 = (await db.execute(select(NotificationLog).where(
        NotificationLog.client_id == cid,
        NotificationLog.trigger_event == "birthday"))).scalars().all()
    assert len(logs2) == 1
