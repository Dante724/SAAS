"""Batch C: commission math and payroll bookkeeping (Oct 2026 payroll audit).

- Reopening an order wiped its commission for good: re-closing reused the reversed key.
- A refund followed by an archive reversed the same commission twice.
- Any partial refund clawed back the whole commission.
- Parts margin ignored the line discount.
- Labour timesheets and shop attendance could both pay the same hours.
- Mark paid posted no cash or expense; paid runs couldn't be voided.
- Payroll actions and wage edits left no audit trail.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


async def _register(client):
    email = f"pc-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _employee(client, H, **extra):
    body = {"name": "Tech", "email": f"tc-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"}
    body.update(extra)
    r = await client.post("/api/staff/employees", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _rule(db, org, **kw):
    from backend.models import CommissionRule
    r = CommissionRule(org_id=org, **kw)
    db.add(r)
    await db.commit()
    return r


async def _till(client, H):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _closed_status(client, H):
    key = f"done-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/statuses", json={
        "name": key, "key": key, "status_group": "closed", "is_terminal": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    return key


async def _labour_order(client, H, tech, price=500):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": "Ravi", "phone": "+15551234"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={
        "location_id": loc, "client_id": cl, "complaint": "x", "assigned_to_id": tech}, headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "job", "qty": 1,
        "unit_price": price, "technician_id": tech}, headers=H)
    assert r.status_code in (200, 201), r.text
    return o


async def _status(client, H, oid, key):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=H)
    assert r.status_code == 200, r.text


async def _pay_order(client, H, oid, till, amount):
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={
        "method": "cash", "account_id": till, "amount": amount}, headers=H)
    assert r.status_code in (200, 201), r.text


async def _commission(db, org, emp):
    from sqlalchemy import func

    from backend.models import EmployeeBalanceLedger as L
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type.in_(("commission", "clawback"))))).scalar())


async def _balance(client, H, emp):
    return (await client.get(f"/api/payroll/balance/{emp}", headers=H)).json()["balance"]


async def _account_balance(client, H, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"]
                if a["id"] == aid)["balance"]


# ── commission cycles ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reopen_then_reclose_pays_commission_again(client, db):
    H, org, _ = await _register(client)
    tech = await _employee(client, H)
    await _rule(db, org, name="Labour 10", event="labor_performed", calc_type="percent", amount=10)
    done = await _closed_status(client, H)
    o = await _labour_order(client, H, tech)

    await _status(client, H, o["id"], done)
    assert await _commission(db, org, tech) == 50
    await _status(client, H, o["id"], o["status"])          # reopen
    assert await _commission(db, org, tech) == 0
    await _status(client, H, o["id"], done)                 # close again
    assert await _commission(db, org, tech) == 50           # was 0: the old key blocked it
    await _status(client, H, o["id"], o["status"])
    await _status(client, H, o["id"], done)
    assert await _commission(db, org, tech) == 50           # never more than once per cycle


@pytest.mark.asyncio
async def test_refund_then_archive_reverses_only_once(client, db):
    H, org, _ = await _register(client)
    tech = await _employee(client, H)
    till = await _till(client, H)
    await _rule(db, org, name="Labour 10", event="labor_performed", calc_type="percent", amount=10)
    done = await _closed_status(client, H)
    o = await _labour_order(client, H, tech)
    await _pay_order(client, H, o["id"], till, 500)
    await _status(client, H, o["id"], done)
    assert await _commission(db, org, tech) == 50

    r = await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": 500, "order_id": o["id"], "reason": "unhappy"}, headers=H)
    assert r.status_code == 201, r.text
    assert await _commission(db, org, tech) == 0
    r = await client.post(f"/api/orders/{o['id']}/archive", json={"reason": "gone", "refund": False}, headers=H)
    assert r.status_code == 200, r.text
    assert await _commission(db, org, tech) == 0            # was -50: archive reversed it again


@pytest.mark.asyncio
async def test_partial_refund_claws_back_its_share(client, db):
    H, org, _ = await _register(client)
    tech = await _employee(client, H)
    till = await _till(client, H)
    await _rule(db, org, name="Labour 10", event="labor_performed", calc_type="percent", amount=10)
    done = await _closed_status(client, H)
    o = await _labour_order(client, H, tech)
    await _pay_order(client, H, o["id"], till, 500)
    await _status(client, H, o["id"], done)

    r = await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": 100, "order_id": o["id"]}, headers=H)
    assert r.status_code == 201, r.text
    assert await _commission(db, org, tech) == 40           # was 0: any refund took it all
    r = await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": 200, "order_id": o["id"]}, headers=H)
    assert await _commission(db, org, tech) == 20           # 300 of 500 refunded

    # a reopen + re-close pays on what the shop kept, not the full order
    await _status(client, H, o["id"], o["status"])
    assert await _commission(db, org, tech) == 0
    await _status(client, H, o["id"], done)
    assert await _commission(db, org, tech) == 20

    # the rest refunded → nothing left, and an archive takes nothing more
    await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": 200, "order_id": o["id"]}, headers=H)
    assert await _commission(db, org, tech) == 0
    await client.post(f"/api/orders/{o['id']}/archive", json={"reason": "x", "refund": False}, headers=H)
    assert await _commission(db, org, tech) == 0


@pytest.mark.asyncio
async def test_parts_margin_uses_the_discounted_price(client, db):
    from backend.models import Part
    H, org, _ = await _register(client)
    tech = await _employee(client, H)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": "S-1", "cost_price": 50, "sell_price": 100}, headers=H)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": 5, "movement_type": "receive"}, headers=H)
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    part.avg_cost, part.cost_price = 50, 50
    await db.commit()
    await _rule(db, org, name="Margin 10", event="product_sold", calc_type="percent",
                percent_basis="margin", amount=10)
    done = await _closed_status(client, H)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x",
                                               "assigned_to_id": tech}, headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": pid, "description": "Screen", "qty": 1,
        "unit_price": 100, "discount": 20, "discount_type": "pct", "technician_id": tech}, headers=H)
    assert r.status_code in (200, 201), r.text
    await _status(client, H, o["id"], done)
    assert await _commission(db, org, tech) == pytest.approx(3.0)   # (80 − 50) × 10%; was 5


# ── one clock pays ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_the_chosen_clock_pays_hourly_staff(client, db):
    from backend.models import Location, Shift, TimeEntry
    H, org, _ = await _register(client)
    labour = await _employee(client, H, wage_type="hourly", wage_value=20)
    shop = await _employee(client, H, wage_type="hourly", wage_value=20, hourly_basis="attendance")
    loc = (await db.execute(select(Location).where(Location.org_id == org))).scalars().first()
    for emp in (labour, shop):
        db.add(Shift(employee_id=emp, location_id=loc.id, started_at=datetime(2026, 8, 3, 9),
                     ended_at=datetime(2026, 8, 3, 17), hours_worked=8.0))
        db.add(TimeEntry(org_id=org, employee_id=emp, started_at=datetime(2026, 8, 3, 9),
                         ended_at=datetime(2026, 8, 3, 17), duration_sec=8 * 3600, cost_rate=20,
                         state="stopped", source="manual"))
    await db.commit()
    window = {"date_from": "2026-08-01", "date_to": "2026-08-31"}

    # labour-hours staff: the timesheet pays, the attendance clock refuses
    r = await client.post("/api/timeclock/timesheet/approve", json={"employee_id": labour, **window}, headers=H)
    assert r.status_code == 200 and r.json()["wage_posted"] == 160, r.text
    r = await client.post("/api/payroll/attendance-wages", json={"employee_id": labour, **window}, headers=H)
    assert r.status_code == 409                              # was 201: the same 8h paid twice

    # attendance staff: shifts pay, approving the timesheet locks it but posts no wage
    r = await client.post("/api/timeclock/timesheet/approve", json={"employee_id": shop, **window}, headers=H)
    assert r.status_code == 200 and r.json()["wage_posted"] == 0, r.text
    r = await client.post("/api/payroll/attendance-wages", json={"employee_id": shop, **window}, headers=H)
    assert r.status_code == 201 and r.json()["wage_posted"] == 160, r.text
    for emp in (labour, shop):
        run = await client.post("/api/payroll/run", json={
            "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"}, headers=H)
        assert run.json()["hourly_total"] == 160, (emp, run.text)   # 8h once, never twice


# ── paying and voiding runs ───────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_paid_takes_the_money_out_of_the_chosen_account(client, db):
    H, org, _ = await _register(client)
    emp = await _employee(client, H)
    till = await _till(client, H)
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31",
        "base_salary": 1000}, headers=H)).json()

    assert (await client.post(f"/api/payroll/runs/{run['id']}/pay", json={}, headers=H)).status_code == 400
    r = await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till}, headers=H)
    assert r.status_code == 200, r.text
    assert await _account_balance(client, H, till) == -1000          # was 0: no cash left the till
    today = datetime.utcnow().date().isoformat()
    pnl = (await client.get(f"/api/finance/pnl?date_from={today}&date_to={today}", headers=H)).json()
    assert pnl["opex"] == 1000                                        # was 0: missing from the P&L
    txn = r.json()["cash_txn_id"]
    # the payout can't be deleted on its own from the cashbox
    assert (await client.delete(f"/api/cash/transactions/{txn}", headers=H)).status_code == 409


@pytest.mark.asyncio
async def test_void_reverses_ledger_and_cash_and_frees_the_period(client, db):
    H, org, _ = await _register(client)
    emp = await _employee(client, H)
    till = await _till(client, H)
    body = {"employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31", "base_salary": 1000}
    run = (await client.post("/api/payroll/run", json=body, headers=H)).json()
    await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till}, headers=H)

    assert (await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": " "}, headers=H)).status_code == 400
    r = await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": "wrong salary"}, headers=H)
    assert r.status_code == 200, r.text
    assert await _account_balance(client, H, till) == 0
    today = datetime.utcnow().date().isoformat()
    assert (await client.get(f"/api/finance/pnl?date_from={today}&date_to={today}", headers=H)).json()["opex"] == 0
    assert await _balance(client, H, emp) == 0
    assert (await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": "again"}, headers=H)).status_code == 409
    assert (await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till}, headers=H)).status_code == 409

    # the period can be accrued again, correctly this time
    again = await client.post("/api/payroll/run", json={**body, "base_salary": 900}, headers=H)
    assert again.status_code == 201, again.text
    assert await _balance(client, H, emp) == 900

    # voiding an accrued (unpaid) run takes its accrual back off the balance
    r = await client.post(f"/api/payroll/runs/{again.json()['id']}/void", json={"reason": "redo"}, headers=H)
    assert r.status_code == 200 and await _balance(client, H, emp) == 0


# ── audit trail ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payroll_actions_and_wage_edits_are_audited(client, db):
    from backend.models import AuditLog
    H, org, me = await _register(client)
    emp = await _employee(client, H, wage_type="hourly", wage_value=20)
    till = await _till(client, H)
    await client.post("/api/payroll/rules", json={"name": "Labour 10", "event": "labor_performed", "amount": 10}, headers=H)
    await client.post("/api/payroll/adjustment", json={"employee_id": emp, "kind": "bonus", "amount": 50}, headers=H)
    await client.post("/api/payroll/coefficients", json={
        "employee_id": emp, "scope_type": "product_category", "scope_ref": "Screens", "coefficient": 2}, headers=H)
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31", "base_salary": 100}, headers=H)).json()
    await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till}, headers=H)
    await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": "oops"}, headers=H)
    await client.patch(f"/api/staff/employees/{emp}", json={"wage_value": 25}, headers=H)

    rows = (await db.execute(select(AuditLog).where(AuditLog.org_id == org))).scalars().all()
    actions = {r.action for r in rows if r.actor_id == me}
    for a in ("payroll.rule_create", "payroll.bonus_add", "payroll.coefficient_create",
              "payroll.run_create", "payroll.run_pay", "payroll.run_void"):
        assert a in actions, (a, actions)
    edit = next(r for r in rows if r.action == "employee.update" and r.entity_id == emp)
    assert edit.diff["wage_value"] == {"from": 20, "to": 25}          # was field names only
