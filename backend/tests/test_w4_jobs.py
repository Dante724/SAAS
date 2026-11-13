"""Wave 4, group A (jobs and notifications): the follow-ups from #214, #225, #227 and #228.

1. The tech app's replayed endpoints (/api/feed/order/{id}, /complete, /checklist, /signature)
   honour Idempotency-Key: the offline queue's replay gets the first answer, not a second write.
2. Card-reader settlement and /finish queue payment_received messages in the payment's own
   transaction (a crash after the commit used to keep the payment and lose the receipt).
3. post_customer_message's staff rules are saved with the message, not after its commit.
4. Chat -> job: a double tap makes one job; a replay with the same key gets the first answer.
5. Self check-in convert respects the plan's monthly job limit.
6. The check-in QR (token in the URL) is cached like other private media: briefly, never shared.
7. Unwinding a job's part outside the person's stock reach names the job and the part.
8. Closing a job stops its timers with the N-10 clip (not past time already logged).
9. A job type whose stored starting status is no longer a status reads back as the shop's
   default (flagged), so the editor can save it, and creating a job of that type still works.
"""
import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.models import EventFeedItem, NotificationLog, Order, Payment
from backend.tests.test_rr2_jobs_notifications import (
    _client_id,
    _Crash,
    _crashes,
    _employee,
    _job_limit_reached,
    _me,
    _order,
    _row,
    _thread,
)

pytestmark = pytest.mark.asyncio

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _key(h):
    return {**h, "Idempotency-Key": str(uuid.uuid4())}


async def _count(db, model, *where):
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def _crash_before(monkeypatch, fn_name, event):
    """Make notification_rules.<fn_name> die (process killed) for `event`, before it does anything."""
    from backend.services import notification_rules as nr
    real = getattr(nr, fn_name)

    async def die(*a, **kw):
        if kw.get("event") == event:
            raise _Crash()
        return await real(*a, **kw)
    monkeypatch.setattr(nr, fn_name, die)


# ── 1: replayed tech-app endpoints ───────────────────────────────────────────────────────────────
async def test_1_feed_post_replays_on_the_same_key(client, tenant, db):
    o = await _order(client, tenant)
    H = _key(tenant)
    body = {"item_type": "comment", "body": "Replaced the screen", "visibility": "client"}
    a = await client.post(f"/api/feed/order/{o['id']}", json=body, headers=H)
    b = await client.post(f"/api/feed/order/{o['id']}", json=body, headers=H)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert a.json()["id"] == b.json()["id"]
    assert await _count(db, EventFeedItem, EventFeedItem.entity_id == o["id"],
                        EventFeedItem.body == "Replaced the screen") == 1


async def test_1_signature_replays_on_the_same_key(client, tenant, db):
    o = await _order(client, tenant)
    H = _key(tenant)
    for _ in range(2):
        r = await client.post(f"/api/orders/{o['id']}/signature", json={"signature": PNG, "note": "field"}, headers=H)
        assert r.status_code == 200, r.text
    assert await _count(db, EventFeedItem, EventFeedItem.entity_id == o["id"],
                        EventFeedItem.item_type == "signature") == 1


async def test_1_checklist_add_replays_on_the_same_key(client, tenant, db):
    from backend.models import OrderChecklistItem
    o = await _order(client, tenant)
    H = _key(tenant)
    answers = []
    for _ in range(2):
        r = await client.post(f"/api/orders/{o['id']}/checklist", json={"label": "Test camera"}, headers=H)
        assert r.status_code == 201, r.text
        answers.append(r.json())
    assert answers[0] == answers[1]
    assert await _count(db, OrderChecklistItem, OrderChecklistItem.order_id == o["id"]) == 1


async def test_1_complete_replays_on_the_same_key(client, tenant, db):
    o = await _order(client, tenant)
    H = _key(tenant)
    a = await client.post(f"/api/orders/{o['id']}/complete", json={"signature": PNG}, headers=H)
    b = await client.post(f"/api/orders/{o['id']}/complete", json={"signature": PNG}, headers=H)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.json() == b.json()                 # the first answer, not "unchanged"
    assert await _count(db, EventFeedItem, EventFeedItem.entity_id == o["id"],
                        EventFeedItem.item_type == "signature") == 1


# ── 2: payment_received is saved with the payment ───────────────────────────────────────────────
async def _receipt_rule(client, tenant, db):
    from backend.models import Client, NotificationRule
    me = await _me(client, tenant)
    cid = await _client_id(client, tenant, email="p@e.com")
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.consent_email = True
    db.add(NotificationRule(org_id=me["org_id"], module="order", scope="client", event="payment_received",
                            channel="email", active=True))
    await db.commit()
    o = await _order(client, tenant, client_id=cid)
    r = await client.post(f"/api/orders/{o['id']}/lines", json={"line_type": "labour", "description": "Fix",
                                                                "qty": 1, "unit_price": 50}, headers=tenant)
    assert r.status_code == 201, r.text
    return o


async def _pays_and_receipts(db, oid):
    db.expire_all()
    pays = await _count(db, Payment, Payment.order_id == oid)
    queued = await _count(db, NotificationLog, NotificationLog.order_id == oid,
                          NotificationLog.trigger_event == "payment_received")
    return pays, queued


async def test_2_finish_queues_the_receipt_with_the_payment(client, tenant, db, monkeypatch):
    o = await _receipt_rule(client, tenant, db)
    aid = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    _crash_before(monkeypatch, "evaluate", "payment_received")
    with _crashes():
        await client.post(f"/api/orders/{o['id']}/finish",
                          json={"payment": {"method": "cash", "account_id": aid, "amount": 50}}, headers=tenant)
    assert await _pays_and_receipts(db, o["id"]) in ((0, 0), (1, 1))


async def test_2_finish_still_sends_the_receipt(client, tenant, db):
    o = await _receipt_rule(client, tenant, db)
    aid = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{o['id']}/finish",
                          json={"payment": {"method": "cash", "account_id": aid, "amount": 50}}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _pays_and_receipts(db, o["id"]) == (1, 1)


async def _reader_charge(client, tenant, oid):
    acc = (await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card"},
                             headers=tenant)).json()["id"]
    r = await client.post("/api/payments/terminal/readers", json={"provider": "simulated", "label": "Desk"},
                          headers=tenant)
    assert r.status_code == 201, r.text
    ch = await client.post("/api/payments/terminal/charge",
                           json={"terminal_id": r.json()["id"], "order_id": oid, "account_id": acc}, headers=tenant)
    assert ch.status_code in (200, 201), ch.text
    return ch.json()["id"]


async def test_2_card_reader_queues_the_receipt_with_the_payment(client, tenant, db, monkeypatch, card_readers_on):
    o = await _receipt_rule(client, tenant, db)
    chid = await _reader_charge(client, tenant, o["id"])
    _crash_before(monkeypatch, "evaluate", "payment_received")
    with _crashes():
        await client.post(f"/api/payments/terminal/charges/{chid}/simulate", json={"outcome": "approve"},
                          headers=tenant)
    assert await _pays_and_receipts(db, o["id"]) in ((0, 0), (1, 1))


async def test_2_card_reader_still_sends_the_receipt(client, tenant, db, card_readers_on):
    o = await _receipt_rule(client, tenant, db)
    chid = await _reader_charge(client, tenant, o["id"])
    r = await client.post(f"/api/payments/terminal/charges/{chid}/simulate", json={"outcome": "approve"},
                          headers=tenant)
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert await _pays_and_receipts(db, o["id"]) == (1, 1)


# ── 3: staff rules on a message to the customer ─────────────────────────────────────────────────
async def _staff_rule_job(client, tenant, db):
    from backend.models import NotificationRule, OrderComment  # noqa: F401
    me = await _me(client, tenant)
    tech = await _employee(client, tenant, "Bench tech")
    db.add(NotificationRule(org_id=me["org_id"], module="order", scope="staff", event="comment_added",
                            channel="email", active=True))
    await db.commit()
    o = await _order(client, tenant, assigned_to_id=tech)
    return o


async def _comments_and_staff_alerts(db, oid):
    from backend.models import OrderComment
    db.expire_all()
    return (await _count(db, OrderComment, OrderComment.order_id == oid),
            await _count(db, NotificationLog, NotificationLog.order_id == oid, NotificationLog.scope == "employee"))


async def test_3_staff_rules_are_saved_with_the_customer_message(client, tenant, db, monkeypatch):
    o = await _staff_rule_job(client, tenant, db)
    _crash_before(monkeypatch, "notify_staff_order", "comment_added")
    with _crashes():
        await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Your phone is ready"}, headers=tenant)
    assert await _comments_and_staff_alerts(db, o["id"]) in ((0, 0), (1, 1))


async def test_3_staff_rules_still_run_and_a_rule_error_keeps_the_message(client, tenant, db, monkeypatch):
    o = await _staff_rule_job(client, tenant, db)
    r = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Your phone is ready"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert await _comments_and_staff_alerts(db, o["id"]) == (1, 1)
    from backend.services import notification_rules as nr

    async def broken(*a, **kw):
        raise RuntimeError("rule blew up")
    monkeypatch.setattr(nr, "notify_staff_order", broken)
    r = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Still here"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert await _comments_and_staff_alerts(db, o["id"]) == (2, 1)


# ── 4: chat -> job double tap ────────────────────────────────────────────────────────────────────
async def test_4_double_tapped_chat_conversion_makes_one_job(client, tenant, db):
    me = await _me(client, tenant)
    tid = await _thread(db, me["org_id"], "+15550177")
    rs = await asyncio.gather(*[client.post(f"/api/chats/threads/{tid}/to-order", headers=tenant) for _ in range(2)])
    assert sorted(x.status_code for x in rs) == [201, 409], [(x.status_code, x.text) for x in rs]
    db.expire_all()
    from backend.models import ChatThread
    t = (await db.execute(select(ChatThread).where(ChatThread.id == tid))).scalar_one()
    assert await _count(db, Order, Order.client_id == t.client_id) == 1


async def test_4_chat_conversion_replays_on_the_same_key(client, tenant, db):
    me = await _me(client, tenant)
    tid = await _thread(db, me["org_id"], "+15550178")
    H = _key(tenant)
    a = await client.post(f"/api/chats/threads/{tid}/to-order", headers=H)
    b = await client.post(f"/api/chats/threads/{tid}/to-order", headers=H)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert a.json()["order_id"] == b.json()["order_id"]


# ── 5: self check-in convert and the job limit ───────────────────────────────────────────────────
async def test_5_checkin_convert_respects_the_job_limit(client, tenant, db, monkeypatch):
    from backend.tests.test_self_checkin import _body, _setup
    s = await _setup(client, tenant)
    assert (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).status_code == 201
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    me = await _me(client, tenant)
    before = await _count(db, Order, Order.org_id == me["org_id"])
    _job_limit_reached(monkeypatch)
    r = await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)
    assert r.status_code == 402, r.text
    assert await _count(db, Order, Order.org_id == me["org_id"]) == before
    item2 = (await client.get("/api/checkins", headers=tenant)).json()["waiting"]
    assert [x["id"] for x in item2] == [item["id"]]          # still in the queue


async def test_5_converted_checkin_still_returns_its_job_at_the_limit(client, tenant, monkeypatch):
    from backend.tests.test_self_checkin import _body, _setup
    s = await _setup(client, tenant)
    await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    first = await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)
    assert first.status_code == 200, first.text
    _job_limit_reached(monkeypatch)
    again = await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)
    assert again.status_code == 200 and again.json()["order_id"] == first.json()["order_id"]


# ── 6: check-in QR caching ───────────────────────────────────────────────────────────────────────
async def test_6_checkin_qr_is_cached_like_private_media(client, tenant):
    from backend.routers.media import MEDIA_CACHE
    from backend.tests.test_self_checkin import _body, _setup
    s = await _setup(client, tenant)
    st = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    r = await client.get(f"/api/public/checkin/s/{st['status_token']}/qr.svg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == MEDIA_CACHE
    assert r.headers.get("referrer-policy") == "no-referrer"


# ── 7: unwinding outside reach names the job ─────────────────────────────────────────────────────
async def test_7_unwind_outside_reach_names_the_job_and_the_part(client, tenant, db):
    from backend.models import Location
    from backend.tests import kit_helpers as K
    from backend.tests.test_qa_f2_stock import _on_hand
    from backend.tests.test_scoping_crm_chat import _scoped_login
    me = await _me(client, tenant)
    main = await K.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    pid = await K.part(client, tenant, main, "Screen W4", 99, 10, qty=5)
    oid, _ = await K.job(client, tenant, main)
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "kind": "product", "line_type": "part", "ref_id": pid, "description": "Screen W4", "qty": 1, "unit_price": 99})
    assert r.status_code in (200, 201), r.text
    line_id = r.json()["line_id"]
    assert (await client.post(f"/api/orders/{oid}/transfer", headers=tenant,
                              json={"location_id": b.id})).status_code == 200
    main_name = (await db.execute(select(Location.name).where(Location.id == main))).scalar_one()
    number = (await _row(db, oid)).order_number
    H, _emp, _role = await _scoped_login(client, db, me["org_id"], email=f"w4-{pid[:6]}@x.dev",
                                         warehouse_scope="selected", warehouse_ids=[b.id])
    r = await client.delete(f"/api/orders/{oid}/lines/{line_id}", headers=H)
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert main_name in detail and number in detail and "Screen W4" in detail, detail
    assert await _on_hand(db, pid) == 4


# ── 8: closing a job clips its timers ────────────────────────────────────────────────────────────
async def test_8_closing_a_job_clips_its_timer_at_time_already_logged(client, tenant, db):
    from backend.models import TimeEntry
    from backend.services.dates import now_utc
    me = await _me(client, tenant)
    o = await _order(client, tenant)
    now = now_utc()
    running = TimeEntry(org_id=me["org_id"], employee_id=me["id"], order_id=o["id"], state="running",
                        source="timer", started_at=now - timedelta(hours=2))
    later = TimeEntry(org_id=me["org_id"], employee_id=me["id"], state="stopped", source="manual",
                      category="admin", started_at=now - timedelta(hours=1),
                      ended_at=now - timedelta(minutes=30), duration_sec=1800)
    db.add_all([running, later])
    await db.commit()
    running_id, later_start = running.id, later.started_at
    r = await client.post(f"/api/orders/{o['id']}/complete", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    e = (await db.execute(select(TimeEntry).where(TimeEntry.id == running_id))).scalar_one()
    assert e.state == "stopped"
    assert e.ended_at == later_start               # not "now": the hour after is already logged
    assert e.needs_review is True
    assert e.duration_sec == 3600


# ── 9: a stale job-type starting status ──────────────────────────────────────────────────────────
async def _stale_type(client, tenant, db):
    from backend.models import WorkOrderType
    r = await client.post("/api/forms/types", json={"name": "Stale"}, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    wot = (await db.execute(select(WorkOrderType).where(WorkOrderType.id == tid))).scalar_one()
    wot.initial_status = "gone_status"
    await db.commit()
    return tid


async def test_9_a_stale_starting_status_reads_back_as_a_real_one(client, tenant, db):
    from backend.routers.orders import is_job_status
    me = await _me(client, tenant)
    tid = await _stale_type(client, tenant, db)
    t = next(x for x in (await client.get("/api/forms/types", headers=tenant)).json()["items"] if x["id"] == tid)
    assert t["initial_status"] != "gone_status"
    assert await is_job_status(db, me["org_id"], "work_order", t["initial_status"])
    assert t["initial_status_stale"] is True
    # the editor sends back what it was given: saving (e.g. a rename) works and repairs the row
    r = await client.put(f"/api/forms/types/{tid}", json={**{k: t[k] for k in (
        "color", "initial_status", "default_asset_type", "is_default", "sched_default_duration",
        "sched_required", "requires_signature", "requires_checklist")}, "name": "Renamed"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["initial_status_stale"] is False


async def test_9_a_stale_starting_status_never_breaks_job_creation(client, tenant, db):
    from backend.routers.orders import is_job_status
    me = await _me(client, tenant)
    tid = await _stale_type(client, tenant, db)
    o = await _order(client, tenant, type_id=tid)
    assert await is_job_status(db, me["org_id"], "work_order", o["status"])
    r = await client.post(f"/api/orders/{o['id']}/duplicate", headers=tenant)
    assert r.status_code in (200, 201), r.text
    dup = await _row(db, r.json().get("id") or r.json().get("order_id"))
    assert await is_job_status(db, me["org_id"], "work_order", dup.status)
    r = await client.patch(f"/api/orders/{o['id']}/type", json={"type_id": tid}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["status"] != "gone_status"


# ── #235 follow-up: generic inbound never matches a duplicate without a message id ───────────────
async def test_generic_inbound_without_an_id_never_returns_another_message(client, tenant, monkeypatch):
    me = await _me(client, tenant)
    sec = (await client.get("/api/settings/webhook-secret", headers=tenant)).json()["webhook_secret"]
    H = {"X-Webhook-Token": sec}
    first = await client.post(f"/api/chats/webhook/{me['org_id']}", headers=H,
                              json={"channel": "sms", "handle": "+15559401", "body": "hello"})
    assert first.status_code == 201 and first.json()["message_id"], first.text
    # ingest stores nothing (as when the shop is suspended): no id was sent, so there is no duplicate
    from backend.services import chat_status as cs
    real = cs.ingest_inbound

    async def nothing(*a, **kw):
        return None, None
    monkeypatch.setattr(cs, "ingest_inbound", nothing)
    r = await client.post(f"/api/chats/webhook/{me['org_id']}", headers=H,
                          json={"channel": "sms", "handle": "+15559402", "body": "other"})
    assert r.status_code == 201, r.text
    assert r.json()["message_id"] is None and r.json()["thread_id"] is None, r.json()
    assert r.json()["duplicate"] is False
    # a real retry (same provider id) still answers with the stored message
    monkeypatch.setattr(cs, "ingest_inbound", real)
    body = {"channel": "sms", "handle": "+15559403", "body": "x", "external_message_id": "SM-w4-1"}
    a = await client.post(f"/api/chats/webhook/{me['org_id']}", headers=H, json=body)
    b = await client.post(f"/api/chats/webhook/{me['org_id']}", headers=H, json=body)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert b.json()["duplicate"] is True and b.json()["message_id"] == a.json()["message_id"]
