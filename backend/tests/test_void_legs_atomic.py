"""Hardening council: a sale void racing a sale into the same till lost postings.

`void_document_legs` reversed each leg with `acc.balance = acc.balance - amount` (read, then
write at commit) while `post_transaction` adds atomically, so a sale that landed between the
void's read and its commit was overwritten by the stale value (live probe: balance 90, active
transactions 120). The void must subtract with the same atomic UPDATE the posting side uses.

The race test holds the void after it has read the till (before the audit write, which is the
first flush) until the new sale has posted and committed inside that window.
"""
import asyncio

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
