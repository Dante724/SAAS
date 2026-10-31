"""Builds 70-72 — payroll rule conditions, timing, cancel-on-delete,
exceptional coefficients, and the accrual workflow."""
from datetime import date

import pytest
from sqlalchemy import select

from backend.models import CommissionRule, EmployeeBalanceLedger


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _order(client, H, **extra):
    body = {"location_id": await _loc(client, H), "complaint": "x"}
    body.update(extra)
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _labour(client, H, oid, price, tech=None):
    body = {"line_type": "labour", "kind": "custom", "description": "job", "qty": 1, "unit_price": price}
    if tech:
        body["technician_id"] = tech
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=H)
    assert r.status_code in (200, 201), r.text


async def _closed_status(client, H, key):
    r = await client.post("/api/statuses", json={
        "name": key, "key": key, "status_group": "closed", "is_terminal": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    return key


async def _rule(db, org_id, **kw):
    r = CommissionRule(org_id=org_id, **kw)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _balance(client, H, emp_id):
    return (await client.get(f"/api/payroll/balance/{emp_id}", headers=H)).json()["balance"]


# ── Build 70: conditions & timing ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_wo_type_condition(client, tenant, db):
    """A rule scoped to a work-order type pays only on that type."""
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    wt = await client.post("/api/forms/types", json={"name": "Warranty"}, headers=tenant)
    assert wt.status_code in (200, 201), wt.text
    warranty = wt.json()["id"]
    await _rule(db, me["org_id"], name="Warranty only", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10, wo_type_id=warranty)
    key = await _closed_status(client, tenant, "done-t")

    o1 = await _order(client, tenant, assigned_to_id=me["id"], type_id=warranty)
    await _labour(client, tenant, o1["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o1['id']}/status", json={"status": key}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)

    o2 = await _order(client, tenant, assigned_to_id=me["id"])          # no type
    await _labour(client, tenant, o2["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o2['id']}/status", json={"status": key}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)   # unchanged


@pytest.mark.asyncio
async def test_urgent_term_condition(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _rule(db, me["org_id"], name="Urgent only", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10, term_condition="urgent")
    key = await _closed_status(client, tenant, "done-u")

    o = await _order(client, tenant, assigned_to_id=me["id"], priority=1)   # urgent
    await _labour(client, tenant, o["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)

    o2 = await _order(client, tenant, assigned_to_id=me["id"])              # normal
    await _labour(client, tenant, o2["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o2['id']}/status", json={"status": key}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)   # unchanged


@pytest.mark.asyncio
async def test_on_status_timing_pays_before_close(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    st = await client.post("/api/statuses", json={
        "name": "In progress x", "key": "inprog-x", "status_group": "in_progress"}, headers=tenant)
    assert st.status_code in (200, 201), st.text
    await _rule(db, me["org_id"], name="Pay at in-progress", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10,
                calc_timing="on_status", trigger_status_key="inprog-x")
    o = await _order(client, tenant, assigned_to_id=me["id"])
    await _labour(client, tenant, o["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "inprog-x"}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_on_status_rule_does_not_double_pay_on_close(client, tenant, db):
    """Re-entering the trigger status (or later closing) must not pay twice."""
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await client.post("/api/statuses", json={
        "name": "Prog d", "key": "prog-d", "status_group": "in_progress"}, headers=tenant)
    await client.post("/api/statuses", json={
        "name": "Hold d", "key": "hold-d", "status_group": "in_progress"}, headers=tenant)
    key = await _closed_status(client, tenant, "done-d")
    await _rule(db, me["org_id"], name="Once", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10,
                calc_timing="on_status", trigger_status_key="prog-d")
    o = await _order(client, tenant, assigned_to_id=me["id"])
    await _labour(client, tenant, o["id"], 100, me["id"])
    for s in ("prog-d", "hold-d", "prog-d", key):     # re-enter trigger, then close
        await client.patch(f"/api/orders/{o['id']}/status", json={"status": s}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)   # paid exactly once


@pytest.mark.asyncio
async def test_cancel_if_deleted_reverses_on_archive(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _rule(db, me["org_id"], name="Cancelable", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10, cancel_if_deleted=True)
    key = await _closed_status(client, tenant, "done-c")
    o = await _order(client, tenant, assigned_to_id=me["id"])
    await _labour(client, tenant, o["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)
    r = await client.post(f"/api/orders/{o['id']}/archive",
                          json={"reason": "mistake", "refund": False}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _balance(client, tenant, me["id"]) == pytest.approx(0.0)   # reversed


@pytest.mark.asyncio
async def test_cancel_flag_off_keeps_commission(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _rule(db, me["org_id"], name="Sticky", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10, cancel_if_deleted=False)
    key = await _closed_status(client, tenant, "done-s")
    o = await _order(client, tenant, assigned_to_id=me["id"])
    await _labour(client, tenant, o["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    await client.post(f"/api/orders/{o['id']}/archive",
                      json={"reason": "x", "refund": False}, headers=tenant)
    assert await _balance(client, tenant, me["id"]) == pytest.approx(10.0)   # kept


# ── Build 72: exceptional commissions & coefficients ──────────────────────────
@pytest.mark.asyncio
async def test_employee_coefficient_multiplies_product_commission(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await _loc(client, tenant)
    part = (await client.post("/api/inventory/parts", json={
        "name": "Widget", "sku": "W-1", "cost_price": 0, "sell_price": 100}, headers=tenant)).json()
    pid = part["id"]
    adj = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": 5, "movement_type": "receive"}, headers=tenant)
    assert adj.status_code in (200, 201), adj.text
    await _rule(db, me["org_id"], name="Product 10", event="product_sold",
                calc_type="percent", percent_basis="total", amount=10)
    c = await client.post("/api/payroll/coefficients", json={
        "employee_id": me["id"], "scope_type": "product", "scope_ref": pid,
        "context": "order", "coefficient": 2}, headers=tenant)
    assert c.status_code == 201, c.text

    key = await _closed_status(client, tenant, "done-coef")
    o = await _order(client, tenant, assigned_to_id=me["id"], location_id=loc)
    ln = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": pid, "description": "Widget",
        "qty": 1, "unit_price": 100, "technician_id": me["id"]}, headers=tenant)
    assert ln.status_code in (200, 201), ln.text
    cl = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    assert cl.status_code == 200, cl.text

    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.employee_id == me["id"],
        EmployeeBalanceLedger.entry_type == "commission"))).scalars().all()
    assert float(sum(r.amount for r in rows)) == pytest.approx(20.0)   # 10% of 100 ×2


@pytest.mark.asyncio
async def test_coefficient_upsert_not_duplicated(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    a = await client.post("/api/payroll/coefficients", json={
        "employee_id": me["id"], "scope_type": "product_category", "scope_ref": "Screens",
        "context": "order", "coefficient": 1.5}, headers=tenant)
    assert a.status_code == 201
    b = await client.post("/api/payroll/coefficients", json={
        "employee_id": me["id"], "scope_type": "product_category", "scope_ref": "Screens",
        "context": "order", "coefficient": 3}, headers=tenant)
    assert b.status_code == 201 and b.json()["id"] == a.json()["id"]
    items = (await client.get(f"/api/payroll/coefficients?employee_id={me['id']}", headers=tenant)).json()["items"]
    screens = [c for c in items if c["scope_ref"] == "Screens"]
    assert len(screens) == 1 and screens[0]["coefficient"] == 3.0


# ── Build 71: accrual workflow ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_current_month_accrual_rejected_draft_allowed(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    from backend.services.dates import today_in
    today = today_in("Asia/Kolkata")   # the test shop's day: "current month" is the shop's
    ps, pe = today.replace(day=1).isoformat(), today.isoformat()
    r = await client.post("/api/payroll/run", json={
        "employee_id": me["id"], "period_start": ps, "period_end": pe, "base_salary": 1000}, headers=tenant)
    assert r.status_code == 400          # can't accrue the current month
    d = await client.post("/api/payroll/run", json={
        "employee_id": me["id"], "period_start": ps, "period_end": pe,
        "base_salary": 1000, "draft": True}, headers=tenant)
    assert d.status_code == 201 and d.json()["status"] == "draft"
    assert await _balance(client, tenant, me["id"]) == pytest.approx(0.0)   # draft doesn't post


@pytest.mark.asyncio
async def test_past_month_accrual_and_duplicate_guard(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    ps, pe = "2025-01-01", "2025-01-31"
    r1 = await client.post("/api/payroll/run", json={
        "employee_id": me["id"], "period_start": ps, "period_end": pe, "base_salary": 1000}, headers=tenant)
    assert r1.status_code == 201 and r1.json()["status"] == "accrued"
    assert await _balance(client, tenant, me["id"]) == pytest.approx(1000.0)
    r2 = await client.post("/api/payroll/run", json={
        "employee_id": me["id"], "period_start": ps, "period_end": pe, "base_salary": 1000}, headers=tenant)
    assert r2.status_code == 409         # one accrual per employee per period

    till = (await client.post("/api/cash/accounts", json={"name": "Till"}, headers=tenant)).json()["id"]
    paid = await client.post(f"/api/payroll/runs/{r1.json()['id']}/pay", json={"account_id": till}, headers=tenant)
    assert paid.status_code == 200
    assert await _balance(client, tenant, me["id"]) == pytest.approx(0.0)   # payout drew it down


@pytest.mark.asyncio
async def test_calculation_preview(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _rule(db, me["org_id"], name="Labor 10", event="labor_performed",
                calc_type="percent", percent_basis="total", amount=10)
    key = await _closed_status(client, tenant, "done-p")
    o = await _order(client, tenant, assigned_to_id=me["id"])
    await _labour(client, tenant, o["id"], 100, me["id"])
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    month = date.today().strftime("%Y-%m")
    calc = (await client.get(f"/api/payroll/calculation?month={month}", headers=tenant)).json()
    mine = [x for x in calc["items"] if x["employee_id"] == me["id"]]
    assert mine and mine[0]["commissions_total"] == pytest.approx(10.0)
    assert mine[0]["accrual_status"] is None      # nothing accrued yet
