"""Periodic jobs registered on the shared job_runner.

Importing this module registers the schedules; main.py imports it before
job_runner.start(). Kept separate from jobs.py to avoid import cycles
(this module pulls in models/services; jobs.py stays dependency-free).
"""
import logging

from sqlalchemy import select

from backend.core.jobs import job_runner

log = logging.getLogger("tekly.jobs")

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
        # A session per org: a DB error leaves its session unusable until rolled back, and with
        # one shared session every org after the failing one failed too (512 orgs, 510 failed).
        async with AsyncSessionLocal() as db:
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
        async with AsyncSessionLocal() as db:   # per org, as in notification-sweep
            try:
                sent += await nr.notify_contact_birthdays(db, org_id=oid)
            except Exception:  # noqa: BLE001
                log.exception("contact-lifecycle-sweep failed for org %s", oid)
    if sent:
        log.info("contact-lifecycle-sweep: sent %d birthday message(s)", sent)


@job_runner.every(60, "webhook-retry-sweep")
async def retry_failed_webhooks() -> None:
    """Re-attempt webhook deliveries on the backoff schedule (1m, 5m, 30m, 2h, 8h, 24h), reclaim
    rows a crashed worker left in "sending", and switch off endpoints that have failed for 24 hours.
    The work lives in services.webhooks.retry_sweep (concurrent, bounded per endpoint)."""
    from backend.services.webhooks import retry_sweep

    retried = await retry_sweep()
    if retried:
        log.info("webhook-retry-sweep: re-attempted %d delivery(ies)", retried)


@job_runner.every(3600, "revoked-session-sweep")
async def sweep_revoked_sessions() -> None:
    """Drop logout records whose sign-in has expired anyway: those tokens fail on exp alone."""
    from datetime import datetime

    from sqlalchemy import delete

    from backend.db.database import AsyncSessionLocal
    from backend.models import RevokedSession

    async with AsyncSessionLocal() as db:
        res = await db.execute(delete(RevokedSession).where(RevokedSession.expires_at < datetime.utcnow()))
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

@job_runner.every(3600, "idempotency-sweep")
async def sweep_idempotency_keys() -> None:
    """Forget Idempotency-Keys older than 48h (services/idempotency.py)."""
    from backend.db.database import AsyncSessionLocal
    from backend.routers.mobile_messages import expire_open_handoffs
    from backend.services.idempotency import sweep

    async with AsyncSessionLocal() as db:
        removed = await sweep(db)
        if removed:
            log.info("idempotency-sweep: removed %d old key(s)", removed)
        expired = await expire_open_handoffs(db)
        if expired:
            log.info("idempotency-sweep: %d phone message(s) never confirmed, marked unknown", expired)


@job_runner.every(900, "sense-brief-sweep")
async def sweep_morning_briefs() -> None:
    """Tekly Sense: send each shop's morning brief once a day, after the owner's chosen hour
    in the shop's time zone. Only shops that turned email or push on have anything to send;
    send_briefs claims the day before sending, so a restart never sends twice."""
    from backend.db.database import AsyncSessionLocal
    from backend.models import AutomationSetting, Organisation
    from backend.services import sense

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(
            select(AutomationSetting.org_id)
            .join(Organisation, Organisation.id == AutomationSetting.org_id)
            .where(AutomationSetting.key == sense.SETTING_KEY, AutomationSetting.enabled == True,  # noqa: E712
                   Organisation.active == True))).scalars().all()  # noqa: E712
        sent = 0
        for oid in org_ids:
            try:
                sent += await sense.send_briefs(db, oid)
            except Exception:  # noqa: BLE001 — one bad org never stalls the sweep
                await db.rollback()
                log.exception("sense-brief-sweep failed for org %s", oid)
        if sent:
            log.info("sense-brief-sweep: sent %d brief(s)", sent)



@job_runner.every(3600, "sense-agents-sweep")
async def sweep_sense_agents() -> None:
    """Tekly Sense agents: draft (or schedule) collections, quote follow-up and win-back
    messages for shops that turned an agent on. Sending happens in notification-sweep, which
    re-checks consent, opening hours and the switches at the moment of sending."""
    from backend.db.database import AsyncSessionLocal
    from backend.models import AutomationSetting, Organisation
    from backend.services import sense, sense_agents

    if sense_agents.platform_off():
        return
    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(
            select(AutomationSetting.org_id)
            .join(Organisation, Organisation.id == AutomationSetting.org_id)
            .where(AutomationSetting.key == sense.SETTING_KEY, AutomationSetting.enabled == True,  # noqa: E712
                   Organisation.active == True))).scalars().all()  # noqa: E712
        made = 0
        for oid in org_ids:
            try:
                made += await sense_agents.run_agents(db, oid)
            except Exception:  # noqa: BLE001 — one bad org never stalls the sweep
                await db.rollback()
                log.exception("sense-agents-sweep failed for org %s", oid)
        if made:
            log.info("sense-agents-sweep: %d message(s) drafted or scheduled", made)
