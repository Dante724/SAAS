"""Launch fixes, batch 1 (16 Oct go/no-go list): coupons can't become cash (B12), settled
orders kill viewed pay links and the Stripe checkout (B3), "own jobs only" holds on bulk
actions and job items (M3), the till close counts invoice cash and archive refunds (B10),
test messages need settings rights, and an unset ENVIRONMENT fails closed on webhooks (M4)."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Order, Organisation, PaymentIntent, Role
from backend.services.auth import hash_password
from backend.tests.test_money_hardening import _account, _order_with_total


async def _pay(client, h, oid, aid, amount):
    return await client.post(f"/api/cash/orders/{oid}/pay", headers=h,
                             json={"method": "cash", "account_id": aid, "amount": amount})


async def _coupon(client, h, code, value, kind="fixed", max_uses=5):
    r = await client.post("/api/coupons", headers=h, json={
        "code": code, "kind": kind, "value": value, "min_total": 0, "max_uses": max_uses})
    assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_coupon_cannot_turn_a_paid_order_into_cash(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 100)
    await _coupon(client, tenant, "TAKE50", 50)
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "take50"})
    assert r.status_code == 400, r.text
    refund = await client.post(f"/api/cash/orders/{oid}/refund", headers=tenant,
                               json={"amount": 50, "account_id": aid, "method": "cash"})
    assert refund.status_code == 400, refund.text      # nothing overpaid to hand out


@pytest.mark.asyncio
async def test_coupon_is_capped_at_what_is_still_owed(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await _pay(client, tenant, oid, aid, 80)
    await _coupon(client, tenant, "BIG50", 50)
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "BIG50"})
    assert r.status_code == 200, r.text
    assert r.json()["discount"] == pytest.approx(20)


@pytest.mark.asyncio
async def test_unlimited_coupon_works_at_the_pos(client, tenant):
    await _coupon(client, tenant, "ALWAYS5", 5, max_uses=0)          # 0 = unlimited
    for _ in range(2):
        r = await client.post("/api/sales", headers=tenant, json={
            "lines": [{"kind": "service", "ref_id": None, "description": "Fix", "qty": 1, "unit_price": 50}],
            "coupon_code": "always5"})
        assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_viewed_pay_link_is_cancelled_and_stripe_checkout_expired(client, tenant, db, monkeypatch):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    link = (await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})).json()
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == link["intent_id"]))).scalar_one()
    # the customer opened it (a real gateway link goes created → viewed), and it's a Stripe
    # checkout: catch the call that closes it
    intent.status, intent.provider, intent.provider_ref = "viewed", "stripe", "cs_test_123"
    await db.commit()
    # the shop's own Stripe keys: links are never closed with the operator's account (B7)
    r = await client.put("/api/integrations/credentials", headers=tenant,
                         json={"provider": "stripe", "data": {"secret_key": "sk_test_x"}})
    assert r.status_code == 200, r.text
    calls = []

    class _Resp:
        status_code = 200

    import httpx
    real_post = httpx.AsyncClient.post

    async def fake_post(self, url, *a, **kw):
        if str(url).startswith("https://api.stripe.com"):
            calls.append(str(url))
            return _Resp()
        return await real_post(self, url, *a, **kw)        # the test client's own requests
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await _pay(client, tenant, oid, aid, 100)
    await db.refresh(intent)
    assert intent.status == "canceled"
    assert calls == ["https://api.stripe.com/v1/checkout/sessions/cs_test_123/expire"]


@pytest.fixture
async def own_jobs(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name=f"Own-{uuid.uuid4().hex[:4]}", permissions={"can_manage_settings": False},
                order_scope="own")
    db.add(role)
    await db.flush()
    email = f"own-{uuid.uuid4().hex[:8]}@scope.test"
    tech = Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="Tech", email=email,
                    phone="+15550100", password_hash=hash_password("pw-own-12345"), active=True)
    other = Employee(org_id=org.id, location_id=owner.location_id, name="Other",
                     email=f"oth-{uuid.uuid4().hex[:8]}@scope.test", password_hash=hash_password("x" * 12), active=True)
    db.add_all([tech, other])
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-own-12345"})).json()
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "x"})).json()["id"]
    await client.patch(f"/api/orders/{oid}/assign", headers=tenant, json={"assigned_to_id": other.id})
    return {"h": {"Authorization": f"Bearer {tok['access_token']}"}, "tech": tech, "oid": oid}


@pytest.mark.asyncio
async def test_own_jobs_role_cannot_bulk_edit_or_reach_items_of_colleagues_jobs(client, own_jobs, db):
    h, oid = own_jobs["h"], own_jobs["oid"]
    r = await client.post("/api/orders/bulk", headers=h, json={
        "order_ids": [oid], "action": "assign", "value": own_jobs["tech"].id})
    assert r.status_code == 200 and r.json()["updated"] == 0, r.text
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await db.refresh(order)
    assert order.assigned_to_id != own_jobs["tech"].id
    r = await client.post("/api/orders/bulk", headers=h, json={"order_ids": [oid], "action": "create_invoice"})
    assert r.json()["updated"] == 0, r.text
    assert (await client.get(f"/api/orders/{oid}/items", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_sample_message_needs_settings_rights(client, own_jobs):
    r = await client.post("/api/notifications/test", headers=own_jobs["h"],
                          json={"channel": "sms", "body": "Hi {client_name}", "sample": True})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_till_close_counts_invoice_cash_and_archive_refunds(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    aid = await _account(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", headers=tenant,
                             json={"location_id": loc, "opening_float": 100})).json()["id"]
    # cash against an invoice, without the client naming the shift
    _cid, oid = await _order_with_total(client, tenant, 60)
    iid = (await client.post("/api/finance/invoices", headers=tenant, json={"order_id": oid})).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=tenant,
                          json={"method": "cash", "amount": 60, "account_id": aid})
    assert r.status_code == 200, r.text
    # an order paid in cash, then archived with a refund back out of the till
    _cid2, oid2 = await _order_with_total(client, tenant, 40)
    await _pay(client, tenant, oid2, aid, 40)
    r = await client.delete(f"/api/orders/{oid2}?refund=true", headers=tenant)
    assert r.status_code in (200, 201), r.text
    # drawer should hold 100 float + 60 invoice cash + 40 − 40 refunded = 160
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=160", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["discrepancy"] == pytest.approx(0), r.json()


@pytest.mark.asyncio
async def test_invoice_payment_rejects_another_shops_shift(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 10)
    iid = (await client.post("/api/finance/invoices", headers=tenant, json={"order_id": oid})).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=tenant,
                          json={"method": "cash", "amount": 10, "shift_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text


def test_unsigned_webhooks_need_an_explicit_dev_environment(monkeypatch):
    from backend.config import Settings
    from backend.core import webhook_security as ws
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(ws, "settings", Settings(_env_file=None))     # ENVIRONMENT never set
    assert ws.settings.ENVIRONMENT == "development"
    assert ws.unsigned_webhooks_allowed() is False
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(ws, "settings", Settings(_env_file=None))
    assert ws.unsigned_webhooks_allowed() is True
