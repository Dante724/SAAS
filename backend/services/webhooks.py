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

# Old names still accepted on subscriptions. The public API docs used to promise
# `booking.created` before bookings were renamed appointments; an endpoint subscribed to the
# old name still receives the event, labelled with the name it subscribed to.
EVENT_ALIASES = {"booking.created": "appointment.created"}


def _delivery_name(endpoint_events: str, event: str):
    """The event name to deliver to this endpoint, or None when it isn't subscribed."""
    if not endpoint_events or endpoint_events.strip() == "*":
        return event
    wanted = {e.strip() for e in endpoint_events.split(",") if e.strip()}
    if event in wanted:
        return event
    for old, new in EVENT_ALIASES.items():
        if new == event and old in wanted:
            return old
    return None


def _subscribed(endpoint_events: str, event: str) -> bool:
    return _delivery_name(endpoint_events, event) is not None


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

    at = datetime.utcnow().isoformat()
    for ep in endpoints:
        name = _delivery_name(ep.events, event)
        if name is None:
            continue
        body = json.dumps({"event": name, "data": payload, "at": at}, default=str)
        delivery = WebhookDelivery(org_id=org_id, endpoint_id=ep.id, event=name,
                                   payload=body, status="pending", attempts=0)
        db.add(delivery)
        await db.flush()
        # Transactional outbox: the row commits (or rolls back) with the caller's change, and
        # the POST happens only after commit. Sending inline used to notify subscribers about
        # changes that were then rolled back, and stalled the request on slow endpoints.
        db.info.setdefault(_OUTBOX_KEY, []).append(delivery.id)
    # caller owns the commit


_OUTBOX_KEY = "repairos_webhook_outbox"


def _register_outbox_hooks() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "after_commit")
    def _flush_outbox(session):
        ids = session.info.pop(_OUTBOX_KEY, None)
        if ids:
            from backend.core.jobs import job_runner
            try:
                job_runner.enqueue(deliver_pending, list(ids), retries=2, name="webhook-outbox")
            except RuntimeError:   # no running loop (sync context): the retry sweep delivers them
                pass

    @event.listens_for(Session, "after_rollback")
    def _drop_outbox(session):
        session.info.pop(_OUTBOX_KEY, None)


_register_outbox_hooks()


async def deliver_pending(delivery_ids: list) -> int:
    """Attempt committed, still-pending deliveries by id (post-commit outbox worker). Rows that
    were rolled back simply don't exist. Anything that fails is retried by the periodic sweep."""
    from backend.db.database import AsyncSessionLocal
    from backend.models import WebhookDelivery, WebhookEndpoint
    sent = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(WebhookDelivery, WebhookEndpoint)
            .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(WebhookDelivery.id.in_(delivery_ids), WebhookDelivery.status == "pending"))).all()
        for delivery, ep in rows:
            if not await claim_delivery(db, delivery, ("pending",)):
                continue      # the retry sweep got there first
            await _attempt(delivery, ep.url, ep.secret, delivery.payload)
            await db.commit()
            sent += 1
    return sent


async def claim_delivery(db, delivery, from_statuses) -> bool:
    """Atomically move a delivery to "sending" so exactly one worker (post-commit outbox or the
    retry sweep, in any process) attempts it. Commits the claim."""
    from sqlalchemy import update

    from backend.models import WebhookDelivery
    res = await db.execute(update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id, WebhookDelivery.status.in_(from_statuses))
        .values(status="sending").execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


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
    import asyncio

    from backend.core.ssrf import UnsafeURLError, pinned_target
    # Re-validate on every attempt (DNS can change after the endpoint was saved) and
    # connect to the exact address that passed the check.
    try:
        target, host_header, extensions = await asyncio.to_thread(pinned_target, url)
    except UnsafeURLError:
        delivery.status = "failed"
        delivery.error = "Blocked: URL does not resolve to a public address"
        return
    event_name = json.loads(body).get("event", "")
    try:
        # follow_redirects=False: a public endpoint must not bounce us to an internal one.
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            resp = await client.post(target, content=body, extensions=extensions, headers={
                **host_header,
                "Content-Type": "application/json",
                "X-Tekly-Event": event_name,
                "X-Tekly-Signature": f"sha256={signature}",
                # Legacy names, kept so receivers built before the rename keep verifying.
                "X-RepairOS-Event": event_name,
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
        # Store a generic reason only — raw socket errors would turn delivery logs into a
        # port/host scanner for whoever configured the endpoint.
        delivery.status = "failed"
        delivery.error = "Timed out" if isinstance(e, httpx.TimeoutException) else "Connection failed"
