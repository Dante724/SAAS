"""Settings → Billing: the shop's Tekly plan, Stripe Checkout to subscribe, plan changes, the
Stripe Customer Portal, and the signed Stripe Billing webhook. Logic in services/billing.py.

Every route here stays usable while the shop is read-only (that's how it gets out of it)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.webhook_security import require_signature
from backend.db.database import get_db
from backend.models import Employee, Organisation
from backend.routers.payments import _verify_stripe_sig
from backend.services import billing
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/billing", tags=["billing"])

MANAGE = "can_manage_settings"


async def _org(db: AsyncSession, org_id: str) -> Organisation:
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()


class PlanIn(BaseModel):
    plan: str
    period: str = "month"


@router.get("")
async def get_billing(db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm(MANAGE))):
    org = await _org(db, current.org_id)
    return {
        **await billing.summary_with_usage(db, org),
        "plans": [{"id": p, "name": billing.PLAN_NAMES[p], "limits": billing.PLAN_LIMITS[p],
                   "features": [billing.FEATURES[f][0] for f in billing.FEATURES
                                if billing.FEATURES[f][1] == p]} for p in billing.PLANS],
        "prices": billing.price_table(),
        "yearly_months": billing.YEARLY_MONTHS,
        "has_subscription": bool(org.stripe_subscription_id) and org.sub_status in ("active", "past_due"),
        "has_billing_account": bool(org.stripe_customer_id),
        "cancel_at_period_end": bool(org.cancel_at_period_end),
        "current_period_end": org.billing_period_end.isoformat() if org.billing_period_end else None,
        "configured": billing.configured(),
    }


@router.post("/checkout")
async def checkout(data: PlanIn, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(require_perm(MANAGE))):
    """Start a Stripe Checkout subscription; the browser goes to the returned url."""
    org = await _org(db, current.org_id)
    return {"url": await billing.create_checkout(db, org, data.plan, data.period, current.email)}


@router.post("/change-plan")
async def change_plan(data: PlanIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm(MANAGE))):
    org = await _org(db, current.org_id)
    await billing.change_plan(db, org, data.plan, data.period)
    await db.commit()
    return {"ok": True, **billing.summary(org)}


@router.post("/portal")
async def portal(db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(require_perm(MANAGE))):
    """Stripe Customer Portal: card, invoices, cancel."""
    return {"url": await billing.create_portal(await _org(db, current.org_id))}


@router.post("/webhook")
async def webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe Billing events from Tekly's own Stripe account. The signature is checked before
    anything is read or stored; outside development/test an unconfigured secret rejects all."""
    from backend.services import ratelimit
    await ratelimit.aenforce("billing_webhook_ip", ratelimit.client_ip(request), 600, 60)
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secrets = [s for s in [settings.STRIPE_BILLING_WEBHOOK_SECRET.strip()] if s]
    require_signature(secrets, any(_verify_stripe_sig(payload, sig, s) for s in secrets),
                      "Stripe Billing")
    try:
        event = json.loads(payload.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Bad payload")
    if not isinstance(event, dict):
        raise HTTPException(400, "Bad payload")
    return await billing.handle_event(db, event)
