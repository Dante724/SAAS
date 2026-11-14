"""Final review BE-8 / BE-6: issuing and spending gift cards.

BE-8  POST /giftcards without account_id minted spendable value (a promotional card) for anyone
      with can_view_finance, for any amount; since FIN-09 that value settles jobs as a payment.
      Now: a sold card takes its money into a cashbox and needs the right to take payments; a
      promotional card (no money behind it) needs the owner-level can_manage_settings, as coupons
      do, and leaves an audit row; every card is capped at GIFT_CARD_MAX.
BE-6  redeem-giftcard, giftcard-refund and the card sale ignored Idempotency-Key: a retried
      redeem with an amount redeemed twice, a retried sale made two cards and two till legs.
"""
import pytest
from sqlalchemy import func, select

from backend.models import AuditLog, GiftCard, Transaction
from backend.tests.test_p1_money_audit import _acct, _balance, _client, _job, _staff

pytestmark = pytest.mark.asyncio


async def _cards(db, cid):
    db.expire_all()
    return (await db.execute(select(func.count()).select_from(GiftCard).where(GiftCard.client_id == cid))).scalar()


async def test_a_promotional_card_needs_the_manage_settings_right(client, tenant, db):
    cid = await _client(client, tenant)
    finance_only = await _staff(client, tenant, deny=("can_manage_settings",))
    r = await client.post("/api/giftcards", json={"value": 500, "client_id": cid}, headers=finance_only)
    assert r.status_code == 403, r.text                      # was 201: free money for a finance viewer
    assert await _cards(db, cid) == 0
    # the owner may, and it is audited as a promotion
    r = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["promotional"] is True
    db.expire_all()
    row = (await db.execute(select(AuditLog).where(AuditLog.action == "giftcard.promo_issue",
                                                   AuditLog.entity_id == r.json()["id"]))).scalar_one()
    assert row.diff["value"] == 50


async def test_selling_a_card_needs_the_take_payments_right(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    no_pay = await _staff(client, tenant, account_ids=[till], deny=("can_take_payments",))
    r = await client.post("/api/giftcards", json={"value": 40, "client_id": cid, "account_id": till}, headers=no_pay)
    assert r.status_code == 403, r.text
    seller = await _staff(client, tenant, account_ids=[till], deny=("can_manage_settings",))
    r = await client.post("/api/giftcards", json={"value": 40, "client_id": cid, "account_id": till}, headers=seller)
    assert r.status_code == 201, r.text
    assert r.json()["promotional"] is False and await _balance(db, till) == 40.0


async def test_a_card_has_an_upper_bound(client, tenant, db):
    from backend.routers.engage import GIFT_CARD_MAX
    cid = await _client(client, tenant)
    r = await client.post("/api/giftcards", json={"value": GIFT_CARD_MAX + 1, "client_id": cid}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/giftcards", json={"value": 1e15, "client_id": cid}, headers=tenant)
    assert r.status_code == 422, r.text
    assert await _cards(db, cid) == 0


async def test_a_retried_card_sale_makes_one_card_and_one_till_leg(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    h = {**tenant, "Idempotency-Key": "gc-sale-1"}
    body = {"value": 60, "client_id": cid, "account_id": till}
    a = await client.post("/api/giftcards", json=body, headers=h)
    b = await client.post("/api/giftcards", json=body, headers=h)
    assert a.status_code == 201 and b.status_code in (200, 201), b.text
    assert a.json()["code"] == b.json()["code"]
    assert await _cards(db, cid) == 1
    legs = (await db.execute(select(func.count()).select_from(Transaction)
                             .where(Transaction.account_id == till))).scalar()
    assert legs == 1 and await _balance(db, till) == 60.0


async def test_a_retried_redeem_and_give_back_run_once(client, tenant, db):
    cid = await _client(client, tenant)
    code = (await client.post("/api/giftcards", json={"value": 200, "client_id": cid}, headers=tenant)).json()["code"]
    oid = await _job(client, tenant, cid, price=150)
    h = {**tenant, "Idempotency-Key": "gc-redeem-1"}
    for _ in range(2):
        r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 40}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["card_balance"] == 160
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == 160   # was 120
    h = {**tenant, "Idempotency-Key": "gc-back-1"}
    for _ in range(2):
        r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code, "amount": 10}, headers=h)
        assert r.status_code == 200, r.text
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == 170   # not 180
