"""P1 audit fixes, 12 Nov 2026 backend audit (docs/audits/2026-11-12-backend-audit/05-workflows-notifications.md).

- WO-08: customer and staff messages are queued in the same transaction as the change that caused
  them (status change, new job, client-visible comment). A crash between the two can't lose them.
- NTF-02: the legacy template path (the only one on the Solo plan) queues outbox rows; nothing is
  sent inside the request.
- NTF-03: the job's "Message customer" button goes through the outbox: consent and STOP apply, the
  message is in the job's history, and the answer says what really happened.
- NTF-04: a rescheduled job's reminder is cancelled and a new one, with the new time, is queued.
- NTF-05: email from the outbox carries a Message-ID made from the outbox row, the same on every try.
- NTF-06: Web Push has an explicit timeout.
- NTF-07: audit.record never hides a failed flush, and a failed audit row doesn't break the caller.
- NTF-01 follow-up: a push made with commit=False goes out only after the caller commits.
"""
import sys
import types
from datetime import timedelta

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


class _Crash(BaseException):
    """The process dying between two statements (not an error the code could handle)."""


class _crashes:
    """Expect the request to die with _Crash (the ASGI stack may wrap it in an exception group)."""

    def __enter__(self):
        return self

    def __exit__(self, typ, exc, tb):
        def has(e):
            return isinstance(e, _Crash) or any(has(x) for x in getattr(e, "exceptions", ()))
        assert exc is not None and has(exc), f"expected the request to crash, got {exc!r}"
        return True


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _job(client, h, **client_fields):
    cid = (await client.post("/api/clients", json={"name": "P", **client_fields}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=h)
    assert r.status_code in (200, 201), r.text
    return cid, r.json()["id"]


async def _consent(db, cid, **kw):
    from backend.models import Client
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    for k, v in kw.items():
        setattr(c, k, v)
    await db.commit()


async def _msgs(db, order_id, event=None):
    from backend.models import NotificationLog
    q = select(NotificationLog).where(NotificationLog.order_id == order_id)
    if event:
        q = q.where(NotificationLog.trigger_event == event)
    return (await db.execute(q.execution_options(populate_existing=True))).scalars().all()


def _crash_after(monkeypatch, event):
    """Let the rules queue their messages for `event`, then kill the request."""
    from backend.services import notification_rules as nr
    real = nr.evaluate

    async def evaluate(db, **kw):
        res = await real(db, **kw)
        if kw.get("event") == event:
            raise _Crash()
        return res
    monkeypatch.setattr(nr, "evaluate", evaluate)


# ── WO-08 ────────────────────────────────────────────────────────────────────────────────────────
async def test_wo08_status_change_and_its_message_commit_together(client, tenant, db, monkeypatch):
    from backend.models import NotificationRule, Order
    oid = (await _me(client, tenant))["org_id"]
    cid, order_id = await _job(client, tenant, email="p@e.com")
    await _consent(db, cid, consent_email=True)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="ready", channel="email", active=True))
    await db.commit()

    _crash_after(monkeypatch, "status_changed")
    with _crashes():
        await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)

    status = (await db.execute(select(Order.status).where(Order.id == order_id)
                               .execution_options(populate_existing=True))).scalar_one()
    queued = await _msgs(db, order_id, "status_changed")
    # Both or neither: never a saved status change whose message was lost.
    assert (status == "ready", len(queued)) in ((False, 0), (True, 1)), (status, len(queued))


async def test_wo08_new_job_and_its_created_message_commit_together(client, tenant, db, monkeypatch):
    from backend.models import NotificationRule, Order
    oid = (await _me(client, tenant))["org_id"]
    cid = (await client.post("/api/clients", json={"name": "C", "email": "c@e.com"}, headers=tenant)).json()["id"]
    await _consent(db, cid, consent_email=True)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="created",
                            channel="email", active=True))
    await db.commit()

    _crash_after(monkeypatch, "created")
    with _crashes():
        await client.post("/api/orders", json={"complaint": "crash", "client_id": cid}, headers=tenant)

    jobs = (await db.execute(select(Order.id).where(Order.org_id == oid, Order.client_id == cid))).scalars().all()
    queued = [m for j in jobs for m in await _msgs(db, j, "created")]
    assert (len(jobs), len(queued)) in ((0, 0), (1, 1)), (jobs, queued)


async def test_wo08_comment_and_its_message_commit_together(client, tenant, db, monkeypatch):
    from backend.models import NotificationRule, OrderComment
    oid = (await _me(client, tenant))["org_id"]
    cid, order_id = await _job(client, tenant, email="p@e.com")
    await _consent(db, cid, consent_email=True)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="comment_added",
                            channel="email", active=True))
    await db.commit()

    _crash_after(monkeypatch, "comment_added")
    with _crashes():
        await client.post(f"/api/orders/{order_id}/comments",
                          json={"body": "Your part arrived", "visible_to_client": True}, headers=tenant)
    comments = (await db.execute(select(OrderComment.id).where(OrderComment.order_id == order_id))).scalars().all()
    queued = await _msgs(db, order_id, "comment_added")
    assert (len(comments), len(queued)) in ((0, 0), (1, 1)), (comments, queued)


async def test_wo08_a_failing_rule_still_keeps_the_status_change(client, tenant, db, monkeypatch):
    """The messages ride a savepoint: a rule that errors loses only the messages, never the change."""
    from backend.models import Order
    from backend.services import notification_rules as nr
    _cid, order_id = await _job(client, tenant)

    async def broken(db, **kw):
        raise RuntimeError("template blew up")
    monkeypatch.setattr(nr, "evaluate", broken)
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)
    assert r.status_code == 200, r.text
    status = (await db.execute(select(Order.status).where(Order.id == order_id)
                               .execution_options(populate_existing=True))).scalar_one()
    assert status == "ready"


async def test_wo08_status_messages_still_queue_and_deliver(client, tenant, db):
    from backend.models import NotificationRule
    from backend.services import notification_rules as nr
    oid = (await _me(client, tenant))["org_id"]
    cid, order_id = await _job(client, tenant, email="p@e.com")
    await _consent(db, cid, consent_email=True)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="ready", channel="email", active=True))
    await db.commit()
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)
    assert r.status_code == 200, r.text
    [m] = await _msgs(db, order_id, "status_changed")
    assert m.status == "scheduled" and m.expect_status == "ready"
    await nr.run_due(db, org_id=oid)
    [m] = await _msgs(db, order_id, "status_changed")
    assert m.status == "sent"


# ── NTF-02 ───────────────────────────────────────────────────────────────────────────────────────
async def _solo(db, org_id):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.plan, org.sub_status = "solo", "active"
    await db.commit()


def _count_sends(monkeypatch) -> list:
    from backend.services import notifications
    sent: list = []
    real = notifications.SimulatedAdapter.send

    async def send(self, **kw):
        sent.append(kw.get("recipient"))
        return await real(self, **kw)
    monkeypatch.setattr(notifications.SimulatedAdapter, "send", send)
    return sent


async def test_ntf02_solo_shop_template_messages_are_queued_not_sent_in_the_request(client, tenant, db, monkeypatch):
    from backend.models import NotificationTemplate
    from backend.services import notification_rules as nr
    oid = (await _me(client, tenant))["org_id"]
    await _solo(db, oid)
    db.add(NotificationTemplate(org_id=oid, name="Got it", channel="email", trigger_event="order_created",
                                subject="We have your device", body_template="Thanks!", active=True))
    db.add(NotificationTemplate(org_id=oid, name="News", channel="email", trigger_event="comment_added",
                                subject="News", body_template="There's news", active=True))
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "S", "email": "s@e.com"}, headers=tenant)).json()["id"]
    await _consent(db, cid, consent_email=True)
    sent = _count_sends(monkeypatch)

    r = await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    order_id = r.json()["id"]
    r = await client.post(f"/api/orders/{order_id}/comments", json={"body": "hi", "visible_to_client": True},
                          headers=tenant)
    assert r.status_code == 201, r.text

    assert sent == []                                       # no provider call inside the requests
    created = await _msgs(db, order_id, "order_created")
    comment = await _msgs(db, order_id, "comment_added")
    assert [m.status for m in created] == ["scheduled"] and [m.status for m in comment] == ["scheduled"]
    assert created[0].template_id and created[0].send_at is not None

    res = await nr.run_due(db, org_id=oid)                  # the outbox delivers them, with retries
    assert res["sent"] == 2 and len(sent) == 2
    assert {m.status for m in await _msgs(db, order_id)} == {"sent"}


async def test_ntf02_legacy_template_respects_consent(client, tenant, db, monkeypatch):
    from backend.models import NotificationTemplate
    oid = (await _me(client, tenant))["org_id"]
    await _solo(db, oid)
    db.add(NotificationTemplate(org_id=oid, name="Got it", channel="sms", trigger_event="order_created",
                                body_template="Thanks!", active=True))
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "S", "phone": "+15550001"}, headers=tenant)).json()["id"]
    await _consent(db, cid, consent_phone=False)
    r = await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)
    [m] = await _msgs(db, r.json()["id"], "order_created")
    assert m.status == "skipped" and "consent" in (m.error or "")


# ── NTF-03 ───────────────────────────────────────────────────────────────────────────────────────
async def test_ntf03_message_customer_respects_stop_and_is_recorded(client, tenant, db, monkeypatch):
    cid, order_id = await _job(client, tenant, phone="+15550002")
    await _consent(db, cid, consent_phone=False)               # the customer replied STOP
    sent = _count_sends(monkeypatch)
    r = await client.post(f"/api/orders/{order_id}/notify", json={"channel": "sms", "body": "Ready!"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False and r.json()["status"] == "skipped"
    assert sent == []
    [m] = await _msgs(db, order_id, "manual")
    assert m.status == "skipped" and "consent" in (m.error or "")


async def test_ntf03_message_customer_reports_a_failed_send_and_retries_it(client, tenant, db, monkeypatch):
    from backend.services import notifications
    cid, order_id = await _job(client, tenant, phone="+15550003")
    await _consent(db, cid, consent_phone=True)

    async def refuse(self, **kw):
        return {"status": "failed", "error": "Twilio 503 busy"}
    monkeypatch.setattr(notifications.SimulatedAdapter, "send", refuse)
    r = await client.post(f"/api/orders/{order_id}/notify", json={"channel": "sms", "body": "Ready!"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["status"] == "retrying"
    [m] = await _msgs(db, order_id, "manual")
    assert m.status == "scheduled" and m.attempts == 1 and m.next_attempt_at is not None   # owed, retried later
    assert "Reply STOP" in m.body                       # first text to this customer says how to stop


async def test_ntf03_message_customer_sent(client, tenant, db):
    cid, order_id = await _job(client, tenant, email="m@e.com")
    await _consent(db, cid, consent_email=True)
    r = await client.post(f"/api/orders/{order_id}/notify",
                          json={"channel": "email", "subject": "Hi", "body": "Ready"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["status"] == "sent"
    [m] = await _msgs(db, order_id, "manual")
    assert m.status == "sent" and m.recipient == "m@e.com"


# ── NTF-04 ───────────────────────────────────────────────────────────────────────────────────────
async def _reminder_job(client, tenant, db, *, minutes_ahead: int):
    from backend.models import NotificationRule, Order
    from backend.services.dates import now_utc, shop_tzname, utc_to_wall
    oid = (await _me(client, tenant))["org_id"]
    cid, order_id = await _job(client, tenant, email="r@e.com")
    await _consent(db, cid, consent_email=True)
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="reminder",
                            reminder_offset_min=60, channel="email", active=True))
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    tz = await shop_tzname(db, oid, o.location_id)
    o.scheduled_for = (utc_to_wall(now_utc(), tz) + timedelta(minutes=minutes_ahead)).replace(microsecond=0)
    await db.commit()
    return oid, order_id, o, tz


async def test_ntf04_reschedule_cancels_the_old_reminder_and_queues_the_new_one(client, tenant, db):
    from backend.services import notification_rules as nr
    from backend.services.dates import now_utc, utc_to_wall
    oid, order_id, o, tz = await _reminder_job(client, tenant, db, minutes_ahead=30)
    assert await nr.queue_reminders(db, org_id=oid) == 1
    [old] = await _msgs(db, order_id, "reminder")
    assert old.status == "scheduled"

    # moved later the same morning: still inside the lead window, at a new time
    new_when = (utc_to_wall(now_utc(), tz) + timedelta(minutes=50)).replace(microsecond=0)
    r = await client.patch(f"/api/orders/{order_id}/schedule",
                           json={"scheduled_for": new_when.isoformat()}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()                                           # the sweep reads in a fresh session
    assert await nr.queue_reminders(db, org_id=oid) == 1     # the new reminder

    rows = sorted(await _msgs(db, order_id, "reminder"), key=lambda m: m.created_at)
    assert len(rows) == 2
    assert rows[0].id == old.id and rows[0].status == "skipped" and "rescheduled" in rows[0].error
    assert rows[1].status == "scheduled" and rows[1].send_at != old.send_at
    assert await nr.queue_reminders(db, org_id=oid) == 0      # still one per (rule, job, time)


async def test_ntf04_an_old_reminder_is_not_sent_after_a_reschedule(client, tenant, db):
    """Even before the next sweep runs: the send-time check sees the job moved."""
    from backend.services import notification_rules as nr
    oid, order_id, o, tz = await _reminder_job(client, tenant, db, minutes_ahead=30)
    await nr.queue_reminders(db, org_id=oid)
    [old] = await _msgs(db, order_id, "reminder")
    old.send_at = old.send_at - timedelta(minutes=5)          # due now
    o.scheduled_for = o.scheduled_for + timedelta(days=3)     # moved out of the window (e.g. the order edit)
    await db.commit()
    res = await nr.run_due(db, org_id=oid, now=old.send_at + timedelta(seconds=1))
    assert res["sent"] == 0
    [old] = await _msgs(db, order_id, "reminder")
    assert old.status == "skipped" and "rescheduled" in old.error


# ── NTF-05 ───────────────────────────────────────────────────────────────────────────────────────
async def test_ntf05_outbox_email_carries_a_stable_message_id(client, tenant, db, monkeypatch):
    from backend.models import NotificationLog
    from backend.services import notification_rules as nr
    from backend.services import notifications
    oid = (await _me(client, tenant))["org_id"]
    seen: list = []

    class Smtp(notifications.SmtpEmailAdapter):
        async def send(self, *, recipient, subject, body, attachment=None, html=None, headers=None):
            seen.append((headers or {}).get("Message-ID"))
            return {"status": "failed", "error": "SMTP: timed out"} if len(seen) == 1 else \
                {"status": "sent", "error": None}
    monkeypatch.setattr(nr, "_adapter_for", lambda ch, creds=None: Smtp({}))
    from backend.services.dates import now_utc
    now = now_utc()
    log = NotificationLog(org_id=oid, scope="client", channel="email", trigger_event="manual",
                          recipient="x@e.com", subject="s", body="b", status="scheduled", send_at=now)
    db.add(log)
    await db.commit()
    await nr.run_due(db, org_id=oid, now=log.send_at + timedelta(seconds=1))
    await db.refresh(log)
    await nr.run_due(db, org_id=oid, now=log.next_attempt_at + timedelta(seconds=1))
    assert len(seen) == 2 and seen[0] == seen[1] and log.id in seen[0]


async def test_ntf05_mailbox_send_gets_the_same_message_id(client, tenant, db, monkeypatch):
    from backend.models import EmailConnection, NotificationLog
    from backend.services import notification_rules as nr
    oid = (await _me(client, tenant))["org_id"]
    db.add(EmailConnection(org_id=oid, provider="gmail", email_address="shop@x.test", access_token="at",
                           connected=True, is_default=True))
    seen: list = []

    async def fake(conn, *, recipient, subject, body, attachment=None, html=False, message_id=None):
        seen.append(message_id)
        return {"status": "sent", "error": None}
    monkeypatch.setattr("backend.services.email_oauth.send_via_connection", fake)
    log = NotificationLog(org_id=oid, scope="client", channel="email", trigger_event="manual",
                          recipient="x@e.com", subject="s", body="b", status="scheduled")
    db.add(log)
    await db.flush()
    from backend.services.dates import now_utc
    log.send_at = now_utc()
    await db.commit()
    await nr.run_due(db, org_id=oid, now=log.send_at + timedelta(seconds=1))
    assert seen and log.id in seen[0]


# ── NTF-06 / NTF-01 follow-up ────────────────────────────────────────────────────────────────────
def _real_push(monkeypatch, sent: list):
    from backend.services import push
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: sent.append(kw)
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)


async def _subscribed(client, tenant, db):
    from backend.models import PushSubscription
    me = await _me(client, tenant)
    db.add(PushSubscription(org_id=me["org_id"], employee_id=me["id"], endpoint="https://push.invalid/p",
                            p256dh="k", auth="a"))
    await db.commit()
    return me["org_id"], me["id"]


async def test_ntf06_web_push_has_an_explicit_timeout(client, tenant, db, monkeypatch):
    from backend.services import push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="job.assigned", title="t")
    assert len(sent) == 1 and sent[0]["timeout"] == push.PUSH_TIMEOUT_S


async def test_ntf01_commit_false_push_waits_for_the_callers_commit(client, tenant, db, monkeypatch):
    from backend.services import push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)

    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Low", commit=False)
    assert sent == []                                   # not before the caller's work is saved
    await db.rollback()                                 # the caller fails
    await push.wait_pending()
    assert sent == []                                   # nothing announced for rolled-back work

    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Low", commit=False)
    assert sent == []
    await db.commit()
    await push.wait_pending()
    assert len(sent) == 1 and '"Low"' in sent[0]["data"]


# ── NTF-07 ───────────────────────────────────────────────────────────────────────────────────────
async def test_ntf07_a_failed_audit_row_leaves_the_callers_work_intact(client, tenant, db):
    from backend.models import AuditLog, Client
    from backend.services import audit
    org_id = (await _me(client, tenant))["org_id"]
    db.add(Client(org_id=org_id, name="KEEP-ME"))
    await audit.record(db, org_id=org_id, actor_id=None, action=None, entity_type="client")  # bad row
    await db.commit()                                   # used to raise PendingRollbackError
    assert (await db.execute(select(Client.id).where(Client.name == "KEEP-ME"))).scalar_one()
    assert not (await db.execute(select(AuditLog.id).where(
        AuditLog.org_id == org_id, AuditLog.action.is_(None)))).first()


async def test_ntf07_the_callers_own_flush_error_is_not_swallowed(client, tenant, db):
    from sqlalchemy.exc import IntegrityError

    from backend.models import Client
    from backend.services import audit
    org_id = (await _me(client, tenant))["org_id"]
    db.add(Client(org_id=org_id, name=None))            # the caller's own bad row
    with pytest.raises(IntegrityError):
        await audit.record(db, org_id=org_id, actor_id=None, action="client.create", entity_type="client")
    await db.rollback()
