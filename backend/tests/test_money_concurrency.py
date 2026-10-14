"""Money: running balances and one-shot reconciliations survive concurrency (security phase 3).

Regressions for audit findings B12, B18, B19:
- till/client/supplier balances were read-modify-write, so concurrent updates lost money;
- mark_succeeded had no claim (webhook + refresh could post twice) and never touched the client
  ledger, so a customer who paid online still showed the full amount owed;
- coupon use counts were incremented in Python, racing past max_uses.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Account, Client, Organisation, PaymentIntent
from backend.routers.cash import post_transaction
from backend.tests.test_money_hardening import _account, _order_with_total


@pytest.mark.asyncio
async def test_concurrent_till_updates_do_not_lose_money(client, tenant, db):
    TestSession = async_sessionmaker(db.bind, expire_on_commit=False)   # a second, independent session
    aid = await _account(client, tenant)
    org_id = (await db.execute(select(Organisation))).scalars().first().id
    stale = (await db.execute(select(Account).where(Account.id == aid))).scalar_one()   # session A sees 0
    async with TestSession() as other:                                                     # session B
        await post_transaction(other, org_id, aid, "income", 50, comment="B")
        await other.commit()
    await post_transaction(db, org_id, aid, "income", 100, comment="A")                  # A, stale copy
    await db.commit()
    async with TestSession() as check:
        bal = (await check.execute(select(Account.balance).where(Account.id == aid))).scalar_one()
    assert float(bal) == 150.0 and float(stale.balance) == 150.0


@pytest.mark.asyncio
async def test_online_payment_posts_to_client_ledger_and_runs_once(client, tenant, db):
    cid, oid = await _order_with_total(client, tenant, 100)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    owed = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert owed == 100.0
    link = (await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})).json()
    for _ in range(2):        # a retried webhook / refresh must not post twice
        await client.post(f"/api/payments/intents/{link['intent_id']}/simulate-pay")
    after = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert round(float(after), 2) == 0.0, after
    db.expire_all()
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == link["intent_id"]))).scalar_one()
    assert i.status == "succeeded" and i.payment_id


@pytest.mark.asyncio
async def test_coupon_cannot_exceed_max_uses(client, tenant):
    r = await client.post("/api/coupons", headers=tenant,
                          json={"code": "ONCE", "kind": "fixed", "value": 5, "max_uses": 1})
    assert r.status_code == 201, r.text
    _c1, o1 = await _order_with_total(client, tenant, 50)
    _c2, o2 = await _order_with_total(client, tenant, 50)
    assert (await client.post(f"/api/orders/{o1}/apply-coupon", headers=tenant, json={"code": "ONCE"})).status_code == 200
    second = await client.post(f"/api/orders/{o2}/apply-coupon", headers=tenant, json={"code": "ONCE"})
    assert second.status_code in (400, 409), second.text


@pytest.mark.asyncio
async def test_client_balance_update_is_atomic(client, tenant, db):
    from backend.services.ledger import post_contact_ledger
    TestSession = async_sessionmaker(db.bind, expire_on_commit=False)
    cid = (await client.post("/api/clients", json={"name": "Race"}, headers=tenant)).json()["id"]
    org_id = (await db.execute(select(Organisation))).scalars().first().id
    (await db.execute(select(Client).where(Client.id == cid))).scalar_one()        # stale copy in A
    async with TestSession() as other:
        await post_contact_ledger(other, org_id, cid, "charge", 30)
        await other.commit()
    await post_contact_ledger(db, org_id, cid, "charge", 20)
    await db.commit()
    async with TestSession() as check:
        bal = (await check.execute(select(Client.balance).where(Client.id == cid))).scalar_one()
    assert float(bal) == 50.0
