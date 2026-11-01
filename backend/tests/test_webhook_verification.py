"""Security: payment webhooks must be signed by the shop that owns the payment.

Regression for the audit finding where Stripe/Square webhooks were only verified when a
*global* env secret existed — shops using their own keys accepted forged "paid" events.
"""
import base64
import hashlib
import hmac
import json
import time

import pytest

from backend.config import settings
from backend.models import PaymentIntent
from backend.services import org_creds

SQ_URL = "https://shop.example/api/payments/square/webhook"


async def _intent(client, tenant, db, **kw):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    i = PaymentIntent(org_id=me["org_id"], amount=kw.pop("amount", 15.0), currency=kw.pop("currency", "usd"),
                      status="pending", **kw)
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def _status(client, intent_id):
    return (await client.get(f"/api/public/pay/{intent_id}")).json()["status"]


def _stripe_event(intent, **obj):
    body = {"metadata": {"intent_id": intent.id, "org_id": intent.org_id},
            "client_reference_id": intent.id, "amount_total": 1500, "currency": "usd",
            "payment_status": "paid"}
    body.update(obj)
    return json.dumps({"type": "checkout.session.completed", "data": {"object": body}}).encode()


def _stripe_sig(secret, raw):
    ts = str(int(time.time()))
    v1 = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


async def _post_stripe(client, raw, sig=None):
    headers = {"content-type": "application/json"}
    if sig:
        headers["stripe-signature"] = sig
    return await client.post("/api/payments/stripe/webhook", content=raw, headers=headers)


@pytest.fixture
def no_global_secrets(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example", raising=False)


# ── Stripe ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stripe_unsigned_event_rejected_in_production_without_any_secret(
        monkeypatch, no_global_secrets, client, tenant, db):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    i = await _intent(client, tenant, db, provider="stripe", provider_ref="cs_1")
    r = await _post_stripe(client, _stripe_event(i))
    assert r.status_code == 400
    assert await _status(client, i.id) != "succeeded"


@pytest.mark.asyncio
async def test_stripe_per_shop_secret_is_enforced(no_global_secrets, client, tenant, db):
    """The exploit: shop has its own keys, no global env secret → forged event used to pass."""
    i = await _intent(client, tenant, db, provider="stripe", provider_ref="cs_2")
    await org_creds.set_creds(db, i.org_id, "stripe", {"secret_key": "sk_test_x", "webhook_secret": "whsec_shop"})
    raw = _stripe_event(i)

    assert (await _post_stripe(client, raw)).status_code == 400                               # unsigned
    assert (await _post_stripe(client, raw, _stripe_sig("whsec_other", raw))).status_code == 400  # wrong key
    assert await _status(client, i.id) != "succeeded"

    ok = await _post_stripe(client, raw, _stripe_sig("whsec_shop", raw))
    assert ok.status_code == 200 and ok.json()["ok"]
    assert await _status(client, i.id) == "succeeded"


@pytest.mark.asyncio
async def test_stripe_global_secret_still_works(monkeypatch, no_global_secrets, client, tenant, db):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_global")
    i = await _intent(client, tenant, db, provider="stripe", provider_ref="cs_3")
    raw = _stripe_event(i)
    assert (await _post_stripe(client, raw, _stripe_sig("whsec_global", raw))).status_code == 200
    assert await _status(client, i.id) == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("override, field", [
    ({"amount_total": 1}, "amount"),
    ({"currency": "eur"}, "currency"),
    ({"metadata": {"intent_id": "PLACEHOLDER", "org_id": "another-org"}}, "org"),
])
async def test_stripe_signed_event_must_match_intent(no_global_secrets, client, tenant, db, override, field):
    i = await _intent(client, tenant, db, provider="stripe", provider_ref="cs_4")
    await org_creds.set_creds(db, i.org_id, "stripe", {"secret_key": "sk_test_x", "webhook_secret": "whsec_shop"})
    if "metadata" in override:
        override["metadata"]["intent_id"] = i.id
    raw = _stripe_event(i, **override)
    r = await _post_stripe(client, raw, _stripe_sig("whsec_shop", raw))
    assert r.status_code == 400, field
    assert await _status(client, i.id) != "succeeded"


@pytest.mark.asyncio
async def test_stripe_event_cannot_settle_a_non_stripe_intent(no_global_secrets, client, tenant, db):
    i = await _intent(client, tenant, db, provider="simulated", provider_ref=None)
    await org_creds.set_creds(db, i.org_id, "stripe", {"secret_key": "sk_test_x", "webhook_secret": "whsec_shop"})
    raw = _stripe_event(i)
    assert (await _post_stripe(client, raw, _stripe_sig("whsec_shop", raw))).status_code == 400
    assert await _status(client, i.id) != "succeeded"


@pytest.mark.asyncio
async def test_stripe_unpaid_async_session_waits_for_async_success(no_global_secrets, client, tenant, db):
    i = await _intent(client, tenant, db, provider="stripe", provider_ref="cs_5")
    await org_creds.set_creds(db, i.org_id, "stripe", {"secret_key": "sk_test_x", "webhook_secret": "whsec_shop"})
    raw = _stripe_event(i, payment_status="unpaid")
    r = await _post_stripe(client, raw, _stripe_sig("whsec_shop", raw))
    assert r.status_code == 200 and r.json().get("pending")
    assert await _status(client, i.id) != "succeeded"

    raw2 = raw.replace(b"checkout.session.completed", b"checkout.session.async_payment_succeeded") \
              .replace(b'"unpaid"', b'"paid"')
    assert (await _post_stripe(client, raw2, _stripe_sig("whsec_shop", raw2))).status_code == 200
    assert await _status(client, i.id) == "succeeded"


# ── Square ───────────────────────────────────────────────────────────────────

def _square_event(order_ref, amount=1500, currency="USD"):
    return json.dumps({"type": "payment.updated", "data": {"object": {"payment": {
        "order_id": order_ref, "status": "COMPLETED",
        "amount_money": {"amount": amount, "currency": currency}}}}}).encode()


def _square_sig(key, raw):
    return base64.b64encode(hmac.new(key.encode(), SQ_URL.encode() + raw, hashlib.sha256).digest()).decode()


async def _post_square(client, raw, sig=None):
    headers = {"content-type": "application/json"}
    if sig:
        headers["x-square-hmacsha256-signature"] = sig
    return await client.post("/api/payments/square/webhook", content=raw, headers=headers)


@pytest.mark.asyncio
async def test_square_unsigned_event_rejected_in_staging(monkeypatch, no_global_secrets, client, tenant, db):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    i = await _intent(client, tenant, db, provider="square", provider_ref="sq_1")
    assert (await _post_square(client, _square_event("sq_1"))).status_code == 400
    assert await _status(client, i.id) != "succeeded"


@pytest.mark.asyncio
async def test_square_per_shop_key_is_enforced(no_global_secrets, client, tenant, db):
    i = await _intent(client, tenant, db, provider="square", provider_ref="sq_2")
    await org_creds.set_creds(db, i.org_id, "square", {"access_token": "t", "location_id": "L",
                                                       "webhook_signature_key": "sqkey"})
    raw = _square_event("sq_2")
    assert (await _post_square(client, raw)).status_code == 400
    assert (await _post_square(client, raw, _square_sig("wrong", raw))).status_code == 400
    assert (await _post_square(client, raw, _square_sig("sqkey", raw))).status_code == 200
    assert await _status(client, i.id) == "succeeded"


@pytest.mark.asyncio
async def test_square_amount_mismatch_rejected(no_global_secrets, client, tenant, db):
    i = await _intent(client, tenant, db, provider="square", provider_ref="sq_3")
    await org_creds.set_creds(db, i.org_id, "square", {"access_token": "t", "location_id": "L",
                                                       "webhook_signature_key": "sqkey"})
    raw = _square_event("sq_3", amount=1)
    assert (await _post_square(client, raw, _square_sig("sqkey", raw))).status_code == 400
    assert await _status(client, i.id) != "succeeded"


# ── Inbound messaging: Meta (Messenger/Instagram/WhatsApp) and Twilio ────────

META_PAYLOAD = {"object": "page", "entry": [
    {"messaging": [{"sender": {"id": "fbuser9"}, "message": {"mid": "sec-mm1", "text": "hi"}}]}]}


@pytest.fixture
def no_messaging_secrets(monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "", raising=False)


@pytest.mark.asyncio
async def test_meta_unsigned_rejected_in_production(monkeypatch, no_messaging_secrets, client, tenant):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.post(f"/api/inbox/webhook/meta/{org}", json=META_PAYLOAD)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_meta_per_shop_app_secret_is_enforced(no_messaging_secrets, client, tenant, db):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    await org_creds.set_creds(db, org, "meta", {"app_secret": "meta_shop", "page_token": "p"})
    raw = json.dumps(META_PAYLOAD).encode()
    url = f"/api/inbox/webhook/meta/{org}"
    hdr = {"content-type": "application/json"}

    assert (await client.post(url, content=raw, headers=hdr)).status_code == 403
    bad = "sha256=" + hmac.new(b"other", raw, hashlib.sha256).hexdigest()
    assert (await client.post(url, content=raw, headers={**hdr, "X-Hub-Signature-256": bad})).status_code == 403
    good = "sha256=" + hmac.new(b"meta_shop", raw, hashlib.sha256).hexdigest()
    r = await client.post(url, content=raw, headers={**hdr, "X-Hub-Signature-256": good})
    assert r.status_code == 201 and r.json()["ingested"] == 1


def _twilio_sig(token, url, form):
    payload = url + "".join(k + form[k] for k in sorted(form))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


@pytest.mark.asyncio
async def test_twilio_unsigned_rejected_in_staging(monkeypatch, no_messaging_secrets, client, tenant):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    r = await client.post(f"/api/chats/webhook/twilio?org_id={org}", data={"From": "+15550001", "Body": "hi"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_twilio_per_shop_token_is_enforced(no_messaging_secrets, client, tenant, db):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    await org_creds.set_creds(db, org, "twilio", {"account_sid": "AC1", "auth_token": "tw_shop"})
    path = f"/api/chats/webhook/twilio?org_id={org}"
    form = {"From": "+15550002", "Body": "hello"}
    url = "http://test" + path

    assert (await client.post(path, data=form)).status_code == 403
    assert (await client.post(path, data=form,
                              headers={"X-Twilio-Signature": _twilio_sig("other", url, form)})).status_code == 403
    ok = await client.post(path, data=form, headers={"X-Twilio-Signature": _twilio_sig("tw_shop", url, form)})
    assert ok.status_code == 200
