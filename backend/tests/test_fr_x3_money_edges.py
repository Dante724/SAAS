"""Final review (13 Nov 2026), fix group X3: money edges.

- BE-1: SQLAlchemy fires before_commit / after_commit when a SAVEPOINT is released (every
  audit.record runs in one). The idempotency claim took that as the work's commit, so a request
  that failed after a savepoint burned its key: the retry was told "already carried out" although
  nothing was saved.
- BE-5 / SEC-21: an idem_begin call without body= let the same key with a different request
  (another amount, another order) replay the first answer. The key is now bound to the method,
  path and body of the request in services/idempotency.py itself, for every caller.
- BE-2: Payment.method was free text on Take payment, prepayment and Record payment. A cash
  payment posted as "gift_card" was left out of money-in and could never be refunded.
- BE-3: a date-only paid_at / actual_date was stored as UTC midnight, so west of UTC a payment
  backdated to day D was reported on D-1 (and east of UTC far ahead, e.g. +14, it still landed
  on D only by luck of the hour).
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from backend.models import AuditLog, OrderStatus, Organisation, Payment, Transaction
from backend.services.dates import day_end_utc, day_start_utc, now_utc, today_in
from backend.tests.test_p1_money_audit import _acct, _client, _job, _me

# ── BE-1 ──────────────────────────────────────────────────────────────────────────────────────

async def test_be1_savepoint_release_does_not_burn_the_key(client, tenant, db):
    """Work that releases a savepoint (audit.record) and then fails leaves the key free: the retry
    runs the work again instead of being told it was already carried out."""
    from backend.services import audit
    from backend.services.idempotency import run_once
    me = await _me(client, tenant)
    req = SimpleNamespace(headers={"Idempotency-Key": "be1-savepoint"})
    calls = []

    async def work():
        calls.append(1)
        await audit.record(db, org_id=me["org_id"], actor_id=None, action="be1.probe", entity_type="x")
        raise HTTPException(400, "validation failed after an audit row")

    for _ in range(2):
        with pytest.raises(HTTPException) as e:
            await run_once(db, me["org_id"], req, "be1.ep", {"a": 1}, work)
        assert e.value.status_code == 400, e.value.detail
    assert len(calls) == 2
    if db.get_bind().dialect.name == "postgresql":
        # (pysqlite emits no BEGIN before a SAVEPOINT, so on SQLite its RELEASE already commits.)
        db.expire_all()
        assert not (await db.execute(select(AuditLog).where(AuditLog.action == "be1.probe"))).scalars().all()


async def test_be1_real_commit_still_keeps_the_key(client, tenant, db):
    """The guard is only for savepoints: work that really committed and then failed is never re-run."""
    from backend.services import audit
    from backend.services.idempotency import DONE_NO_ANSWER, run_once
    me = await _me(client, tenant)
    req = SimpleNamespace(headers={"Idempotency-Key": "be1-real-commit"})
    calls = []

    async def work():
        calls.append(1)
        await audit.record(db, org_id=me["org_id"], actor_id=None, action="be1.kept", entity_type="x")
        await db.commit()
        raise RuntimeError("answer lost after the commit")

    with pytest.raises(RuntimeError):
        await run_once(db, me["org_id"], req, "be1.ep2", {"a": 1}, work)
    with pytest.raises(HTTPException) as e:
        await run_once(db, me["org_id"], req, "be1.ep2", {"a": 1}, work)
    assert e.value.status_code == 409 and e.value.detail == DONE_NO_ANSWER
    assert len(calls) == 1


async def test_be1_refused_finish_can_be_retried_with_the_same_key(client, tenant, db):
    """Finish & take 40 of 100 into a status that needs full payment is refused; nothing is saved,
    so the retry with the same key is refused for the same reason, not "already carried out"."""
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, price=100)
    me = await _me(client, tenant)
    db.expire_all()
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == me["org_id"], OrderStatus.record_type == "work_order",
        OrderStatus.status_group == "closed", OrderStatus.active == True))).scalars().first()  # noqa: E712
    st.require_payment = True
    await db.commit()
    h = {**tenant, "Idempotency-Key": "be1-finish"}
    body = {"status": st.key, "payment": {"method": "cash", "account_id": till, "amount": 40}}
    r1 = await client.post(f"/api/orders/{oid}/finish", json=body, headers=h)
    assert r1.status_code in (400, 409), r1.text
    r2 = await client.post(f"/api/orders/{oid}/finish", json=body, headers=h)
    assert r2.status_code == r1.status_code, r2.text
    assert "already been carried out" not in r2.text
    db.expire_all()
    assert not (await db.execute(select(Payment).where(Payment.order_id == oid))).scalars().all()


# ── BE-5 / SEC-21 ─────────────────────────────────────────────────────────────────────────────

async def test_be5_same_key_other_amount_is_refused_not_replayed(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant, price=100)
    h = {**tenant, "Idempotency-Key": "be5-pay"}
    r1 = await client.post(f"/api/cash/orders/{oid}/pay",
                           json={"method": "cash", "account_id": till, "amount": 40}, headers=h)
    assert r1.status_code == 200, r1.text
    again = await client.post(f"/api/cash/orders/{oid}/pay",
                              json={"method": "cash", "account_id": till, "amount": 40}, headers=h)
    assert again.status_code == 200 and again.json() == r1.json()      # a true retry still replays
    r2 = await client.post(f"/api/cash/orders/{oid}/pay",
                           json={"method": "cash", "account_id": till, "amount": 60}, headers=h)
    assert r2.status_code == 422, r2.text


async def test_be5_same_key_other_order_is_refused(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    a = await _job(client, tenant, price=100)
    b = await _job(client, tenant, price=100)
    h = {**tenant, "Idempotency-Key": "be5-other-order"}
    body = {"method": "cash", "account_id": till, "amount": 100}
    assert (await client.post(f"/api/cash/orders/{a}/pay", json=body, headers=h)).status_code == 200
    r = await client.post(f"/api/cash/orders/{b}/pay", json=body, headers=h)
    assert r.status_code == 422, r.text
    db.expire_all()
    assert not (await db.execute(select(Payment).where(Payment.order_id == b))).scalars().all()


async def test_be5_finish_key_bound_to_body(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant, price=100)
    h = {**tenant, "Idempotency-Key": "be5-finish"}
    r1 = await client.post(f"/api/orders/{oid}/finish",
                           json={"payment": {"method": "cash", "account_id": till, "amount": 40}}, headers=h)
    assert r1.status_code == 200, r1.text
    r2 = await client.post(f"/api/orders/{oid}/finish",
                           json={"payment": {"method": "cash", "account_id": till, "amount": 100}}, headers=h)
    assert r2.status_code == 422, r2.text


# ── BE-2 ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["gift_card", "refund", "anything"])
async def test_be2_take_payment_refuses_reserved_methods(client, tenant, db, method):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant, price=100)
    r = await client.post(f"/api/cash/orders/{oid}/pay",
                          json={"method": method, "account_id": till, "amount": 100}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post(f"/api/cash/orders/{oid}/prepay",
                          json={"method": method, "account_id": till, "amount": 50}, headers=tenant)
    assert r.status_code == 422, r.text
    db.expire_all()
    assert not (await db.execute(select(Payment).where(Payment.order_id == oid))).scalars().all()


async def test_be2_allowed_methods_still_work(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    card = await _acct(client, tenant, "Card", option="card")
    oid = await _job(client, tenant, price=100)
    for method, acc, amt in (("cash", till, 30), ("card", card, 30), ("cashless", card, 10)):
        r = await client.post(f"/api/cash/orders/{oid}/pay",
                              json={"method": method, "account_id": acc, "amount": amt}, headers=tenant)
        assert r.status_code == 200, r.text
    r = await client.post(f"/api/cash/orders/{oid}/prepay",
                          json={"method": "card", "account_id": card, "amount": 15}, headers=tenant)
    assert r.status_code == 201, r.text


async def _invoice(client, h, price=100):
    cid = await _client(client, h)
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": price}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.parametrize("method", ["gift_card", "refund", "anything"])
async def test_be2_record_payment_refuses_reserved_methods(client, tenant, db, method):
    till = await _acct(client, tenant, "Till")
    inv = await _invoice(client, tenant)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": method, "amount": 100, "account_id": till}, headers=tenant)
    assert r.status_code == 422, r.text


async def test_be2_record_payment_bank_transfer_still_works(client, tenant, db):
    bank = await _acct(client, tenant, "Bank", option="cashless")
    inv = await _invoice(client, tenant)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": "transfer", "amount": 100, "account_id": bank}, headers=tenant)
    assert r.status_code == 200, r.text


# ── BE-3 ──────────────────────────────────────────────────────────────────────────────────────

async def _set_tz(client, tenant, db, tz):
    me = await _me(client, tenant)
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"]).values(timezone=tz))
    await db.commit()
    return me["org_id"]


@pytest.mark.parametrize("tz", ["America/Los_Angeles", "Pacific/Kiritimati"])
async def test_be3_backdated_invoice_payment_lands_on_the_shop_day(client, tenant, db, tz):
    from backend.services.revenue import cash_collected
    org_id = await _set_tz(client, tenant, db, tz)
    till = await _acct(client, tenant, "Till")
    inv = await _invoice(client, tenant)
    day = today_in(tz) - timedelta(days=3)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": "cash", "amount": 100, "account_id": till, "paid_at": day.isoformat()},
                          headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    p = (await db.execute(select(Payment).where(Payment.invoice_id == inv["id"]))).scalars().first()
    assert day_start_utc(day, tz) <= p.paid_at < day_end_utc(day, tz), p.paid_at
    on_day = await cash_collected(db, org_id, day_start_utc(day, tz), day_end_utc(day, tz))
    assert on_day["in"] == 100
    before = day - timedelta(days=1)
    assert (await cash_collected(db, org_id, day_start_utc(before, tz), day_end_utc(before, tz)))["in"] == 0
    txn = (await db.execute(select(Transaction).where(
        Transaction.org_id == org_id, Transaction.account_id == till))).scalars().first()
    assert day_start_utc(day, tz) <= txn.actual_date < day_end_utc(day, tz), txn.actual_date


@pytest.mark.parametrize("tz", ["America/Los_Angeles", "Pacific/Kiritimati"])
async def test_be3_backdated_cash_entry_lands_on_the_shop_day(client, tenant, db, tz):
    org_id = await _set_tz(client, tenant, db, tz)
    till = await _acct(client, tenant, "Till")
    day = today_in(tz) - timedelta(days=2)
    r = await client.post("/api/cash/transactions", json={
        "account_id": till, "txn_type": "expense", "amount": 25, "actual_date": day.isoformat()}, headers=tenant)
    assert r.status_code == 201, r.text
    db.expire_all()
    txn = (await db.execute(select(Transaction).where(
        Transaction.org_id == org_id, Transaction.account_id == till))).scalars().one()
    assert day_start_utc(day, tz) <= txn.actual_date < day_end_utc(day, tz), txn.actual_date
    assert txn.is_retroactive is True


@pytest.mark.parametrize("tz", ["America/Los_Angeles", "Pacific/Kiritimati"])
async def test_be3_todays_date_is_now_not_midnight(client, tenant, db, tz):
    org_id = await _set_tz(client, tenant, db, tz)
    till = await _acct(client, tenant, "Till")
    r = await client.post("/api/cash/transactions", json={
        "account_id": till, "txn_type": "income", "amount": 25, "actual_date": today_in(tz).isoformat()},
        headers=tenant)
    assert r.status_code == 201, r.text
    db.expire_all()
    txn = (await db.execute(select(Transaction).where(
        Transaction.org_id == org_id, Transaction.account_id == till))).scalars().one()
    assert abs(txn.actual_date - now_utc()) < timedelta(minutes=5), txn.actual_date
    assert txn.is_retroactive is False


async def test_be3_future_shop_day_is_refused(client, tenant, db):
    """Kiritimati is up to a day ahead of UTC: its today is accepted, its tomorrow is not."""
    tz = "Pacific/Kiritimati"
    await _set_tz(client, tenant, db, tz)
    till = await _acct(client, tenant, "Till")
    tomorrow = today_in(tz) + timedelta(days=1)
    r = await client.post("/api/cash/transactions", json={
        "account_id": till, "txn_type": "income", "amount": 25, "actual_date": tomorrow.isoformat()},
        headers=tenant)
    assert r.status_code == 422, r.text


async def test_be3_an_explicit_instant_is_kept(client, tenant, db):
    """A value with a zone is an instant: stored as that instant in UTC."""
    tz = "America/Los_Angeles"
    org_id = await _set_tz(client, tenant, db, tz)
    till = await _acct(client, tenant, "Till")
    when = (now_utc() - timedelta(days=2)).replace(microsecond=0)
    r = await client.post("/api/cash/transactions", json={
        "account_id": till, "txn_type": "income", "amount": 25, "actual_date": when.isoformat() + "+00:00"},
        headers=tenant)
    assert r.status_code == 201, r.text
    db.expire_all()
    txn = (await db.execute(select(Transaction).where(
        Transaction.org_id == org_id, Transaction.account_id == till))).scalars().one()
    assert txn.actual_date == when

