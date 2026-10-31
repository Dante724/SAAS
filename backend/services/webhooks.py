"""Outbound webhook delivery.

emit_event() is called from request handlers after a meaningful change. It finds
every active endpoint subscribed to the event, writes a WebhookDelivery row, and
attempts a signed POST. Delivery is best-effort and fully isolated: any failure
is recorded on the delivery row and never propagates into the caller's request.
"""
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.services.dates import now_utc

# Canonical event names other modules emit. Every one of these fires somewhere (a test checks
# the list against the code), and docs/public-api-v1.md lists the same set.
EVENTS = [
    "order.created", "order.status_changed", "order.closed",
    "estimate.accepted", "payment.recorded", "appointment.created",
    "time_entry.stopped",
    # WS-C: inquiries, tags, bundles and products (API v2)
    "inquiry.created", "inquiry.updated", "inquiry.converted",
    "tag.created", "tag.updated", "tag.deleted",
    "bundle.created", "bundle.updated", "bundle.deleted",
    "product.created", "product.updated",
]
# Old names the docs once promised. An endpoint subscribed to one receives the real event.
ALIASES = {"booking.created": "appointment.created"}


def valid_events(raw: str) -> str:
    """Normalise a subscription csv; raises ValueError naming any unknown event."""
    if not raw or raw.strip() == "*":
        return "*"
    names = [e.strip() for e in raw.split(",") if e.strip()]
    bad = [e for e in names if e not in EVENTS and e not in ALIASES]
    if bad:
        raise ValueError(", ".join(bad))
    return ",".join(dict.fromkeys(names))


def _subscribed(endpoint_events: str, event: str) -> bool:
    if not endpoint_events or endpoint_events.strip() == "*":
        return True
    wanted = {ALIASES.get(e.strip(), e.strip()) for e in endpoint_events.split(",") if e.strip()}
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
                       "at": now_utc().isoformat()}, default=str)
    for ep in endpoints:
        if not _subscribed(ep.events, event):
            continue
        delivery = WebhookDelivery(org_id=org_id, endpoint_id=ep.id, event=event,
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
        .values(status="sending", claimed_at=datetime.utcnow())
        .execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


# Retry backoff (R3): 1, 2, 4, 8… minutes after each failed attempt, at most 6 hours.
_BACKOFF_BASE_S = 60
_BACKOFF_MAX_S = 6 * 3600


def next_attempt_after(attempts: int) -> datetime:
    wait = min(_BACKOFF_BASE_S * (2 ** max(attempts - 1, 0)), _BACKOFF_MAX_S)
    return datetime.utcnow() + timedelta(seconds=wait)


async def _attempt(delivery, url: str, secret: str, body: str) -> None:
    """One delivery attempt. Records outcome on the delivery row; never raises."""
    await _attempt_once(delivery, url, secret, body)
    if delivery.status == "failed":
        delivery.next_attempt_at = next_attempt_after(delivery.attempts)
    elif delivery.status == "sent":
        delivery.next_attempt_at = None


async def _attempt_once(delivery, url: str, secret: str, body: str) -> None:
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
            delivery.delivered_at = now_utc()
        else:
            delivery.status = "failed"
            delivery.error = f"HTTP {resp.status_code}"
    except Exception as e:  # network error, bad URL, timeout, etc.
        # Store a generic reason only — raw socket errors would turn delivery logs into a
        # port/host scanner for whoever configured the endpoint.
        delivery.status = "failed"
        delivery.error = "Timed out" if isinstance(e, httpx.TimeoutException) else "Connection failed"
