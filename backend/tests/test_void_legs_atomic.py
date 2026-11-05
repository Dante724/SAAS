"""Hardening council: a sale void racing a sale into the same till lost postings.

`void_document_legs` reversed each leg with `acc.balance = acc.balance - amount` (read, then
write at commit) while `post_transaction` adds atomically, so a sale that landed between the
void's read and its commit was overwritten by the stale value (live probe: balance 90, active
transactions 120). The void must subtract with the same atomic UPDATE the posting side uses.

The race test holds the void after it has read the till (before the audit write, which is the
first flush) until the new sale has posted and committed inside that window.
"""
import asyncio
import os

import pytest
from sqlalchemy import func, select

from backend.models import Transaction

BODY_LINE = {"kind": "service", "description": "Svc", "qty": 1, "unit_price": 10}


def _hold(monkeypatch, module, name, started, released, **only):
    """Wrap an async helper so that, on the call matching `only`, it signals `started` and
    waits (bounded) for `released` before running — the other racer gets its turn inside the
    window between the caller's reads and its first flush. Bounded, so code that already holds
    the row lock (the fixed path: the other racer then blocks on it) still completes."""
    orig = getattr(module, name)

    async def held(*a, **kw):
        if all(kw.get(k) == v for k, v in only.items()):
            started.set()
            await asyncio.wait({asyncio.ensure_future(released.wait())}, timeout=1.5)
        return await orig(*a, **kw)

    monkeypatch.setattr(module, name, held)


async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, h, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
                if a["id"] == aid)["balance"]


async def _ledger(db, aid):
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.account_id == aid,
        func.coalesce(Transaction.status, "active") == "active"))).scalar() or 0)


@pytest.mark.asyncio
async def test_till_balance_matches_ledger_when_void_races_sale(client, tenant, db, monkeypatch):
    from backend.services import audit
    acc = await _till(client, tenant)
    body = {"account_id": acc, "lines": [BODY_LINE]}
    first = await client.post("/api/sales", json=body, headers=tenant)
    assert first.status_code == 201, first.text

    # The void has read the till but not yet written it when the second sale lands in full.
    voided_read, sale_done = asyncio.Event(), asyncio.Event()
    _hold(monkeypatch, audit, "record", voided_read, sale_done, action="sale.delete")

    async def sell():
        await voided_read.wait()
        try:
            return await client.post("/api/sales", json=body, headers=tenant)
        finally:
            sale_done.set()

    dele, sale = await asyncio.gather(
        client.delete(f"/api/sales/{first.json()['id']}", params={"reason": "oops"}, headers=tenant),
        sell())
    assert dele.status_code == 200, dele.text
    assert sale.status_code == 201, sale.text

    assert await _ledger(db, acc) == pytest.approx(10)
    assert await _balance(client, tenant, acc) == pytest.approx(10)


@pytest.mark.asyncio
async def test_void_legs_subtract_each_leg_once(client, tenant, db):
    """Plain (unraced) void still brings the till back to its ledger."""
    acc = await _till(client, tenant)
    body = {"account_id": acc, "lines": [BODY_LINE]}
    s = (await client.post("/api/sales", json=body, headers=tenant)).json()["id"]
    await client.post("/api/sales", json=body, headers=tenant)
    r = await client.delete(f"/api/sales/{s}", params={"reason": "oops"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _ledger(db, acc) == pytest.approx(10)
    assert await _balance(client, tenant, acc) == pytest.approx(10)


# ── Review (rev14): two voids over the same two tills must not deadlock ──────────────────────
# `void_document_legs` walked a document's legs in whatever order Postgres returned them, so two
# voids whose documents hit tills A and B in opposite order each took one till's row lock and then
# waited for the other's: Postgres broke the deadlock by failing one request (500). Reversing the
# legs in account order makes every void take the tills in the same sequence, so the second simply
# queues behind the first. Postgres-only: SQLite has no row locks to deadlock on.
_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


async def _split_sale(client, h, db, first, second):
    """A sale taken into `first`, with a second leg posted into `second` the way a split tender
    lands (the API posts one leg per sale today; the void walks every leg of the document)."""
    from backend.models import Sale
    from backend.routers.cash import post_transaction
    r = await client.post("/api/sales", json={"account_id": first, "lines": [BODY_LINE]}, headers=h)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    org_id = (await db.execute(select(Sale.org_id).where(Sale.id == sid))).scalar_one()
    await post_transaction(db, org_id, second, "income", 10, ref_type="sale", ref_id=sid, comment="split")
    await db.commit()
    return sid


@pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row-lock deadlock needs Postgres")
@pytest.mark.asyncio
async def test_opposite_order_split_voids_do_not_deadlock(client, tenant, db, monkeypatch):
    from backend.routers import cash
    till_a, till_b = await _till(client, tenant), await _till(client, tenant)
    s1 = await _split_sale(client, tenant, db, till_a, till_b)   # legs posted A then B
    s2 = await _split_sale(client, tenant, db, till_b, till_a)   # legs posted B then A

    # Hold each void after its first till update long enough for the other void to take its own
    # first till, so both are mid-document when they reach the second till.
    orig = cash.add_to_column

    async def slow_add(db_, obj, attr, delta, **kw):
        out = await orig(db_, obj, attr, delta, **kw)
        await asyncio.sleep(0.3)
        return out

    monkeypatch.setattr(cash, "add_to_column", slow_add)
    r1, r2 = await asyncio.gather(
        client.delete(f"/api/sales/{s1}", params={"reason": "oops"}, headers=tenant),
        client.delete(f"/api/sales/{s2}", params={"reason": "oops"}, headers=tenant),
        return_exceptions=True)
    for r in (r1, r2):
        assert not isinstance(r, BaseException), repr(r)
        assert r.status_code == 200, r.text
    assert await _ledger(db, till_a) == pytest.approx(0)
    assert await _ledger(db, till_b) == pytest.approx(0)
    assert await _balance(client, tenant, till_a) == pytest.approx(0)
    assert await _balance(client, tenant, till_b) == pytest.approx(0)
