"""Webhook endpoints management + delivery log (Public-API platform, v1).

Everything here needs can_manage_settings. Signing secrets are returned only when they are
created (POST) or rotated (POST /{id}/rotate); lists show a short hint instead.
"""
import json
from datetime import datetime
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, WebhookDelivery, WebhookEndpoint
from backend.services import webhooks as wh
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

MANAGE = require_perm("can_manage_settings")


def _iso(v):
    return v.isoformat() if v else None


def _ser(e: WebhookEndpoint, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    overlap = e.previous_secret_expires_at if (e.previous_secret and e.previous_secret_expires_at
                                               and e.previous_secret_expires_at > now) else None
    return {"id": e.id, "url": e.url, "description": e.description, "events": e.events,
            "active": e.active, "secret_hint": wh.secret_hint(e.secret),
            "previous_secret_expires_at": _iso(overlap), "failing_since": _iso(e.failing_since),
            "disabled_reason": e.disabled_reason, "created_at": _iso(e.created_at)}


def _ser_delivery(d: WebhookDelivery, with_payload: bool = False) -> dict:
    out = {"id": d.id, "endpoint_id": d.endpoint_id, "event": d.event, "status": d.status,
           "response_code": d.response_code, "attempts": d.attempts, "error": d.error,
           "created_at": _iso(d.created_at), "delivered_at": _iso(d.delivered_at),
           "next_attempt_at": _iso(d.next_attempt_at)}
    if with_payload:
        try:
            out["payload"] = json.loads(d.payload or "{}")
        except ValueError:
            out["payload"] = None
    return out


async def _live_endpoints(db, org_id):
    return (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == org_id, WebhookEndpoint.kind == "endpoint",
        WebhookEndpoint.deleted_at.is_(None)).order_by(WebhookEndpoint.created_at))).scalars().all()


async def _get(db, org_id, eid) -> WebhookEndpoint:
    e = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.id == eid, WebhookEndpoint.org_id == org_id, WebhookEndpoint.kind == "endpoint",
        WebhookEndpoint.deleted_at.is_(None)))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Endpoint not found")
    return e


@router.get("")
async def list_endpoints(db: AsyncSession = Depends(get_db), current: Employee = Depends(MANAGE)):
    rows = await _live_endpoints(db, current.org_id)
    now = datetime.utcnow()
    return {"items": [_ser(e, now) for e in rows], "available_events": wh.EVENTS,
            "event_catalogue": [{"name": k, "description": v} for k, v in wh.EVENT_CATALOGUE.items()],
            "max_endpoints": wh.MAX_ENDPOINTS_PER_ORG}


Events = Union[str, list[str]]


class EndpointIn(BaseModel):
    url: str = Field(max_length=500)
    events: Events = "*"          # csv, list, or *
    description: Optional[str] = Field(None, max_length=200)
    active: bool = True


async def require_webhook_url(url: str) -> str:
    """Endpoints must be public addresses (https on 443/8443 once deployed), never internal
    services or cloud metadata. Checked again, pinned, on every delivery."""
    import asyncio

    from backend.core.ssrf import UnsafeURLError, assert_webhook_url
    url = (url or "").strip()
    try:
        await asyncio.to_thread(assert_webhook_url, url)
    except UnsafeURLError:
        from backend.config import settings
        raise HTTPException(400, "URL must be a public https:// address" if settings.is_deployed
                            else "URL must be a public http:// or https:// address")
    return url


def _events(value) -> str:
    try:
        return wh.normalize_events(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("", status_code=201)
async def create_endpoint(data: EndpointIn, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(MANAGE)):
    e, secret = await create_endpoint_core(db, current.org_id, url=data.url, events=data.events,
                                           description=data.description, active=data.active)
    await db.commit()
    await db.refresh(e)
    return {**_ser(e), "secret": secret}   # the only time the secret is shown


async def create_endpoint_core(db: AsyncSession, org_id: str, *, url: str, events, description=None,
                               active: bool = True) -> tuple[WebhookEndpoint, str]:
    """The one way to add an endpoint (staff screen and API v2): the per-shop cap (B7),
    catalogue-checked events, the SSRF/https URL rule, and a new encrypted whsec_ secret.
    Flushes; the caller commits and shows the returned secret once."""
    count = (await db.execute(select(func.count()).select_from(WebhookEndpoint).where(
        WebhookEndpoint.org_id == org_id, WebhookEndpoint.kind == "endpoint",
        WebhookEndpoint.deleted_at.is_(None)))).scalar() or 0
    if count >= wh.MAX_ENDPOINTS_PER_ORG:
        raise HTTPException(409, f"A shop can have up to {wh.MAX_ENDPOINTS_PER_ORG} webhook endpoints")
    events = _events(events)
    url = await require_webhook_url(url)
    secret = wh.new_secret()
    e = WebhookEndpoint(org_id=org_id, url=url, events=events, active=active,
                        description=(description or "").strip() or None,
                        secret=wh.store_secret(secret))
    db.add(e)
    await db.flush()
    return e, secret


class EndpointPatch(BaseModel):
    url: Optional[str] = Field(None, max_length=500)
    events: Optional[Events] = None
    description: Optional[str] = Field(None, max_length=200)
    active: Optional[bool] = None


@router.patch("/{eid}")
async def update_endpoint(eid: str, data: EndpointPatch, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(MANAGE)):
    e = await _get(db, current.org_id, eid)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("url") is not None:
        e.url = await require_webhook_url(changes["url"])
    if changes.get("events") is not None:
        e.events = _events(changes["events"])
    if "description" in changes:
        e.description = (changes["description"] or "").strip() or None
    if changes.get("active") is not None:
        if changes["active"] and not e.active:      # turned back on: start the health clock again
            e.failing_since, e.disabled_reason = None, None
        e.active = changes["active"]
    await db.commit()
    await db.refresh(e)
    return {"ok": True, "endpoint": _ser(e)}


@router.delete("/{eid}")
async def delete_endpoint(eid: str, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(MANAGE)):
    """Soft delete (B14): the delivery log keeps its rows; nothing more is sent."""
    await delete_endpoint_core(db, await _get(db, current.org_id, eid))
    await db.commit()
    return {"ok": True}


async def delete_endpoint_core(db: AsyncSession, e: WebhookEndpoint) -> None:
    """Soft delete shared with API v2: the log keeps its rows, queued retries are abandoned."""
    e.active = False
    e.deleted_at = datetime.utcnow()
    await db.execute(update(WebhookDelivery).where(
        WebhookDelivery.endpoint_id == e.id, WebhookDelivery.status.in_(("pending", "failed")))
        .values(status="abandoned", next_attempt_at=None).execution_options(synchronize_session=False))


@router.post("/{eid}/rotate")
async def rotate_endpoint_secret(eid: str, db: AsyncSession = Depends(get_db),
                                 current: Employee = Depends(MANAGE)):
    """New signing secret. For 24 hours deliveries carry a signature for the old and the new
    secret, so the receiver can switch over without dropping events."""
    e = await _get(db, current.org_id, eid)
    secret = wh.rotate_secret(e)
    await db.commit()
    await db.refresh(e)
    return {**_ser(e), "secret": secret}


@router.post("/{eid}/test")
async def send_test_event(eid: str, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(MANAGE)):
    """Send a signed `webhook.test` event now and report what the endpoint answered."""
    import uuid

    e = await _get(db, current.org_id, eid)
    now = datetime.utcnow()
    body = wh.envelope("evt_" + uuid.uuid4().hex, wh.TEST_EVENT, current.org_id,
                       {"message": "Test event from Tekly", "endpoint_id": e.id}, now)
    d = WebhookDelivery(org_id=current.org_id, endpoint_id=e.id, event=wh.TEST_EVENT, payload=body,
                        status="sending", attempts=0, claimed_at=now, created_at=now)
    db.add(d)
    await db.commit()
    sem, _ = wh._limits()
    async with sem:
        await wh._attempt(d, e.url, wh.signing_secrets(e), body)
    d.next_attempt_at = None          # a test is not retried
    await db.commit()
    await db.refresh(d)
    return _ser_delivery(d)


@router.get("/deliveries")
async def list_deliveries(
    endpoint_id: Optional[str] = None,
    status: Optional[str] = Query(None, max_length=12),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(MANAGE),      # B10: payloads describe customers and money
):
    q = select(WebhookDelivery).where(WebhookDelivery.org_id == current.org_id)
    if endpoint_id:
        q = q.where(WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        q = q.where(WebhookDelivery.status == status)
    rows = (await db.execute(q.order_by(desc(WebhookDelivery.created_at)).limit(limit))).scalars().all()
    return {"items": [_ser_delivery(d) for d in rows]}


async def _delivery(db, org_id, did) -> WebhookDelivery:
    d = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.id == did, WebhookDelivery.org_id == org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Delivery not found")
    return d


@router.get("/deliveries/{did}")
async def get_delivery(did: str, db: AsyncSession = Depends(get_db), current: Employee = Depends(MANAGE)):
    return _ser_delivery(await _delivery(db, current.org_id, did), with_payload=True)


@router.post("/deliveries/{did}/redeliver")
async def redeliver(did: str, db: AsyncSession = Depends(get_db), current: Employee = Depends(MANAGE)):
    """Send a delivery again now, with the same body and Tekly-Webhook-Id (receivers dedupe on it)."""
    d = await _delivery(db, current.org_id, did)
    if d.status in ("pending", "sending"):
        raise HTTPException(409, "This delivery is already being sent")
    ep = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == d.endpoint_id))).scalar_one_or_none()
    if not ep or ep.deleted_at is not None:
        raise HTTPException(409, "The endpoint was deleted")
    if not ep.active:
        raise HTTPException(409, "Turn the endpoint on first")
    d.status, d.attempts, d.next_attempt_at, d.error = "pending", 0, None, None
    await db.commit()
    status = await wh.deliver_one(d.id, ("pending",))
    await db.refresh(d)
    return {**_ser_delivery(d), "result": status}
