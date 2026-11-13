"""Web Push subscription management for technician devices."""

import hmac

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee, PushSubscription
from backend.services.auth import get_current_employee
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/push", tags=["push"])


class SubKeys(BaseModel):
    p256dh: str
    auth: str


class SubIn(BaseModel):
    endpoint: str
    keys: SubKeys
    user_agent: str | None = None


@router.get("/vapid-public-key")
async def vapid_public_key():
    """Public key the browser uses to create a push subscription. Empty string
    means push is not configured (the client then skips subscribing)."""
    return {"public_key": settings.VAPID_PUBLIC_KEY, "enabled": bool(settings.VAPID_PUBLIC_KEY)}


@router.post("/subscribe", status_code=201)
async def subscribe(
    data: SubIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # A push endpoint is one browser. The same browser signing in as someone else (or at another
    # shop) moves its subscription along, otherwise it keeps getting the old account's notifications.
    # Audit SEC-09: but only the browser itself may move it. Knowing the endpoint URL was enough to
    # take a subscription over; now a subscription bound to another employee moves only when the
    # caller also presents its keys (the browser's p256dh + auth secret, which only that browser's
    # push subscription holds). skip_tenant_filter: the earlier owner may be another shop; only
    # its id, owner and keys are read, never handed back.
    existing = (await db.execute(select(
        PushSubscription.id, PushSubscription.employee_id, PushSubscription.p256dh, PushSubscription.auth).where(
        PushSubscription.endpoint == data.endpoint).order_by(PushSubscription.id)
        .execution_options(skip_tenant_filter=True))).first()
    existing_id = existing[0] if existing else None
    if existing and existing[1] != current.id and not (
            hmac.compare_digest((existing[2] or "").encode(), data.keys.p256dh.encode())
            and hmac.compare_digest((existing[3] or "").encode(), data.keys.auth.encode())):
        raise HTTPException(409, "This browser's notifications are linked to another account. Turn "
                                 "notifications off and on again in this browser to move them here.")
    if existing_id:
        await db.execute(update(PushSubscription).where(PushSubscription.endpoint == data.endpoint)
                         .values(employee_id=current.id, org_id=current.org_id, p256dh=data.keys.p256dh,
                                 auth=data.keys.auth, user_agent=data.user_agent, last_used_at=now_utc())
                         .execution_options(skip_tenant_filter=True, synchronize_session=False))
        await db.commit()
        return {"ok": True, "id": existing_id, "updated": True}
    sub = PushSubscription(
        org_id=current.org_id, employee_id=current.id, endpoint=data.endpoint,
        p256dh=data.keys.p256dh, auth=data.keys.auth, user_agent=data.user_agent)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return {"ok": True, "id": sub.id, "updated": False}


class UnsubIn(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
async def unsubscribe(
    data: UnsubIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    sub = (await db.execute(select(PushSubscription).where(
        PushSubscription.endpoint == data.endpoint,
        PushSubscription.employee_id == current.id))).scalar_one_or_none()
    if sub:
        await db.delete(sub)
        await db.commit()
    return {"ok": True}
