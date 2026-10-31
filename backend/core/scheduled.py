"""Periodic jobs registered on the shared job_runner.

Importing this module registers the schedules; main.py imports it before
job_runner.start(). Kept separate from jobs.py to avoid import cycles
(this module pulls in models/services; jobs.py stays dependency-free).
"""
import logging

from sqlalchemy import select

from backend.core.jobs import job_runner
from backend.services.dates import now_utc

log = logging.getLogger("tekly.jobs")

_MAX_ATTEMPTS = 5


@job_runner.every(600, "status-sla-sweep")
async def sweep_status_sla() -> None:
    """Flag work orders that have exceeded their status time limit.

    Surfaces SLA breaches onto the order's activity timeline (one event per
    breach, deduped) so they are visible without anyone polling the API.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.services.sla import sweep_breaches

    async with AsyncSessionLocal() as db:
        written = await sweep_breaches(db)
        if written:
            log.info("status-sla-sweep: flagged %d new SLA breach(es)", written)


@job_runner.every(120, "notification-sweep")
async def sweep_notifications() -> None:
    """N-GAP-1 — actually fire scheduled notifications.

    The rules engine queues appointment reminders and delayed / working-hours
    sends as NotificationLog rows with a future send_at; nothing was flushing
    them. This tick, per active org, (1) queues any reminders now inside their
    lead window and (2) dispatches every queued send whose time has come.

    ponytail: per-org loop over active orgs — fine at pilot scale; swap to a
    single cross-org query if the org count ever makes this hot.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.models import Organisation
    from backend.services import notification_rules as nr

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(
            select(Organisation.id).where(Organisation.active == True)  # noqa: E712
        )).scalars().all()
        queued = sent = 0
        for oid in org_ids:
            try:
                queued += await nr.queue_reminders(db, org_id=oid)
                queued += await nr.queue_appointment_reminders(db, org_id=oid)
                res = await nr.run_due(db, org_id=oid)
                sent += res.get("sent", 0)
            except Exception:  # noqa: BLE001 — one bad org never stalls the sweep
                log.exception("notification-sweep failed for org %s", oid)
        if queued or sent:
            log.info("notification-sweep: queued %d, sent %d", queued, sent)


@job_runner.every(3600, "contact-lifecycle-sweep")
async def sweep_contact_lifecycle() -> None:
    """144ja (N-P4) — fire Contacts-module birthday greetings.

    Runs hourly but is idempotent per client+rule+year, so a contact is greeted
    once on their birthday no matter how many ticks land that day.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.models import Organisation
    from backend.services import notification_rules as nr

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(
            select(Organisation.id).where(Organisation.active == True)  # noqa: E712
        )).scalars().all()
        sent = 0
        for oid in org_ids:
            try:
                sent += await nr.notify_contact_birthdays(db, org_id=oid)
            except Exception:  # noqa: BLE001
                log.exception("contact-lifecycle-sweep failed for org %s", oid)
        if sent:
            log.info("contact-lifecycle-sweep: sent %d birthday message(s)", sent)


@job_runner.every(300, "webhook-retry-sweep")
async def retry_failed_webhooks() -> None:
    """Re-attempt webhook deliveries that failed, with a hard attempt cap.

    Moves transient webhook failures off the request path: the original POST is
    best-effort during the request; this sweep gives it durable retries.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.models import WebhookDelivery, WebhookEndpoint
    from backend.services.webhooks import _attempt, claim_delivery

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.status.in_(("failed", "pending")),
                WebhookDelivery.attempts < _MAX_ATTEMPTS,
            ).limit(100)
        )).scalars().all()
        if not rows:
            return
        retried = 0
        for d in rows:
            ep = (await db.execute(
                select(WebhookEndpoint).where(WebhookEndpoint.id == d.endpoint_id)
            )).scalar_one_or_none()
            if not ep or not ep.active:
                d.status = "abandoned"
                continue
            if not await claim_delivery(db, d, ("failed", "pending")):
                continue      # another worker (or the post-commit outbox) is on it
            await _attempt(d, ep.url, ep.secret, d.payload)
            await db.commit()
            retried += 1
        await db.commit()
        log.info("webhook-retry-sweep: re-attempted %d delivery(ies)", retried)


@job_runner.every(3600, "revoked-session-sweep")
async def sweep_revoked_sessions() -> None:
    """Drop logout records whose sign-in has expired anyway: those tokens fail on exp alone."""

    from sqlalchemy import delete

    from backend.db.database import AsyncSessionLocal
    from backend.models import RevokedSession

    async with AsyncSessionLocal() as db:
        res = await db.execute(delete(RevokedSession).where(RevokedSession.expires_at < now_utc()))
        await db.commit()
        if res.rowcount:
            log.info("revoked-session-sweep: removed %d expired record(s)", res.rowcount)


@job_runner.every(900, "chat-autocomplete-sweep")
async def autocomplete_replied_chats() -> None:
    """C3 — a chat the shop answered and the customer hasn't written back to in 24h is done:
    move it from Replied to Completed. A new customer message reopens it."""
    from backend.db.database import AsyncSessionLocal
    from backend.services.chat_status import autocomplete_replied

    async with AsyncSessionLocal() as db:
        done = await autocomplete_replied(db)
        if done:
            log.info("chat-autocomplete-sweep: completed %d replied chat(s)", done)
