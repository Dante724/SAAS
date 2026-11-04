"""Architecture review (Medium): money-moving POSTs that had no Idempotency-Key path.

For each one:
- three identical requests with one key, released together, post once (the others are told the
  first is still running, or get its answer);
- a retry with the same key and the same body gets the first answer back and posts nothing;
- the same key with a different body is refused (422), not replayed as if it were the same.

And card readers: two charges started on one reader at the same moment send one charge to the
reader; the other is a clean 409. The start check alone was check-then-insert, so both requests
could pass it; the partial unique index on the reader's open charge stops the second.

The races start every request at one barrier inside the request (its DB session is open, nothing
read yet), so on Postgres they really run side by side on separate connections.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from backend.db.database import get_db
from backend.models import CreditNote, PayrollRun, ProcessorPayout, Sale, TerminalCharge, Transaction
from main import app


# ── helpers ──────────────────────────────────────────────────────────────────
def _released_together(monkeypatch, parties: int):
    """Every request waits at one barrier once its DB session is open, so `parties` requests
    run their handlers at the same time."""
    barrier = asyncio.Barrier(parties)
    sessions = app.dependency_overrides.get(get_db, get_db)

    async def _db():
        async for s in sessions():
            try:
                await asyncio.wait_for(barrier.wait(), 10)
            except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
                pass
            yield s

    monkeypatch.setitem(app.dependency_overrides, get_db, _db)


async def _race(monkeypatch, send, parties=3):
    _released_together(monkeypatch, parties)
    try:
        return await asyncio.gather(*[send() for _ in range(parties)])
    finally:
        monkeypatch.undo()


async def _count(db, model, *where):
    db.expire_all()
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def _ok_once(rs, ok):
    codes = [r.status_code for r in rs]
    assert codes.count(ok) >= 1, [r.text for r in rs]
    assert all(c in (ok, 409) for c in codes), [r.text for r in rs]
    bodies = [r.json() for r in rs if r.status_code == ok]
    assert all(b == bodies[0] for b in bodies), bodies      # a replay is the first answer
    return bodies[0]


async def _till(client, h, name="Till", option="cash"):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _org(db, aid):
    from backend.models import Account
    return (await db.execute(select(Account.org_id).where(Account.id == aid))).scalar_one()


async def _check(client, h, monkeypatch, url, body, other_body, ok, posted):
    """The three behaviours for one endpoint. `posted()` counts what the endpoint writes."""
    key = f"k-{uuid.uuid4().hex}"
    hk = {**h, "Idempotency-Key": key}
    rs = await _race(monkeypatch, lambda: client.post(url, json=body, headers=hk))
    first = _ok_once(rs, ok)
    assert await posted() == 1
    again = await client.post(url, json=body, headers=hk)
    assert again.status_code == ok and again.json() == first, again.text
    assert await posted() == 1
    other = await client.post(url, json=other_body, headers=hk)
    assert other.status_code == 422, other.text
    assert await posted() == 1
    return first


# ── /api/cash/transactions ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_cash_entry_posts_once_per_key(client, tenant, db, monkeypatch):
    aid = await _till(client, tenant)
    body = {"account_id": aid, "txn_type": "income", "amount": 25, "comment": "idem-cash"}

    async def posted():
        return await _count(db, Transaction, Transaction.account_id == aid, Transaction.comment == "idem-cash")
    await _check(client, tenant, monkeypatch, "/api/cash/transactions", body, {**body, "amount": 26}, 201, posted)


# ── /api/cash/transfer ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_transfer_moves_money_once_per_key(client, tenant, db, monkeypatch):
    a1 = await _till(client, tenant)
    a2 = await _till(client, tenant, "Bank", "card")
    body = {"from_account_id": a1, "to_account_id": a2, "amount": 40, "comment": "idem-transfer"}

    async def posted():
        return await _count(db, Transaction, Transaction.account_id == a1, Transaction.txn_type == "transfer")
    first = await _check(client, tenant, monkeypatch, "/api/cash/transfer", body, {**body, "amount": 41}, 201, posted)
    assert first["debited"] == 40
    assert await _count(db, Transaction, Transaction.account_id == a2, Transaction.txn_type == "transfer") == 1


# ── /api/finance/payouts ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_processor_payout_is_recorded_once_per_key(client, tenant, db, monkeypatch):
    aid = await _till(client, tenant)
    org = await _org(db, aid)
    body = {"provider": "stripe", "gross_amount": 100, "fee_amount": 2.9, "reference": "po_idem"}

    async def posted():
        return await _count(db, ProcessorPayout, ProcessorPayout.org_id == org)
    await _check(client, tenant, monkeypatch, "/api/finance/payouts", body, {**body, "gross_amount": 101}, 201, posted)


# ── /api/payroll/runs/{id}/pay ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_payroll_run_is_paid_once_per_key(client, tenant, db, monkeypatch):
    till = await _till(client, tenant)
    till2 = await _till(client, tenant, "Safe")
    emp = (await client.post("/api/staff/employees", json={
        "name": "Tech", "email": f"tc-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"},
        headers=tenant)).json()["id"]
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31", "base_salary": 1000},
        headers=tenant)).json()

    async def posted():
        return await _count(db, Transaction, Transaction.ref_type == "payroll_run", Transaction.ref_id == run["id"])
    first = await _check(client, tenant, monkeypatch, f"/api/payroll/runs/{run['id']}/pay",
                         {"account_id": till}, {"account_id": till2}, 200, posted)
    assert first["status"] == "paid"
    db.expire_all()
    assert (await db.execute(select(PayrollRun.status).where(PayrollRun.id == run["id"]))).scalar_one() == "paid"


# ── /api/sales/{id}/pay ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_parked_sale_is_paid_once_per_key(client, tenant, db, monkeypatch):
    till = await _till(client, tenant)
    till2 = await _till(client, tenant, "Till 2")
    s = (await client.post("/api/sales", json={"lines": [
        {"kind": "service", "ref_id": None, "description": "Fit", "qty": 1, "unit_price": 30}]},
        headers=tenant)).json()
    assert s["paid"] is False

    async def posted():
        return await _count(db, Transaction, Transaction.ref_type == "sale", Transaction.ref_id == s["id"],
                            Transaction.txn_type == "income")
    first = await _check(client, tenant, monkeypatch, f"/api/sales/{s['id']}/pay",
                         {"account_id": till, "cash_tendered": 40}, {"account_id": till2}, 200, posted)
    assert first["paid"] is True and first["change_due"] == 10
    db.expire_all()
    assert (await db.execute(select(Sale.account_id).where(Sale.id == s["id"]))).scalar_one() == till


# ── /api/finance/credit-notes ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_credit_note_is_created_once_per_key(client, tenant, db, monkeypatch):
    aid = await _till(client, tenant)
    org = await _org(db, aid)
    cid = (await client.post("/api/clients", json={"name": "Credit client"}, headers=tenant)).json()["id"]
    body = {"client_id": cid, "reason": "Goodwill",
            "lines": [{"description": "Goodwill credit", "qty": 1, "unit_price": 15}]}
    other = {**body, "lines": [{"description": "Goodwill credit", "qty": 1, "unit_price": 16}]}

    async def posted():
        return await _count(db, CreditNote, CreditNote.org_id == org)
    first = await _check(client, tenant, monkeypatch, "/api/finance/credit-notes", body, other, 201, posted)
    assert first["status"] == "draft" and first["total"] == 15


# ── /api/payments/terminal/charge ────────────────────────────────────────────
async def _reader(client, h):
    r = await client.post("/api/payments/terminal/readers", json={"provider": "simulated", "label": "Front desk"},
                          headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _order(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid


@pytest.mark.asyncio
async def test_reader_charge_is_sent_once_per_key(client, tenant, db, monkeypatch):
    tid = await _reader(client, tenant)
    oid = await _order(client, tenant)
    oid2 = await _order(client, tenant)

    async def posted():
        return await _count(db, TerminalCharge, TerminalCharge.terminal_id == tid)
    first = await _check(client, tenant, monkeypatch, "/api/payments/terminal/charge",
                         {"terminal_id": tid, "order_id": oid}, {"terminal_id": tid, "order_id": oid2}, 201, posted)
    assert first["status"] == "pending" and first["amount"] == 100


@pytest.mark.asyncio
async def test_two_charges_at_once_on_one_reader_send_one(client, tenant, db, monkeypatch):
    """No key (two tills, or a double tap): both requests pass the "reader busy?" check together.
    Exactly one charge goes to the reader; the other is a 409 naming the reader."""
    from backend.services import terminal as svc
    tid = await _reader(client, tenant)
    oids = [await _order(client, tenant), await _order(client, tenant)]
    # Both requests read "nothing open on this reader" before either inserts its charge.
    barrier = asyncio.Barrier(2)
    orig = svc._org_currency

    async def held(*a, **kw):
        out = await orig(*a, **kw)
        try:
            await asyncio.wait_for(barrier.wait(), 10)
        except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
            pass
        return out
    monkeypatch.setattr(svc, "_org_currency", held)
    rs = await asyncio.gather(*[client.post("/api/payments/terminal/charge",
                                            json={"terminal_id": tid, "order_id": o}, headers=tenant) for o in oids])
    monkeypatch.undo()
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409], [r.text for r in rs]
    lost = next(r for r in rs if r.status_code == 409)
    assert "already in progress on this reader" in lost.json()["detail"]
    db.expire_all()
    assert await _count(db, TerminalCharge, TerminalCharge.terminal_id == tid) == 1
    assert await _count(db, TerminalCharge, TerminalCharge.terminal_id == tid, TerminalCharge.status == "pending") == 1
    # The reader is free again once that charge is cancelled.
    won = next(r for r in rs if r.status_code == 201).json()
    assert (await client.post(f"/api/payments/terminal/charges/{won['id']}/cancel", headers=tenant)).status_code == 200
    again = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oids[0]},
                              headers=tenant)
    assert again.status_code == 201, again.text


@pytest.mark.asyncio
async def test_migration_resolves_existing_duplicate_open_charges_without_deleting(client, tenant, db):
    """Data from before the index: a reader already holding several pending charges. The
    migration keeps the newest open, marks the older ones failed (never deletes a money record),
    and the unique index then builds."""
    import importlib.util
    from datetime import timedelta
    from pathlib import Path

    from sqlalchemy import text

    from backend.services.dates import now_utc
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "idg01_idempotency_gaps.py"
    spec = importlib.util.spec_from_file_location("idg01", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    tid, tid2 = await _reader(client, tenant), await _reader(client, tenant)
    oid = await _order(client, tenant)
    org = (await db.execute(text("SELECT org_id FROM payment_terminals WHERE id = :t"), {"t": tid})).scalar_one()
    await db.execute(text(f"DROP INDEX {mig.INDEX}"))      # the schema as it was before idg01
    t0 = now_utc().replace(tzinfo=None)
    rows = [("old", tid, "pending", 2), ("mid", tid, "pending", 1), ("new", tid, "pending", 0),
            ("done", tid, "succeeded", 0), ("other", tid2, "pending", 0)]
    ids = {name: f"{name}-{tid[:8]}" for name, *_ in rows}
    for name, t, st, hrs in rows:
        db.add(TerminalCharge(id=ids[name], org_id=org, terminal_id=t, provider="simulated",
                              order_id=oid, amount=10, status=st, created_at=t0 - timedelta(hours=hrs)))
    await db.commit()
    try:
        conn = await db.connection()
        assert await conn.run_sync(mig._resolve) == 2
        await db.commit()
    finally:
        await db.execute(text(f"CREATE UNIQUE INDEX {mig.INDEX} ON terminal_charges (terminal_id) "
                              "WHERE status = 'pending'"))
        await db.commit()
    db.expire_all()
    got = dict((await db.execute(select(TerminalCharge.id, TerminalCharge.status).where(
        TerminalCharge.org_id == org))).all())
    assert got == {ids["old"]: "failed", ids["mid"]: "failed", ids["new"]: "pending",
                   ids["done"]: "succeeded", ids["other"]: "pending"}
    err = (await db.execute(select(TerminalCharge.error).where(TerminalCharge.id == ids["old"]))).scalar_one()
    assert "Superseded" in err
