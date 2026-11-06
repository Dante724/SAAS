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




# ── migration idg01 ──────────────────────────────────────────────────────────
def _migration():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "idg01_idempotency_gaps.py"
    spec = importlib.util.spec_from_file_location("idg01", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


async def _drop_open_charge_indexes(db, mig):
    from sqlalchemy import text
    for name in mig.INDEXES:
        await db.execute(text(f"DROP INDEX IF EXISTS {name}"))
    await db.commit()


async def _run_upgrade(db, mig):
    """Run idg01's upgrade() the way alembic does, on its own connection."""
    def run(sync_conn):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        ctx = MigrationContext.configure(sync_conn)
        with ctx.begin_transaction():
            with Operations.context(ctx):
                mig.upgrade()
        if sync_conn.in_transaction():
            sync_conn.commit()
    async with db.bind.connect() as conn:
        await conn.run_sync(run)


async def _charge_indexes(db):
    import sqlalchemy as sa

    def names(c):
        return {i["name"] for i in sa.inspect(c).get_indexes("terminal_charges")}
    db.expire_all()
    async with db.bind.connect() as conn:
        return await conn.run_sync(names)


@pytest.mark.asyncio
async def test_migration_runs_again_after_a_failed_index_build(db):
    """Review #2: the column is committed before the CONCURRENTLY build, so a build that failed
    (e.g. a duplicate slipped in) leaves the column behind. Running the migration again must work."""
    mig = _migration()
    await _drop_open_charge_indexes(db, mig)            # the failed build: column there, no index
    await _run_upgrade(db, mig)
    assert set(mig.INDEXES) <= await _charge_indexes(db)
    await _run_upgrade(db, mig)                          # and once more, with everything in place
    assert set(mig.INDEXES) <= await _charge_indexes(db)


@pytest.mark.asyncio
async def test_migration_resolves_existing_duplicate_open_charges_without_deleting(client, tenant, db):
    """Data from before the indexes: a reader holding several pending charges, an order and a sale
    each pending on two readers. The newest pending stays open, the older ones are marked failed
    as superseded (never deleted: they are money records), and every unique index then builds."""
    from datetime import timedelta

    from sqlalchemy import text

    from backend.services.dates import now_utc
    mig = _migration()
    t1, t2, t3 = await _reader(client, tenant), await _reader(client, tenant), await _reader(client, tenant)
    o = [await _order(client, tenant) for _ in range(4)]
    sale = (await client.post("/api/sales", json={"lines": [
        {"kind": "service", "ref_id": None, "description": "Fit", "qty": 1, "unit_price": 30}]},
        headers=tenant)).json()["id"]
    org = (await db.execute(text("SELECT org_id FROM payment_terminals WHERE id = :t"), {"t": t1})).scalar_one()
    await _drop_open_charge_indexes(db, mig)             # the schema as it was before idg01
    t0 = now_utc().replace(tzinfo=None)
    rows = [  # name, reader, order, sale, status, hours ago
        ("old", t1, o[0], None, "pending", 3), ("mid", t1, o[1], None, "pending", 2),
        ("new", t1, o[2], None, "pending", 0), ("done", t1, o[0], None, "succeeded", 0),
        ("other", t2, o[3], None, "pending", 0), ("dup-order", t3, o[3], None, "pending", 1),
        ("sale-new", t3, None, sale, "pending", 0), ("sale-old", t2, None, sale, "failed", 4),
        ("sale-dup", t2, None, sale, "pending", 5)]
    ids = {name: f"{name}-{t1[:8]}" for name, *_ in rows}
    for name, t, oid, sid, st, hrs in rows:
        db.add(TerminalCharge(id=ids[name], org_id=org, terminal_id=t, provider="simulated",
                              order_id=oid, sale_id=sid, amount=10, status=st,
                              created_at=t0 - timedelta(hours=hrs)))
    await db.commit()
    await _run_upgrade(db, mig)
    db.expire_all()
    got = dict((await db.execute(select(TerminalCharge.id, TerminalCharge.status).where(
        TerminalCharge.org_id == org))).all())
    assert got == {ids["old"]: "failed", ids["mid"]: "failed", ids["new"]: "pending", ids["done"]: "succeeded",
                   ids["other"]: "pending", ids["dup-order"]: "failed", ids["sale-new"]: "pending",
                   ids["sale-old"]: "failed", ids["sale-dup"]: "failed"}
    for name in ("old", "mid", "dup-order", "sale-dup"):
        err = (await db.execute(select(TerminalCharge.error).where(TerminalCharge.id == ids[name]))).scalar_one()
        assert err.startswith("Superseded"), (name, err)
    assert set(mig.INDEXES) <= await _charge_indexes(db)


# ── review #6: one open charge per order / sale, across readers ──────────────
@pytest.mark.asyncio
async def test_two_readers_charging_one_order_at_once_send_one(client, tenant, db, monkeypatch):
    from backend.services import terminal as svc
    readers = [await _reader(client, tenant), await _reader(client, tenant)]
    oid = await _order(client, tenant)
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
                                            json={"terminal_id": t, "order_id": oid}, headers=tenant) for t in readers])
    monkeypatch.undo()
    assert sorted(r.status_code for r in rs) == [201, 409], [r.text for r in rs]
    lost = next(r for r in rs if r.status_code == 409)
    assert "already waiting on a reader" in lost.json()["detail"]
    assert await _count(db, TerminalCharge, TerminalCharge.order_id == oid) == 1


# ── review #3: the work committed, then the answer was lost ──────────────────
@pytest.mark.asyncio
async def test_key_is_kept_when_the_work_committed_before_failing(client, tenant, db, monkeypatch):
    from backend.routers import credit_notes as cnr
    aid = await _till(client, tenant)
    org = await _org(db, aid)
    cid = (await client.post("/api/clients", json={"name": "Credit client"}, headers=tenant)).json()["id"]
    body = {"client_id": cid, "reason": "Goodwill", "lines": [{"description": "Goodwill", "qty": 1, "unit_price": 15}]}
    hk = {**tenant, "Idempotency-Key": f"k-{uuid.uuid4().hex}"}
    orig, seen = cnr._detail, {"n": 0}

    async def answer_lost(*a, **kw):          # the draft is committed; building the answer fails
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("answer lost")
        return await orig(*a, **kw)
    monkeypatch.setattr(cnr, "_detail", answer_lost)
    try:
        r = await client.post("/api/finance/credit-notes", json=body, headers=hk)
        assert r.status_code >= 500
    except RuntimeError:
        pass
    assert await _count(db, CreditNote, CreditNote.org_id == org) == 1
    retry = await client.post("/api/finance/credit-notes", json=body, headers=hk)
    assert retry.status_code == 409, retry.text
    assert "already" in retry.json()["detail"]
    assert await _count(db, CreditNote, CreditNote.org_id == org) == 1        # no second draft


# ── review #8: a request that died mid-flight must not hold its key for 48h ──
async def _stale_claim(db, org, key, minutes):
    from datetime import datetime, timedelta

    from backend.models import IdempotencyRecord
    db.add(IdempotencyRecord(org_id=org, key=key, endpoint="cash.transaction",
                             created_at=datetime.utcnow() - timedelta(minutes=minutes)))
    await db.commit()


@pytest.mark.asyncio
async def test_a_dead_in_flight_key_can_be_retried_after_two_minutes(client, tenant, db):
    aid = await _till(client, tenant)
    org = await _org(db, aid)
    body = {"account_id": aid, "txn_type": "income", "amount": 25, "comment": "idem-stale"}
    fresh, stale = f"k-{uuid.uuid4().hex}", f"k-{uuid.uuid4().hex}"
    await _stale_claim(db, org, fresh, 0.5)      # still in flight: refused
    await _stale_claim(db, org, stale, 3)        # its request died 3 minutes ago: retry goes through
    r = await client.post("/api/cash/transactions", json=body, headers={**tenant, "Idempotency-Key": fresh})
    assert r.status_code == 409, r.text
    r = await client.post("/api/cash/transactions", json=body, headers={**tenant, "Idempotency-Key": stale})
    assert r.status_code == 201, r.text
    again = await client.post("/api/cash/transactions", json=body, headers={**tenant, "Idempotency-Key": stale})
    assert again.status_code == 201 and again.json() == r.json()
    assert await _count(db, Transaction, Transaction.account_id == aid, Transaction.comment == "idem-stale") == 1


@pytest.mark.asyncio
async def test_a_slow_original_cannot_post_after_its_key_was_taken_over(client, tenant, db, monkeypatch):
    """The original is still running past the 2-minute window when a retry takes its key over.
    The retry posts; the original's commit is refused, so the money is posted once."""
    from sqlalchemy import update

    from backend.models import IdempotencyRecord
    from backend.routers import cash
    aid = await _till(client, tenant)
    key = f"k-{uuid.uuid4().hex}"
    hk = {**tenant, "Idempotency-Key": key}
    body = {"account_id": aid, "txn_type": "income", "amount": 25, "comment": "idem-slow"}
    started, go = asyncio.Event(), asyncio.Event()
    orig = cash._create_transaction

    async def slow_first(*a, **kw):
        if not started.is_set():
            started.set()
            await asyncio.wait_for(go.wait(), 10)
        return await orig(*a, **kw)
    monkeypatch.setattr(cash, "_create_transaction", slow_first)
    first = asyncio.ensure_future(client.post("/api/cash/transactions", json=body, headers=hk))
    await asyncio.wait_for(started.wait(), 10)
    from datetime import datetime, timedelta
    await db.execute(update(IdempotencyRecord).where(IdempotencyRecord.key == key).values(
        created_at=datetime.utcnow() - timedelta(minutes=3)))           # the original looks dead
    await db.commit()
    retry = await client.post("/api/cash/transactions", json=body, headers=hk)
    assert retry.status_code == 201, retry.text
    go.set()
    late = await first
    assert late.status_code == 409, late.text
    assert await _count(db, Transaction, Transaction.account_id == aid, Transaction.comment == "idem-slow") == 1
    replay = await client.post("/api/cash/transactions", json=body, headers=hk)
    assert replay.status_code == 201 and replay.json() == retry.json()


# ── review #4/#5: provider errors and superseded charges (Stripe, mocked) ────
@pytest.fixture
def flaky_stripe(monkeypatch):
    import httpx

    from backend.config import settings
    from backend.services import terminal as svc
    from backend.tests.test_opf_card_readers import FakeStripe

    class Flaky(FakeStripe):
        fail_process = False      # process_payment_intent answers 500 (did the reader get it?)
        fail_cancel = False       # canceling the intent answers 500

        def __call__(self, req):
            p = req.url.path
            if self.fail_process and p.endswith("/process_payment_intent"):
                self.calls.append((req.method, p, {}))
                return httpx.Response(500, json={"error": {"message": "upstream timeout"}})
            if self.fail_cancel and p.startswith("/v1/payment_intents/") and p.endswith("/cancel"):
                self.calls.append((req.method, p, {}))
                return httpx.Response(500, json={"error": {"message": "upstream timeout"}})
            return super().__call__(req)

        def created(self):
            return sum(1 for m, p, _ in self.calls if m == "POST" and p == "/v1/payment_intents")

    fake = Flaky()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


async def _stripe_reader(client, h):
    r = await client.put("/api/integrations/credentials", json={"provider": "stripe", "data": {
        "secret_key": "sk_test_fake", "webhook_secret": "whsec_fake"}}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.post("/api/payments/terminal/readers", json={
        "provider": "stripe", "label": "Front desk",
        "address": {"line1": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_reader_that_does_not_answer_keeps_the_charge_waiting_and_a_retry_sends_nothing(
        client, tenant, db, flaky_stripe):
    """Review #4: the reader call failed in a way that doesn't say whether the reader got it
    (a 5xx or a timeout) and the intent couldn't be canceled. The card may still be taken, so the
    charge stays waiting (the poll and the webhook settle it) and a retry of the same tap gets
    that charge back instead of sending a second one."""
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    flaky_stripe.fail_process = flaky_stripe.fail_cancel = True
    hk = {**tenant, "Idempotency-Key": f"k-{uuid.uuid4().hex}"}
    r = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=hk)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending" and "reader" in (r.json()["error"] or "").lower()
    flaky_stripe.fail_process = flaky_stripe.fail_cancel = False
    again = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=hk)
    assert again.status_code == 201 and again.json()["id"] == r.json()["id"], again.text
    assert flaky_stripe.created() == 1                                       # one intent at Stripe
    assert await _count(db, TerminalCharge, TerminalCharge.terminal_id == tid) == 1


@pytest.mark.asyncio
async def test_reader_refusal_that_was_canceled_frees_the_key_for_a_new_try(client, tenant, db, flaky_stripe):
    """The reader call failed and the intent was canceled: nothing can be taken on it, so the
    tap failed cleanly and trying again (same key) sends a fresh charge."""
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    flaky_stripe.fail_process = True
    hk = {**tenant, "Idempotency-Key": f"k-{uuid.uuid4().hex}"}
    r = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=hk)
    assert r.status_code == 502, r.text
    flaky_stripe.fail_process = False
    again = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=hk)
    assert again.status_code == 201, again.text
    assert flaky_stripe.pi["pi_1"]["status"] == "canceled" and again.json()["status"] == "pending"


async def _superseded(db, charge_id, days_ago=0):
    from datetime import timedelta

    from sqlalchemy import update

    from backend.services.dates import now_utc
    mig = _migration()
    await db.execute(update(TerminalCharge).where(TerminalCharge.id == charge_id).values(
        status="failed", error=mig.NOTE, created_at=now_utc().replace(tzinfo=None) - timedelta(days=days_ago)))
    await db.commit()


@pytest.mark.asyncio
async def test_superseded_charge_captured_later_is_booked_and_flagged(client, tenant, db, flaky_stripe):
    """Review #5: a charge the migration marked superseded is still polled for 7 days, so a
    capture is booked (and flagged as a possible double payment) even without the webhook."""
    from backend.models import Payment
    from backend.services import terminal as svc
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    ch = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid},
                            headers=tenant)).json()
    await _superseded(db, ch["id"])
    flaky_stripe.fail_cancel = True                       # too late to cancel: the card was taken
    flaky_stripe.pi["pi_1"].update(status="succeeded", amount_received=flaky_stripe.pi["pi_1"]["amount"])
    assert await svc.reconcile_superseded(db) == 1
    db.expire_all()
    got = (await db.execute(select(TerminalCharge).where(TerminalCharge.id == ch["id"]))).scalar_one()
    assert got.status == "succeeded" and got.payment_id
    assert await _count(db, Payment, Payment.order_id == oid) == 1
    from backend.models import EventFeedItem
    notes = (await db.execute(select(EventFeedItem.body).where(EventFeedItem.entity_id == oid))).scalars().all()
    assert any("Card reader payment" in n and "double payment" in n for n in notes), notes


@pytest.mark.asyncio
async def test_superseded_charge_still_open_at_the_provider_is_canceled_there(client, tenant, db, flaky_stripe):
    from backend.models import Payment
    from backend.services import terminal as svc
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    ch = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid},
                            headers=tenant)).json()
    await _superseded(db, ch["id"])
    await svc.reconcile_superseded(db)
    assert flaky_stripe.pi["pi_1"]["status"] == "canceled"
    assert ("POST", "/v1/payment_intents/pi_1/cancel") in [(m, p) for m, p, _ in flaky_stripe.calls]
    assert await _count(db, Payment, Payment.order_id == oid) == 0
    # Older than 7 days: no longer polled.
    oid2 = await _order(client, tenant)
    flaky_stripe.calls.clear()
    ch2 = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid2},
                             headers=tenant)).json()
    await _superseded(db, ch2["id"], days_ago=8)
    flaky_stripe.calls.clear()
    await svc.reconcile_superseded(db)
    assert not [p for _, p, _ in flaky_stripe.calls if "pi_2" in p]
