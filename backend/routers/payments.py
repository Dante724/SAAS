"""Online payments (Phase 2A). Create a hosted-checkout PaymentIntent, list
intents, confirm in simulated mode, and receive Stripe's webhook."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee, PaymentIntent
from backend.services import payments as pay
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/payments", tags=["payments"])


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
    return {"gateway": name,
            "org_provider": prov,
            "available": await pay.available_gateways_org(db, current.org_id),
            "live": name not in ("simulated", "unconfigured")}


class CheckoutIn(BaseModel):
    order_id: Optional[str] = None
    amount: float
    account_id: Optional[str] = None
    currency: str = "usd"


@router.post("/checkout", status_code=201)
async def create_checkout(
    data: CheckoutIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    if data.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    try:
        intent = await pay.create_intent(
            db, org_id=current.org_id, order_id=data.order_id,
            amount=data.amount, account_id=data.account_id, currency=data.currency)
    except RuntimeError as e:
        raise HTTPException(502, f"Gateway error: {e}")
    return _ser(intent)


@router.get("/intents")
async def list_intents(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    rows = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.org_id == current.org_id).order_by(desc(PaymentIntent.created_at)))).scalars().all()
    return {"items": [_ser(i) for i in rows]}


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
    i.status = "canceled"
    await db.commit()
    return {"ok": True, "status": i.status, "lifecycle": _LIFECYCLE.get(i.status, i.status)}


@router.post("/intents/{intent_id}/simulate-pay")
async def simulate_pay(intent_id: str, db: AsyncSession = Depends(get_db)):
    """Simulated-gateway success hook (no auth: mirrors a hosted-checkout return).
    Only works while the simulated gateway is active."""
    from backend.config import settings
    if settings.is_production:
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


@router.post("/square/webhook")
async def square_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Square payment webhook. Verified with the webhook signature key
    (HMAC-SHA256 over notification_url + body); fail-closed when the key is set."""
    raw = await request.body()
    if settings.SQUARE_WEBHOOK_SIGNATURE_KEY:
        import base64
        import hashlib
        import hmac
        url = (settings.PUBLIC_BASE_URL or "").rstrip("/") + "/api/payments/square/webhook"
        digest = hmac.new(settings.SQUARE_WEBHOOK_SIGNATURE_KEY.encode(),
                          url.encode() + raw, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode()
        got = request.headers.get("x-square-hmacsha256-signature", "")
        if not hmac.compare_digest(expected, got):
            raise HTTPException(400, "Bad signature")
    import json
    try:
        event = json.loads(raw.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Bad payload")
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
    from backend.services.payments import mark_succeeded
    await mark_succeeded(db, intent)
    return {"ok": True}


@router.post("/terminal/connection-token")
async def terminal_connection_token(db: AsyncSession = Depends(get_db),
                                    current: Employee = Depends(require_perm("can_view_finance"))):
    """Stripe Terminal: connection token for the reader SDK (503 when Stripe absent)."""
    from backend.services import payments as pay
    try:
        return {"secret": await pay.terminal_connection_token(db, current.org_id)}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


class TerminalIntentIn(BaseModel):
    intent_id: str


@router.post("/terminal/create-intent")
async def terminal_create_intent(
    data: TerminalIntentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Create a card_present PaymentIntent on Stripe for an existing RepairOS
    intent, so a reader can collect it in person."""
    from backend.services import payments as pay
    intent = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.id == data.intent_id, PaymentIntent.org_id == current.org_id))).scalar_one_or_none()
    if not intent:
        raise HTTPException(404, "Payment not found")
    try:
        res = await pay.terminal_payment_intent(db, current.org_id, amount=intent.amount,
                                                currency=intent.currency, intent_id=intent.id)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    intent.provider = "stripe"
    intent.provider_ref = res["provider_ref"]
    await db.commit()
    return {"client_secret": res["client_secret"], "provider_ref": res["provider_ref"]}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe checkout.session.completed -> reconcile. Signature verified when a
    webhook secret is configured; otherwise the raw event is trusted (dev only)."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if settings.STRIPE_WEBHOOK_SECRET:
        if not _verify_stripe_sig(payload, sig, settings.STRIPE_WEBHOOK_SECRET):
            raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(payload.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Bad payload")
    if event.get("type") != "checkout.session.completed":
        return {"ok": True, "ignored": event.get("type")}
    obj = (event.get("data") or {}).get("object") or {}
    intent_id = (obj.get("metadata") or {}).get("intent_id") or obj.get("client_reference_id")
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Unknown intent")
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
