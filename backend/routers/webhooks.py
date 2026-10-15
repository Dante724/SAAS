"""Webhook endpoints management + delivery log (Public-API platform, v1)."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import Employee, WebhookEndpoint, WebhookDelivery
from backend.services.auth import get_current_employee, require_perm
from backend.services.webhooks import EVENTS

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _ser(e: WebhookEndpoint) -> dict:
    return {"id": e.id, "url": e.url, "secret": e.secret, "events": e.events,
            "active": e.active, "created_at": e.created_at.isoformat() if e.created_at else None}


@router.get("")
async def list_endpoints(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),   # returns signing secrets
):
    rows = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == current.org_id).order_by(WebhookEndpoint.created_at))).scalars().all()
    return {"items": [_ser(e) for e in rows], "available_events": EVENTS}


class EndpointIn(BaseModel):
    url: str
    events: str = "*"        # csv or *
    active: bool = True


async def _require_public_url(url: str) -> None:
    """Endpoints must be public http(s) addresses — never internal services or cloud
    metadata. Checked again on every delivery (services/webhooks.py)."""
    import asyncio

    from backend.core.ssrf import UnsafeURLError, assert_safe_url
    try:
        await asyncio.to_thread(assert_safe_url, (url or "").strip())
    except UnsafeURLError:
        raise HTTPException(400, "URL must be a public http:// or https:// address")


@router.post("", status_code=201)
async def create_endpoint(
    data: EndpointIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    await _require_public_url(data.url)
    e = WebhookEndpoint(org_id=current.org_id, url=data.url.strip(), events=data.events or "*",
                        active=data.active)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _ser(e)


class EndpointPatch(BaseModel):
    url: Optional[str] = None
    events: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/{eid}")
async def update_endpoint(
    eid: str, data: EndpointPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    e = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.id == eid, WebhookEndpoint.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Endpoint not found")
    changes = data.model_dump(exclude_unset=True)
    if "url" in changes:
        await _require_public_url(changes["url"])
        changes["url"] = changes["url"].strip()
    for k, v in changes.items():
        setattr(e, k, v)
    await db.commit()
    return {"ok": True}


@router.delete("/{eid}")
async def delete_endpoint(
    eid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    e = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.id == eid, WebhookEndpoint.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Endpoint not found")
    await db.delete(e)
    await db.commit()
    return {"ok": True}


@router.get("/deliveries")
async def list_deliveries(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.org_id == current.org_id)
        .order_by(desc(WebhookDelivery.created_at)).limit(min(limit, 200)))).scalars().all()
    return {"items": [{
        "id": d.id, "endpoint_id": d.endpoint_id, "event": d.event, "status": d.status,
        "response_code": d.response_code, "attempts": d.attempts, "error": d.error,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
    } for d in rows]}
