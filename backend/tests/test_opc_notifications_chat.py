"""Team C — notification currency (C1), short message variables (C2), chat statuses + spam
(C3) and the folded-in inbox webhooks (C4)."""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import (
    ChatThread,
    Client,
    NotificationLog,
    NotificationRule,
    NotificationTemplate,
    Order,
    Organisation,
)
from backend.services import chat_status, message_vars
from backend.services.currency import format_money


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _emp_with_role(client, H, perms):
    role = (await client.post("/api/staff/roles", json={"name": "R", "permissions": perms}, headers=H)).json()
    email = f"e-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Emp", "email": email, "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _usd(db, org_id):
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.currency, org.currency_code = "$", "USD"
    await db.commit()


async def _order(client, H, db, *, total=229.5, **client_fields):
    cid = (await client.post("/api/clients", json={"name": "Sam Rivera", "phone": "+15550100",
                                                   "email": "sam@x.dev", **client_fields}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=H)).json()["id"]
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.total = total
    await db.commit()
    return oid, cid


# ── C1: currency ─────────────────────────────────────────────────────────────

def test_format_money_keeps_cents_and_uses_the_symbol():
    assert format_money(229.5, "$", "USD") == "$229.50"
    assert format_money(1234567.891, "€", "EUR") == "€1,234,567.89"
    assert format_money(1500, "¥", "JPY") == "¥1,500"           # zero-decimal currency
    assert format_money(5, "AED", "AED") == "AED 5.00"


async def test_total_in_a_sent_message_uses_org_currency_with_decimals(client, tenant, db):
    me = await _me(client, tenant)
    await _usd(db, me["org_id"])
    await _order(client, tenant, db, total=229.5)
    r = (await client.post("/api/notifications/test", json={
        "channel": "email", "subject": "Job", "body": "Total {total}, due {balance_due}", "sample": True},
        headers=tenant)).json()
    assert "Total $229.50, due $229.50" in r["body"]
    assert "₹" not in r["body"]


async def test_auth_me_currency_comes_from_the_org(client, tenant, db):
    me = await _me(client, tenant)
    await _usd(db, me["org_id"])
    me = await _me(client, tenant)
    assert me["currency"] == "$" and me["currency_code"] == "USD"


async def test_legacy_fire_notifications_formats_total(client, tenant, db):
    from backend.routers.notifications import fire_notifications
    me = await _me(client, tenant)
    await _usd(db, me["org_id"])
    oid, _ = await _order(client, tenant, db, total=99.9)
    db.add(NotificationTemplate(org_id=me["org_id"], channel="sms", trigger_event="order_created",
                                body_template="Order {order_number}: {total}", active=True))
    await db.commit()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await fire_notifications(db, me["org_id"], "order_created", order) == 1
    from backend.models import EventFeedItem
    rows = (await db.execute(select(EventFeedItem).where(EventFeedItem.entity_id == oid))).scalars().all()
    assert any("$99.90" in (r.body or "") for r in rows)


# ── C2: variables ────────────────────────────────────────────────────────────

def test_missing_values_are_dropped_cleanly():
    v = {"client_first_name": "", "client_age": "", "order_number": "WO-1"}
    assert message_vars.apply("Hi {client_first_name}, job {order_number} is ready.", v) == "Hi, job WO-1 is ready."
    assert message_vars.apply("{client_first_name} your job is ready", v) == "your job is ready"
    assert message_vars.apply("Happy birthday {client_first_name} {client_age}!", v) == "Happy birthday!"
    assert message_vars.apply("A {client_age} b", v) == "A b"
    assert "—" not in message_vars.apply("x {client_age} y", v)
    assert message_vars.apply("Job {order_number}. Age: {client_age}. Thanks", v) == "Job WO-1. Thanks"
    assert message_vars.apply("Age: {client_age}\nJob {order_number}", v) == "Job WO-1"


def test_birthday_and_age():
    assert message_vars._birthday("1990-03-14", today=date(2026, 3, 13)) == ("14 March", "35")
    assert message_vars._birthday("1990-03-14", today=date(2026, 3, 14)) == ("14 March", "36")
    assert message_vars._birthday("--03-14") == ("14 March", "")    # year unknown → no age
    assert message_vars._birthday("garbage") == ("", "")


async def test_new_variables_render_in_a_rule_send(client, tenant, db):
    me = await _me(client, tenant)
    org_id = me["org_id"]
    await _usd(db, org_id)
    oid, cid = await _order(client, tenant, db, total=150, birthday="1990-03-14")
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.scheduled_for = datetime(2026, 10, 30, 10, 0)
    body = ("Hi {client_first_name} {client_last_name} ({client_phone}). Born {client_birthday}, age {client_age}. "
            "Due {balance_due} of {total}. Booked {scheduled_for}. Portal: {portal_link}")
    tpl = NotificationTemplate(org_id=org_id, channel="sms", trigger_event="status_change",
                               body_template=body, active=True)
    db.add(tpl)
    await db.flush()
    db.add(NotificationRule(org_id=org_id, module="order", scope="client", event="created",
                            channel="sms", template_id=tpl.id, active=True))
    await db.commit()
    from backend.services import notification_rules as nr
    await nr.evaluate(db, org_id=org_id, order_id=oid, event="created")
    log = (await db.execute(select(NotificationLog).where(NotificationLog.order_id == oid,
                                                          NotificationLog.template_id == tpl.id))).scalars().first()
    assert log.body.startswith("Hi Sam Rivera (+15550100). Born 14 March, age ")
    assert "Due $150.00 of $150.00." in log.body
    assert "Booked Fri 30 Oct, 10:00." in log.body
    # the link in the message is a live one (only its hash is stored)
    token = log.body.split("/portal/")[1].split()[0]
    from backend.services import portal_links
    assert (await portal_links.client_for(db, token)).id == cid
    assert "{" not in log.body


async def test_missing_client_fields_leave_no_gaps_in_a_real_send(client, tenant, db):
    me = await _me(client, tenant)
    org_id = me["org_id"]
    cid = (await client.post("/api/clients", json={"name": "Cher", "phone": "+1555"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()["id"]
    tpl = NotificationTemplate(org_id=org_id, channel="sms", trigger_event="status_change", active=True,
                               body_template="Hi {client_first_name} {client_last_name}, age {client_age}. Booked {scheduled_for}.")
    db.add(tpl)
    await db.flush()
    db.add(NotificationRule(org_id=org_id, module="order", scope="client", event="created",
                            channel="sms", template_id=tpl.id, active=True))
    await db.commit()
    from backend.services import notification_rules as nr
    await nr.evaluate(db, org_id=org_id, order_id=oid, event="created")
    log = (await db.execute(select(NotificationLog).where(NotificationLog.template_id == tpl.id))).scalars().first()
    assert log.body.startswith("Hi Cher, age. Booked.")


async def test_birthday_greeting_renders_short_client_variables(client, tenant, db):
    me = await _me(client, tenant)
    org_id = me["org_id"]
    today = datetime.utcnow()
    cid = (await client.post("/api/clients", json={
        "name": "Ana Lopez", "phone": "+1777", "birthday": f"1990-{today.month:02d}-{today.day:02d}"},
        headers=tenant)).json()["id"]
    tpl = NotificationTemplate(org_id=org_id, channel="sms", trigger_event="birthday", active=True,
                               body_template="Happy birthday {client_first_name}, {client_age} today!")
    db.add(tpl)
    await db.flush()
    db.add(NotificationRule(org_id=org_id, module="contact", event="birthday", channel="sms",
                            template_id=tpl.id, active=True))
    await db.commit()
    from backend.services import notification_rules as nr
    await nr.notify_contact_birthdays(db, org_id=org_id)
    log = (await db.execute(select(NotificationLog).where(NotificationLog.client_id == cid))).scalars().first()
    assert log.body == f"Happy birthday Ana, {today.year - 1990} today!"


async def test_variable_picker_lists_the_new_variables(client, tenant):
    new = ["{client_first_name}", "{client_last_name}", "{client_phone}", "{portal_link}",
           "{balance_due}", "{scheduled_for}", "{client_birthday}", "{client_age}"]
    groups = (await client.get("/api/notifications/variables", headers=tenant)).json()["groups"]
    assert list(groups)[0] == "Message" and all(v in groups["Message"] for v in new)
    contact = (await client.get("/api/notifications/variables?module=contact", headers=tenant)).json()["groups"]
    assert "{client_age}" in contact["Message"] and "{total}" not in contact["Message"]
    lead = (await client.get("/api/notifications/variables?module=lead", headers=tenant)).json()["groups"]
    assert "Message" not in lead                    # lead sends don't render them
    tpl = (await client.get("/api/notifications/templates", headers=tenant)).json()
    assert all(v in tpl["variables"] for v in new)


# ── C3: chat statuses ────────────────────────────────────────────────────────

async def _thread(client, H, phone="+15559000", first="hello"):
    r = await client.post("/api/chats/threads", json={"channel": "sms", "contact_phone": phone,
                                                      "first_message": first}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _status(client, H, tid):
    for folder in ("inbox", "spam"):
        for t in (await client.get(f"/api/chats/threads?folder={folder}", headers=H)).json()["items"]:
            if t["id"] == tid:
                return t
    raise AssertionError("thread not listed")


async def test_status_follows_messages_and_a_new_message_reopens(client, tenant):
    t = await _thread(client, tenant)
    assert t["status"] == "new"
    tid = t["id"]
    r = (await client.post(f"/api/chats/threads/{tid}/send", json={"body": "on it"}, headers=tenant)).json()
    assert r["thread_status"] == "replied"
    r = (await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "thanks, when?"}, headers=tenant)).json()
    assert r["thread_status"] == "reply_needed"
    p = await client.patch(f"/api/chats/threads/{tid}", json={"status": "completed"}, headers=tenant)
    assert p.status_code == 200 and p.json()["status"] == "completed"
    await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "one more thing"}, headers=tenant)
    assert (await _status(client, tenant, tid))["status"] == "reply_needed"


async def test_completed_thread_never_answered_reopens_as_new(client, tenant):
    tid = (await _thread(client, tenant, phone="+15559001"))["id"]
    await client.patch(f"/api/chats/threads/{tid}", json={"status": "completed"}, headers=tenant)
    await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "hello?"}, headers=tenant)
    assert (await _status(client, tenant, tid))["status"] == "new"


async def test_failed_send_is_not_a_reply(client, tenant, monkeypatch):
    from backend.routers import chats
    tid = (await _thread(client, tenant, phone="+15559002"))["id"]

    async def broken(db, org_id, t, body):
        return {"status": "failed", "error": "provider down"}, False
    monkeypatch.setattr(chats, "_transport", broken)
    r = (await client.post(f"/api/chats/threads/{tid}/send", json={"body": "hi"}, headers=tenant)).json()
    assert r["status"] == "failed" and r["thread_status"] == "new"


async def test_status_filter_and_counts(client, tenant):
    a = (await _thread(client, tenant, phone="+15559100"))["id"]
    b = (await _thread(client, tenant, phone="+15559101"))["id"]
    await client.post(f"/api/chats/threads/{b}/send", json={"body": "hi"}, headers=tenant)
    d = (await client.get("/api/chats/threads?status=new", headers=tenant)).json()
    assert [t["id"] for t in d["items"]] == [a]
    assert d["counts"]["new"] == 1 and d["counts"]["replied"] == 1 and d["counts"]["all"] == 2
    assert d["counts"]["spam"] == 0
    assert (await client.get("/api/chats/threads?status=bogus", headers=tenant)).status_code == 422
    assert (await client.patch(f"/api/chats/threads/{a}", json={"status": "bogus"},
                               headers=tenant)).status_code == 422


async def test_replied_threads_auto_complete_after_24h(client, tenant, db):
    old = (await _thread(client, tenant, phone="+15559200"))["id"]
    fresh = (await _thread(client, tenant, phone="+15559201"))["id"]
    waiting = (await _thread(client, tenant, phone="+15559202"))["id"]
    for tid in (old, fresh):
        await client.post(f"/api/chats/threads/{tid}/send", json={"body": "done"}, headers=tenant)
    rows = {t.id: t for t in (await db.execute(select(ChatThread).where(
        ChatThread.id.in_([old, fresh, waiting])))).scalars().all()}
    rows[old].last_outbound_at = datetime.utcnow() - timedelta(hours=25)
    rows[fresh].last_outbound_at = datetime.utcnow() - timedelta(hours=23)
    await db.commit()
    assert await chat_status.autocomplete_replied(db) >= 1
    assert (await _status(client, tenant, old))["status"] == "completed"
    assert (await _status(client, tenant, fresh))["status"] == "replied"
    assert (await _status(client, tenant, waiting))["status"] == "new"


def test_autocomplete_job_is_registered():
    from backend.core import scheduled  # noqa: F401 — registers the jobs
    from backend.core.jobs import job_runner
    assert "chat-autocomplete-sweep" in {p.name for p in job_runner._periodics}


# ── C3: spam ─────────────────────────────────────────────────────────────────

async def test_spam_folder_and_restore(client, tenant):
    tid = (await _thread(client, tenant, phone="+15559300"))["id"]
    r = (await client.patch(f"/api/chats/threads/{tid}", json={"is_spam": True}, headers=tenant)).json()
    assert r["is_spam"] is True and r["spam_at"]
    inbox = (await client.get("/api/chats/threads", headers=tenant)).json()
    assert tid not in [t["id"] for t in inbox["items"]] and inbox["counts"]["spam"] == 1
    assert inbox["counts"]["all"] == 0
    spam = (await client.get("/api/chats/threads?folder=spam", headers=tenant)).json()
    assert [t["id"] for t in spam["items"]] == [tid]
    r = (await client.patch(f"/api/chats/threads/{tid}", json={"is_spam": False}, headers=tenant)).json()
    assert r["is_spam"] is False and r["spam_at"] is None
    assert tid in [t["id"] for t in (await client.get("/api/chats/threads", headers=tenant)).json()["items"]]


async def test_spam_threads_raise_no_notifications(client, tenant, db, monkeypatch):
    from backend.services import push
    pushed = []

    async def fake_notify(db, **kw):
        pushed.append(kw)
    monkeypatch.setattr(push, "notify", fake_notify)
    me = await _me(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Spam Co", "phone": "+15559400"},
                             headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()["id"]
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.assigned_to_id = me["id"]
    await db.commit()
    tid = (await _thread(client, tenant, phone="+15559400"))["id"]

    await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "real question"}, headers=tenant)
    assert len(pushed) == 1
    await client.patch(f"/api/chats/threads/{tid}", json={"is_spam": True}, headers=tenant)
    await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "BUY NOW"}, headers=tenant)
    sec = (await client.get("/api/settings/webhook-secret", headers=tenant)).json()["webhook_secret"]
    await client.post(f"/api/chats/webhook/{me['org_id']}", headers={"X-Webhook-Token": sec},
                      json={"channel": "sms", "handle": "+15559400", "body": "BUY MORE"})
    assert len(pushed) == 1, "spam must not notify"


# ── tenant isolation + permissions ───────────────────────────────────────────

async def test_other_org_cannot_see_or_change_a_thread(client, tenant):
    tid = (await _thread(client, tenant, phone="+15559500"))["id"]
    other = await _tenant(client)
    assert (await client.patch(f"/api/chats/threads/{tid}", json={"is_spam": True},
                               headers=other)).status_code == 404
    assert (await client.get(f"/api/chats/threads/{tid}/messages", headers=other)).status_code == 404
    assert (await client.post(f"/api/chats/threads/{tid}/send", json={"body": "x"},
                              headers=other)).status_code == 404
    assert tid not in [t["id"] for t in (await client.get("/api/chats/threads", headers=other)).json()["items"]]


async def test_status_and_spam_need_can_manage_chats(client, tenant):
    tid = (await _thread(client, tenant, phone="+15559600"))["id"]
    denied = await _emp_with_role(client, tenant, {"can_manage_chats": False})
    assert (await client.patch(f"/api/chats/threads/{tid}", json={"status": "completed"},
                               headers=denied)).status_code == 403
    assert (await client.patch(f"/api/chats/threads/{tid}", json={"is_spam": True},
                               headers=denied)).status_code == 403
    allowed = await _emp_with_role(client, tenant, {"can_manage_chats": True})
    assert (await client.patch(f"/api/chats/threads/{tid}", json={"status": "completed"},
                               headers=allowed)).status_code == 200


def test_built_in_roles_grant_can_manage_chats():
    from backend.services.permissions import TEMPLATES, new_role_permissions
    for name in TEMPLATES:
        assert new_role_permissions(name)["can_manage_chats"] is True


# ── C4: webhooks folded into Chats ───────────────────────────────────────────

async def test_twilio_webhook_is_idempotent_on_message_sid(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    form = {"From": "+15559700", "Body": "is it ready?", "MessageSid": "SM123"}
    for _ in range(2):
        r = await client.post(f"/api/chats/webhook/twilio?org_id={org}", data=form)
        assert r.status_code == 200
    t = [t for t in (await client.get("/api/chats/threads", headers=tenant)).json()["items"]
         if t["contact_phone"] == "+15559700"]
    assert len(t) == 1 and t[0]["status"] == "new"
    msgs = (await client.get(f"/api/chats/threads/{t[0]['id']}/messages", headers=tenant)).json()["items"]
    assert len(msgs) == 1


async def test_same_provider_message_id_in_two_orgs_is_not_a_duplicate(client, tenant):
    other = await _tenant(client)
    for H in (tenant, other):
        org = (await _me(client, H))["org_id"]
        sec = (await client.get("/api/settings/webhook-secret", headers=H)).json()["webhook_secret"]
        r = (await client.post(f"/api/chats/webhook/{org}", headers={"X-Webhook-Token": sec},
                               json={"channel": "sms", "handle": "+1555", "body": "hi",
                                     "external_message_id": "shared-id"})).json()
        assert "duplicate" not in r


async def test_communication_tab_shows_chat_messages(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Dee", "phone": "+15559800"},
                             headers=tenant)).json()["id"]
    tid = (await _thread(client, tenant, phone="+15559800", first="hi there"))["id"]
    await client.post(f"/api/chats/threads/{tid}/send", json={"body": "hello Dee"}, headers=tenant)
    items = (await client.get(f"/api/clients/{cid}/communication", headers=tenant)).json()["items"]
    assert [i["direction"] for i in items[:2]] == ["outbound", "inbound"]
    assert items[0]["channel"] == "sms" and items[0]["author"] == "Admin"


@pytest.mark.parametrize("url", ["/api/chats/webhook/{org}", "/api/inbox/webhook/{org}"])
async def test_generic_webhooks_reject_a_wrong_token(client, tenant, url):
    org = (await _me(client, tenant))["org_id"]
    await client.get("/api/settings/webhook-secret", headers=tenant)    # issues the token
    r = await client.post(url.format(org=org), headers={"X-Webhook-Token": "nope"},
                          json={"channel": "sms", "handle": "+1", "body": "x"})
    assert r.status_code == 401


async def test_client_ledger_shows_the_org_currency_not_rupees(client, tenant, db):
    me = await _me(client, tenant)
    await _usd(db, me["org_id"])
    cid = (await client.post("/api/clients", json={"name": "Ledger"}, headers=tenant)).json()["id"]
    r = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert r["currency"]["symbol"] == "$" and r["currency"]["code"] == "USD"


# ── review fixes ─────────────────────────────────────────────────────────────

def test_substituted_values_are_not_expanded_again():
    out = message_vars.apply("Hi {client_name}, call {Company Phone}",
                             {"client_name": "{portal_link} {Company Phone}"}, {"{Company Phone}": "555"})
    assert out == "Hi {portal_link} {Company Phone}, call 555"


async def test_sample_preview_does_not_mint_a_portal_link(client, tenant, db):
    _, cid = await _order(client, tenant, db)
    r = (await client.post("/api/notifications/test", json={
        "channel": "email", "subject": "x", "body": "Hi. Portal: {portal_link}. Bye", "sample": True},
        headers=tenant)).json()
    assert r["body"] == "Hi. Bye"
    from backend.models import ClientPortalLink
    assert (await db.execute(select(ClientPortalLink).where(ClientPortalLink.client_id == cid))).first() is None


async def test_legacy_fire_notifications_uses_the_real_shop_name(client, tenant, db):
    from backend.routers.notifications import fire_notifications
    me = await _me(client, tenant)
    oid, _ = await _order(client, tenant, db)
    db.add(NotificationTemplate(org_id=me["org_id"], channel="sms", trigger_event="order_created",
                                body_template="From {shop_name}", active=True))
    await db.commit()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await fire_notifications(db, me["org_id"], "order_created", order)
    from backend.models import EventFeedItem
    rows = (await db.execute(select(EventFeedItem).where(EventFeedItem.entity_id == oid))).scalars().all()
    assert any("From Test Co" in (r.body or "") for r in rows)


async def test_racing_retry_cannot_store_a_message_twice(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    t, m = await chat_status.ingest_inbound(db, org, channel="sms", handle="+15558800", body="a", external_id="race-1")
    await db.commit()
    from backend.models import ChatMessage
    db.add(ChatMessage(thread_id=t.id, direction="in", body="dup", status="received", external_id="race-1"))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_generic_webhook_rejects_oversized_handle(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    sec = (await client.get("/api/settings/webhook-secret", headers=tenant)).json()["webhook_secret"]
    r = await client.post(f"/api/chats/webhook/{org}", headers={"X-Webhook-Token": sec},
                          json={"channel": "sms", "handle": "x" * 300, "body": "hi"})
    assert r.status_code == 422
