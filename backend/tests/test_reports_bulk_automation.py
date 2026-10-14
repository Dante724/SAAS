"""Builds 96-97 — warehouse reports, bulk product actions, missed-call automation,
and the printable payroll statement."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import (
    AutomationSetting,
    EmployeeBalanceLedger,
    Part,
    StockItemBatch,
    Task,
)


async def _wh_tok(client, H):
    sec = (await client.get("/api/settings/webhook-secret", headers=H)).json()["webhook_secret"]
    return {"X-Webhook-Token": sec}


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _wh(client, H):
    items = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"]
    return next(w for w in items if w["is_default"])


async def _part(client, H, name="Widget", sku="WS-1", cost=5, sell=10, min_stock=0):
    return (await client.post("/api/inventory/parts", json={
        "name": name, "sku": sku, "cost_price": cost, "sell_price": sell,
        "min_stock": min_stock}, headers=H)).json()


async def _stock(client, H, part_id, qty):
    loc = await _loc(client, H)
    r = await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=H)
    assert r.status_code in (200, 201), r.text


# ══ Build 96: warehouse reports ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_new_reports_are_in_the_catalog(client, tenant):
    cat = (await client.get("/api/reports/catalog", headers=tenant)).json()
    keys = {r["key"] for tab in cat["tabs"] for r in tab["reports"]}
    assert {"assortment", "purchase_list", "expiration"} <= keys


@pytest.mark.asyncio
async def test_assortment_report_values_stock_by_warehouse(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, cost=4)
    await _stock(client, tenant, p["id"], 10)

    r = (await client.get("/api/reports/run/assortment", headers=tenant)).json()
    assert "Warehouse" in r["columns"] and "Value" in r["columns"]
    row = next(x for x in r["rows"] if x[1] == "Widget")
    assert row[0] == w["name"]            # sits in the default warehouse
    assert row[4] == 10                   # available
    assert row[7] == pytest.approx(40.0)  # 10 × cost 4


@pytest.mark.asyncio
async def test_purchase_list_nets_off_reserved_and_on_order(client, tenant, db):
    from backend.models import POLine, PurchaseOrder, Supplier
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)

    # min 10, have 6 → short 4
    p = await _part(client, tenant, sku="RE-1", min_stock=10)
    await _stock(client, tenant, p["id"], 6)
    r = (await client.get("/api/reports/run/purchase_list", headers=tenant)).json()
    row = next(x for x in r["rows"] if x[1] == "RE-1")
    assert row[7] == 4                    # suggest order

    # 3 already on an open PO → shortfall drops to 1
    sup = Supplier(org_id=me["org_id"], name="S", balance=0)
    db.add(sup)
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=loc, po_number="PO-X", status="ordered")
    db.add(po)
    await db.flush()
    db.add(POLine(po_id=po.id, part_id=p["id"], qty_ordered=3, qty_received=0))
    await db.commit()

    r2 = (await client.get("/api/reports/run/purchase_list", headers=tenant)).json()
    row2 = next(x for x in r2["rows"] if x[1] == "RE-1")
    assert row2[6] == 3 and row2[7] == 1  # on order 3, suggest 1


@pytest.mark.asyncio
async def test_purchase_list_counts_reserved_as_unavailable(client, tenant, db):
    from backend.models import StockLevel
    p = await _part(client, tenant, sku="RS-1", min_stock=5)
    await _stock(client, tenant, p["id"], 5)
    # reserve 3 → only 2 free, so we're 3 short of the minimum
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    sl.qty_reserved = 3
    await db.commit()

    r = (await client.get("/api/reports/run/purchase_list", headers=tenant)).json()
    row = next(x for x in r["rows"] if x[1] == "RS-1")
    assert row[4] == 2 and row[7] == 3    # free 2, suggest 3


@pytest.mark.asyncio
async def test_expiration_report_flags_expired_and_soon(client, tenant, db):
    me = await _me(client, tenant)
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="EX-1", cost=2)
    db.add_all([
        StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                       location_id=loc, unit_cost=2, qty_received=10, qty_remaining=10,
                       expiry_date=datetime.utcnow() - timedelta(days=3)),   # expired
        StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                       location_id=loc, unit_cost=2, qty_received=5, qty_remaining=5,
                       expiry_date=datetime.utcnow() + timedelta(days=10)),  # soon
        StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                       location_id=loc, unit_cost=2, qty_received=5, qty_remaining=5,
                       expiry_date=datetime.utcnow() + timedelta(days=200)), # ok
    ])
    await db.commit()

    r = (await client.get("/api/reports/run/expiration", headers=tenant)).json()
    statuses = [row[6] for row in r["rows"]]
    assert "expired" in statuses and "expiring soon" in statuses
    # sorted soonest/most-overdue first
    assert r["rows"][0][6] == "expired"
    assert r["rows"][0][7] == pytest.approx(20.0)   # 10 × cost 2 at risk


@pytest.mark.asyncio
async def test_report_csv_export_works_for_new_report(client, tenant):
    p = await _part(client, tenant)
    await _stock(client, tenant, p["id"], 3)
    r = await client.get("/api/reports/export/assortment", headers=tenant)
    assert r.status_code == 200


# ══ Build 96: bulk product actions ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_bulk_price_adjustment_is_percentage(client, tenant, db):
    a = await _part(client, tenant, name="A", sku="A-1", sell=100)
    b = await _part(client, tenant, name="B", sku="B-1", sell=10)
    r = await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [a["id"], b["id"]], "action": "adjust_price_pct", "number": 10},
        headers=tenant)
    assert r.status_code == 200 and r.json()["changed"] == 2

    ra = (await db.execute(select(Part).where(Part.id == a["id"]))).scalar_one()
    rb = (await db.execute(select(Part).where(Part.id == b["id"]))).scalar_one()
    await db.refresh(ra)
    await db.refresh(rb)
    assert ra.sell_price == pytest.approx(110.0)   # +10%
    assert rb.sell_price == pytest.approx(11.0)     # same intent, different absolute


@pytest.mark.asyncio
async def test_bulk_set_min_stock_and_active(client, tenant, db):
    p = await _part(client, tenant, sku="BK-1")
    await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [p["id"]], "action": "set_min_stock", "number": 15}, headers=tenant)
    row = (await db.execute(select(Part).where(Part.id == p["id"]))).scalar_one()
    await db.refresh(row)
    assert row.min_stock == 15

    await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [p["id"]], "action": "set_active", "number": 0}, headers=tenant)
    await db.refresh(row)
    assert row.active is False


@pytest.mark.asyncio
async def test_bulk_rejects_bad_input(client, tenant):
    p = await _part(client, tenant, sku="BAD-1")
    empty = await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [], "action": "set_active", "number": 1}, headers=tenant)
    assert empty.status_code == 400
    unknown = await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [p["id"]], "action": "nonsense"}, headers=tenant)
    assert unknown.status_code == 422


# ══ Build 97: missed-call automation ═════════════════════════════════════════
@pytest.mark.asyncio
async def test_missed_call_automation_off_by_default(client, tenant, db):
    me = await _me(client, tenant)
    r = (await client.get("/api/comms/automations/task_on_missed_call",
                          headers=tenant)).json()
    assert r["enabled"] is False

    # a missed call with the automation off creates no task
    await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "test", "direction": "inbound", "from_number": "+1555",
        "to_number": "+1999", "status": "missed"}, headers=await _wh_tok(client, tenant))
    tasks = (await db.execute(select(Task).where(Task.org_id == me["org_id"]))).scalars().all()
    assert not tasks


@pytest.mark.asyncio
async def test_missed_call_creates_followup_task_when_on(client, tenant, db):
    me = await _me(client, tenant)
    on = await client.put("/api/comms/automations/task_on_missed_call", json={
        "enabled": True, "config": {"priority": "urgent"}}, headers=tenant)
    assert on.status_code == 200

    r = await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "test", "direction": "inbound", "from_number": "+15550101",
        "to_number": "+1999", "status": "missed"}, headers=await _wh_tok(client, tenant))
    assert r.status_code == 201
    assert r.json()["follow_up_task_id"]

    task = (await db.execute(select(Task).where(
        Task.id == r.json()["follow_up_task_id"]))).scalar_one()
    assert "+15550101" in task.title or "Call back" in task.title
    assert task.priority == "urgent"

    # a completed (answered) call makes no task
    r2 = await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "test", "direction": "inbound", "from_number": "+15550101",
        "to_number": "+1999", "status": "completed"}, headers=await _wh_tok(client, tenant))
    assert r2.json().get("follow_up_task_id") is None


@pytest.mark.asyncio
async def test_missed_call_task_goes_to_client_manager(client, tenant, db):
    me = await _me(client, tenant)
    # a known client whose account manager is the current user
    c = (await client.post("/api/clients", json={
        "name": "Known", "phone": "+15551234"}, headers=tenant)).json()
    upd = await client.put(f"/api/clients/{c['id']}", json={
        "name": "Known", "phone": "+15551234", "manager_id": me["id"]}, headers=tenant)
    assert upd.status_code == 200, upd.text
    await client.put("/api/comms/automations/task_on_missed_call", json={
        "enabled": True, "config": {"assign_to": None}}, headers=tenant)

    r = await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "test", "direction": "inbound", "from_number": "+15551234",
        "to_number": "+1999", "status": "missed"}, headers=await _wh_tok(client, tenant))
    task = (await db.execute(select(Task).where(
        Task.id == r.json()["follow_up_task_id"]))).scalar_one()
    assert task.client_id == c["id"]
    assert task.assigned_to_id == me["id"]      # routed to the account manager


@pytest.mark.asyncio
async def test_unknown_automation_key_rejected(client, tenant):
    r = await client.put("/api/comms/automations/not_a_thing", json={
        "enabled": True}, headers=tenant)
    assert r.status_code == 422


# ══ Build 97: printable payroll statement ════════════════════════════════════
@pytest.mark.asyncio
async def test_payroll_statement_pdf(client, tenant, db):
    me = await _me(client, tenant)
    db.add_all([
        EmployeeBalanceLedger(org_id=me["org_id"], employee_id=me["id"],
                              entry_type="commission", amount=50, event="order_closed",
                              note="WO-1"),
        EmployeeBalanceLedger(org_id=me["org_id"], employee_id=me["id"],
                              entry_type="penalty", amount=-10, event="late", note=""),
    ])
    await db.commit()

    r = await client.get(f"/api/print/payroll-statement/{me['id']}", headers=tenant)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_payroll_statement_blocks_other_employees(client, tenant, db):
    """An employee can print their own, but not a colleague's, unless they manage payroll."""
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    me = await _me(client, tenant)

    # a role that explicitly cannot manage payroll
    role = Role(org_id=me["org_id"], name="Tech",
                permissions={"can_manage_payroll": False})
    db.add(role)
    await db.flush()
    other = Employee(org_id=me["org_id"], name="Colleague", email="col@x.dev",
                     password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(other)
    await db.commit()
    await db.refresh(other)
    tok = (await client.post("/api/auth/token", data={
        "username": "col@x.dev", "password": "pw-123456789"})).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    own = await client.get(f"/api/print/payroll-statement/{other.id}", headers=H)
    assert own.status_code == 200                 # their own is fine

    theirs = await client.get(f"/api/print/payroll-statement/{me['id']}", headers=H)
    assert theirs.status_code == 403              # someone else's is not
