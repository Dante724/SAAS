"""Audit DB-09, the rest of the list: every other stored total that can be re-derived from what it
summarises is checked by the nightly sweep too.

- invoice_total   Invoice.total = max(subtotal − discount + tax, 0)
- invoice_credit  Invoice.credited = Σ issued credit notes on it
- credit_note     CreditNote.subtotal / tax_total / total = Σ its lines' net / tax / total
- purchase_return PurchaseReturn.total = Σ line totals (each line priced at up to 4 dp)
- write_off       WriteOff.total_cost = Σ qty × unit_cost
- posting         Posting.total = Σ line totals (legacy documents, frozen)
- reserved        Σ StockLevel.qty_reserved per (part, location) = Σ live holds there
- payroll_run     gross = parts; base / payout / void legs in the ledger match the run
- prepayment      Order.prepayment_total = Σ prepayment payments on the job

Not checked (not derivable from what is stored; written in reconcile.py): invoice subtotal,
discount and tax (a whole-job invoice has no lines until it is locked, and a manual subtotal is
allowed), coupon uses (a work-order use is only a line's description), and a payroll run's bonus,
penalty, commission and hourly snapshots.
"""
import pytest
from sqlalchemy import select, update

from backend.models import (
    CreditNote,
    Invoice,
    Order,
    PayrollRun,
    PurchaseReturn,
    StockLevel,
    WriteOff,
)
from backend.services import reconcile

REST = ("invoice_total", "invoice_credit", "credit_note", "purchase_return", "write_off", "posting",
        "reserved", "payroll_run", "prepayment")


async def _shop(client, h, db):
    from backend.models import Posting, PostingLine
    org = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Rest"}, headers=h)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Cell", "sku": "RR-1"}, headers=h)).json()["id"]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": loc, "qty": 20, "movement_type": "receive", "unit_cost": 3.333})
    assert r.status_code == 200, r.text
    wh = (await client.get("/api/settings/warehouses?kind=product", headers=h)).json()["items"][0]["id"]
    # a job: prepaid, invoiced, part-credited, with a part on hold
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "location_id": loc,
        "lines": [{"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 120}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 40, "account_id": till}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/inventory/reserve", headers=h,
                          json={"part_id": pid, "location_id": loc, "qty": 2, "order_id": oid})
    assert r.status_code == 201, r.text
    iid = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=h)).json()["id"]
    r = await client.post("/api/finance/credit-notes", headers=h, json={
        "invoice_id": iid, "lines": [{"description": "Goodwill", "qty": 3, "unit_price": 3.335}]})
    assert r.status_code == 201, r.text
    cn = r.json()["id"]
    assert (await client.post(f"/api/finance/credit-notes/{cn}/issue", headers=h)).status_code == 200
    # stock documents
    r = await client.post("/api/stock-docs/write-offs", headers=h, json={
        "warehouse_id": wh, "lines": [{"part_id": pid, "qty": 3}]})
    assert r.status_code == 201, r.text
    wo = r.json()["id"]
    r = await client.post("/api/stock-docs/returns", headers=h, json={
        "warehouse_id": wh, "settlement": "cash", "account_id": till,
        "lines": [{"part_id": pid, "qty": 3, "unit_cost": 3.333}]})
    assert r.status_code in (200, 201), r.text
    pr = r.json()["id"]
    post = Posting(org_id=org, number="PST-1", warehouse_id=wh, total=10.0, status="posted")
    db.add(post)
    await db.flush()
    post_id = post.id
    db.add(PostingLine(posting_id=post_id, part_id=pid, qty=2, unit_cost=5, total=10.0))
    await db.commit()
    # an employee paid through a payroll run, and a voided one
    eid = (await client.post("/api/staff/employees", json={
        "name": "Pay", "email": f"pay-{org[:6]}@t.dev", "password": "pw-emp-12345",
        "wage_type": "fixed", "wage_value": 0}, headers=h)).json()["id"]
    r = await client.post("/api/payroll/run", headers=h, json={
        "employee_id": eid, "period_start": "2026-04-01", "period_end": "2026-04-30",
        "base_salary": 1000, "bonus": 50, "penalty": 20})
    assert r.status_code in (200, 201), r.text
    run = r.json()["id"]
    r = await client.post(f"/api/payroll/runs/{run}/pay", json={"account_id": till}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.post("/api/payroll/run", headers=h, json={
        "employee_id": eid, "period_start": "2026-05-01", "period_end": "2026-05-31", "base_salary": 900})
    voided = r.json()["id"]
    r = await client.post(f"/api/payroll/runs/{voided}/void", json={"reason": "wrong"}, headers=h)
    assert r.status_code == 200, r.text
    db.expire_all()
    return {"org": org, "loc": loc, "part": pid, "order": oid, "invoice": iid, "credit_note": cn,
            "write_off": wo, "purchase_return": pr, "posting": post_id, "run": run, "voided_run": voided}


def test_the_rest_is_registered():
    for name in REST:
        assert name in reconcile.CHECKS


@pytest.mark.asyncio
async def test_real_flows_reconcile_on_every_check(client, tenant, db):
    s = await _shop(client, tenant, db)
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []


async def _corrupt(db, s, check):
    """Move one stored value by +3 behind the API's back; return the entity id to expect."""
    if check == "invoice_total":
        await db.execute(update(Invoice).where(Invoice.id == s["invoice"]).values(total=Invoice.total + 3))
        return s["invoice"]
    if check == "invoice_credit":
        await db.execute(update(Invoice).where(Invoice.id == s["invoice"]).values(credited=Invoice.credited + 3))
        return s["invoice"]
    if check == "credit_note":
        await db.execute(update(CreditNote).where(CreditNote.id == s["credit_note"])
                         .values(total=CreditNote.total + 3))
        return s["credit_note"]
    if check == "purchase_return":
        await db.execute(update(PurchaseReturn).where(PurchaseReturn.id == s["purchase_return"])
                         .values(total=PurchaseReturn.total + 3))
        return s["purchase_return"]
    if check == "write_off":
        await db.execute(update(WriteOff).where(WriteOff.id == s["write_off"])
                         .values(total_cost=WriteOff.total_cost + 3))
        return s["write_off"]
    if check == "posting":
        from backend.models import Posting
        await db.execute(update(Posting).where(Posting.id == s["posting"]).values(total=13.0))
        return s["posting"]
    if check == "reserved":
        await db.execute(update(StockLevel).where(StockLevel.part_id == s["part"], StockLevel.location_id == s["loc"])
                         .values(qty_reserved=StockLevel.qty_reserved + 3))
        return f"{s['part']}@{s['loc']}"
    if check == "payroll_run":
        await db.execute(update(PayrollRun).where(PayrollRun.id == s["run"])
                         .values(gross_total=PayrollRun.gross_total + 3))
        return s["run"]
    await db.execute(update(Order).where(Order.id == s["order"])
                     .values(prepayment_total=Order.prepayment_total + 3))
    return s["order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("check", REST)
async def test_each_kind_of_drift_is_reported(client, tenant, db, check):
    s = await _shop(client, tenant, db)
    want = await _corrupt(db, s, check)
    await db.commit()
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    expected = [(check, want)]
    if check == "credit_note":
        # an issued note's total is also what its invoice counts as credited: both are named
        expected = [("invoice_credit", s["invoice"]), ("credit_note", want)]
    assert [(m.check, m.entity_id) for m in found] == expected, found
    assert found[-1].diff == pytest.approx(3, abs=0.01)


@pytest.mark.asyncio
async def test_payroll_ledger_legs_are_checked_against_the_run(client, tenant, db):
    """The run's figures are consistent with each other, but its base leg in the ledger isn't."""
    from backend.models import EmployeeBalanceLedger as L
    s = await _shop(client, tenant, db)
    await db.execute(update(L).where(L.ref_type == "payroll_run", L.ref_id == s["run"], L.entry_type == "base")
                     .values(amount=L.amount + 3))
    await db.commit()
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    assert [(m.check, m.entity_id) for m in found] == [("payroll_run", s["run"])], found


@pytest.mark.asyncio
async def test_a_voided_runs_reversal_must_cancel_it(client, tenant, db):
    from backend.models import EmployeeBalanceLedger as L
    s = await _shop(client, tenant, db)
    await db.execute(update(L).where(L.ref_type == "payroll_run_void", L.ref_id == s["voided_run"])
                     .values(amount=L.amount + 3))
    await db.commit()
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    assert [(m.check, m.entity_id) for m in found] == [("payroll_run", s["voided_run"])], found


@pytest.mark.asyncio
async def test_a_released_hold_no_longer_counts(client, tenant, db):
    from backend.models import StockReservation
    s = await _shop(client, tenant, db)
    hold = (await db.execute(select(StockReservation.id).where(StockReservation.order_id == s["order"]))).scalar_one()
    await db.execute(update(StockReservation).where(StockReservation.id == hold).values(released=True))
    await db.commit()
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    assert [(m.check, m.diff) for m in found] == [("reserved", pytest.approx(2))], found
