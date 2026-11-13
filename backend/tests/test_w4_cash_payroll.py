"""Wave 4 group C1 (cash / payroll follow-ups).

- Payroll void checks the cashbox through scoping.assert_account_allowed (the same check every other
  cash move uses). Its own inline check also read the role's account list, so a shop owner whose role
  happens to list cashboxes could pay a run from a till and then not void it.
- Reverse a supplier payment: the supplier owes-balance comes back, the till gets the money back with
  an opposite row, the contact ledger is evened out; idempotent, permission- and cashbox-checked,
  audited.
- The cash ledger tells the UI which rows are a split contact leg still waiting to be accepted
  (POST /cash/transactions/{id}/accept-split) and which supplier payments can be reversed.
- FIN-12: open_drawer_shift locks the open shift and re-checks it, so a payment never links to a
  drawer that a concurrent close has just closed.
"""
import uuid

from sqlalchemy import select, update

from backend.models import Account, AuditLog, CashShift, ClientLedgerEntry, Employee, Role, Supplier, Transaction
from backend.services import reconcile, scoping
from backend.tests.test_p1_money_audit import _acct, _balance, _loc, _me, _staff

# ── payroll void ────────────────────────────────────────────────────────────────────────────────

async def _paid_run(client, H, till):
    from backend.tests.test_batchC_payroll_money import _employee
    emp = await _employee(client, H)
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31",
        "base_salary": 1000}, headers=H)).json()
    r = await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till}, headers=H)
    assert r.status_code == 200, r.text
    return run["id"]


async def test_payroll_void_checks_the_cashbox_with_the_shared_helper(client, tenant, db, monkeypatch):
    till = await _acct(client, tenant, "Till")
    run = await _paid_run(client, tenant, till)
    seen = []
    real = scoping.assert_account_allowed

    def spy(session, account_id):
        seen.append(account_id)
        return real(session, account_id)
    monkeypatch.setattr(scoping, "assert_account_allowed", spy)
    r = await client.post(f"/api/payroll/runs/{run}/void", json={"reason": "wrong"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert till in seen, seen


async def test_the_owner_can_void_a_run_paid_from_a_till_their_role_does_not_list(client, tenant, db):
    """The owner is never cashbox-restricted (services/auth): they pay from any till, so they can
    void what they paid. The old inline check read the role list and refused."""
    till, safe = await _acct(client, tenant, "Till"), await _acct(client, tenant, "Safe")
    run = await _paid_run(client, tenant, till)
    me = await _me(client, tenant)
    role_id = (await db.execute(select(Employee.role_id).where(Employee.id == me["id"]))).scalar_one()
    assert role_id, "the shop owner has a role"
    await db.execute(update(Role).where(Role.id == role_id).values(account_ids=[safe]))
    await db.commit()
    r = await client.post(f"/api/payroll/runs/{run}/void", json={"reason": "wrong"}, headers=tenant)
    assert r.status_code == 200, r.text                    # was 403
    assert await _balance(db, till) == 0.0


# ── reverse a supplier payment ──────────────────────────────────────────────────────────────────

async def _supplier_paid(client, h, db, till, amount=120):
    await client.post("/api/cash/transactions", json={"account_id": till, "txn_type": "income",
                                                      "amount": 500}, headers=h)
    r = await client.post("/api/suppliers", json={"name": f"Parts {uuid.uuid4().hex[:4]}"}, headers=h)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    r = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": amount, "account_id": till}, headers=h)
    assert r.status_code == 200, r.text
    db.expire_all()
    leg = (await db.execute(select(Transaction).where(
        Transaction.ref_type == "supplier_payment", Transaction.ref_id == sid))).scalar_one()
    return sid, leg.id


async def _supplier_balance(db, sid):
    db.expire_all()
    return float((await db.execute(select(Supplier.balance).where(Supplier.id == sid))).scalar_one() or 0)


async def test_reversing_a_supplier_payment_puts_everything_back(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    sid, txn = await _supplier_paid(client, tenant, db, till)
    assert await _balance(db, till) == 380.0
    assert await _supplier_balance(db, sid) == -120.0

    r = await client.post(f"/api/suppliers/{sid}/payments/{txn}/reverse", json={"reason": ""}, headers=tenant)
    assert r.status_code == 400, r.text                     # a reason is required
    r = await client.post(f"/api/suppliers/{sid}/payments/{txn}/reverse", json={"reason": "paid twice"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    rev = r.json()["reversal_id"]
    assert await _balance(db, till) == 500.0
    assert await _supplier_balance(db, sid) == 0.0
    row = (await db.execute(select(Transaction).where(Transaction.id == rev))).scalar_one()
    assert row.reverses_txn_id == txn and float(row.amount) == 120.0
    orig = (await db.execute(select(Transaction).where(Transaction.id == txn))).scalar_one()
    assert orig.status == "corrected"                      # the payment itself stays on record
    s = (await db.execute(select(Supplier).where(Supplier.id == sid))).scalar_one()
    led = (await db.execute(select(ClientLedgerEntry.amount).where(
        ClientLedgerEntry.client_id == s.client_id, ClientLedgerEntry.ref_type == "supplier_payment"))).scalars().all()
    assert sorted(float(a) for a in led) == [-120.0, 120.0]
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "supplier.payment_reverse", AuditLog.entity_id == txn))).scalars().all()
    assert len(audit) == 1 and audit[0].diff["reason"] == "paid twice"
    assert await reconcile.find_mismatches(db, org_id=(await _me(client, tenant))["org_id"]) == []

    # idempotent: a second click returns the same reversal, nothing moves again
    r = await client.post(f"/api/suppliers/{sid}/payments/{txn}/reverse", json={"reason": "paid twice"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["reversal_id"] == rev
    assert await _balance(db, till) == 500.0
    assert await _supplier_balance(db, sid) == 0.0

    # the reversal row can't be undone from Cash on its own
    d = await client.delete(f"/api/cash/transactions/{rev}", headers=tenant)
    assert d.status_code == 409, d.text
    c = await client.post(f"/api/cash/transactions/{rev}/correct", json={"delta": 5, "reason": "x"}, headers=tenant)
    assert c.status_code == 409, c.text


async def test_reverse_supplier_payment_respects_the_cashbox_and_permissions(client, tenant, db):
    till, safe = await _acct(client, tenant, "Till"), await _acct(client, tenant, "Safe")
    sid, txn = await _supplier_paid(client, tenant, db, till)
    url = f"/api/suppliers/{sid}/payments/{txn}/reverse"
    other = await _staff(client, tenant, account_ids=[safe])
    r = await client.post(url, json={"reason": "x"}, headers=other)
    assert r.status_code == 403, r.text
    no_void = await _staff(client, tenant, deny=("can_delete_transactions",))
    r = await client.post(url, json={"reason": "x"}, headers=no_void)
    assert r.status_code == 403, r.text
    assert await _balance(db, till) == 380.0
    assert await _supplier_balance(db, sid) == -120.0

    # a payment of another supplier, or a cash row that isn't a supplier payment
    sid2 = (await client.post("/api/suppliers", json={"name": "Other"}, headers=tenant)).json()["id"]
    assert (await client.post(f"/api/suppliers/{sid2}/payments/{txn}/reverse", json={"reason": "x"},
                              headers=tenant)).status_code == 404
    manual = (await db.execute(select(Transaction.id).where(
        Transaction.account_id == till, Transaction.ref_type.is_(None)))).scalars().first() or \
        (await db.execute(select(Transaction.id).where(
            Transaction.account_id == till, Transaction.ref_type == "manual"))).scalars().first()
    assert (await client.post(f"/api/suppliers/{sid}/payments/{manual}/reverse", json={"reason": "x"},
                              headers=tenant)).status_code == 404


# ── the cash ledger flags pending split legs and reversible supplier payments ──────────────────

async def test_cash_ledger_flags_split_legs_and_reversible_supplier_payments(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    sid, txn = await _supplier_paid(client, tenant, db, till)
    org = (await _me(client, tenant))["org_id"]
    split = Transaction(org_id=org, account_id=till, txn_type="income", amount=50, ref_type="client",
                        ref_id="c1", status="deleted", void_reason="pre-#213 void")
    db.add(split)
    await db.commit()

    async def rows():
        r = await client.get(f"/api/cash/transactions?account_id={till}", headers=tenant)
        assert r.status_code == 200, r.text
        return {t["id"]: t for t in r.json()["items"]}

    got = await rows()
    assert got[split.id]["split_pending"] is True
    assert got[txn]["split_pending"] is False and got[txn]["supplier_payment_reversible"] is True
    assert all(t["supplier_payment_reversible"] is False for i, t in got.items() if i != txn)

    r = await client.post(f"/api/cash/transactions/{split.id}/accept-split", json={"reason": "settled"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/suppliers/{sid}/payments/{txn}/reverse", json={"reason": "x"}, headers=tenant)
    assert r.status_code == 200, r.text
    got = await rows()
    assert got[split.id]["split_pending"] is False
    assert got[txn]["supplier_payment_reversible"] is False
    assert not any(t["split_pending"] for t in got.values())


# ── FIN-12: a payment never links to a drawer that is closing ───────────────────────────────────

async def test_open_drawer_shift_locks_and_rechecks_the_shift(client, tenant, db, monkeypatch):
    from sqlalchemy.dialects import postgresql

    from backend.routers import cash
    from backend.services.dates import now_utc
    loc = await _loc(client, tenant)
    till = await _acct(client, tenant, "Till")
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 0}, headers=tenant)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    org = (await _me(client, tenant))["org_id"]
    acc = (await db.execute(select(Account).where(Account.id == till))).scalar_one()
    assert await cash.open_drawer_shift(db, org, acc) == sid

    locked = []
    real = db.execute

    async def racing(stmt, *a, **kw):
        if getattr(stmt, "_for_update_arg", None) is not None:
            locked.append(str(stmt.compile(dialect=postgresql.dialect())))
            # the close commits between picking the shift and locking it
            await real(update(CashShift).where(CashShift.id == sid).values(closed_at=now_utc()))
        return await real(stmt, *a, **kw)
    monkeypatch.setattr(db, "execute", racing)
    assert await cash.open_drawer_shift(db, org, acc) is None     # was the closing shift's id
    assert locked and ("FOR SHARE" in locked[0] or "FOR UPDATE" in locked[0]), locked
    assert "cash_shifts" in locked[0]
