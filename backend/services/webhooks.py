"""Outbound webhook delivery.

emit_event() is called from request handlers after a meaningful change. It finds
every active endpoint subscribed to the event, writes a WebhookDelivery row, and
attempts a signed POST. Delivery is best-effort and fully isolated: any failure
is recorded on the delivery row and never propagates into the caller's request.
"""
import json
import hmac
import hashlib
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Canonical event names other modules emit.
EVENTS = [
    "order.created", "order.status_changed", "order.closed",
    "estimate.accepted", "payment.recorded", "appointment.created",
    "time_entry.stopped",
]


def _subscribed(endpoint_events: str, event: str) -> bool:
    if not endpoint_events or endpoint_events.strip() == "*":
        return True
    wanted = {e.strip() for e in endpoint_events.split(",") if e.strip()}
    return event in wanted


async def emit_event(db: AsyncSession, org_id: str, event: str, payload: dict) -> None:
    """Fan an event out to all subscribed endpoints for the org. Best-effort."""
    try:
        from backend.models import WebhookEndpoint, WebhookDelivery
    except Exception:
        return
    try:
        endpoints = (await db.execute(select(WebhookEndpoint).where(
            WebhookEndpoint.org_id == org_id,
            WebhookEndpoint.active == True))).scalars().all()  # noqa: E712
    except Exception:
        return
    if not endpoints:
        return

    body = json.dumps({"event": event, "data": payload,
                       "at": datetime.utcnow().isoformat()}, default=str)
    for ep in endpoints:
        if not _subscribed(ep.events, event):
            continue
        delivery = WebhookDelivery(org_id=org_id, endpoint_id=ep.id, event=event,
                                   payload=body, status="pending", attempts=0)
        db.add(delivery)
        await db.flush()
        await _attempt(delivery, ep.url, ep.secret, body)
    # caller owns the commit


async def _attempt(delivery, url: str, secret: str, body: str) -> None:
    """One delivery attempt. Records outcome on the delivery row; never raises."""
    delivery.attempts += 1
    signature = hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).hexdigest()
    try:
        import httpx
    except Exception:
        delivery.status = "pending"
        delivery.error = "httpx unavailable; delivery skipped"
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, content=body, headers={
                "Content-Type": "application/json",
                "X-RepairOS-Event": json.loads(body).get("event", ""),
                "X-RepairOS-Signature": f"sha256={signature}",
            })
        delivery.response_code = resp.status_code
        if 200 <= resp.status_code < 300:
            delivery.status = "sent"
            delivery.delivered_at = datetime.utcnow()
        else:
            delivery.status = "failed"
            delivery.error = f"HTTP {resp.status_code}"
    except Exception as e:  # network error, bad URL, timeout, etc.
        delivery.status = "failed"
        delivery.error = str(e)[:500]
