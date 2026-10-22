"""A1 payments breadth: Square/SumUp gateways, per-org provider, webhook, terminal."""
import base64
import hashlib
import hmac
import json

import pytest


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def _fake_client(handler):
    class Fake:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw): return handler("POST", url, kw)
        async def get(self, url, **kw): return handler("GET", url, kw)
    return Fake


@pytest.mark.asyncio
async def test_square_gateway_request_and_parse(monkeypatch, db, client, tenant):
    import httpx

    from backend.config import settings
    from backend.models import PaymentIntent
    from backend.services.payments import SquareGateway

    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", "sq-test")
    monkeypatch.setattr(settings, "SQUARE_LOCATION_ID", "LOC1")
    seen = {}

    def handler(method, url, kw):
        seen.update(method=method, url=url, json=kw.get("json"), headers=kw.get("headers"))
        return _Resp(200, {"payment_link": {"id": "plink_1", "order_id": "sqord_9",
                                            "url": "https://square.link/u/abc"}})
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(handler))

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    intent = PaymentIntent(org_id=me["org_id"], provider="square", amount=42.5, currency="usd", status="created")
    db.add(intent)
    await db.flush()
    res = await SquareGateway().create_checkout(intent=intent, description="Order WO-1")
    assert seen["url"].endswith("/v2/online-checkout/payment-links")
    assert "squareupsandbox" in seen["url"]                       # sandbox default
    assert seen["json"]["quick_pay"]["price_money"] == {"amount": 4250, "currency": "USD"}
    assert seen["headers"]["Authorization"] == "Bearer sq-test"
    assert res["provider_ref"] == "sqord_9" and res["checkout_url"].startswith("https://square.link")


@pytest.mark.asyncio
async def test_sumup_gateway_parse(monkeypatch, db, client, tenant):
    import httpx

    from backend.config import settings
    from backend.models import PaymentIntent
    from backend.services.payments import SumUpGateway

    monkeypatch.setattr(settings, "SUMUP_API_KEY", "su-test")
    monkeypatch.setattr(settings, "SUMUP_MERCHANT_CODE", "M123")
    # SumUp keys always count as live (review S1), so the gateway only runs with live payments on.
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ALLOW_LIVE_PAYMENTS", True)
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(
        lambda m, u, k: _Resp(201, {"id": "chk_7", "hosted_checkout_url": "https://pay.sumup.com/x"})))
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    intent = PaymentIntent(org_id=me["org_id"], provider="sumup", amount=10, currency="eur", status="created")
    db.add(intent)
    await db.flush()
    res = await SumUpGateway().create_checkout(intent=intent, description="d")
    assert res["provider_ref"] == "chk_7" and "sumup.com" in res["checkout_url"]


@pytest.mark.asyncio
async def test_org_provider_setting_and_unconfigured_rejection(client, tenant):
    cfg = (await client.get("/api/payments/config", headers=tenant)).json()
    assert cfg["org_provider"] == "auto" and cfg["available"]["simulated"] is True

    ok = await client.put("/api/staff/company", json={"payment_provider": "square"}, headers=tenant)
    assert ok.status_code == 200
    bad = await client.put("/api/staff/company", json={"payment_provider": "paypal"}, headers=tenant)
    assert bad.status_code == 422

    # Square selected but not configured -> payment link fails cleanly (502), not 500
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 20.0}, headers=tenant)
    r = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)
    assert r.status_code == 502 and "not configured" in r.json()["detail"]

    # back to auto -> simulated link works and reports the intent's real provider
    await client.put("/api/staff/company", json={"payment_provider": "auto"}, headers=tenant)
    r2 = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)
    assert r2.status_code == 201 and r2.json()["gateway"] == "simulated"


@pytest.mark.asyncio
async def test_square_webhook_signature_and_reconciliation(monkeypatch, client, tenant, db):
    from backend.config import settings
    from backend.models import PaymentIntent

    monkeypatch.setattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "whsig")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example")
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    intent = PaymentIntent(org_id=me["org_id"], provider="square", provider_ref="sqord_42",
                           amount=15.0, currency="usd", status="pending")
    db.add(intent)
    await db.commit()
    await db.refresh(intent)

    event = {"type": "payment.updated",
             "data": {"object": {"payment": {"order_id": "sqord_42", "status": "COMPLETED"}}}}
    raw = json.dumps(event).encode()
    url = "https://shop.example/api/payments/square/webhook"
    sig = base64.b64encode(hmac.new(b"whsig", url.encode() + raw, hashlib.sha256).digest()).decode()

    bad = await client.post("/api/payments/square/webhook", content=raw,
                            headers={"x-square-hmacsha256-signature": "nope",
                                     "content-type": "application/json"})
    assert bad.status_code == 400

    ok = await client.post("/api/payments/square/webhook", content=raw,
                           headers={"x-square-hmacsha256-signature": sig,
                                    "content-type": "application/json"})
    assert ok.status_code == 200 and ok.json()["ok"]
    info = (await client.get(f"/api/public/pay/{intent.id}")).json()
    assert info["status"] == "succeeded"


@pytest.mark.asyncio
async def test_sumup_refresh_poll_marks_paid(monkeypatch, client, tenant, db):
    import httpx

    from backend.config import settings
    from backend.models import PaymentIntent

    monkeypatch.setattr(settings, "SUMUP_API_KEY", "su-test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")      # SumUp keys count as live (S1)
    monkeypatch.setattr(settings, "ALLOW_LIVE_PAYMENTS", True)
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(
        lambda m, u, k: _Resp(200, {"id": "chk_9", "status": "PAID"})))
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    intent = PaymentIntent(org_id=me["org_id"], provider="sumup", provider_ref="chk_9",
                           amount=9.0, currency="eur", status="pending")
    db.add(intent)
    await db.commit()
    await db.refresh(intent)
    r = await client.post(f"/api/payments/intents/{intent.id}/refresh")
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
