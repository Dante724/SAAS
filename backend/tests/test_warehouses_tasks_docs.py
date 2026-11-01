"""Builds 84-90 — warehouses, appointments, tasks, supplier returns, write-off documents,
per-warehouse access, and asset-warehouse automation."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import (
    Device,
    Employee,
    Part,
    Role,
    StockLevel,
    Task,
    Warehouse,
)
from backend.services.auth import hash_password
from backend.services.dates import now_utc


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _default_wh(client, H):
    whs = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()
    return next(w for w in whs["items"] if w["is_default"])


async def _part_with_stock(client, tenant, db, qty=10, name="Widget"):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/inventory/parts", json={
        "name": name, "sku": f"{name}-1", "cost_price": 5, "sell_price": 10},
        headers=tenant)).json()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return p


async def _employee(client, db, org_id, email, **role_kw):
    role = Role(org_id=org_id, name="R", permissions={}, **role_kw)
    db.add(role)
    await db.flush()
    e = Employee(org_id=org_id, name="Emp", email=email,
                 password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    tok = (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-123456789"})).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, e


# ══ Build 84: warehouses ═════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_every_location_gets_a_default_warehouse(client, tenant):
    """The migration/bootstrap guarantees the compatibility shim: existing
    location-addressed stock still resolves to a real warehouse."""
    w = await _default_wh(client, tenant)
    assert w["kind"] == "product" and w["scope"] == "location"
    assert w["location_id"] and w["is_default"] is True


@pytest.mark.asyncio
async def test_location_stock_is_addressed_by_warehouse(client, tenant, db):
    p = await _part_with_stock(client, tenant, db, qty=4)
    w = await _default_wh(client, tenant)
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    assert sl.warehouse_id == w["id"]        # posted through the default warehouse
    assert sl.qty_available == 4


@pytest.mark.asyncio
async def test_create_product_and_asset_warehouses(client, tenant):
    loc = await _loc(client, tenant)
    a = await client.post("/api/settings/warehouses", json={
        "name": "Van 1", "kind": "product", "scope": "company"}, headers=tenant)
    assert a.status_code == 201, a.text
    assert a.json()["scope"] == "company" and a.json()["location_id"] is None

    b = await client.post("/api/settings/warehouses", json={
        "name": "Loaners", "kind": "asset", "scope": "location", "location_id": loc},
        headers=tenant)
    assert b.status_code == 201, b.text
    assert b.json()["kind"] == "asset"

    # a location warehouse must name its location
    bad = await client.post("/api/settings/warehouses", json={
        "name": "Nowhere", "kind": "product", "scope": "location"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_warehouse_list_reports_asset_label_from_template(client, tenant):
    r = (await client.get("/api/settings/warehouses", headers=tenant)).json()
    assert r["asset_label"]                    # e.g. "Devices" / "Vehicles"
    assert set(r["actions"]) >= {"post", "writeoff", "transfer"}


@pytest.mark.asyncio
async def test_warehouse_delete_blocked_while_it_holds_stock(client, tenant, db):
    """Deleting a warehouse with stock in it would silently lose the stock."""
    loc = await _loc(client, tenant)
    w = (await client.post("/api/settings/warehouses", json={
        "name": "Annex", "kind": "product", "scope": "location", "location_id": loc},
        headers=tenant)).json()
    p = (await client.post("/api/inventory/parts", json={
        "name": "Bolt", "sku": "B-1"}, headers=tenant)).json()
    db.add(StockLevel(part_id=p["id"], location_id=loc, warehouse_id=w["id"],
                      qty_available=3, qty_reserved=0))
    await db.commit()

    blocked = await client.delete(f"/api/settings/warehouses/{w['id']}", headers=tenant)
    assert blocked.status_code == 409
    assert "transfer" in blocked.json()["detail"].lower()

    # empty it, then it can go — and it stays restorable
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.warehouse_id == w["id"]))).scalar_one()
    sl.qty_available = 0
    await db.commit()
    ok = await client.delete(f"/api/settings/warehouses/{w['id']}", headers=tenant)
    assert ok.status_code == 200 and ok.json()["restorable"] is True
    assert not any(x["id"] == w["id"] for x in
                   (await client.get("/api/settings/warehouses", headers=tenant)).json()["items"])
    back = await client.post(f"/api/settings/warehouses/{w['id']}/restore", headers=tenant)
    assert back.status_code == 200


@pytest.mark.asyncio
async def test_default_warehouse_cannot_be_deleted(client, tenant):
    w = await _default_wh(client, tenant)
    r = await client.delete(f"/api/settings/warehouses/{w['id']}", headers=tenant)
    assert r.status_code == 409


# ══ Build 89: per-warehouse allowed actions + per-employee access ════════════
@pytest.mark.asyncio
async def test_warehouse_forbids_disallowed_action(client, tenant, db):
    """A warehouse that doesn't permit write-offs refuses them from anyone."""
    loc = await _loc(client, tenant)
    w = (await client.post("/api/settings/warehouses", json={
        "name": "Receiving only", "kind": "product", "scope": "location",
        "location_id": loc, "allowed_actions": ["post"]}, headers=tenant)).json()
    p = await _part_with_stock(client, tenant, db, qty=5, name="Gear")
    r = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "reason": "damaged",
        "lines": [{"part_id": p["id"], "qty": 1}]}, headers=tenant)
    assert r.status_code == 403
    assert "doesn't allow" in r.json()["detail"]


@pytest.mark.asyncio
async def test_per_employee_access_overrides_role(client, tenant, db):
    me = await _me(client, tenant)
    w = await _default_wh(client, tenant)
    H, emp = await _employee(client, db, me["org_id"], "wh-limited@x.dev")

    # explicitly grant only 'post' in this warehouse
    r = await client.post(f"/api/settings/warehouses/{w['id']}/access", json={
        "employee_id": emp.id, "actions": ["post"], "explicit": True}, headers=tenant)
    assert r.status_code == 200

    p = await _part_with_stock(client, tenant, db, qty=5, name="Nut")
    loc = await _loc(client, tenant)
    ok = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 1, "movement_type": "receive"}, headers=H)
    assert ok.status_code in (200, 201)        # 'post' is granted

    denied = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 1, "movement_type": "writeoff"}, headers=H)
    assert denied.status_code == 403           # 'writeoff' is not

    acc = (await client.get(f"/api/settings/warehouses/{w['id']}/access", headers=tenant)).json()
    mine = next(x for x in acc["items"] if x["employee_id"] == emp.id)
    assert mine["explicit"] is True and mine["actions"] == ["post"]


# ══ Build 88: write-off documents ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_write_off_document_and_delete_restores_stock(client, tenant, db):
    p = await _part_with_stock(client, tenant, db, qty=10, name="Screen")
    w = await _default_wh(client, tenant)

    doc = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "reason": "damaged",
        "lines": [{"part_id": p["id"], "qty": 3, "comment": "dropped"}]}, headers=tenant)
    assert doc.status_code == 201, doc.text
    d = doc.json()
    assert d["lines"][0]["comment_public"] is False       # internal by default

    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    await db.refresh(sl)
    assert sl.qty_available == 7

    gone = await client.delete(f"/api/stock-docs/write-offs/{d['id']}", headers=tenant)
    assert gone.status_code == 200 and gone.json()["stock_restored"] is True
    await db.refresh(sl)
    assert sl.qty_available == 10                          # stock came back


@pytest.mark.asyncio
async def test_write_off_line_edit_settles_the_difference(client, tenant, db):
    p = await _part_with_stock(client, tenant, db, qty=10, name="Cable")
    w = await _default_wh(client, tenant)
    d = (await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "lines": [{"part_id": p["id"], "qty": 4}]},
        headers=tenant)).json()
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    await db.refresh(sl)
    assert sl.qty_available == 6

    # correcting 4 → 2 hands 2 back
    r = await client.patch(
        f"/api/stock-docs/write-offs/{d['id']}/lines/{d['lines'][0]['id']}",
        json={"qty": 2, "comment": "recount", "comment_public": True}, headers=tenant)
    assert r.status_code == 200, r.text
    await db.refresh(sl)
    assert sl.qty_available == 8
    assert r.json()["lines"][0]["comment_public"] is True


@pytest.mark.asyncio
async def test_cannot_write_off_more_than_held(client, tenant, db):
    p = await _part_with_stock(client, tenant, db, qty=2, name="Fuse")
    w = await _default_wh(client, tenant)
    r = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "lines": [{"part_id": p["id"], "qty": 5}]}, headers=tenant)
    assert r.status_code == 409


# ══ Build 87: supplier returns ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_return_to_supplier_credits_balance_and_removes_stock(client, tenant, db):
    from backend.models import Supplier
    me = await _me(client, tenant)
    p = await _part_with_stock(client, tenant, db, qty=10, name="Board")
    w = await _default_wh(client, tenant)
    sup = Supplier(org_id=me["org_id"], name="Acme Parts", balance=0)
    db.add(sup)
    await db.commit()
    await db.refresh(sup)

    r = await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "supplier_id": sup.id, "settlement": "balance",
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 5}]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(10.0)

    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    await db.refresh(sl)
    assert sl.qty_available == 8            # goods went back to the supplier
    await db.refresh(sup)
    assert sup.balance == pytest.approx(-10.0)   # we're owed for them

    # deleting the return puts the goods back and undoes the credit
    d = await client.delete(f"/api/stock-docs/returns/{r.json()['id']}", headers=tenant)
    assert d.status_code == 200
    await db.refresh(sl)
    await db.refresh(sup)
    assert sl.qty_available == 10 and sup.balance == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_paid_return_cannot_be_deleted(client, tenant, db):
    from backend.models import PurchaseReturn, Supplier
    me = await _me(client, tenant)
    p = await _part_with_stock(client, tenant, db, qty=5, name="Chip")
    w = await _default_wh(client, tenant)
    sup = Supplier(org_id=me["org_id"], name="S", balance=0)
    db.add(sup)
    await db.commit()
    await db.refresh(sup)

    r = (await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "supplier_id": sup.id,
        "lines": [{"part_id": p["id"], "qty": 1}]}, headers=tenant)).json()
    doc = (await db.execute(select(PurchaseReturn).where(
        PurchaseReturn.id == r["id"]))).scalar_one()
    doc.paid = True
    await db.commit()

    blocked = await client.delete(f"/api/stock-docs/returns/{r['id']}", headers=tenant)
    assert blocked.status_code == 409
    assert "payment" in blocked.json()["detail"].lower()


# ══ Build 85: appointments ═══════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_appointment_availability_respects_schedule_and_busy(client, tenant):
    me = await _me(client, tenant)
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-09-01", "kind": "shift",
        "start": "09:00", "end": "17:00"}, headers=tenant)
    b = await client.post("/api/appointments", json={
        "client_name": "Walk-in", "employee_id": me["id"],
        "scheduled_for": "2026-09-01T10:00:00", "duration_min": 60}, headers=tenant)
    assert b.status_code in (200, 201), b.text

    slots = (await client.get(
        f"/api/appointments/availability?date_str=2026-09-01&duration_min=60&employee_id={me['id']}",
        headers=tenant)).json()["slots"]
    windows = {(s["start"], s["end"]) for s in slots}
    assert ("09:00", "10:00") in windows       # before the appointment
    assert ("11:00", "17:00") in windows       # after it


@pytest.mark.asyncio
async def test_appointment_availability_empty_on_day_off(client, tenant):
    me = await _me(client, tenant)
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-09-02", "kind": "day_off"}, headers=tenant)
    r = (await client.get(
        f"/api/appointments/availability?date_str=2026-09-02&employee_id={me['id']}",
        headers=tenant)).json()
    assert r["slots"] == []


@pytest.mark.asyncio
async def test_appointment_to_work_order(client, tenant, db):
    from backend.models import Appointment, Order
    me = await _me(client, tenant)
    b = (await client.post("/api/appointments", json={
        "client_name": "Jo", "client_phone": "+18001", "employee_id": me["id"],
        "scheduled_for": "2026-09-03T11:00:00", "duration_min": 90,
        "note": "Wobbly wheel"}, headers=tenant)).json()

    r = await client.post(f"/api/appointments/{b['id']}/to-order", headers=tenant)
    assert r.status_code == 201, r.text
    o = (await db.execute(select(Order).where(Order.id == r.json()["order_id"]))).scalar_one()
    assert o.complaint == "Wobbly wheel"
    assert o.scheduled_for == datetime(2026, 9, 3, 11, 0)

    row = (await db.execute(select(Appointment).where(Appointment.id == b["id"]))).scalar_one()
    await db.refresh(row)
    assert row.order_id == o.id and row.status == "done"

    dupe = await client.post(f"/api/appointments/{b['id']}/to-order", headers=tenant)
    assert dupe.status_code == 409


@pytest.mark.asyncio
async def test_appointment_reminder_queues(client, tenant, db):
    from backend.models import NotificationLog
    cid = (await client.post("/api/clients", json={
        "name": "C", "phone": "+18009", "email": "c@x.dev"}, headers=tenant)).json()["id"]
    await client.post("/api/notifications/rules", json={
        "module": "appointment", "event": "reminder", "channel": "email",
        "reminder_offset_min": 120}, headers=tenant)
    me = await _me(client, tenant)
    # Scheduled times are the shop's wall clock (the test shop runs on the default India time),
    # not UTC: "UTC + 1h" was 4.5 hours in the past there (launch B6).
    from backend.services.dates import local_now
    soon = local_now("Asia/Kolkata") + timedelta(minutes=60)
    b = await client.post("/api/appointments", json={
        "client_id": cid, "employee_id": me["id"],
        "scheduled_for": soon.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_min": 30}, headers=tenant)
    assert b.status_code in (200, 201), b.text

    q1 = (await client.post("/api/notifications/rules/queue-reminders", headers=tenant)).json()
    assert q1["appointments"] == 1
    q2 = (await client.post("/api/notifications/rules/queue-reminders", headers=tenant)).json()
    assert q2["appointments"] == 0        # idempotent

    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.trigger_event == "reminder"))).scalars().first()
    assert log.status == "scheduled"


# ══ Build 86: tasks ══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_task_links_and_feed(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={
        "name": "Task client", "phone": "+19001"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={
        "location_id": loc, "complaint": "x"}, headers=tenant)).json()

    t = await client.post("/api/tasks", json={
        "title": "Order the part", "assigned_to_id": me["id"],
        "order_id": o["id"], "client_id": cid,
        "due_at": "2026-09-10T12:00:00"}, headers=tenant)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]

    detail = (await client.get(f"/api/tasks/{tid}", headers=tenant)).json()
    assert detail["order_number"] == o["order_number"]
    assert detail["client_name"] == "Task client"

    c = await client.post(f"/api/tasks/{tid}/comments", json={"body": "Supplier called"},
                          headers=tenant)
    assert c.status_code == 201
    feed = (await client.get(f"/api/tasks/{tid}/feed", headers=tenant)).json()
    bodies = [f["body"] for f in feed["items"]]
    assert "Supplier called" in bodies
    assert any("created" in b.lower() for b in bodies)


@pytest.mark.asyncio
async def test_task_on_time_completion_flag(client, tenant):
    me = await _me(client, tenant)
    future = (now_utc() + timedelta(days=1)).isoformat()
    t = (await client.post("/api/tasks", json={
        "title": "Early", "assigned_to_id": me["id"], "due_at": future},
        headers=tenant)).json()
    done = await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert done.json()["completed_on_time"] is True

    past = (now_utc() - timedelta(days=1)).isoformat()
    late = (await client.post("/api/tasks", json={
        "title": "Late", "assigned_to_id": me["id"], "due_at": past},
        headers=tenant)).json()
    assert late["overdue"] is True
    d2 = await client.patch(f"/api/tasks/{late['id']}", json={"status": "done"}, headers=tenant)
    assert d2.json()["completed_on_time"] is False


@pytest.mark.asyncio
async def test_task_commission_only_when_on_time(client, tenant, db):
    """Build 70's execution-term conditions apply to tasks unchanged."""
    from backend.models import CommissionRule
    me = await _me(client, tenant)
    db.add(CommissionRule(org_id=me["org_id"], name="On-time task", event="task_closed",
                          calc_type="flat", amount=5, term_condition="on_time"))
    await db.commit()

    past = (now_utc() - timedelta(days=1)).isoformat()
    late = (await client.post("/api/tasks", json={
        "title": "Late", "assigned_to_id": me["id"], "due_at": past}, headers=tenant)).json()
    await client.patch(f"/api/tasks/{late['id']}", json={"status": "done"}, headers=tenant)
    bal = (await client.get(f"/api/payroll/balance/{me['id']}", headers=tenant)).json()
    assert bal["balance"] == pytest.approx(0.0)      # late → no commission

    future = (now_utc() + timedelta(days=1)).isoformat()
    ok = (await client.post("/api/tasks", json={
        "title": "Prompt", "assigned_to_id": me["id"], "due_at": future},
        headers=tenant)).json()
    await client.patch(f"/api/tasks/{ok['id']}", json={"status": "done"}, headers=tenant)
    bal2 = (await client.get(f"/api/payroll/balance/{me['id']}", headers=tenant)).json()
    assert bal2["balance"] == pytest.approx(5.0)     # on time → paid


@pytest.mark.asyncio
async def test_task_delete_and_mine_filter(client, tenant, db):
    me = await _me(client, tenant)
    H, other = await _employee(client, db, me["org_id"], "tasker@x.dev")
    mine = (await client.post("/api/tasks", json={
        "title": "Mine", "assigned_to_id": me["id"]}, headers=tenant)).json()
    # authored AND assigned by the other employee — nothing to do with me
    (await client.post("/api/tasks", json={
        "title": "Theirs", "assigned_to_id": other.id}, headers=H)).json()

    # "My tasks" is assignee OR author, as in Orderry
    lst = (await client.get("/api/tasks?mine=true", headers=tenant)).json()
    titles = {t["title"] for t in lst["items"]}
    assert "Mine" in titles and "Theirs" not in titles

    d = await client.delete(f"/api/tasks/{mine['id']}", headers=tenant)
    assert d.status_code == 200
    assert (await db.execute(select(Task).where(
        Task.id == mine["id"]))).scalar_one_or_none() is None


# ══ Build 90: asset warehouses + status automation ═══════════════════════════
@pytest.mark.asyncio
async def test_asset_follows_the_job_through_warehouses(client, tenant, db):
    """Check-in → Pending repair, assignment → the tech's warehouse, ready → Ready for
    pickup, close → back to the client's own asset warehouse."""
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={
        "name": "Owner", "phone": "+17777"}, headers=tenant)).json()["id"]
    dev = (await client.post(f"/api/clients/{cid}/devices", json={
        "brand": "Acme", "model": "X", "serial": "S-1"}, headers=tenant)).json()

    for key, group in (("wip-90", "in_progress"), ("ready-90", "ready"), ("done-90", "closed")):
        r = await client.post("/api/statuses", json={
            "name": key, "key": key, "status_group": group,
            "is_terminal": group == "closed"}, headers=tenant)
        assert r.status_code in (200, 201), r.text

    o = (await client.post("/api/orders", json={
        "location_id": loc, "complaint": "x", "device_id": dev["id"],
        "assigned_to_id": me["id"]}, headers=tenant)).json()

    async def _wh_of_device():
        d = (await db.execute(select(Device).where(Device.id == dev["id"]))).scalar_one()
        await db.refresh(d)
        if not d.warehouse_id:
            return None
        return (await db.execute(select(Warehouse).where(
            Warehouse.id == d.warehouse_id))).scalar_one()

    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "wip-90"}, headers=tenant)
    w = await _wh_of_device()
    assert w.asset_role == "employee" and w.employee_id == me["id"]

    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready-90"}, headers=tenant)
    w = await _wh_of_device()
    assert w.system_key == "ready_for_pickup"

    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-90"}, headers=tenant)
    w = await _wh_of_device()
    assert w.asset_role == "client" and w.client_id == cid    # home again
