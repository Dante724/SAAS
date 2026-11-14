"""Periodic jobs registered on the shared job_runner.

Importing this module registers the schedules; main.py imports it before
job_runner.start(). Kept separate from jobs.py to avoid import cycles
(this module pulls in models/services; jobs.py stays dependency-free).
"""
import logging

from sqlalchemy import select

from backend.core.jobs import job_runner
from backend.services.dates import now_utc
from backend.services.org_live import live_org_clause

log = logging.getLogger("tekly.jobs")


async def _per_shop(label: str, org_ids, fn) -> int:
    """Run ``fn(db, org_id)`` for each shop in a session of its own, scoped to that shop.

    Audit SEC-05: with no tenant set the ORM guards are off (reads aren't filtered and child rows
    with no org_id get no cross-shop check), and one session shared by every shop keeps shop 1's
    rows in its identity map while shop 2 runs. Here every shop gets a fresh session with
    ``set_tenant``, its work is committed on its own, and a failure is rolled back and logged
    without touching the shops after it. ``fn`` returns a count (or None). Platform-wide sweeps
    (SLA, chat autocomplete, retention, reconcile) don't use this: they are explicit opt-outs."""
    from backend.db.database import AsyncSessionLocal
    from backend.db.tenancy import set_tenant
    total = 0
    for oid in org_ids:
        async with AsyncSessionLocal() as db:
            set_tenant(db, oid)             # this session is this shop's only
            try:
                total += (await fn(db, oid)) or 0
                await db.commit()
            except Exception:  # noqa: BLE001 — one bad shop never stalls the sweep
                log.exception("%s failed for org %s", label, oid)
                await db.rollback()
    return total

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


# Both notification jobs below deliver the outbox. Only one may deliver at a time (in this process
# and, on Postgres, across processes): while one is sending, the other could otherwise reclaim
# a row it is still in the middle of sending and send it twice.
_DELIVERY = {"busy": False}


async def _deliver_exclusively(fn) -> bool:
    """Run fn as THE outbox deliverer, or return False straight away when another job is."""
    from backend.core.jobs import run_exclusive
    if _DELIVERY["busy"]:
        return False
    _DELIVERY["busy"] = True
    try:
        return await run_exclusive("notification-delivery", fn)
    finally:
        _DELIVERY["busy"] = False


@job_runner.every(120, "notification-sweep")
async def sweep_notifications() -> None:
    """N-GAP-1 — actually fire scheduled notifications.

    The rules engine queues appointment reminders and delayed / working-hours
    sends as NotificationLog rows with a future send_at; nothing was flushing
    them. This tick, per active org, (1) queues any reminders now inside their
    lead window and (2) dispatches every queued send whose time has come — unless
    the notification-outbox job is delivering right now, in which case it only
    queues and leaves the sending to that job.

    ponytail: per-org loop over active orgs — fine at pilot scale; swap to a
    single cross-org query if the org count ever makes this hot.
    """
    from backend.db.database import AsyncSessionLocal
    from backend.db.tenancy import set_tenant
    from backend.models import Organisation
    from backend.services import notification_rules as nr

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(
            select(Organisation.id).where(live_org_clause())   # suspended shops send nothing
        )).scalars().all()

    async def tick(deliver: bool) -> None:
        queued = sent = 0
        for oid in org_ids:
            # A session per org: a DB error leaves its session unusable until rolled back, and with
            # one shared session every org after the failing one failed too (512 orgs, 510 failed).
            async with AsyncSessionLocal() as db:
                set_tenant(db, oid)             # this session is this shop's only
                try:
                    queued += await nr.queue_reminders(db, org_id=oid)
                    queued += await nr.queue_appointment_reminders(db, org_id=oid)
                    if deliver:
                        res = await nr.run_due(db, org_id=oid)
                        sent += res.get("sent", 0)
                    await db.commit()
                except Exception:  # noqa: BLE001 — one bad org never stalls the sweep
                    log.exception("notification-sweep failed for org %s", oid)
                    # R2: a failed statement leaves the shared session needing a rollback; without
                    # it every later shop in this tick failed too.
                    await db.rollback()
        if queued or sent:
            log.info("notification-sweep: queued %d, sent %d", queued, sent)

    if not await _deliver_exclusively(lambda: tick(True)):
        await tick(False)


@job_runner.every(30, "notification-outbox")
async def deliver_notifications() -> None:
    """Send queued customer/staff messages within ~30 seconds. Rules only queue a message (the
    request never calls Twilio/SMTP); this delivers it, retries provider failures with backoff and
    reclaims sends a crashed worker left behind (services/notification_rules.run_due). One query
    finds the shops with something due, so idle shops cost nothing; each shop then gets its own
    session, and one shop's error never stops the others. Skips the tick while the slower
    notification-sweep is delivering (one deliverer at a time)."""
    from backend.db.database import AsyncSessionLocal
    from backend.db.tenancy import set_tenant
    from backend.services import notification_rules as nr

    async def tick() -> None:
        async with AsyncSessionLocal() as db:
            org_ids = await nr.orgs_with_due(db)
        totals = {"sent": 0, "retrying": 0, "dead": 0}
        for oid in org_ids:
            async with AsyncSessionLocal() as db:
                set_tenant(db, oid)             # this session is this shop's only
                try:
                    res = await nr.run_due(db, org_id=oid)
                    for k in totals:
                        totals[k] += res.get(k, 0)
                except Exception:  # noqa: BLE001 — one bad org never stalls the sweep
                    log.exception("notification-outbox failed for org %s", oid)
                    await db.rollback()
        if any(totals.values()):
            log.info("notification-outbox: sent %(sent)d, retrying %(retrying)d, gave up %(dead)d", totals)

    await _deliver_exclusively(tick)


@job_runner.every(3600, "automation-time-sweep")
async def sweep_time_automations() -> None:
    """Tasks phase 0 — fire time-based automations ("work order overdue", "product low on stock")
    hourly. Until now they only ran when someone pressed POST /api/automations/sweep.

    Only shops with an active time-based rule are visited. Safe to run repeatedly: every rule
    fires once per order overdue spell / per low-stock dip (automation_fires), and its tasks are
    de-duplicated by source_ref. Backlog guard: a rule only fires for orders that became overdue
    AFTER it went live (AutomationRule.activated_at), so switching a rule on doesn't create a task
    for every order that has been late for months — the manual sweep endpoint is the deliberate
    way to cover those. Low stock is a current state, so a newly enabled rule fires once for each
    product that is low right now (at most once until it recovers).
    """
    from backend.db.database import AsyncSessionLocal
    from backend.models import AutomationRule, Organisation
    from backend.services import automation as auto

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(select(AutomationRule.org_id)
                                    .join(Organisation, Organisation.id == AutomationRule.org_id).where(
            AutomationRule.active == True,  # noqa: E712
            AutomationRule.event.in_(auto.TIME_EVENTS), live_org_clause()).distinct())).scalars().all()
    fired = await _per_shop("automation-time-sweep", org_ids, auto.sweep_time_based)
    if fired:
        log.info("automation-time-sweep: fired %d rule action(s)", fired)


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
            select(Organisation.id).where(live_org_clause())   # suspended shops send nothing
        )).scalars().all()
    sent = await _per_shop("contact-lifecycle-sweep", org_ids,
                           lambda db, oid: nr.notify_contact_birthdays(db, org_id=oid))
    if sent:
        log.info("contact-lifecycle-sweep: sent %d birthday message(s)", sent)


@job_runner.every(3600, "membership-sweep")
async def sweep_memberships() -> None:
    """Memberships (docs/SPEC_memberships.md): monthly and renewal invoices, missed-payment pause
    and lapse, included-item reminders. Hourly, but every step is idempotent per day."""
    from backend.db.database import AsyncSessionLocal
    from backend.models import Membership, Organisation
    from backend.services import memberships as mb

    async with AsyncSessionLocal() as db:
        org_ids = (await db.execute(select(Membership.org_id).distinct()
                                    .join(Organisation, Organisation.id == Membership.org_id)
                                    .where(live_org_clause()))).scalars().all()   # suspended: no billing

    async def one(db, oid) -> int:
        org = (await db.execute(select(Organisation).where(
            Organisation.id == oid, live_org_clause()))).scalar_one_or_none()
        if org is None:
            return 0
        stats = await mb.sweep(db, org)
        await db.commit()
        if any((stats or {}).values()):
            log.info("membership-sweep %s: %s", oid, stats)
        return 1

    await _per_shop("membership-sweep", org_ids, one)


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


@job_runner.every(24 * 3600, "retention-purge")
async def purge_old_records() -> None:
    """Delete notifications, call records, messages and audit entries older than each shop's
    retention setting (none by default), and expired customer-portal links."""
    from backend.db.database import AsyncSessionLocal
    from backend.services.retention import purge_all

    async with AsyncSessionLocal() as db:
        done = {k: n for k, n in (await purge_all(db)).items() if n}
        if done:
            log.info("retention-purge: deleted %s", done)


@job_runner.every(24 * 3600, "passcode-encrypt-sweep")
async def encrypt_plain_passcodes() -> None:
    """Passcode-like custom fields saved before TOKEN_ENCRYPTION_KEY was set are encrypted
    here rather than waiting for someone to edit the record. Without a key it does nothing."""
    from backend.db.database import AsyncSessionLocal
    from backend.db.secret_json import encrypt_plain_rows

    async with AsyncSessionLocal() as db:
        conn = await db.connection()
        done = await conn.run_sync(encrypt_plain_rows)
        await db.commit()
        if done:
            log.info("passcode-encrypt-sweep: encrypted %d record(s)", done)


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


@job_runner.every(600, "superseded-charge-sweep")
async def sweep_superseded_charges() -> None:
    """Card-reader charges migration idg01 marked superseded: cancel them at the provider and keep
    polling them for 7 days, so a card taken anyway is booked and flagged. Also cancels pending
    charges the reader never confirmed, or that went stale, so they stop holding their reader
    (services/terminal.reconcile_superseded)."""
    from backend.db.database import AsyncSessionLocal
    from backend.services.terminal import reconcile_superseded

    async with AsyncSessionLocal() as db:
        checked = await reconcile_superseded(db)
        if checked:
            log.info("superseded-charge-sweep: checked %d charge(s)", checked)


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
                   live_org_clause()))).scalars().all()
    sent = await _per_shop("sense-brief-sweep", org_ids, sense.send_briefs)
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
                   live_org_clause()))).scalars().all()
    made = await _per_shop("sense-agents-sweep", org_ids, sense_agents.run_agents)
    if made:
        log.info("sense-agents-sweep: %d message(s) drafted or scheduled", made)


@job_runner.every(24 * 3600, "integrity-sweep", delay_first=True)
async def sweep_integrity() -> None:
    """Daily (first run one day after boot, so deploys and restarts don't each trigger a full
    sweep): every stored running total must equal the ledger it summarises (tills, client
    balances, invoice amount_paid, on-hand stock). Mismatches are logged at ERROR and counted in
    /metrics as repairos_integrity_mismatches; nothing is changed. See services/reconcile.py."""
    from backend.db.database import AsyncSessionLocal
    from backend.services.reconcile import sweep

    async with AsyncSessionLocal() as db:
        await sweep(db)


@job_runner.every(900, "task-overdue-sweep")
async def sweep_overdue_tasks() -> None:
    """tk02: tell every assignee once when an open task passes its due time (bell + push,
    kind task.overdue). Held outside the branch's working hours until the next opening; see
    services/task_alerts.py for the rules."""
    from backend.db.database import AsyncSessionLocal
    from backend.db.tenancy import set_tenant
    from backend.services.task_alerts import overdue_org_ids, sweep_overdue

    now = now_utc()
    async with AsyncSessionLocal() as db:
        org_ids = await overdue_org_ids(db, now)
    sent = 0
    for oid in org_ids:
        async with AsyncSessionLocal() as db:   # a session per shop: one failure never stalls the rest
            set_tenant(db, oid)                 # this session is this shop's only
            try:
                sent += await sweep_overdue(db, oid, now)
            except Exception:  # noqa: BLE001
                log.exception("task-overdue-sweep failed for org %s", oid)
                await db.rollback()
    if sent:
        log.info("task-overdue-sweep: alerted %d overdue task(s)", sent)


@job_runner.every(6 * 3600, "media-usage-sweep")
async def sweep_media_usage() -> None:
    """PERF-22: tell the platform admins (never the shop) once when a shop's stored media passes
    MEDIA_ALERT_BYTES; logged at ERROR on tekly.media and counted in /metrics. Platform-wide, reads
    sizes only. See services/media_usage.py."""
    from backend.db.database import AsyncSessionLocal
    from backend.services.media_usage import sweep

    async with AsyncSessionLocal() as db:
        await sweep(db)
