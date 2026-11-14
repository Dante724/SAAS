"""P0 audit fixes (2026-11-12 backend audit):

- WO-01: sending the status a job already has is a no-op (no second customer message, status log,
  audit row or webhook; closed_at, status_entered_at and warranty keep their values).
- NTF-01: push.notify(commit=False) never commits the caller's transaction, even when a real
  Web Push goes out.
- CLI-01: a suspended shop is not live: the outbox, webhooks, sweeps and public intake pages stop,
  the tracking page stays readable, and unsuspending picks everything up again.
"""
import asyncio
import sys
import types
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.tests.lapse_modes import OFF_MODES, off_mode, set_off_status

pytestmark = pytest.mark.asyncio


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _job(client, tenant, **client_fields):
    cid = (await client.post("/api/clients", json={"name": "P", **client_fields}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return cid, r.json()["id"]


# ── WO-01 ────────────────────────────────────────────────────────────────────────────────────────
async def test_wo01_same_status_again_sends_and_logs_nothing_more(client, tenant, db):
    from backend.models import (
        AuditLog,
        Client,
        NotificationLog,
        NotificationRule,
        OrderStatusLog,
        WebhookDelivery,
        WebhookEndpoint,
    )
    oid = (await _me(client, tenant))["org_id"]
    cid, order_id = await _job(client, tenant, email="p@e.com")
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.consent_email = True
    db.add(NotificationRule(org_id=oid, module="order", scope="client", event="status_changed",
                            trigger_status_key="ready", channel="email", active=True))
    db.add(WebhookEndpoint(org_id=oid, url="https://hooks.example.com/in", events="order.status_changed",
                           active=True))
    await db.commit()

    first = await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)
    assert first.status_code == 200, first.text
    assert not first.json().get("unchanged")
    for _ in range(2):            # a double tap and a phone retry
        r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"}, headers=tenant)
        assert r.status_code == 200, r.text
        assert r.json()["unchanged"] is True and r.json()["status"] == "ready"

    msgs = (await db.execute(select(func.count()).select_from(NotificationLog).where(
        NotificationLog.order_id == order_id, NotificationLog.trigger_event == "status_changed"))).scalar()
    logs = (await db.execute(select(func.count()).select_from(OrderStatusLog).where(
        OrderStatusLog.order_id == order_id, OrderStatusLog.to_status == "ready"))).scalar()
    audits = (await db.execute(select(func.count()).select_from(AuditLog).where(
        AuditLog.entity_id == order_id, AuditLog.action == "order.status_change"))).scalar()
    hooks = (await db.execute(select(func.count()).select_from(WebhookDelivery).where(
        WebhookDelivery.org_id == oid, WebhookDelivery.event == "order.status_changed"))).scalar()
    assert (msgs, logs, audits, hooks) == (1, 1, 1, 1)


async def test_wo01_closing_twice_keeps_closed_at_sla_clock_and_warranty(client, tenant, db):
    from backend.models import Order
    _cid, order_id = await _job(client, tenant)
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    o = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    closed, entered, warranty = o.closed_at, o.status_entered_at, o.warranty_until
    assert closed is not None
    await asyncio.sleep(0.05)
    r = await client.patch(f"/api/orders/{order_id}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["unchanged"] is True and r.json()["triggers_payment"] is False
    await db.refresh(o)
    assert (o.closed_at, o.status_entered_at, o.warranty_until) == (closed, entered, warranty)


async def test_wo01_bulk_set_status_to_the_same_status_is_a_no_op(client, tenant, db):
    from backend.models import OrderStatusLog
    _cid, order_id = await _job(client, tenant)
    assert (await client.patch(f"/api/orders/{order_id}/status", json={"status": "ready"},
                               headers=tenant)).status_code == 200
    r = await client.post("/api/orders/bulk", json={"order_ids": [order_id], "action": "set_status",
                                                    "value": "ready"}, headers=tenant)
    assert r.status_code == 200, r.text
    n = (await db.execute(select(func.count()).select_from(OrderStatusLog).where(
        OrderStatusLog.order_id == order_id, OrderStatusLog.to_status == "ready"))).scalar()
    assert n == 1


# ── NTF-01 ───────────────────────────────────────────────────────────────────────────────────────
def _real_push(monkeypatch, sent: list):
    """Pretend VAPID is configured and pywebpush is installed (nothing leaves the machine)."""
    from backend.services import push
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: sent.append(kw)
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)
    monkeypatch.setattr("backend.services.push._endpoint_is_public", lambda url: True)   # no DNS in tests


async def test_ntf01_real_push_with_commit_false_never_commits_the_callers_work(client, tenant, db, monkeypatch):
    from backend.models import Client, PushSubscription, StaffNotification
    from backend.services import push
    me = await _me(client, tenant)
    org_id, emp_id = me["org_id"], me["id"]
    db.add(PushSubscription(org_id=org_id, employee_id=emp_id, endpoint="https://fcm.googleapis.com/fcm/send/x",
                            p256dh="k", auth="a"))
    await db.commit()
    sent: list = []
    _real_push(monkeypatch, sent)

    db.add(Client(org_id=org_id, name="SHOULD-ROLL-BACK"))     # the caller's unfinished work
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder",
                      title="Low stock", body="x", commit=False)
    assert sent == []                                           # P1 follow-up: waits for the caller's commit
    await db.rollback()                                         # the caller then fails (e.g. a 409)
    await push.wait_pending()
    assert sent == []                                           # so nothing announced rolled-back work
    left = (await db.execute(select(Client).where(Client.name == "SHOULD-ROLL-BACK"))).scalars().all()
    bell = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == emp_id, StaffNotification.title == "Low stock"))).scalars().all()
    assert left == [] and bell == []


async def test_ntf01_committing_callers_still_save_the_bell_and_touch_the_device(client, tenant, db, monkeypatch):
    from backend.models import PushSubscription, StaffNotification
    from backend.services import push
    me = await _me(client, tenant)
    org_id, emp_id = me["org_id"], me["id"]
    sub = PushSubscription(org_id=org_id, employee_id=emp_id, endpoint="https://fcm.googleapis.com/fcm/send/y",
                           p256dh="k", auth="a")
    db.add(sub)
    await db.commit()
    sent: list = []
    _real_push(monkeypatch, sent)
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Saved")
    later: list = []
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Deferred",
                      commit=False, deferred=later)
    await db.commit()
    await push.dispatch_deferred(db, later)
    assert len(sent) == 2
    await db.rollback()                         # nothing pending: both were committed by their owners
    titles = set((await db.execute(select(StaffNotification.title).where(
        StaffNotification.employee_id == emp_id))).scalars().all())
    assert {"Saved", "Deferred"} <= titles
    await db.refresh(sub)
    assert sub.last_used_at is not None


# ── CLI-01 ───────────────────────────────────────────────────────────────────────────────────────
# Owner decision 7: a shop past its lapse grace is held exactly like a suspended one, so the CLI-01
# cases run both ways (backend/tests/lapse_modes.py).
@pytest.fixture(params=OFF_MODES)
def off(request):
    with off_mode(request.param):
        yield request.param


async def _set_status(db, org_id, status):
    await set_off_status(db, org_id, status)


async def test_cli01_suspended_shop_outbox_waits_and_unsuspend_delivers(client, tenant, db, off):
    from backend.models import NotificationLog
    from backend.services import notification_rules as nr
    from backend.services.dates import now_utc
    org_id = (await _me(client, tenant))["org_id"]
    row = NotificationLog(org_id=org_id, scope="client", channel="email", trigger_event="manual",
                          recipient="x@example.invalid", subject="s", body="b",
                          send_at=now_utc() - timedelta(minutes=1), status="scheduled")
    db.add(row)
    await db.commit()
    await _set_status(db, org_id, "suspended")
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 403
    assert org_id not in await nr.orgs_with_due(db)
    res = await nr.run_due(db, org_id=org_id)                 # a direct call is held at send time too
    assert res["sent"] == 0
    await db.refresh(row)
    assert row.status == "scheduled" and not row.attempts      # untouched: nothing used up

    await _set_status(db, org_id, "active")                    # unsuspended
    assert org_id in await nr.orgs_with_due(db)
    assert (await nr.run_due(db, org_id=org_id))["sent"] == 1


async def test_cli01_suspended_shop_webhooks_wait(client, tenant, db, monkeypatch, off):
    from backend.models import WebhookDelivery, WebhookEndpoint
    from backend.services import webhooks as wh
    org_id = (await _me(client, tenant))["org_id"]
    ep = WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/in", secret=wh.store_secret("s"),
                         events="*", active=True)
    db.add(ep)
    await db.commit()
    d = WebhookDelivery(org_id=org_id, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                        status="pending", attempts=0)
    db.add(d)
    await db.commit()
    tried: list = []

    async def fake_attempt(delivery, url, secret, body, *, now=None):
        tried.append(delivery.id)
        delivery.status = "sent"
    monkeypatch.setattr(wh, "_attempt", fake_attempt)

    await _set_status(db, org_id, "suspended")
    assert await wh.deliver_one(d.id) is None
    assert await wh.retry_sweep() == 0
    await db.refresh(d)
    assert tried == [] and d.status == "pending" and d.attempts == 0

    await _set_status(db, org_id, "active")
    assert await wh.retry_sweep() == 1
    await db.refresh(d)
    assert tried == [d.id] and d.status == "sent"


async def test_cli01_sweeps_skip_a_suspended_shop(client, tenant, db, off):
    """Every sweep picks shops through services.org_live.live_org_clause."""
    from backend.core import scheduled
    from backend.models import Organisation
    from backend.services.org_live import live_org_clause, org_id_is_live
    from backend.services.task_alerts import overdue_org_ids
    org_id = (await _me(client, tenant))["org_id"]
    live = select(Organisation.id).where(live_org_clause())
    assert org_id in (await db.execute(live)).scalars().all()
    await _set_status(db, org_id, "suspended")
    assert org_id not in (await db.execute(live)).scalars().all()
    assert await org_id_is_live(db, org_id) is False
    assert org_id not in await overdue_org_ids(db)
    # every sweep that now filters on the clause still runs cleanly
    for sweep in (scheduled.sweep_notifications, scheduled.sweep_time_automations,
                  scheduled.sweep_contact_lifecycle, scheduled.sweep_memberships,
                  scheduled.sweep_morning_briefs, scheduled.sweep_sense_agents,
                  scheduled.retry_failed_webhooks):
        await sweep()
    await _set_status(db, org_id, "trial")
    assert await org_id_is_live(db, org_id) is True


async def test_cli01_public_pages_close_intake_but_tracking_stays_readable(client, tenant, db, off):
    org_id = (await _me(client, tenant))["org_id"]
    _cid, order_id = await _job(client, tenant)
    token = (await client.get(f"/api/orders/{order_id}", headers=tenant)).json()["public_token"]
    points = (await client.get("/api/checkins/points", headers=tenant)).json()["items"]
    point = points[0]["path"].split("/")[-1]
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 200
    assert (await client.get(f"/api/public/checkin/{point}")).status_code == 200

    await _set_status(db, org_id, "suspended")
    # intake and customer actions are closed
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 403
    assert (await client.get(f"/api/public/checkin/{point}")).status_code == 403
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "When is it ready?"})
    assert r.status_code == 403
    # the customer can still see where their device is
    assert (await client.get(f"/api/public/track/{token}")).status_code == 200

    await _set_status(db, org_id, "active")
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 200
    assert (await client.get(f"/api/public/checkin/{point}")).status_code == 200
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "When is it ready?"})
    assert r.status_code == 200, r.text
