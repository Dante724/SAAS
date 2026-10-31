"""Workstream B — dropped jobs (gap #5).

Moving a work order into a dropped-group status needs a reason, skips the close checks that only
apply to finished work, writes off what the customer still owes (no fake debt) and doesn't draft
an invoice. Reopening undoes the write-off.
"""
import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _job(client, H, *, price=100.0, cost=30.0):
    cid = (await client.post("/api/clients", json={"name": "Dropped Dan"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "complaint": "Cracked screen"}, headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Screen",
        "qty": 1, "unit_price": price, "cost_price": cost}, headers=H)
    assert r.status_code in (200, 201), r.text
    return o["id"], cid


async def _pay(db, oid, amount):
    from backend.models import Payment
    db.add(Payment(order_id=oid, amount=amount, method="cash", kind="payment"))
    await db.commit()


async def _order(db, oid):
    from backend.models import Order
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await db.refresh(o)
    return o


async def test_dropping_needs_a_reason(client, tenant):
    oid, _ = await _job(client, tenant)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"}, headers=tenant)
    assert r.status_code == 422, r.text
    assert "reason" in r.json()["detail"].lower()
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped", "reason": "   "}, headers=tenant)
    assert r.status_code == 422


async def test_dropping_writes_off_the_balance_and_leaves_no_debt(client, tenant, db):
    from backend.models import ClientLedgerEntry, Invoice, Organisation
    from backend.services.order_money import balance_due
    oid, cid = await _job(client, tenant)
    o = await _order(db, oid)
    org = (await db.execute(select(Organisation).where(Organisation.id == o.org_id))).scalar_one()
    org.auto_draft_invoice_on_close = True
    await db.commit()
    await _pay(db, oid, 40)
    r = await client.patch(f"/api/orders/{oid}/status",
                           json={"status": "dropped", "reason": "Never came back"}, headers=tenant)
    assert r.status_code == 200, r.text
    o = await _order(db, oid)
    assert o.cancel_reason == "Never came back"
    assert float(o.written_off) == 60.0
    assert o.closed_at is not None
    assert await balance_due(db, o) == 0
    # the customer's ledger is charged only what they paid — not the written-off 60
    charged = (await db.execute(select(func.coalesce(func.sum(ClientLedgerEntry.amount), 0)).where(
        ClientLedgerEntry.client_id == cid, ClientLedgerEntry.entry_type == "charge",
        ClientLedgerEntry.ref_id == oid))).scalar()
    assert abs(float(charged)) == 40.0
    # no invoice is drafted for a dropped job, even with auto-draft on
    n = (await db.execute(select(func.count()).select_from(Invoice).where(Invoice.order_id == oid))).scalar()
    assert n == 0
    # the order payload carries the write-off for the screen
    body = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert body["written_off"] == 60.0


async def test_dropping_skips_finished_work_checks(client, tenant, db):
    from backend.models import Order, OrderChecklistItem, OrderLine, WorkOrderType
    oid, _ = await _job(client, tenant)
    o = await _order(db, oid)
    wt = WorkOrderType(org_id=o.org_id, name="Signed", requires_signature=True, requires_checklist=True)
    db.add(wt)
    await db.flush()
    o.type_id = wt.id
    db.add(OrderChecklistItem(org_id=o.org_id, order_id=oid, label="Test screen", required=True, done=False))
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().first()
    line.pending_stock = 1
    await db.commit()
    # closing normally is blocked…
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 409
    # …dropping isn't
    r = await client.patch(f"/api/orders/{oid}/status",
                           json={"status": "dropped", "reason": "Too expensive"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await db.execute(select(Order.status).where(Order.id == oid))).scalar() == "dropped"


async def test_checks_set_on_the_dropped_status_itself_still_apply(client, tenant, db):
    from backend.models import Order, OrderStatus
    oid, _ = await _job(client, tenant)
    org = (await db.execute(select(Order.org_id).where(Order.id == oid))).scalar()
    st = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == org, OrderStatus.key == "dropped",
                                                     OrderStatus.record_type == "work_order"))).scalar_one()
    st.require_comment = True
    await db.commit()
    r = await client.patch(f"/api/orders/{oid}/status",
                           json={"status": "dropped", "reason": "Too expensive"}, headers=tenant)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "comment_required" and "comment" in r.json()["detail"]["message"]


async def test_reopening_a_dropped_job_restores_the_balance(client, tenant, db):
    from backend.services.order_money import balance_due
    oid, _ = await _job(client, tenant)
    await _pay(db, oid, 25)
    assert (await client.patch(f"/api/orders/{oid}/status",
                               json={"status": "dropped", "reason": "No parts"}, headers=tenant)).status_code == 200
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text
    o = await _order(db, oid)
    assert float(o.written_off or 0) == 0
    assert await balance_due(db, o) == 75.0


async def test_dropping_a_fully_paid_job_writes_off_nothing(client, tenant, db):
    oid, _ = await _job(client, tenant)
    await _pay(db, oid, 100)
    assert (await client.patch(f"/api/orders/{oid}/status",
                               json={"status": "dropped", "reason": "Changed mind"}, headers=tenant)).status_code == 200
    assert float((await _order(db, oid)).written_off or 0) == 0


async def test_write_off_is_audited(client, tenant, db):
    from backend.models import AuditLog
    oid, _ = await _job(client, tenant)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped", "reason": "Gone"}, headers=tenant)
    rows = (await db.execute(select(AuditLog).where(AuditLog.entity_id == oid,
                                                    AuditLog.action == "order.write_off"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].diff["amount"] == 100.0
    assert rows[0].diff["reason"] == "Gone"


async def _staff(client, H, perms):
    import uuid
    role = (await client.post("/api/staff/roles", json={"name": f"R{uuid.uuid4().hex[:4]}", "permissions": perms},
                              headers=H)).json()
    em = f"s-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={"name": "Staff", "email": em, "password": "pw-test-12345",
                                                     "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": em, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def test_writing_off_a_debt_needs_the_finance_right(client, tenant, db):
    tech = await _staff(client, tenant, {"can_view_finance": False})
    owed, _ = await _job(client, tenant)
    r = await client.patch(f"/api/orders/{owed}/status", json={"status": "dropped", "reason": "Gone"}, headers=tech)
    assert r.status_code == 403, r.text
    o = await _order(db, owed)
    assert o.status != "dropped" and float(o.written_off or 0) == 0
    # a job with nothing owed can still be dropped by them
    paid, _ = await _job(client, tenant)
    await _pay(db, paid, 100)
    r = await client.patch(f"/api/orders/{paid}/status", json={"status": "dropped", "reason": "Gone"}, headers=tech)
    assert r.status_code == 200, r.text


async def test_bulk_drop_needs_a_reason_too(client, tenant):
    oid, _ = await _job(client, tenant)
    r = await client.post("/api/orders/bulk", json={"order_ids": [oid], "action": "set_status", "value": "dropped"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 0 and "reason" in r.json()["skipped"][0]["reason"].lower()


async def test_refund_after_dropping_does_not_bring_the_debt_back(client, tenant, db):
    from backend.models import ClientLedgerEntry
    from backend.services.order_money import balance_due
    oid, cid = await _job(client, tenant)
    await _pay(db, oid, 40)
    assert (await client.patch(f"/api/orders/{oid}/status",
                               json={"status": "dropped", "reason": "Gone"}, headers=tenant)).status_code == 200
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    r = await client.post("/api/returns/refunds", json={"account_id": acc, "amount": 40, "order_id": oid,
                                                         "reason": "Deposit back"}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await _order(db, oid)
    assert float(o.written_off) == 100.0
    assert await balance_due(db, o) == 0
    charged = (await db.execute(select(func.coalesce(func.sum(ClientLedgerEntry.amount), 0)).where(
        ClientLedgerEntry.client_id == cid, ClientLedgerEntry.entry_type == "charge",
        ClientLedgerEntry.ref_id == oid))).scalar()
    assert float(charged) == 0


async def test_moving_between_dropped_statuses_keeps_the_reason_and_needs_no_new_rights(client, tenant, db):
    from backend.models import Order, OrderStatus
    oid, _ = await _job(client, tenant)
    org = (await db.execute(select(Order.org_id).where(Order.id == oid))).scalar()
    db.add(OrderStatus(org_id=org, key="refused", name="Refused", status_group="dropped", is_terminal=True,
                       record_type="work_order", active=True))
    await db.commit()
    assert (await client.patch(f"/api/orders/{oid}/status",
                               json={"status": "dropped", "reason": "Gone"}, headers=tenant)).status_code == 200
    tech = await _staff(client, tenant, {"can_view_finance": False, "can_edit_closed_ticket": True})
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "refused"}, headers=tech)
    assert r.status_code == 200, r.text
    o = await _order(db, oid)
    assert o.status == "refused" and o.cancel_reason == "Gone" and float(o.written_off) == 100.0
