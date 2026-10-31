"""Money: pay links charge the shop's own currency in the right minor units (security phase 3).

Regression for audit finding B2: payment links were created with currency="usd" whatever the
shop's currency (an INR 5,000 balance became a $5,000 Stripe checkout), and every currency was
multiplied by 100 (JPY overcharged 100x, KWD undercharged 10x).
"""
import pytest
from sqlalchemy import select

from backend.models import Organisation, PaymentIntent
from backend.services.money import minor_units
from backend.tests.test_money_hardening import _order_with_total
from backend.tests.test_payments_providers import _fake_client, _Resp


@pytest.mark.parametrize("amount, cur, expected", [
    (12.34, "usd", 1234), (5000, "INR", 500000), (5000, "jpy", 5000), (1.234, "kwd", 1234),
    (0.1 + 0.2, "eur", 30), (19.995, "gbp", 2000),
])
def test_minor_units_per_currency(amount, cur, expected):
    assert minor_units(amount, cur) == expected


@pytest.mark.asyncio
async def test_pay_link_uses_the_shops_base_currency(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.currency_code = "INR"
    await db.commit()
    _cid, oid = await _order_with_total(client, tenant, 5000)
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})
    assert r.status_code == 201, r.text
    assert r.json()["currency"].lower() == "inr"
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == r.json()["intent_id"]))).scalar_one()
    assert intent.currency == "inr"


@pytest.mark.asyncio
async def test_stripe_checkout_sends_zero_decimal_amounts_correctly(monkeypatch, client, tenant, db):
    import httpx

    from backend.services.payments import StripeGateway
    seen = {}

    def handler(method, url, kw):
        seen.update(kw.get("data") or {})
        return _Resp(200, {"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"})
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(handler))
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    intent = PaymentIntent(org_id=me["org_id"], provider="stripe", amount=5000, currency="jpy", status="created")
    db.add(intent)
    await db.flush()
    await StripeGateway({"secret_key": "sk_test_x"}).create_checkout(intent=intent, description="WO-1")
    assert seen["line_items[0][price_data][currency]"] == "jpy"
    assert seen["line_items[0][price_data][unit_amount]"] == "5000"     # not 500000
