"""Re-review 2 (13 Nov 2026), work orders and notifications: review-out/jobs-notifications.md.

- N1  the tech PWA's offline replay sends one Idempotency-Key per mutation, made when it is first
      sent and kept in the queue; the status endpoint (like comments) honours it.
- N2  continue, split, chat -> job and the e-commerce import run the shared create pipeline
      (valid status, asset rule, job limit, active people, audit row, "created" messages), and
      the import numbers with the real allocator.
- N3  staff workflow rules skip deactivated employees (email, SMS and the bell).
- N4  a savepoint rollback drops only the pushes queued inside it; a savepoint release sends
      nothing early; the payload carries the bell row's id; shutdown waits for pending pushes.
- N5  a same-status change that carries a comment or note saves it (no status side effects).
- N6  status and line replies return the job's new updated_at, so the drawer's If-Match stays fresh.
- N7  status webhooks and the feedback request commit with the status change.
- N8  converting a booking twice makes one job.
- N9  the moved-reminders sweep runs a fixed number of queries, not one or two per reminder.
- WO-08 payment_received messages commit with the payment.
"""
import asyncio
import json
import pathlib
import shutil
import subprocess
import sys
import types
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from backend.models import AuditLog, NotificationLog, Order

pytestmark = pytest.mark.asyncio

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
class _Crash(BaseException):
    """The process dying between two statements (not an error the code could handle)."""


class _crashes:
    def __enter__(self):
        return self

    def __exit__(self, typ, exc, tb):
        def has(e):
            return isinstance(e, _Crash) or any(has(x) for x in getattr(e, "exceptions", ()))
        assert exc is not None and has(exc), f"expected the request to crash, got {exc!r}"
        return True


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _client_id(client, h, **kw):
    r = await client.post("/api/clients", json={"name": "Pat", **kw}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order(client, h, **kw):
    body = {"complaint": "screen", **kw}
    if "client_id" not in body:
        body["client_id"] = await _client_id(client, h)
    r = await client.post("/api/orders", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _employee(client, h, name="Tech"):
    r = await client.post("/api/staff/employees", headers=h,
                          json={"name": name, "email": f"e-{uuid.uuid4().hex[:8]}@t.dev", "phone": "+15550123"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _deactivate(db, emp_id):
    """Leave the person on their jobs (deactivation doesn't reassign) but switch them off."""
    from backend.models import Employee
    e = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    e.active = False
    await db.commit()


async def _audits(db, oid, action="order.create"):
    return (await db.execute(select(AuditLog).where(
        AuditLog.entity_id == oid, AuditLog.action == action))).scalars().all()


async def _row(db, oid):
    return (await db.execute(select(Order).where(Order.id == oid)
                             .execution_options(populate_existing=True))).scalar_one()


async def _status_keys(db, org_id, record_type="work_order"):
    from backend.models import OrderStatus
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, org_id)
    await db.commit()
    return set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == record_type,
        OrderStatus.active == True))).scalars().all())  # noqa: E712


async def _open_status(db, org_id, current):
    """Another open (non-terminal) work-order status to move a job to."""
    from backend.models import OrderStatus
    await _status_keys(db, org_id)
    return (await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "work_order", OrderStatus.active == True,  # noqa: E712
        OrderStatus.is_terminal == False, OrderStatus.key != current)  # noqa: E712
        .order_by(OrderStatus.sort_order))).scalars().first()


def _capture_created(monkeypatch):
    from backend.services import notification_rules as nr
    seen = []
    real = nr.evaluate

    async def spy(db_, *, org_id, order_id, event, **kw):
        seen.append((order_id, event))
        return await real(db_, org_id=org_id, order_id=order_id, event=event, **kw)
    monkeypatch.setattr(nr, "evaluate", spy)
    return seen


async def _one_open_ticket(db, org_id):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.asset_one_open_ticket = True
    await db.commit()


def _job_limit_reached(monkeypatch):
    from fastapi import HTTPException

    from backend.services import billing

    async def full(db, org_id):
        raise HTTPException(402, "Monthly job limit reached")
    monkeypatch.setattr(billing, "assert_can_add_job", full)


def _real_push(monkeypatch, sent: list):
    from backend.services import push
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: sent.append(kw)
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)
    monkeypatch.setattr("backend.services.push._endpoint_is_public", lambda url: True)   # no DNS in tests


async def _subscribed(client, tenant, db):
    from backend.models import PushSubscription
    me = await _me(client, tenant)
    db.add(PushSubscription(org_id=me["org_id"], employee_id=me["id"], endpoint="https://fcm.googleapis.com/fcm/send/p",
                            p256dh="k", auth="a"))
    await db.commit()
    return me["org_id"], me["id"]


async def _multi_asset_shop(client):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
                                                      "password": "pw-test-12345"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.put("/api/staff/company", json={"industry_template": "tools"}, headers=h)).status_code == 200
    assert (await client.put("/api/staff/company", json={"multi_asset_jobs": True}, headers=h)).status_code == 200
    return h


async def _job_with_device(client, h, **kw):
    cid = await _client_id(client, h)
    dev = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "DeWalt",
                                                  "model": "DCD791", "serial": f"D-{uuid.uuid4().hex[:6]}"},
                             headers=h)).json()["id"]
    return await _order(client, h, client_id=cid, device_id=dev, **kw), cid, dev


# ── N2: continue / split / chat / e-commerce run the create pipeline ─────────────────────────────
async def test_n2_continue_runs_the_create_pipeline(client, tenant, db, monkeypatch):
    me = await _me(client, tenant)
    keys = await _status_keys(db, me["org_id"])
    o, _cid, _dev = await _job_with_device(client, tenant)
    seen = _capture_created(monkeypatch)
    r = await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 201, r.text
    new = await _row(db, r.json()["id"])
    assert new.status in keys
    assert new.barcode and new.public_token
    assert await _audits(db, new.id)
    assert (new.id, "created") in seen


async def test_n2_continue_honours_one_open_ticket_per_asset(client, tenant, db):
    me = await _me(client, tenant)
    o, _cid, _dev = await _job_with_device(client, tenant)
    await _one_open_ticket(db, me["org_id"])
    r = await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 409, r.text
    assert "open ticket" in r.json()["detail"]


async def test_n2_continue_respects_the_job_limit(client, tenant, monkeypatch):
    o = await _order(client, tenant)
    _job_limit_reached(monkeypatch)
    r = await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 402, r.text


async def test_n2_continue_does_not_copy_a_deactivated_assignee(client, tenant, db):
    ex = await _employee(client, tenant, "Leaver")
    o = await _order(client, tenant, assigned_to_id=ex, manager_id=ex)
    await _deactivate(db, ex)
    r = await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 201, r.text
    new = await _row(db, r.json()["id"])
    assert new.assigned_to_id != ex and new.manager_id != ex


async def test_n2_split_runs_the_create_pipeline_with_a_valid_status(client, db, monkeypatch):
    h = await _multi_asset_shop(client)
    me = await _me(client, h)
    keys = await _status_keys(db, me["org_id"])
    ex = await _employee(client, h, "Leaver")
    o, _cid, _dev = await _job_with_device(client, h, assigned_to_id=ex)
    saw = (await client.post(f"/api/orders/{o['id']}/items", json={"label": "Saw"}, headers=h)).json()["id"]
    await _deactivate(db, ex)
    seen = _capture_created(monkeypatch)
    r = await client.post(f"/api/orders/{o['id']}/items/{saw}/split", headers=h)
    assert r.status_code == 201, r.text
    new = await _row(db, r.json()["id"])
    assert new.status in keys
    assert new.barcode
    assert new.assigned_to_id != ex
    assert await _audits(db, new.id)
    assert (new.id, "created") in seen


async def test_n2_split_respects_the_job_limit(client, monkeypatch):
    h = await _multi_asset_shop(client)
    o, _cid, _dev = await _job_with_device(client, h)
    saw = (await client.post(f"/api/orders/{o['id']}/items", json={"label": "Saw"}, headers=h)).json()["id"]
    _job_limit_reached(monkeypatch)
    r = await client.post(f"/api/orders/{o['id']}/items/{saw}/split", headers=h)
    assert r.status_code == 402, r.text


async def _thread(db, org_id, phone="+15550100"):
    from backend.models import ChatMessage, ChatThread
    t = ChatThread(org_id=org_id, channel="sms", contact_phone=phone, contact_name="Chatty")
    db.add(t)
    await db.flush()
    db.add(ChatMessage(thread_id=t.id, org_id=org_id, direction="in", body="My screen is cracked"))
    await db.commit()
    return t.id


async def test_n2_chat_to_job_runs_the_create_pipeline(client, tenant, db, monkeypatch):
    me = await _me(client, tenant)
    est_keys = await _status_keys(db, me["org_id"], "estimate")
    seen = _capture_created(monkeypatch)
    r = await client.post(f"/api/chats/threads/{await _thread(db, me['org_id'])}/to-order", headers=tenant)
    assert r.status_code == 201, r.text
    new = await _row(db, r.json()["order_id"])
    assert new.barcode and new.public_token
    assert await _audits(db, new.id)
    assert (new.id, "created") in seen
    # an estimate gets an estimate status (not the work-order key "new")
    r = await client.post(f"/api/chats/threads/{await _thread(db, me['org_id'], '+15550101')}/to-order",
                          params={"is_estimate": True}, headers=tenant)
    assert r.status_code == 201, r.text
    est = await _row(db, r.json()["order_id"])
    assert est.is_estimate and est.status in est_keys
    assert est.order_number.startswith("E")


async def test_n2_ecommerce_import_uses_the_real_numbers_and_the_pipeline(client, tenant, db, monkeypatch):
    # An estimate first: count(*)+1 then hands the import a number the work-order sequence reuses.
    await _order(client, tenant, is_estimate=True)
    integ = await client.post("/api/ecommerce/integrations", json={
        "platform": "shopify", "store_url": "https://x.myshopify.com"}, headers=tenant)
    seen = _capture_created(monkeypatch)
    r = await client.post(f"/api/ecommerce/integrations/{integ.json()['id']}/sync-order", json={
        "external_order_id": "SHOP-77", "client_name": "Web Buyer", "client_phone": "+15559001"}, headers=tenant)
    assert r.status_code == 200, r.text
    imported = await _row(db, r.json()["order_id"])
    assert imported.barcode and await _audits(db, imported.id)
    assert (imported.id, "created") in seen
    numbers = {imported.order_number}
    for _ in range(3):
        o = await _order(client, tenant)
        numbers.add(o["order_number"])
    assert len(numbers) == 4, numbers


# ── N3: deactivated staff get no workflow email, SMS or bell ─────────────────────────────────────
async def test_n3_staff_rules_skip_a_deactivated_assignee(client, tenant, db):
    from backend.models import NotificationRule, StaffNotification
    me = await _me(client, tenant)
    ex = await _employee(client, tenant, "Leaver")
    o = await _order(client, tenant, assigned_to_id=ex)
    for ch in ("email", "sms", "in_app"):
        db.add(NotificationRule(org_id=me["org_id"], module="order", scope="staff", event="status_changed",
                                channel=ch, active=True))
    await db.commit()
    await _deactivate(db, ex)
    bells_before = len((await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == ex))).scalars().all())    # the "new job" alert from before
    to = await _open_status(db, me["org_id"], (await _row(db, o["id"])).status)
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": to}, headers=tenant)
    assert r.status_code == 200, r.text
    logs = (await db.execute(select(NotificationLog).where(NotificationLog.employee_id == ex))).scalars().all()
    bells = (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == ex))).scalars().all()
    assert logs == [] and len(bells) == bells_before


async def test_n3_push_notify_skips_a_deactivated_employee(client, tenant, db):
    from backend.models import StaffNotification
    from backend.services import push
    me = await _me(client, tenant)
    ex = await _employee(client, tenant, "Leaver")
    await _deactivate(db, ex)
    assert await push.notify(db, org_id=me["org_id"], employee_id=ex, kind="job.assigned", title="x") is None
    assert not (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == ex))).first()


# ── N4: pushes and savepoints ────────────────────────────────────────────────────────────────────
async def test_n4_a_savepoint_rollback_keeps_pushes_queued_before_it(client, tenant, db, monkeypatch):
    from backend.services import audit, push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Low", commit=False)
    await audit.record(db, org_id=org_id, actor_id=None, action=None, entity_type="client")  # savepoint rolls back
    await db.commit()
    await push.wait_pending()
    assert len(sent) == 1


async def test_n4_a_push_queued_inside_a_rolled_back_savepoint_is_dropped(client, tenant, db, monkeypatch):
    from backend.services import push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Kept", commit=False)
    try:
        async with db.begin_nested():
            await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Gone",
                              commit=False)
            raise RuntimeError("undo this part")
    except RuntimeError:
        pass
    await db.commit()
    await push.wait_pending()
    assert [json.loads(s["data"])["title"] for s in sent] == ["Kept"]


async def test_n4_a_savepoint_release_sends_nothing_before_the_real_commit(client, tenant, db, monkeypatch):
    from backend.services import audit, push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Low", commit=False)
    await audit.record(db, org_id=org_id, actor_id=None, action="client.update", entity_type="client")  # released
    await push.wait_pending()
    assert sent == []                       # the caller hasn't committed yet
    await db.rollback()                     # and then it fails
    await push.wait_pending()
    assert sent == []


async def test_n4_commit_false_payload_carries_the_bell_id(client, tenant, db, monkeypatch):
    from backend.services import push
    org_id, emp_id = await _subscribed(client, tenant, db)
    sent: list = []
    _real_push(monkeypatch, sent)
    n = await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder", title="Low2", commit=False)
    await db.commit()
    await push.wait_pending()
    assert json.loads(sent[0]["data"])["id"] == n.id and n.id


async def test_n4_shutdown_waits_for_pushes_in_flight(monkeypatch):
    import main
    from backend.services import push
    called = []

    async def spy():
        called.append(True)

    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(push, "wait_pending", spy)
    monkeypatch.setattr(main, "init_db", nothing)
    monkeypatch.setattr(main.job_runner, "start", nothing)
    monkeypatch.setattr(main.job_runner, "stop", nothing)
    monkeypatch.setattr(main.settings, "SEED_DEMO", False)
    async with main.lifespan(main.app):
        assert called == []
    assert called == [True]


async def test_n4_webhook_outbox_survives_an_unrelated_savepoint_rollback(client, tenant, db, monkeypatch):
    """The same savepoint rules for webhook deliveries: an audit savepoint that fails must not drop
    the deliveries queued before it, and a released savepoint must not send them before the commit."""
    from backend.core.jobs import job_runner
    from backend.models import WebhookEndpoint
    from backend.services import audit, webhooks
    org_id = (await _me(client, tenant))["org_id"]
    db.add(WebhookEndpoint(org_id=org_id, url="https://hooks.invalid/x", events="*"))
    await db.commit()
    enqueued = []
    monkeypatch.setattr(job_runner, "enqueue", lambda fn, ids, **kw: enqueued.append(list(ids)))
    await webhooks.emit_event(db, org_id, "order.status_changed", {"id": "x", "from": "a", "to": "b"})
    await audit.record(db, org_id=org_id, actor_id=None, action="client.update", entity_type="client")  # released
    assert enqueued == []                   # nothing goes out before the caller's commit
    await audit.record(db, org_id=org_id, actor_id=None, action=None, entity_type="client")  # rolled back
    await db.commit()
    assert len(enqueued) == 1 and len(enqueued[0]) == 1


# ── N5: same-status change with a comment or note ────────────────────────────────────────────────
async def test_n5_same_status_with_a_comment_and_note_saves_them_without_side_effects(client, tenant, db):
    from backend.models import EventFeedItem as FeedItem
    from backend.models import OrderComment, OrderStatusLog
    o = await _order(client, tenant)
    st = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["status"]
    logs_before = (await db.execute(select(func.count()).select_from(OrderStatusLog).where(
        OrderStatusLog.order_id == o["id"]))).scalar()
    msgs_before = (await db.execute(select(func.count()).select_from(NotificationLog).where(
        NotificationLog.order_id == o["id"]))).scalar()
    r = await client.patch(f"/api/orders/{o['id']}/status",
                           json={"status": st, "comment": "Customer called, waiting for part",
                                 "note": "same status note"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["unchanged"] is True
    bodies = (await db.execute(select(OrderComment.body).where(OrderComment.order_id == o["id"]))).scalars().all()
    assert bodies == ["Customer called, waiting for part"]
    feed = (await db.execute(select(FeedItem.body).where(FeedItem.entity_id == o["id"]))).scalars().all()
    assert any("same status note" in (b or "") for b in feed), feed
    # no status side effects: no new status log row, no messages
    assert (await db.execute(select(func.count()).select_from(OrderStatusLog).where(
        OrderStatusLog.order_id == o["id"]))).scalar() == logs_before
    assert (await db.execute(select(func.count()).select_from(NotificationLog).where(
        NotificationLog.order_id == o["id"]))).scalar() == msgs_before


# ── N1: the status endpoint honours the replay's Idempotency-Key ────────────────────────────────
async def test_n1_status_change_with_a_comment_replays_on_the_same_key(client, tenant, db):
    from backend.models import OrderComment
    o = await _order(client, tenant)
    st = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["status"]
    H = {**tenant, "Idempotency-Key": str(uuid.uuid4())}
    answers = []
    for _ in range(2):
        r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": st, "comment": "hello"}, headers=H)
        assert r.status_code == 200, r.text
        answers.append(r.json())
    assert answers[0] == answers[1]
    assert len((await db.execute(select(OrderComment).where(OrderComment.order_id == o["id"]))).scalars().all()) == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
async def test_n1_pwa_mutate_sends_one_key_and_replays_it():
    """Run pwa.js under node with a fake IndexedDB: the first send fails (no signal), the replay
    must carry the same Idempotency-Key the first attempt sent."""
    script = r"""
const fs = require('fs');
const stores = {};
function req(fn) { const r = {}; setTimeout(() => { try { r.result = fn(); r.onsuccess && r.onsuccess(); } catch (e) { r.error = e; r.onerror && r.onerror(); } }, 0); return r; }
function store(name) {
  const s = stores[name] || (stores[name] = { rows: new Map(), seq: 0, keyPath: name === 'queue' ? 'id' : 'k' });
  return {
    add(v) { return req(() => { const id = ++s.seq; s.rows.set(id, Object.assign({}, v, { id })); return id; }); },
    put(v) { return req(() => { s.rows.set(v[s.keyPath], v); return v[s.keyPath]; }); },
    get(k) { return req(() => s.rows.get(k)); },
    getAll() { return req(() => Array.from(s.rows.values())); },
    delete(k) { return req(() => { s.rows.delete(k); }); },
  };
}
const dbObj = { objectStoreNames: { contains: (n) => !!stores[n] }, createObjectStore: (n) => store(n),
                transaction: (n) => ({ objectStore: () => store(n) }) };
globalThis.indexedDB = { open() { const r = {}; setTimeout(() => { r.result = dbObj; r.onupgradeneeded && r.onupgradeneeded(); r.onsuccess(); }, 0); return r; } };
globalThis.window = globalThis; window.addEventListener = () => {}; window.dispatchEvent = () => {};
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
const ls = { ros_tok: 'tok' }; globalThis.localStorage = { getItem: (k) => ls[k] || null, setItem: (k, v) => { ls[k] = v; } };
Object.defineProperty(globalThis, 'navigator', { value: { onLine: true }, configurable: true });
globalThis.setTimeout = ((orig) => (fn, ms) => orig(fn, ms > 100 ? 1e9 : ms))(setTimeout);
const calls = []; let failNext = true;
globalThis.fetch = async (url, init) => {
  calls.push({ url, headers: Object.assign({}, init.headers) });
  if (failNext) { failNext = false; throw new TypeError('network'); }
  return { ok: true, status: 200, json: async () => ({ ok: true }), clone() { return this; } };
};
eval(fs.readFileSync(process.argv[1], 'utf8'));
(async () => {
  const r = await RosPWA.mutate('POST', '/api/orders/1/comments', { body: 'hi', visible_to_client: true });
  if (!r.queued) throw new Error('expected the request to be queued');
  await RosPWA.flush();
  const k1 = calls[0].headers['Idempotency-Key'], k2 = calls[1] && calls[1].headers['Idempotency-Key'];
  console.log(JSON.stringify({ k1, k2, n: calls.length }));
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(2); });
"""
    p = subprocess.run(["node", "-e", script, str(ROOT / "frontend/static/js/pwa.js")],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["n"] == 2
    assert out["k1"] and out["k1"] == out["k2"], out


# ── N6 / WO-03: replies carry the job's new version ─────────────────────────────────────────────
async def test_n6_line_and_status_replies_keep_the_drawer_version_fresh(client, tenant, db):
    o = await _order(client, tenant)
    to = await _open_status(db, (await _me(client, tenant))["org_id"], (await _row(db, o["id"])).status)
    v = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["updated_at"]
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={"tech_notes": "a"}, headers={**tenant, "If-Match": v})
    assert r.status_code == 200, r.text
    v = r.json()["updated_at"]
    r = await client.post(f"/api/orders/{o['id']}/lines", json={"description": "Labour", "qty": 1, "unit_price": 10,
                                                                "line_type": "labour", "kind": "service"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json().get("updated_at"), r.json()
    v = r.json()["updated_at"]
    r = await client.patch(f"/api/orders/{o['id']}/lines/{r.json()['line_id']}", json={"qty": 2}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json().get("updated_at"), r.json()
    v = r.json()["updated_at"]
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": to}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json().get("updated_at"), r.json()
    v = r.json()["updated_at"]
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={"tech_notes": "b"}, headers={**tenant, "If-Match": v})
    assert r.status_code == 200, r.text
    # and someone else's save in between is still caught
    other = await client.patch(f"/api/orders/{o['id']}/notes", json={"conclusion": "theirs"}, headers=tenant)
    assert other.status_code == 200, other.text
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={"tech_notes": "c"},
                           headers={**tenant, "If-Match": r.json()["updated_at"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_order", r.text


# ── N7: status webhooks and the feedback request commit with the change ─────────────────────────
async def test_n7_status_webhooks_and_feedback_commit_with_the_status_change(client, tenant, db, monkeypatch):
    from backend.models import Feedback, FeedbackSettings, OrderStatus, WebhookDelivery, WebhookEndpoint
    from backend.services import push
    me = await _me(client, tenant)
    await _status_keys(db, me["org_id"])
    o = await _order(client, tenant)
    closing = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == me["org_id"], OrderStatus.record_type == "work_order",
        OrderStatus.is_terminal == True, OrderStatus.status_group == "closed"))).scalars().first()  # noqa: E712
    db.add(WebhookEndpoint(org_id=me["org_id"], url="https://hooks.invalid/x", events="*"))
    db.add(FeedbackSettings(org_id=me["org_id"], active=True, trigger_status_id=closing.id))
    await db.commit()

    async def die(*a, **kw):
        raise _Crash()
    monkeypatch.setattr(push, "dispatch_deferred", die)    # right after the status change commits
    with _crashes():
        await client.patch(f"/api/orders/{o['id']}/status", json={"status": closing.key, "allow_unpaid": True},
                           headers=tenant)
    assert (await _row(db, o["id"])).status == closing.key
    events = set((await db.execute(select(WebhookDelivery.event).where(
        WebhookDelivery.org_id == me["org_id"]))).scalars().all())
    assert {"order.status_changed", "order.closed"} <= events, events
    assert (await db.execute(select(Feedback.id).where(Feedback.order_id == o["id"]))).first()


# ── WO-08: payment_received messages commit with the payment ────────────────────────────────────
async def test_wo08_payment_received_message_commits_with_the_payment(client, tenant, db, monkeypatch):
    from backend.models import Client, NotificationRule, Payment
    from backend.services import notification_rules as nr
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
    aid = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    real = nr.evaluate

    async def evaluate(db_, **kw):
        res = await real(db_, **kw)
        if kw.get("event") == "payment_received":
            raise _Crash()
        return res
    monkeypatch.setattr(nr, "evaluate", evaluate)
    from backend.services import push

    async def die(*a, **kw):
        raise _Crash()
    monkeypatch.setattr(push, "dispatch_deferred", die)
    with _crashes():
        await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": aid, "amount": 50},
                          headers=tenant)
    pays = (await db.execute(select(Payment.id).where(Payment.order_id == o["id"]))).scalars().all()
    queued = (await db.execute(select(NotificationLog.id).where(
        NotificationLog.order_id == o["id"], NotificationLog.trigger_event == "payment_received"))).scalars().all()
    assert (len(pays), len(queued)) in ((0, 0), (1, 1)), (pays, queued)


async def test_wo08_payment_received_still_queues_without_a_crash(client, tenant, db):
    from backend.models import Client, NotificationRule
    me = await _me(client, tenant)
    cid = await _client_id(client, tenant, email="p@e.com")
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.consent_email = True
    db.add(NotificationRule(org_id=me["org_id"], module="order", scope="client", event="payment_received",
                            channel="email", active=True))
    await db.commit()
    o = await _order(client, tenant, client_id=cid)
    await client.post(f"/api/orders/{o['id']}/lines", json={"line_type": "labour", "description": "Fix",
                                                             "qty": 1, "unit_price": 50}, headers=tenant)
    aid = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": aid, "amount": 20},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    r = await client.post(f"/api/cash/orders/{o['id']}/prepay", json={"account_id": aid, "amount": 5},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    queued = (await db.execute(select(NotificationLog.id).where(
        NotificationLog.order_id == o["id"], NotificationLog.trigger_event == "payment_received"))).scalars().all()
    assert len(queued) == 2


# ── N8: converting a booking twice at once makes one job ─────────────────────────────────────────
async def test_n8_double_tapped_booking_conversion_makes_one_job(client, tenant, db):
    cid = await _client_id(client, tenant)
    when = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = await client.post("/api/appointments", json={"client_id": cid, "scheduled_for": when, "note": "dropoff"},
                          headers=tenant)
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    rs = await asyncio.gather(*[client.post(f"/api/appointments/{bid}/to-order", headers=tenant) for _ in range(2)])
    assert sorted(x.status_code for x in rs) == [201, 409], [(x.status_code, x.text) for x in rs]
    jobs = (await db.execute(select(Order.id).where(Order.client_id == cid))).scalars().all()
    assert len(jobs) == 1


async def test_n8_booking_conversion_replays_on_the_same_key(client, tenant, db):
    cid = await _client_id(client, tenant)
    when = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    bid = (await client.post("/api/appointments", json={"client_id": cid, "scheduled_for": when},
                             headers=tenant)).json()["id"]
    H = {**tenant, "Idempotency-Key": str(uuid.uuid4())}
    a = await client.post(f"/api/appointments/{bid}/to-order", headers=H)
    b = await client.post(f"/api/appointments/{bid}/to-order", headers=H)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert a.json()["order_id"] == b.json()["order_id"]


# ── N9: the moved-reminders sweep doesn't query per row ─────────────────────────────────────────
async def test_n9_cancel_moved_reminders_runs_a_fixed_number_of_queries(client, tenant, db):
    from backend.models import NotificationRule
    from backend.services import notification_rules as nr
    from backend.services.dates import now_utc
    me = await _me(client, tenant)
    rule = NotificationRule(org_id=me["org_id"], module="order", scope="client", event="reminder",
                            reminder_offset_min=60, channel="email", active=True)
    db.add(rule)
    await db.commit()
    moved = []
    for i in range(6):
        o = await _order(client, tenant)
        row = await _row(db, o["id"])
        row.scheduled_for = (now_utc() + timedelta(hours=3 + i)).replace(microsecond=0)
        db.add(NotificationLog(org_id=me["org_id"], scope="client", channel="email", trigger_event="reminder",
                               rule_id=rule.id, order_id=row.id, recipient="r@e.com", subject="s", body="b",
                               status="scheduled", send_at=now_utc() - timedelta(days=1)))   # not the job's time
        await db.commit()
        moved.append(row.id)
    await db.commit()

    statements = []
    engine = db.get_bind().sync_engine if hasattr(db.get_bind(), "sync_engine") else db.get_bind()

    def count(*a, **kw):
        statements.append(a[2])
    event.listen(engine, "before_cursor_execute", count)
    try:
        n = await nr._cancel_moved_reminders(db, me["org_id"], {})
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert n == 6
    assert len(statements) <= 3, statements
    await db.commit()
    left = (await db.execute(select(NotificationLog.status).where(NotificationLog.order_id.in_(moved))
                             .execution_options(populate_existing=True))).scalars().all()
    assert set(left) == {"skipped"}
