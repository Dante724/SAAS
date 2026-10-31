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
from backend.services.dates import now_utc

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
    if data.order_id:
        # A checkout for an order asks at most for what's still owed on it, whatever currency
        # the request names (create_intent refuses one that isn't the shop's own).
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
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Intent not found")
    if i.viewed_at is None:
        i.viewed_at = now_utc()
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
async def refresh_intent_status(intent_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Poll the provider for payment status (public: the link is the credential).
    Used by the hosted pay page for providers without a signed webhook (SumUp)."""
    from backend.services import payments as pay
    from backend.services import ratelimit
    await ratelimit.aenforce("pay_refresh_ip", ratelimit.client_ip(request), 60, 60)   # review S8
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not intent:
        raise HTTPException(404, "Payment not found")
    try:
        # At most one provider call per link every 5 seconds, however often the page polls.
        await ratelimit.aenforce("pay_refresh_poll", intent.id, 1, 5)
    except HTTPException:
        return _ser(intent)
    intent = await pay.refresh_intent(db, intent)
    return _ser(intent)


def _minor_units(amount, currency: str) -> int:
    # Must match how the gateways sent the amount at checkout (services/payments.py).
    from backend.services.money import minor_units
    return minor_units(amount, currency)


# ── Provider webhooks ────────────────────────────────────────────────────────
# Review S8: a caller must not be able to tell "no such payment" from "bad signature".
# - The org-scoped URLs (/stripe/webhook/{org_id}, /square/webhook/{org_id}) verify the signature
#   with that shop's secret BEFORE looking anything up, and only look inside that shop.
# - The original URLs still find the payment first (to know whose secret to use); an event that
#   matches nothing must verify against the operator-wide secret, else it gets the same 400.
# Every signature failure answers 400 "Invalid signature", and each sender IP is rate limited.

_UNMATCHED = {"ok": True}


async def _webhook_limit(request: Request) -> None:
    from backend.services import ratelimit
    await ratelimit.aenforce("pay_webhook_ip", ratelimit.client_ip(request), 600, 60)


def _signature_check(secrets: list[str], verified: bool, provider: str) -> None:
    try:
        require_signature(secrets, verified, provider)
    except HTTPException:
        raise HTTPException(400, "Invalid signature")   # one answer for every failure


def _square_verified(request: Request, raw: bytes, secrets: list[str]) -> bool:
    import base64
    import hashlib
    import hmac
    # Square signs the notification URL it was given, so it must match the path that was called.
    url = (settings.PUBLIC_BASE_URL or "").rstrip("/") + request.url.path
    got = request.headers.get("x-square-hmacsha256-signature", "")
    return any(hmac.compare_digest(
        base64.b64encode(hmac.new(s.encode(), url.encode() + raw, hashlib.sha256).digest()).decode(), got)
        for s in secrets)


async def _require_square_signature(request: Request, raw: bytes, db: AsyncSession, org_id: Optional[str]) -> None:
    """Verify with the shop's own signature key (or the operator-wide one). org_id=None (an event
    that matched nothing) checks the operator-wide key only."""
    if org_id:
        secrets = await provider_secrets(db, org_id, "square", "webhook_signature_key",
                                         settings.SQUARE_WEBHOOK_SIGNATURE_KEY)
    else:
        secrets = [s for s in [settings.SQUARE_WEBHOOK_SIGNATURE_KEY] if s]
    _signature_check(secrets, _square_verified(request, raw, secrets), "Square")


async def _require_stripe_signature(payload: bytes, sig: str, db: AsyncSession, org_id: Optional[str]) -> None:
    if org_id:
        secrets = await provider_secrets(db, org_id, "stripe", "webhook_secret", settings.STRIPE_WEBHOOK_SECRET)
    else:
        secrets = [s for s in [settings.STRIPE_WEBHOOK_SECRET] if s]
    _signature_check(secrets, any(_verify_stripe_sig(payload, sig, s) for s in secrets), "Stripe")


def _parse(raw: bytes) -> dict:
    try:
        event = json.loads(raw.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Bad payload")
    if not isinstance(event, dict):
        raise HTTPException(400, "Bad payload")
    return event


@router.post("/square/webhook")
async def square_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Square webhook (original URL). Verified with the key of the shop that owns the matched
    payment, or the operator-wide key: HMAC-SHA256 over notification_url + body. Fails closed
    outside development/test when no key is configured."""
    await _webhook_limit(request)
    raw = await request.body()
    return await _square_event(request, raw, _parse(raw), db, scope_org=None)


@router.post("/square/webhook/{org_id}")
async def square_webhook_for_shop(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Square webhook for one shop: verified with that shop's key before anything is looked up."""
    await _webhook_limit(request)
    raw = await request.body()
    await _require_square_signature(request, raw, db, org_id)
    return await _square_event(request, raw, _parse(raw), db, scope_org=org_id)


async def _square_event(request: Request, raw: bytes, event: dict, db: AsyncSession, *,
                        scope_org: Optional[str]) -> dict:
    if event.get("type") == "terminal.checkout.updated":
        return await _square_terminal_event(request, raw, event, db, scope_org=scope_org)
    if event.get("type") not in ("payment.created", "payment.updated"):
        return {"ok": True, "ignored": event.get("type")}
    payment = ((event.get("data") or {}).get("object") or {}).get("payment") or {}
    if (payment.get("status") or "").upper() != "COMPLETED":
        return {"ok": True, "pending": True}
    q = select(PaymentIntent).where(PaymentIntent.provider == "square",
                                    PaymentIntent.provider_ref == payment.get("order_id"))
    if scope_org:
        q = q.where(PaymentIntent.org_id == scope_org)
    intent = (await db.execute(q)).scalar_one_or_none()
    if not scope_org:
        # Verify with the secret of the org that owns the matched intent — never trust the body first.
        await _require_square_signature(request, raw, db, intent.org_id if intent else None)
    if not intent:
        return _UNMATCHED
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


async def _square_terminal_event(request: Request, raw: bytes, event: dict, db: AsyncSession, *,
                                 scope_org: Optional[str]) -> dict:
    """F3: a Square Terminal checkout changed. Matched by checkout id to the charge we started,
    verified with THAT shop's key (or verified first on the shop's own URL); applied once per event_id."""
    from backend.services import terminal as term
    co = ((event.get("data") or {}).get("object") or {}).get("checkout") or {}
    ch = await term.find_charge(db, "square", co.get("id"), org_id=scope_org)
    if not scope_org:
        await _require_square_signature(request, raw, db, ch.org_id if ch else None)
    if not ch:
        return _UNMATCHED
    event_id = event.get("event_id")
    if not event_id:
        raise HTTPException(400, "Missing event id")
    if not await term.claim_event(db, "square", event_id, ch.org_id):
        return {"ok": True, "duplicate": True}
    await term.apply_square_checkout(db, ch, co)
    await db.commit()
    return {"ok": True, "status": ch.status}


async def _stripe_terminal_event(payload: bytes, sig: str, event: dict, db: AsyncSession, *,
                                 scope_org: Optional[str]) -> dict:
    """F2: a card_present PaymentIntent we sent to a reader succeeded, failed or was canceled.
    Matched by PaymentIntent id, verified with the owning shop's signing secret (never trusting
    metadata in the body), applied once per event id."""
    from backend.services import terminal as term
    pi = (event.get("data") or {}).get("object") or {}
    ch = await term.find_charge(db, "stripe", pi.get("id"), org_id=scope_org)
    if not scope_org:
        await _require_stripe_signature(payload, sig, db, ch.org_id if ch else None)
    if not ch:
        return _UNMATCHED   # e.g. the PaymentIntent behind a hosted checkout
    if not event.get("id"):
        raise HTTPException(400, "Missing event id")
    if not await term.claim_event(db, "stripe", event["id"], ch.org_id):
        return {"ok": True, "duplicate": True}
    await term.apply_stripe_intent(db, ch, pi)
    await db.commit()
    return {"ok": True, "status": ch.status}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe webhook (original URL). Verified with the signing secret of the shop that owns
    the payment (or the operator-wide one); fails closed outside development/test when none is
    configured. A checkout must also match the intent's provider, org, amount and currency, and
    Stripe must report it as paid."""
    await _webhook_limit(request)
    payload = await request.body()
    return await _stripe_event(payload, request.headers.get("stripe-signature", ""), _parse(payload), db,
                               scope_org=None)


@router.post("/stripe/webhook/{org_id}")
async def stripe_webhook_for_shop(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe webhook for one shop: verified with that shop's secret before anything is looked up."""
    await _webhook_limit(request)
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    await _require_stripe_signature(payload, sig, db, org_id)
    return await _stripe_event(payload, sig, _parse(payload), db, scope_org=org_id)


async def _stripe_event(payload: bytes, sig: str, event: dict, db: AsyncSession, *,
                        scope_org: Optional[str]) -> dict:
    if event.get("type") in ("payment_intent.succeeded", "payment_intent.payment_failed",
                             "payment_intent.canceled"):
        return await _stripe_terminal_event(payload, sig, event, db, scope_org=scope_org)
    if event.get("type") not in ("checkout.session.completed",
                                 "checkout.session.async_payment_succeeded"):
        return {"ok": True, "ignored": event.get("type")}
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    intent_id = meta.get("intent_id") or obj.get("client_reference_id")
    q = select(PaymentIntent).where(PaymentIntent.id == intent_id)
    if scope_org:
        q = q.where(PaymentIntent.org_id == scope_org)
    i = (await db.execute(q)).scalar_one_or_none()
    if not scope_org:
        await _require_stripe_signature(payload, sig, db, i.org_id if i else None)
    if not i:
        return _UNMATCHED
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
        pairs = [p.strip().split("=", 1) for p in header.split(",") if "=" in p]
        ts = next((v for k, v in pairs if k == "t"), None)
        # Review S9: while a signing secret is being rolled Stripe sends one v1 per secret.
        sigs = [v for k, v in pairs if k == "v1"]
        if not ts or not sigs:
            return False
        # Reject stale/replayed events: the signed timestamp must be within tolerance.
        if abs(time.time() - int(ts)) > 300:
            return False
        signed = f"{ts}.".encode() + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, v) for v in sigs)
    except Exception:
        return False
