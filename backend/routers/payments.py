"""Online payments (Phase 2A). Create a hosted-checkout PaymentIntent, list
intents, confirm in simulated mode, and receive Stripe's webhook."""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import paging as _paging
from backend.config import settings
from backend.core.webhook_security import provider_secrets, require_signature
from backend.db.database import get_db
from backend.models import Employee, PaymentIntent
from backend.services import payments as pay
from backend.services.auth import assert_perm, get_current_employee, require_perm

router = APIRouter(prefix="/api/payments", tags=["payments"])
log = logging.getLogger("tekly")


# A2: map the raw intent status onto the client-facing pay-link lifecycle.
_LIFECYCLE = {
    "created": "Created", "viewed": "Viewed", "pending": "Waiting for payment",
    "succeeded": "Paid", "failed": "Failed", "canceled": "Revoked",
}


def _ser(i: PaymentIntent) -> dict:
    return {"id": i.id, "order_id": i.order_id, "invoice_id": i.invoice_id,
            "account_id": i.account_id, "provider": i.provider, "provider_ref": i.provider_ref,
            "amount": i.amount, "currency": i.currency, "status": i.status,
            "lifecycle": _LIFECYCLE.get(i.status, i.status),
            "checkout_url": i.checkout_url, "payment_id": i.payment_id, "error": i.error,
            "viewed_at": i.viewed_at.isoformat() if i.viewed_at else None,
            "created_at": i.created_at.isoformat() if i.created_at else None}


@router.get("/config")
async def payments_config(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.services import payments as pay
    prov = await pay.org_provider(db, current.org_id)
    sel = prov if prov != "auto" else None
    name = await pay.gateway_name_org(db, current.org_id, sel)
    from backend.services import org_creds
    from backend.services.payment_guard import test_mode as _test_mode
    test_mode = name in ("simulated", "unconfigured") or _test_mode(
        name, await org_creds.effective(db, current.org_id, name, read_only=True))
    return {"gateway": name, "test_mode": test_mode,
            "org_provider": prov,
            "available": await pay.available_gateways_org(db, current.org_id),
            "live": name not in ("simulated", "unconfigured")}


class CheckoutIn(BaseModel):
    order_id: Optional[str] = None
    amount: float
    account_id: Optional[str] = None
    currency: Optional[str] = None      # default: the org's base currency


@router.post("/checkout", status_code=201)
async def create_checkout(
    data: CheckoutIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    await assert_perm(db, current, "can_create_payment_links")
    if data.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if data.order_id and not data.currency:
        # A checkout for an order asks at most for what's still owed on it.
        from backend.models import Order
        from backend.services.order_money import balance_due
        order = (await db.execute(select(Order).where(
            Order.id == data.order_id, Order.org_id == current.org_id))).scalar_one_or_none()
        if not order:
            raise HTTPException(404, "Order not found")
        owed = await balance_due(db, order)
        if data.amount > owed + 0.005:
            raise HTTPException(422, f"Amount is more than the {owed:.2f} still owed on this order.")
    try:
        intent = await pay.create_intent(
            db, org_id=current.org_id, order_id=data.order_id,
            amount=data.amount, account_id=data.account_id, currency=data.currency)
    except RuntimeError as e:
        raise HTTPException(502, f"Gateway error: {e}")
    return _ser(intent)


@router.get("/intents")
async def list_intents(
    skip: int = _paging.skip_query(), limit: int = _paging.limit_query(100),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    from sqlalchemy import func
    q = select(PaymentIntent).where(PaymentIntent.org_id == current.org_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(q.order_by(desc(PaymentIntent.created_at))
                             .offset(skip).limit(_paging.clamp(limit)))).scalars().all()
    return {"items": [_ser(i) for i in rows], "total": total}


@router.get("/intents/{intent_id}")
async def get_intent(
    intent_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    i = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.id == intent_id, PaymentIntent.org_id == current.org_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Intent not found")
    return _ser(i)


@router.post("/intents/{intent_id}/view")
async def mark_viewed(intent_id: str, db: AsyncSession = Depends(get_db)):
    """A2: public hook — the client opened the pay link. Advances Created→Viewed
    so staff can see the link was seen. Idempotent; never regresses a later state."""
    from datetime import datetime as _dt
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Intent not found")
    if i.viewed_at is None:
        i.viewed_at = _dt.utcnow()
    if i.status == "created":
        i.status = "viewed"
    await db.commit()
    return {"ok": True, "status": i.status, "lifecycle": _LIFECYCLE.get(i.status, i.status)}


@router.post("/intents/{intent_id}/revoke")
async def revoke_intent(
    intent_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """A2: staff revoke — kills an unpaid link immediately (improves on Orderry,
    which cannot revoke individual links). A paid link cannot be revoked."""
    i = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.id == intent_id, PaymentIntent.org_id == current.org_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Intent not found")
    if i.status == "succeeded":
        raise HTTPException(400, "A paid link cannot be revoked")
    from backend.services.order_money import revoke_pay_link
    await revoke_pay_link(db, i)
    await db.commit()
    return {"ok": True, "status": i.status, "lifecycle": _LIFECYCLE.get(i.status, i.status)}


@router.post("/intents/{intent_id}/simulate-pay")
async def simulate_pay(intent_id: str, db: AsyncSession = Depends(get_db)):
    """Simulated-gateway success hook (no auth: mirrors a hosted-checkout return).
    Only works while the simulated gateway is active."""
    from backend.config import settings
    if settings.is_deployed:
        # the simulated gateway is a dev/test affordance; never let an unauthenticated
        # caller mark an intent paid in production even if one was somehow created there.
        raise HTTPException(403, "Simulated payments are disabled in production")
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Intent not found")
    if i.provider != "simulated":
        raise HTTPException(400, "This intent uses a live gateway; pay via its checkout URL")
    i = await pay.mark_succeeded(db, i)
    return {"ok": True, "status": i.status, "payment_id": i.payment_id}


@router.post("/intents/{intent_id}/refresh")
async def refresh_intent_status(intent_id: str, db: AsyncSession = Depends(get_db)):
    """Poll the provider for payment status (public: the link is the credential).
    Used by the hosted pay page for providers without a signed webhook (SumUp)."""
    from backend.services import payments as pay
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not intent:
        raise HTTPException(404, "Payment not found")
    intent = await pay.refresh_intent(db, intent)
    return _ser(intent)


def _minor_units(amount, currency: str) -> int:
    # Must match how the gateways sent the amount at checkout (services/payments.py).
    from backend.services.money import minor_units
    return minor_units(amount, currency)


@router.post("/square/webhook")
async def square_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Square payment webhook. Verified with the shop's webhook signature key (or the
    operator-wide one): HMAC-SHA256 over notification_url + body. Fails closed outside
    development/test when no key is configured."""
    raw = await request.body()
    try:
        event = json.loads(raw.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Bad payload")
    if event.get("type") == "terminal.checkout.updated":
        return await _square_terminal_event(request, raw, event, db)
    if event.get("type") not in ("payment.created", "payment.updated"):
        return {"ok": True, "ignored": event.get("type")}
    payment = ((event.get("data") or {}).get("object") or {}).get("payment") or {}
    if (payment.get("status") or "").upper() != "COMPLETED":
        return {"ok": True, "pending": True}
    order_ref = payment.get("order_id")
    intent = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.provider == "square", PaymentIntent.provider_ref == order_ref))).scalar_one_or_none()
    if not intent:
        return {"ok": True, "unmatched": True}
    # Verify with the secret of the org that owns the matched intent — never trust the body first.
    await _require_square_signature(request, raw, db, intent.org_id)
    money = payment.get("amount_money") or {}
    if money.get("amount") is not None and int(money["amount"]) != _minor_units(intent.amount, intent.currency):
        log.warning("Square webhook amount mismatch for intent %s", intent.id)
        raise HTTPException(400, "Amount mismatch")
    if money.get("currency") and money["currency"].lower() != (intent.currency or "").lower():
        log.warning("Square webhook currency mismatch for intent %s", intent.id)
        raise HTTPException(400, "Currency mismatch")
    from backend.services.payments import mark_succeeded
    await mark_succeeded(db, intent)
    return {"ok": True}


async def _require_square_signature(request: Request, raw: bytes, db: AsyncSession, org_id: str) -> None:
    """HMAC-SHA256 over notification_url + body with the signature key of the shop that owns the
    matched payment (or the operator-wide key). Fails closed outside development/test."""
    import base64
    import hashlib
    import hmac
    secrets = await provider_secrets(db, org_id, "square", "webhook_signature_key",
                                     settings.SQUARE_WEBHOOK_SIGNATURE_KEY)
    url = (settings.PUBLIC_BASE_URL or "").rstrip("/") + "/api/payments/square/webhook"
    got = request.headers.get("x-square-hmacsha256-signature", "")
    verified = any(hmac.compare_digest(
        base64.b64encode(hmac.new(s.encode(), url.encode() + raw, hashlib.sha256).digest()).decode(), got)
        for s in secrets)
    require_signature(secrets, verified, "Square")


async def _square_terminal_event(request: Request, raw: bytes, event: dict, db: AsyncSession) -> dict:
    """F3: a Square Terminal checkout changed. Matched by checkout id to the charge we started, then
    verified with THAT shop's key; applied once per event_id."""
    from backend.services import terminal as term
    co = ((event.get("data") or {}).get("object") or {}).get("checkout") or {}
    ch = await term.find_charge(db, "square", co.get("id"))
    if not ch:
        return {"ok": True, "unmatched": True}
    await _require_square_signature(request, raw, db, ch.org_id)
    event_id = event.get("event_id")
    if not event_id:
        raise HTTPException(400, "Missing event id")
    if not await term.claim_event(db, "square", event_id, ch.org_id):
        return {"ok": True, "duplicate": True}
    await term.apply_square_checkout(db, ch, co)
    await db.commit()
    return {"ok": True, "status": ch.status}


async def _stripe_terminal_event(payload: bytes, sig: str, event: dict, db: AsyncSession) -> dict:
    """F2: a card_present PaymentIntent we sent to a reader succeeded, failed or was canceled.
    Matched by PaymentIntent id, verified with the owning shop's signing secret (never trusting
    metadata in the body), applied once per event id."""
    from backend.services import terminal as term
    pi = (event.get("data") or {}).get("object") or {}
    ch = await term.find_charge(db, "stripe", pi.get("id"))
    if not ch:
        return {"ok": True, "unmatched": True}   # e.g. the PaymentIntent behind a hosted checkout
    secrets = await provider_secrets(db, ch.org_id, "stripe", "webhook_secret", settings.STRIPE_WEBHOOK_SECRET)
    require_signature(secrets, any(_verify_stripe_sig(payload, sig, s) for s in secrets), "Stripe")
    if not event.get("id"):
        raise HTTPException(400, "Missing event id")
    if not await term.claim_event(db, "stripe", event["id"], ch.org_id):
        return {"ok": True, "duplicate": True}
    await term.apply_stripe_intent(db, ch, pi)
    await db.commit()
    return {"ok": True, "status": ch.status}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe Checkout completion -> reconcile. Verified with the signing secret of the
    shop that owns the intent (or the operator-wide one); fails closed outside
    development/test when none is configured. The event must also match the intent's
    provider, org, amount and currency, and Stripe must report it as paid."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = json.loads(payload.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Bad payload")
    if event.get("type") in ("payment_intent.succeeded", "payment_intent.payment_failed",
                             "payment_intent.canceled"):
        return await _stripe_terminal_event(payload, sig, event, db)
    if event.get("type") not in ("checkout.session.completed",
                                 "checkout.session.async_payment_succeeded"):
        return {"ok": True, "ignored": event.get("type")}
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    intent_id = meta.get("intent_id") or obj.get("client_reference_id")
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Unknown intent")
    secrets = await provider_secrets(db, i.org_id, "stripe", "webhook_secret",
                                     settings.STRIPE_WEBHOOK_SECRET)
    require_signature(secrets, any(_verify_stripe_sig(payload, sig, s) for s in secrets), "Stripe")
    if i.provider != "stripe" or (meta.get("org_id") and meta["org_id"] != i.org_id):
        log.warning("Stripe webhook does not match intent %s (provider/org)", i.id)
        raise HTTPException(400, "Event does not match payment")
    if obj.get("amount_total") is not None and int(obj["amount_total"]) != _minor_units(i.amount, i.currency):
        log.warning("Stripe webhook amount mismatch for intent %s", i.id)
        raise HTTPException(400, "Amount mismatch")
    if obj.get("currency") and obj["currency"].lower() != (i.currency or "").lower():
        log.warning("Stripe webhook currency mismatch for intent %s", i.id)
        raise HTTPException(400, "Currency mismatch")
    # Delayed methods (bank debits) complete the session before the money arrives;
    # Stripe then sends checkout.session.async_payment_succeeded.
    if obj.get("payment_status") not in (None, "paid", "no_payment_required"):
        return {"ok": True, "pending": True}
    await pay.mark_succeeded(db, i)
    return {"ok": True}


def _verify_stripe_sig(payload: bytes, header: str, secret: str) -> bool:
    import hashlib
    import hmac
    import time
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts, v1 = parts.get("t"), parts.get("v1")
        if not ts or not v1:
            return False
        # Reject stale/replayed events: the signed timestamp must be within tolerance.
        if abs(time.time() - int(ts)) > 300:
            return False
        signed = f"{ts}.".encode() + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)
    except Exception:
        return False
