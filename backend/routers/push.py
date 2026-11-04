"""Web Push subscription management for technician devices."""

from fastapi import APIRouter, Depends
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
    # A push endpoint is one browser. Whoever subscribes it last owns it, whichever shop had it
    # before: otherwise a browser that signed in at shop B and then at shop A keeps getting B's
    # notifications. skip_tenant_filter: the earlier owner may be another shop; only its id is
    # read, and the row is then deliberately handed to this shop and employee.
    existing_id = (await db.execute(select(PushSubscription.id).where(
        PushSubscription.endpoint == data.endpoint).order_by(PushSubscription.id)
        .execution_options(skip_tenant_filter=True))).scalars().first()
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
