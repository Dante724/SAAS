"""Re-review money N4: the cashbox restriction blocked flows whose target account the cashier
doesn't choose: a cash drop into the safe, archiving a job paid by card, and an online payment
settled into the online account during a restricted request."""
from sqlalchemy import select

from backend.models import Account, PaymentIntent
from backend.services import scoping
from backend.tests.test_p1_money_audit import _acct, _balance, _income, _job, _staff


async def test_restricted_cashier_can_drop_cash_into_the_safe_but_not_take_it_out(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    await _income(client, tenant, till, 200)
    s = await _staff(client, tenant, account_ids=[till])
    r = await client.post("/api/cash/transfer", json={"from_account_id": till, "to_account_id": safe,
                                                      "amount": 150}, headers=s)
    assert r.status_code == 201, r.text
    assert (await _balance(db, till), await _balance(db, safe)) == (50.0, 150.0)
    r = await client.post("/api/cash/transfer", json={"from_account_id": safe, "to_account_id": till,
                                                      "amount": 10}, headers=s)
    assert r.status_code == 403, r.text


async def test_restricted_cashier_can_archive_a_job_paid_by_card(client, tenant, db):
    till = await _acct(client, tenant, "Front till")
    card = await _acct(client, tenant, "Card terminal", option="card")
    oid = await _job(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "card", "account_id": card,
                                                               "amount": 60}, headers=tenant)
    assert r.status_code == 200, r.text
    s = await _staff(client, tenant, account_ids=[till])
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "cancelled", "refund": True}, headers=s)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 60
    assert await _balance(db, card) == 0.0


async def test_online_payment_settles_into_the_online_account_inside_a_restricted_request(client, tenant, db):
    till = await _acct(client, tenant, "Front till")
    oid = await _job(client, tenant)
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 30}, headers=tenant)
    assert r.status_code == 201, r.text
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == r.json()["id"]))).scalar_one()
    from backend.services import payments as pay
    scoping.bind_account_scope(db, [till])     # as a cashier limited to the till would be
    try:
        intent = await pay.mark_succeeded(db, intent)
        assert intent.status == "succeeded"
        assert scoping.session_allowed_accounts(db) == frozenset([till])   # scope put back
    finally:
        scoping.bind_account_scope(db, None)
    db.expire_all()
    online = (await db.execute(select(Account).where(Account.balance == 30))).scalars().all()
    assert len(online) == 1 and online[0].id != till
