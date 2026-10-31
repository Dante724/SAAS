"""Money integrity: one definition of "paid" for every path (security phase 3).

Regressions for audit findings B1, B3, B7, B11, B12, B14, B17:
- refunds via /returns/refunds were invisible to the refund cap (repeatable till drain);
- pay links used total − prepayment_total, so in-store payments didn't count (double charge);
- invoice payments accepted negatives/overpayment and weren't linked to the order;
- refund_overpayment paid out any amount; gift-card redemption was uncapped;
- archive refunds left phantom store credit; float sums left paid invoices "partial".
"""
import pytest

from backend.tests.test_money_hardening import _account, _order_with_total


async def _pay(client, h, oid, aid, amount):
    return await client.post(f"/api/cash/orders/{oid}/pay", headers=h,
                             json={"method": "cash", "account_id": aid, "amount": amount})


async def _refund(client, h, aid, amount, **kw):
    return await client.post("/api/returns/refunds", headers=h, json={"account_id": aid, "amount": amount, **kw})


@pytest.mark.asyncio
async def test_refunds_cannot_be_repeated_past_what_was_paid(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    assert (await _pay(client, tenant, oid, aid, 100)).status_code in (200, 201)
    assert (await _refund(client, tenant, aid, 100, order_id=oid)).status_code == 201
    again = await _refund(client, tenant, aid, 100, order_id=oid)
    assert again.status_code == 400, again.text
    assert (await _refund(client, tenant, aid, 1, order_id=oid)).status_code == 400


@pytest.mark.asyncio
async def test_refund_then_archive_does_not_refund_twice(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 100)
    assert (await _refund(client, tenant, aid, 100, order_id=oid)).status_code == 201
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=tenant)
    assert r.status_code in (200, 201), r.text
    assert float(r.json().get("refunded", 0) or 0) == 0


@pytest.mark.asyncio
async def test_refund_must_reference_an_order(client, tenant):
    aid = await _account(client, tenant)
    assert (await _refund(client, tenant, aid, 10)).status_code == 400


@pytest.mark.asyncio
async def test_pay_link_reflects_in_store_payment(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 100)
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})
    assert r.status_code == 400, r.text          # nothing to collect — not a second charge


@pytest.mark.asyncio
async def test_settling_in_store_cancels_an_open_pay_link(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    link = await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})
    assert link.status_code == 201, link.text
    await _pay(client, tenant, oid, aid, 100)
    st = (await client.get(f"/api/public/pay/{link.json()['intent_id']}")).json()
    assert st["status"] == "canceled"


@pytest.mark.asyncio
async def test_overpayment_refund_is_capped(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 100)
    for amount in (50, -50):          # nothing overpaid; negatives never allowed
        r = await client.post(f"/api/cash/orders/{oid}/refund", headers=tenant,
                              json={"amount": amount, "account_id": aid, "method": "cash"})
        assert r.status_code == 400, (amount, r.text)


@pytest.mark.asyncio
async def test_invoice_payment_validation_and_order_link(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    inv = await client.post("/api/finance/invoices", headers=tenant, json={"order_id": oid})
    assert inv.status_code in (200, 201), inv.text
    iid = inv.json()["id"]
    pay = f"/api/finance/invoices/{iid}/pay"
    assert (await client.post(pay, headers=tenant, json={"amount": -100, "method": "cash", "account_id": aid})).status_code == 400
    total = float(inv.json().get("total") or 100)
    assert (await client.post(pay, headers=tenant, json={"amount": total + 50, "method": "cash", "account_id": aid})).status_code == 400
    assert (await client.post(pay, headers=tenant, json={"amount": total, "method": "cash", "account_id": aid})).status_code == 200
    # the order now knows it's paid: the default "pay balance" tap has nothing to collect
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant, json={"method": "cash", "account_id": aid})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_archive_refund_does_not_leave_store_credit(client, tenant):
    cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    await _pay(client, tenant, oid, aid, 100)
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=tenant)
    assert r.status_code in (200, 201), r.text
    bal = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert round(float(bal), 2) == 0.0, bal


@pytest.mark.asyncio
async def test_split_payment_with_float_cents_marks_invoice_paid(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 300.30)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 100.10)
    await _pay(client, tenant, oid, aid, 200.20)
    invs = (await client.get("/api/finance/invoices", headers=tenant)).json()
    items = invs.get("items", invs) if isinstance(invs, dict) else invs
    mine = [i for i in items if i.get("order_id") == oid]
    assert mine and mine[0]["status"] == "paid", mine
