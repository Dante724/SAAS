"""Audit 12 Nov 2026, P1 work-order findings (docs/audits/2026-11-12-backend-audit):

* WO-03  header edits had no optimistic concurrency: the last writer silently won.
* WO-04  /schedule reassigned jobs without can_assign_staff; /notes edited closed jobs, unaudited.
* WO-05  seven other ways of making a job skipped the create pipeline (audit row, notifications,
         commissions, automations, checklists, the one-open-ticket-per-asset rule).
* WO-06  orders.status was never checked against the shop's statuses on create, type switch or
         duplicate (Orderry Q25).
* WO-07  status-change, comment, finish, notify and assign side effects sat in `except: pass`.
* Q20    the closed / locks_edits guard missed discount lines, /schedule, /notes, reserve-stock.
* SEC-03 "selected locations" with none picked saw every location; transfers weren't scope-checked.
* CLI-02 a deactivated employee was still auto-assigned jobs and still got job pushes.
* CLI-03 comment, notify and duplicate had no Idempotency-Key handling.
"""
import ast
import logging
import pathlib
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import AuditLog, Order


# ── helpers ──────────────────────────────────────────────────────────────────
async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _client_id(client, h, name="C"):
    r = await client.post("/api/clients", json={"name": name}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order(client, h, **kw):
    body = {"complaint": "screen", **kw}
    if "client_id" not in body:
        body["client_id"] = await _client_id(client, h)
    r = await client.post("/api/orders", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _seed(db, org_id):
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, org_id)
    await db.commit()


async def _set_status(db, oid, key, closed=False):
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.status = key
    if closed:
        o.closed_at = datetime.utcnow()
    await db.commit()


async def _locking_status(db, org_id):
    from backend.models import OrderStatus
    db.add(OrderStatus(org_id=org_id, key="qc_locked", name="QC locked", status_group="in_progress",
                       record_type="work_order", is_estimate=False, locks_edits=True, active=True,
                       sort_order=50))
    await db.commit()
    return "qc_locked"


async def _role_user(client, h, **role):
    r = await client.post("/api/settings/rbac/roles", json={"name": f"R-{uuid.uuid4().hex[:4]}", **role},
                          headers=h)
    assert r.status_code == 201, r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=h)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, r.json()["id"]


async def _employee(client, h, name="Tech"):
    r = await client.post("/api/staff/employees", headers=h,
                          json={"name": name, "email": f"e-{uuid.uuid4().hex[:8]}@t.dev"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _audits(db, oid, action):
    return (await db.execute(select(AuditLog).where(
        AuditLog.entity_id == oid, AuditLog.action == action))).scalars().all()


def _capture_created(monkeypatch):
    """Record every 'created' notification-rules evaluation (the create pipeline's signature)."""
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


# ── WO-03: optimistic concurrency on header edits ────────────────────────────
@pytest.mark.asyncio
async def test_header_edit_with_a_stale_version_is_refused(client, tenant):
    o = await _order(client, tenant)
    seen = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    v1 = seen["updated_at"]
    assert v1
    # The counter saves the notes with the version they read…
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={"tech_notes": "cracked"},
                           headers={**tenant, "If-Match": v1})
    assert r.status_code == 200, r.text
    v2 = r.json()["updated_at"]
    assert v2 != v1
    # …and the technician, still holding the old version, is told instead of overwriting.
    for path, body in (("priority", {"priority": 2}), ("notes", {"conclusion": "x"}),
                       ("custom-data", {"custom_data": {}}), ("schedule", {"duration_min": 30}),
                       ("assign", {"manager_id": ""})):
        r = await client.patch(f"/api/orders/{o['id']}/{path}", json=body, headers={**tenant, "If-Match": v1})
        assert r.status_code == 409, (path, r.text)
        assert r.json()["detail"]["code"] == "stale_order"
    # The fresh version saves; no header keeps the old behaviour for clients that don't send it.
    r = await client.patch(f"/api/orders/{o['id']}/priority", json={"priority": 2}, headers={**tenant, "If-Match": v2})
    assert r.status_code == 200, r.text
    assert (await client.patch(f"/api/orders/{o['id']}/priority", json={"priority": 1}, headers=tenant)).status_code == 200


# ── WO-04 + Q20: /schedule and /notes ────────────────────────────────────────
@pytest.mark.asyncio
async def test_schedule_needs_can_assign_staff_to_change_the_assignee(client, tenant, db):
    T, _ = await _role_user(client, tenant, permissions={"can_assign_staff": False})
    other = await _employee(client, tenant)
    o = await _order(client, tenant)
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={"assigned_to_id": other}, headers=T)
    assert r.status_code == 403, r.text
    # moving the time alone is still fine
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={"duration_min": 90}, headers=T)
    assert r.status_code == 200, r.text
    # with the right, the reassignment is saved and audited
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={"assigned_to_id": other}, headers=tenant)
    assert r.status_code == 200, r.text
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    await db.refresh(row)
    assert row.assigned_to_id == other
    assert await _audits(db, o["id"], "order.assign")


@pytest.mark.asyncio
async def test_notes_and_schedule_respect_the_closed_job_guard_and_notes_are_audited(client, tenant, db):
    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    T, _ = await _role_user(client, tenant, permissions={"can_edit_closed_ticket": False})
    o = await _order(client, tenant)
    r = await client.patch(f"/api/orders/{o['id']}/notes", json={"conclusion": "replaced"}, headers=tenant)
    assert r.status_code == 200
    audits = await _audits(db, o["id"], "order.notes")
    assert audits and "conclusion" in (audits[0].diff or {})
    await _set_status(db, o["id"], "done", closed=True)
    assert (await client.patch(f"/api/orders/{o['id']}/notes", json={"conclusion": "y"}, headers=T)).status_code == 403
    assert (await client.patch(f"/api/orders/{o['id']}/schedule", json={"duration_min": 15}, headers=T)).status_code == 403
    # the owner holds "Edit the closed ticket"
    assert (await client.patch(f"/api/orders/{o['id']}/notes", json={"conclusion": "y"}, headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_locked_status_blocks_discounts_schedule_notes_and_reserve_stock(client, tenant, db):
    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    key = await _locking_status(db, me["org_id"])
    o = await _order(client, tenant)
    await _set_status(db, o["id"], key)
    oid = o["id"]
    calls = [
        ("patch", f"/api/orders/{oid}/schedule", {"duration_min": 15}),
        ("patch", f"/api/orders/{oid}/notes", {"tech_notes": "x"}),
        ("post", f"/api/orders/{oid}/reserve-stock", {"hold_days": 3}),
        ("post", f"/api/orders/{oid}/apply-coupon", {"code": "NOPE"}),
        ("post", f"/api/orders/{oid}/redeem-giftcard", {"code": "NOPE", "amount": 1}),
        ("post", f"/api/orders/{oid}/redeem-loyalty", {"points": 10}),
    ]
    for method, url, body in calls:
        r = await getattr(client, method)(url, json=body, headers=tenant)
        assert r.status_code == 409, (url, r.status_code, r.text)


# ── WO-05 / WO-06 / Q27: every way of creating a job runs the create pipeline ─
@pytest.mark.asyncio
async def test_inquiry_conversion_runs_the_create_pipeline(client, tenant, db, monkeypatch):
    seen = _capture_created(monkeypatch)
    r = await client.post("/api/leads", json={"name": "Ann", "phone": "+15550001111", "inquiry": "battery"},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    r = await client.post(f"/api/leads/{r.json()['id']}/convert", headers=tenant)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert await _audits(db, oid, "order.create")
    assert (oid, "created") in seen


@pytest.mark.asyncio
async def test_inquiry_conversion_honours_one_open_ticket_per_asset(client, tenant, db):
    me = await _me(client, tenant)
    await _one_open_ticket(db, me["org_id"])
    first = await client.post("/api/leads", json={"name": "Bo", "phone": "+15550002222", "serial": "SN-77"},
                              headers=tenant)
    r = await client.post(f"/api/leads/{first.json()['id']}/convert", headers=tenant)
    assert r.status_code == 200, r.text
    cid = r.json()["client_id"]
    again = await client.post("/api/leads", json={"name": "Bo", "phone": "+15550002222", "serial": "SN-77",
                                                  "client_id": cid}, headers=tenant)
    r = await client.post(f"/api/leads/{again.json()['id']}/convert", headers=tenant)
    assert r.status_code == 409, r.text
    assert "open ticket" in r.json()["detail"]


@pytest.mark.asyncio
async def test_booking_conversion_runs_the_create_pipeline(client, tenant, db, monkeypatch):
    seen = _capture_created(monkeypatch)
    cid = await _client_id(client, tenant)
    when = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = await client.post("/api/appointments", json={"client_id": cid, "scheduled_for": when, "note": "dropoff"},
                          headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/appointments/{r.json()['id']}/to-order", headers=tenant)
    assert r.status_code == 201, r.text
    oid = r.json()["order_id"]
    assert await _audits(db, oid, "order.create")
    assert (oid, "created") in seen


@pytest.mark.asyncio
async def test_api_create_runs_the_create_pipeline(client, tenant, db, monkeypatch):
    seen = _capture_created(monkeypatch)
    me = await _me(client, tenant)
    k = await client.post("/api/api-keys", json={"name": "zap", "scopes": ["read", "write"]}, headers=tenant)
    assert k.status_code == 201, k.text
    A = {"Authorization": f"Bearer {k.json()['key']}"}
    cid = await _client_id(client, tenant)
    r = await client.post("/api/v1/orders", json={"client_id": cid, "location_id": me["location_id"],
                                                  "complaint": "api job"}, headers=A)
    assert r.status_code == 201, r.text
    oid = r.json()["data"]["id"]
    assert (oid, "created") in seen
    assert await _audits(db, oid, "order.create")


@pytest.mark.asyncio
async def test_duplicate_runs_the_pipeline_with_a_valid_status_and_the_asset_rule(client, tenant, db, monkeypatch):
    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    from backend.models import OrderStatus
    keys = set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == me["org_id"], OrderStatus.active == True))).scalars().all())  # noqa: E712
    est = await _order(client, tenant, is_estimate=True)
    seen = _capture_created(monkeypatch)
    r = await client.post(f"/api/orders/{est['id']}/duplicate", headers=tenant)
    assert r.status_code == 201, r.text
    dup = (await db.execute(select(Order).where(Order.id == r.json()["id"]))).scalar_one()
    assert dup.status in keys and dup.status != "draft"
    o = await _order(client, tenant, asset_serial="DUP-1")
    r = await client.post(f"/api/orders/{o['id']}/duplicate", headers=tenant)
    assert r.status_code == 201, r.text
    assert await _audits(db, r.json()["id"], "order.create")
    assert (r.json()["id"], "created") in seen
    # with one-open-ticket on, copying a job whose asset is still in the shop is refused
    await _one_open_ticket(db, me["org_id"])
    r = await client.post(f"/api/orders/{o['id']}/duplicate", headers=tenant)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_estimate_conversion_honours_one_open_ticket_per_asset(client, tenant, db):
    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    job = await _order(client, tenant, asset_serial="EST-1")
    row = (await db.execute(select(Order).where(Order.id == job["id"]))).scalar_one()
    est = await _order(client, tenant, client_id=row.client_id, device_id=row.device_id, is_estimate=True)
    await _one_open_ticket(db, me["org_id"])
    r = await client.post(f"/api/orders/{est['id']}/convert", headers=tenant)
    assert r.status_code == 409, r.text
    row = (await db.execute(select(Order).where(Order.id == est["id"]))).scalar_one()
    await db.refresh(row)
    assert row.is_estimate   # nothing converted


@pytest.mark.asyncio
async def test_job_type_initial_status_must_be_a_real_status(client, tenant, db):
    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    r = await client.post("/api/forms/types", json={"name": "Bad", "initial_status": "nonsense"}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/forms/types", json={"name": "Good"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    assert (await client.put(f"/api/forms/types/{tid}", json={"name": "Good", "initial_status": "nonsense"},
                             headers=tenant)).status_code == 422
    # a row that slipped in before the check falls back to the shop's default on create
    from backend.models import WorkOrderType
    wot = (await db.execute(select(WorkOrderType).where(WorkOrderType.id == tid))).scalar_one()
    wot.initial_status = "nonsense"
    await db.commit()
    o = await _order(client, tenant, type_id=tid)
    assert o["status"] != "nonsense"
    from backend.models import OrderStatus
    assert (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == me["org_id"], OrderStatus.key == o["status"],
        OrderStatus.record_type == "work_order", OrderStatus.active == True))).scalars().first()  # noqa: E712


# ── WO-07: no swallowed side effects in orders.py ────────────────────────────
def test_orders_router_has_no_bare_except_pass():
    src = pathlib.Path(__file__).resolve().parents[1] / "routers" / "orders.py"
    bad = [h.lineno for h in ast.walk(ast.parse(src.read_text()))
           if isinstance(h, ast.ExceptHandler) and len(h.body) == 1 and isinstance(h.body[0], ast.Pass)]
    assert bad == [], f"except: pass at orders.py lines {bad}"


@pytest.mark.asyncio
async def test_a_failing_status_webhook_is_logged_not_swallowed(client, tenant, db, monkeypatch, caplog):
    from backend.services import webhooks

    async def boom(*a, **kw):
        raise RuntimeError("webhook store down")

    me = await _me(client, tenant)
    await _seed(db, me["org_id"])
    o = await _order(client, tenant)
    monkeypatch.setattr(webhooks, "emit_event", boom)
    with caplog.at_level(logging.ERROR):
        r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert any("webhook" in rec.getMessage() and o["id"] in rec.getMessage() for rec in caplog.records), \
        [rec.getMessage() for rec in caplog.records]


@pytest.mark.asyncio
async def test_a_failing_comment_rule_is_logged_and_not_a_500(client, tenant, monkeypatch, caplog):
    from backend.services import notification_rules

    async def boom(*a, **kw):
        raise RuntimeError("rules down")

    o = await _order(client, tenant)
    monkeypatch.setattr(notification_rules, "evaluate", boom)
    with caplog.at_level(logging.ERROR):
        r = await client.post(f"/api/orders/{o['id']}/comments", json={"body": "ready soon", "visible_to_client": True},
                              headers=tenant)
    assert r.status_code == 201, r.text
    assert any("comment" in rec.getMessage() for rec in caplog.records)


# ── SEC-03: location scope ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_selected_locations_with_none_picked_sees_no_orders(client, tenant, db):
    from backend.models import Role
    o = await _order(client, tenant)
    T, emp_id = await _role_user(client, tenant)
    from backend.models import Employee
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one()
    role.location_scope, role.location_ids = "selected", "[]"
    await db.commit()
    r = await client.get("/api/orders", headers=T)
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"] if isinstance(body, dict) else body
    assert o["id"] not in [i["id"] for i in items]
    assert (await client.get(f"/api/orders/{o['id']}", headers=T)).status_code in (403, 404)


@pytest.mark.asyncio
async def test_transfer_to_a_location_outside_the_role_is_refused(client, tenant, db):
    import json

    from backend.models import Employee, Location, Role
    me = await _me(client, tenant)
    other = Location(org_id=me["org_id"], name="Branch B")
    db.add(other)
    await db.commit()
    T, emp_id = await _role_user(client, tenant, permissions={"can_transfer_tickets": True})
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one()
    role.location_scope, role.location_ids = "selected", json.dumps([me["location_id"]])
    await db.commit()
    o = await _order(client, tenant, location_id=me["location_id"])
    r = await client.post(f"/api/orders/{o['id']}/transfer", json={"location_id": other.id}, headers=T)
    assert r.status_code == 403, r.text
    # the owner can
    r = await client.post(f"/api/orders/{o['id']}/transfer", json={"location_id": other.id}, headers=tenant)
    assert r.status_code == 200, r.text


# ── CLI-02: deactivated employees ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_deactivated_employee_is_not_auto_assigned_or_pushed(client, tenant, db, monkeypatch):
    from backend.models import Client, PushSubscription
    from backend.services import push
    me = await _me(client, tenant)
    ex = await _employee(client, tenant, "Ex Tech")
    db.add(PushSubscription(org_id=me["org_id"], employee_id=ex, endpoint="https://fcm.googleapis.com/fcm/send/ex",
                            p256dh="k", auth="a"))
    await db.commit()
    cid = await _client_id(client, tenant)
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.manager_id = ex
    await db.commit()
    assert (await client.delete(f"/api/staff/employees/{ex}", headers=tenant)).status_code == 200
    assert not (await db.execute(select(PushSubscription).where(PushSubscription.employee_id == ex))).scalars().all()
    sent = []

    async def capture(db_, emp_id, payload):
        sent.append(emp_id)
    monkeypatch.setattr(push, "_dispatch_push", capture)
    o = await _order(client, tenant, client_id=cid)
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    assert row.assigned_to_id != ex and row.manager_id != ex
    # nor can they be picked by hand
    r = await client.patch(f"/api/orders/{o['id']}/assign", json={"assigned_to_id": ex}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/orders", json={"client_id": cid, "assigned_to_id": ex, "complaint": "x"}, headers=tenant)
    assert r.status_code == 422, r.text
    assert ex not in sent


# ── CLI-03: idempotency on double-submit-prone POSTs ─────────────────────────
@pytest.mark.asyncio
async def test_comment_duplicate_and_notify_replay_on_the_same_key(client, tenant, db):
    from backend.models import OrderComment
    o = await _order(client, tenant)
    H = {**tenant, "Idempotency-Key": str(uuid.uuid4())}
    for _ in range(2):
        r = await client.post(f"/api/orders/{o['id']}/comments", json={"body": "hi"}, headers=H)
        assert r.status_code == 201, r.text
    assert len((await db.execute(select(OrderComment).where(OrderComment.order_id == o["id"]))).scalars().all()) == 1

    H = {**tenant, "Idempotency-Key": str(uuid.uuid4())}
    ids = set()
    for _ in range(2):
        r = await client.post(f"/api/orders/{o['id']}/duplicate", headers=H)
        assert r.status_code == 201, r.text
        ids.add(r.json()["id"])
    assert len(ids) == 1

    from backend.models import NotificationLog
    c = await client.post("/api/clients", json={"name": "Mail", "email": "m@example.com"}, headers=tenant)
    o2 = await _order(client, tenant, client_id=c.json()["id"])
    H = {**tenant, "Idempotency-Key": str(uuid.uuid4())}
    answers = []
    for _ in range(2):
        r = await client.post(f"/api/orders/{o2['id']}/notify", json={"channel": "email", "body": "Ready"}, headers=H)
        assert r.status_code == 200, r.text
        answers.append(r.json().get("message_id"))
    assert answers[0] == answers[1]
    msgs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o2["id"], NotificationLog.trigger_event == "manual"))).scalars().all()
    assert len(msgs) == 1
