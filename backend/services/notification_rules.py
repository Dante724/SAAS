"""Build 80 — the external-notification rules engine.

A NotificationRule decides *whether and when* to send; a NotificationTemplate is
*what* gets sent. Rules support what Orderry's per-module notification settings do:

  * event triggers (created / status_changed / closed) scoped to a status and a location
  * **delayed sending** — e.g. ask for feedback 60 minutes after the job closes
  * **working-hours-only** — a send that would land at 23:10 is held to the next
    working morning instead of waking the customer
  * **appointment reminders** — fire N minutes *before* an order's scheduled_for

Every send is an outbox row: evaluating a rule writes a NotificationLog with status='scheduled'
and a `send_at` (now, or later for delayed / out-of-hours / reminder sends), in the caller's
transaction. Nothing calls Twilio or SMTP inside a request. The worker tick (`run_due`) claims each
due row with a compare-and-set, sends it, retries transient provider errors with backoff, gives up
("dead") on permanent errors or after OUTBOX_MAX_ATTEMPTS, and reclaims rows a crashed worker left
in "sending". Same shape as the webhook outbox in services/webhooks.py.
"""
import asyncio
import atexit
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import (
    Appointment,
    NotificationLog,
    NotificationRule,
    NotificationTemplate,
    Order,
)
from backend.services.dates import now_utc
from backend.services.notifications import _adapter_for, channel_consented, notif_creds, try_oauth_email

logger = logging.getLogger("tekly.notifications")

log_ = logging.getLogger("tekly.notifications")


# Texts that market to the customer rather than tell them about their own job/booking: they use
# the non-transactional part of the operator's daily text budget (notifications.notif_creds).
def is_marketing_event(trigger_event: Optional[str]) -> bool:
    ev = trigger_event or ""
    return ev == "birthday" or ev.startswith("agent.win_back")


async def _send(db, org_id, channel, *, recipient, subject, body, log=None, headers=None, transactional=None):
    """144jn: send through THIS shop's own provider credentials (per-tenant).
    An email is HTML only when its template was when the message was filled (log.is_html, fixed
    when the log row is first saved); built-in and template-less messages are plain text.

    Email goes out from the shop's connected Gmail/Outlook account when there is one
    (same as the old template path, build 144ae) and falls back to SMTP / simulated.
    Mail that carries extra headers (List-Unsubscribe on Tekly Sense emails) stays on
    SMTP, since the mailbox send can't set them."""
    from backend.services.notifications import log_is_html
    html = await log_is_html(db, log) if log is not None else False
    if channel == "email" and not headers:
        result = await try_oauth_email(db, org_id, recipient=recipient, subject=subject, body=body, html=html)
        if result is not None:
            return result
    if transactional is None:
        transactional = not is_marketing_event(log.trigger_event if log is not None else None)
    creds = await notif_creds(db, org_id, channel, transactional=transactional)
    adapter = _adapter_for(channel, creds)
    if headers:
        return await adapter.send(recipient=recipient, subject=subject, body=body, html=html, headers=headers)
    return await adapter.send(recipient=recipient, subject=subject, body=body, html=html)

WORK_START_MIN = 9 * 60      # 09:00
WORK_END_MIN = 18 * 60       # 18:00


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def shift_into_working_hours(when: datetime, start_min: int = WORK_START_MIN,
                             end_min: int = WORK_END_MIN) -> datetime:
    """Push a send time into the working window. Before it → same-day open; after
    it → next day's open. 144jc: window is per-location when resolved by the caller,
    else the org default (09:00–18:00)."""
    minutes = when.hour * 60 + when.minute
    if minutes < start_min:
        return when.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
    if minutes >= end_min:
        nxt = when + timedelta(days=1)
        return nxt.replace(hour=start_min // 60, minute=start_min % 60, second=0, microsecond=0)
    return when


async def _location_window(db: AsyncSession, location_id: Optional[str],
                           when: datetime) -> tuple[int, int]:
    """(open_min, close_min) for a location on the day of `when`, from
    Location.working_hours JSON. Falls back to the org default when unset/closed.

    ponytail: a day marked closed just falls back to the default window rather
    than hunting for the next open day — fine at pilot scale.
    """
    if not location_id:
        return WORK_START_MIN, WORK_END_MIN
    from backend.models import Location
    loc = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
    wh = getattr(loc, "working_hours", None) if loc else None
    if not isinstance(wh, dict):
        return WORK_START_MIN, WORK_END_MIN
    day = wh.get(_WEEKDAYS[when.weekday()])
    if not isinstance(day, dict) or day.get("closed"):
        return WORK_START_MIN, WORK_END_MIN
    def _m(hhmm, default):
        try:
            h, m = str(hhmm).split(":"); return int(h) * 60 + int(m)
        except Exception:
            return default
    return _m(day.get("open"), WORK_START_MIN), _m(day.get("close"), WORK_END_MIN)


async def _into_working_hours(db: AsyncSession, org_id: str, location_id: Optional[str],
                              send_at: datetime) -> datetime:
    """Launch B6: send_at is UTC but opening hours are the shop's own clock. Shift on the shop's
    clock, then hand back UTC (a 09:00 opening in New York is 13:00 or 14:00 UTC, not 09:00)."""
    from backend.services.dates import shop_tzname, utc_to_wall, wall_to_utc
    tz = await shop_tzname(db, org_id, location_id)
    local = utc_to_wall(send_at, tz)
    s, e = await _location_window(db, location_id, local)
    return wall_to_utc(shift_into_working_hours(local, s, e), tz)


# Scheduled times are shop wall-clock, `now` and send_at are UTC. No shop is more than 14 hours
# from UTC, so a SQL window this much wider than the lead time can't miss an appointment; each
# row is then checked exactly on its own shop's clock.
_TZ_SLACK = timedelta(hours=14)


async def _reminder_send_at(db: AsyncSession, org_id: str, location_id: Optional[str],
                            start: datetime, lead: timedelta, now: datetime, tz_cache: dict):
    """When a reminder for a `start` (shop wall-clock) should go out, in UTC, or None when the
    start isn't inside the lead window yet (or has passed) on the shop's own clock."""
    from backend.services.dates import shop_tzname, utc_to_wall, wall_to_utc
    key = location_id or ""
    if key not in tz_cache:
        tz_cache[key] = await shop_tzname(db, org_id, location_id)
    tz = tz_cache[key]
    local_now = utc_to_wall(now, tz)
    if not (local_now < start <= local_now + lead):
        return None
    return wall_to_utc(start - lead, tz)


async def _latest_chat_channel(db: AsyncSession, org_id: str, client_id: str) -> Optional[str]:
    """The channel of the client's most recent conversation, if any (whatsapp|sms|email).
    Used by rules with chat_first=True so a reply lands where the client last talked to us."""
    from backend.models import ChatThread
    row = (await db.execute(
        select(ChatThread.channel).where(
            ChatThread.org_id == org_id, ChatThread.client_id == client_id)
        .order_by(ChatThread.last_message_at.desc().nullslast())
        .limit(1))).scalar_one_or_none()
    return row


def _rule_matches(rule: NotificationRule, *, event: str, status_key: Optional[str],
                  order: Order) -> bool:
    if not rule.active or rule.module != "order":
        return False
    if (rule.scope or "client") != "client":   # staff-scoped rules handled by notify_staff_order
        return False
    if rule.event != event:
        return False
    # 144jb: Jobs vs Estimates scoping — a record-type-scoped rule only fires for
    # that record type (None = any).
    if rule.record_type and getattr(order, "record_type", "work_order") != rule.record_type:
        return False
    if rule.trigger_status_key and rule.trigger_status_key != status_key:
        return False
    if rule.location_id and rule.location_id != order.location_id:
        return False
    return True


def _status_overrides(matched: list) -> list:
    """A status's own message replaces the catch-all ("Every status change" / "Job finished")
    on that channel, so the customer gets one message per channel, not two."""
    own = {r.channel for r in matched if r.trigger_status_key}
    return [r for r in matched if r.trigger_status_key or r.channel not in own]


async def _render(db: AsyncSession, org_id: str, order: Order, rule: NotificationRule):
    """Render the rule's template against the order's variables. Falls back to a
    plain status line when the rule has no template attached."""
    from backend.routers.documents import _resolve
    variables = await _resolve(db, org_id, order)
    if isinstance(variables, tuple):   # build 144s made _resolve return (doc_map, rows)
        variables = variables[0]
    tpl = None
    if rule.template_id:
        tpl = (await db.execute(select(NotificationTemplate).where(
            NotificationTemplate.id == rule.template_id))).scalar_one_or_none()
    if tpl:
        from backend.services.message_vars import render_for_order
        subject, body = await render_for_order(db, org_id, order, tpl.subject or None,
                                               tpl.body_template or "", doc_vars=variables)
        return subject, body, tpl.id
    # Sensible default per event when the rule has no template attached.
    ev = rule.event
    if ev == "created":
        return (f"Order {order.order_number} received",
                f"We've received your order {order.order_number}. We'll keep you posted.", None)
    if ev == "comment_added":
        return (f"Update on {order.order_number}",
                f"There's a new update on your order {order.order_number}.", None)
    if ev == "payment_received":
        return (f"Payment received — {order.order_number}",
                f"Thanks! We've recorded your payment on order {order.order_number}.", None)
    if ev == "closed":
        return (f"Order {order.order_number} complete",
                f"Your order {order.order_number} is complete.", None)
    from backend.routers.documents import _status_name
    status = await _status_name(db, org_id, order)
    return (f"Update on {order.order_number}",
            f"Your order {order.order_number} is now: {status}", None)


OPT_OUT_FOOTER = "Reply STOP to opt out."


async def with_opt_out(db: AsyncSession, org_id: str, client_id: Optional[str], channel: str, body: str) -> str:
    """US/Canada texting rules: the first text a customer gets from the shop says how to
    stop them. Later texts stay short. Email is unaffected (it has its own unsubscribe)."""
    if channel not in ("sms", "whatsapp") or not client_id or OPT_OUT_FOOTER in (body or ""):
        return body
    prior = (await db.execute(select(NotificationLog.id).where(
        NotificationLog.org_id == org_id, NotificationLog.client_id == client_id,
        NotificationLog.channel.in_(["sms", "whatsapp"]),
        NotificationLog.status.in_(["sent", "scheduled", "sending"])).limit(1))).scalar_one_or_none()
    return body if prior else f"{(body or '').rstrip()}\n{OPT_OUT_FOOTER}"


# Order events fired by a job ENTERING a status (orders.py: "closed" when the new status closes it).
STATUS_EVENTS = ("status_changed", "closed")


async def evaluate(db: AsyncSession, *, org_id: str, order_id: str, event: str,
                   status_key: Optional[str] = None, now: Optional[datetime] = None,
                   commit: bool = True) -> dict:
    """Run every matching rule for an order event and queue what it sends: immediate sends are
    due now, delayed / out-of-hours ones later. The outbox sweep (`run_due`) delivers them; this
    never calls a provider. commit=False leaves the rows in the caller's transaction, so they are
    saved together with the change that triggered them (or not at all)."""
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return {"sent": 0, "scheduled": 0, "matched": 0}
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == org_id)
        .options(selectinload(Order.client), selectinload(Order.lines),
                 selectinload(Order.device), selectinload(Order.assigned_to)))).scalar_one_or_none()
    if not order:
        return {"sent": 0, "scheduled": 0}

    matched = _status_overrides(
        [r for r in rules if _rule_matches(r, event=event, status_key=status_key, order=order)])
    queued = scheduled = 0
    for rule in matched:
        subject, body, tpl_id = await _render(db, org_id, order, rule)
        client = order.client
        channel = rule.channel
        # Build 144em: "try the client's latest chat first" — if the rule opts in and
        # the client has an open conversation, route through that channel instead.
        if rule.chat_first and client:
            chat_ch = await _latest_chat_channel(db, org_id, client.id)
            if chat_ch:
                channel = chat_ch
        recipient = (client.email if channel == "email" else client.phone) if client else None
        body = await with_opt_out(db, org_id, order.client_id, channel, body)

        send_at = now + timedelta(minutes=rule.delay_min or 0)
        if rule.working_hours_only:
            send_at = await _into_working_hours(db, org_id, order.location_id, send_at)

        log = NotificationLog(
            org_id=org_id, scope="client", channel=channel, trigger_event=event,
            template_id=tpl_id, rule_id=rule.id, order_id=order.id, client_id=order.client_id,
            recipient=recipient, subject=subject, body=body, send_at=send_at)
        if status_key and event in STATUS_EVENTS:
            # Tasks phase 0: a delayed "your device is ready" must not go out after the job has
            # moved on (collected, cancelled ...). run_due re-checks this before sending.
            log.expect_status = order.status
        db.add(log)

        if not recipient:
            log.status = "skipped"
            log.error = f"Client has no {channel} on file"
        elif not channel_consented(client, channel):
            log.status = "skipped"
            log.error = f"No client consent for {channel}"
        else:
            log.status = "scheduled"      # the outbox sweep sends it when due
            if send_at <= now:
                queued += 1
            else:
                scheduled += 1
    await (db.commit() if commit else db.flush())
    return {"sent": 0, "queued": queued, "scheduled": scheduled, "matched": len(matched)}


async def queue_reminders(db: AsyncSession, *, org_id: str,
                          now: Optional[datetime] = None) -> int:
    """For every `reminder` rule, queue a send for each order whose scheduled_for
    falls within the rule's lead time. Idempotent: an order already holding a
    scheduled/sent reminder for that rule is skipped."""
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return 0
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.event == "reminder",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    queued = 0
    tz_cache: dict = {}
    for rule in rules:
        lead = timedelta(minutes=rule.reminder_offset_min or 0)
        orders = (await db.execute(select(Order).where(
            Order.org_id == org_id, Order.scheduled_for.isnot(None),
            Order.scheduled_for > now - _TZ_SLACK, Order.scheduled_for <= now + lead + _TZ_SLACK,
            Order.archived == False)  # noqa: E712
            .options(selectinload(Order.client), selectinload(Order.lines),
                     selectinload(Order.device), selectinload(Order.assigned_to)))).scalars().all()
        for order in orders:
            if rule.location_id and rule.location_id != order.location_id:
                continue
            send_at = await _reminder_send_at(db, org_id, order.location_id, order.scheduled_for,
                                              lead, now, tz_cache)
            if send_at is None:
                continue
            # Any earlier row is a duplicate, whatever its status: a send that failed (a Twilio
            # timeout) used to be queued again on every 2-minute tick, ~30 texts an hour.
            dupe = (await db.execute(select(NotificationLog.id).where(
                NotificationLog.rule_id == rule.id, NotificationLog.order_id == order.id,
            ).limit(1))).scalar_one_or_none()
            if dupe:
                continue
            subject, body, tpl_id = await _render(db, org_id, order, rule)
            client = order.client
            recipient = (client.email if rule.channel == "email" else client.phone) if client else None
            body = await with_opt_out(db, org_id, order.client_id, rule.channel, body)
            if not recipient:
                status, err = "skipped", f"Client has no {rule.channel} on file"
            elif not channel_consented(client, rule.channel):
                status, err = "skipped", f"No client consent for {rule.channel}"
            else:
                status, err = "scheduled", None
            db.add(NotificationLog(
                org_id=org_id, scope="client", channel=rule.channel, trigger_event="reminder",
                template_id=tpl_id, rule_id=rule.id, order_id=order.id, client_id=order.client_id,
                recipient=recipient, subject=subject, body=body,
                send_at=send_at, status=status, error=err))
            queued += 1
    await db.commit()
    return queued


# ── The outbox: delivering queued notifications ────────────────────────────────────────────
# Wait before attempt 2, 3, ... (1m, 5m, 30m, 2h, 8h); after OUTBOX_MAX_ATTEMPTS sends the row is
# "dead". A permanent error (bad number/address, opted out, no provider) is dead at once.
RETRY_SCHEDULE_S = (60, 300, 1800, 7200, 28800)
OUTBOX_MAX_ATTEMPTS = 1 + len(RETRY_SCHEDULE_S)
# A row "sending" for this long was left by a worker that died mid-send (provider calls time out
# after 10 seconds). It is retried, so a crash can at worst repeat one message; it never loses it.
STUCK_SENDING_AFTER = timedelta(minutes=10)
OUTBOX_BATCH = 200                 # per shop per tick; the rest go next tick
# A batch stops claiming new rows after this long, and one send is cut off after SEND_TIMEOUT_S,
# so nothing a batch claims can sit in "sending" anywhere near STUCK_SENDING_AFTER.
OUTBOX_BATCH_SECONDS = 120
SEND_TIMEOUT_S = 60
LAST_ERROR_MAX = 240
INTERRUPTED = "Interrupted while sending"

_PERMANENT_TEXT = (
    "on file",                        # "No phone number on file", "Client has no sms on file"
    "no recipient",
    "consent",                        # "No client consent for sms"
    "opted out",
    "unsubscribed",
    "provider is configured",         # a deploy with no provider for the channel
    "must be a public address",       # a shop SMTP host we refuse to connect to
    "live twilio texting is off",     # test servers refuse billed texts (twilio_voice.sms_refusal)
)


def is_permanent_error(error: Optional[str]) -> bool:
    """Would sending the same message again fail the same way? Then retrying is pointless (and,
    for a customer who replied STOP, wrong). Unknown errors count as transient: they get the
    bounded retries and then end "dead" anyway."""
    if not error:
        return False
    e = error.lower()
    if any(t in e for t in _PERMANENT_TEXT):
        return True
    m = re.match(r"twilio (\d{3})\b", e)
    if m:   # a 4xx is the request itself (bad/unsubscribed number), except auth, timeout, rate limit
        code = int(m.group(1))
        return 400 <= code < 500 and code not in (401, 403, 408, 429)
    if e.startswith("smtp"):  # the server refused the recipient address (550 / 5.1.x)
        return bool(re.search(r"@[^']*': \(5[0-5]\d,|\b5\.1\.\d+\b", e))
    return False


def next_retry_delay(attempts: int) -> Optional[int]:
    """Seconds to wait after the `attempts`-th failed send, or None once out of attempts."""
    if attempts < 1 or attempts >= OUTBOX_MAX_ATTEMPTS:
        return None
    return RETRY_SCHEDULE_S[attempts - 1]


def _short(error: Optional[str]) -> Optional[str]:
    return (str(error).strip()[:LAST_ERROR_MAX] or None) if error else None


def _due(now: datetime):
    """A queued row whose time has come (and, after a failed try, whose backoff is over)."""
    return and_(NotificationLog.status == "scheduled",
                NotificationLog.send_at.isnot(None), NotificationLog.send_at <= now,
                or_(NotificationLog.next_attempt_at.is_(None), NotificationLog.next_attempt_at <= now))


def _stuck(now: datetime):
    return and_(NotificationLog.status == "sending", NotificationLog.claimed_at.isnot(None),
                NotificationLog.claimed_at < now - STUCK_SENDING_AFTER)


async def orgs_with_due(db: AsyncSession, now: Optional[datetime] = None) -> list:
    """Active shops that have something for the outbox to do right now (one query for all)."""
    from backend.models import Organisation
    now = now or now_utc()
    return list((await db.execute(
        select(NotificationLog.org_id).distinct()
        .join(Organisation, Organisation.id == NotificationLog.org_id)
        .where(Organisation.active == True, or_(_due(now), _stuck(now)))  # noqa: E712
    )).scalars().all())


async def _reclaim_stuck(db: AsyncSession, org_id: str, now: datetime) -> int:
    """Rows a crashed worker left in "sending": the interrupted try counts as an attempt (so a
    message that crashes the worker every time still runs out), then they are due again. Old rows
    from before the outbox have no claimed_at and are left alone, as they always were."""
    retry = await db.execute(update(NotificationLog).where(
        NotificationLog.org_id == org_id, _stuck(now),
        NotificationLog.attempts + 1 < OUTBOX_MAX_ATTEMPTS)
        .values(status="scheduled", attempts=NotificationLog.attempts + 1, next_attempt_at=now,
                last_error=INTERRUPTED, claimed_at=None)
        .execution_options(synchronize_session=False))
    # Out of attempts: dead. Through the ORM so the row is redacted like every finished message.
    spent = (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org_id, _stuck(now))
        .execution_options(populate_existing=True))).scalars().all()
    for row in spent:
        row.attempts = (row.attempts or 0) + 1
        row.status, row.error, row.last_error = "dead", INTERRUPTED, INTERRUPTED
        row.claimed_at = row.next_attempt_at = None
    await db.commit()
    n = (retry.rowcount or 0) + len(spent)
    if n:
        log_.warning("notification outbox: org %s reclaimed %d stuck message(s), %d given up",
                     org_id, n, len(spent))
    return n


async def _claim(db: AsyncSession, org_id: str, log_id: str, now: datetime) -> bool:
    """Compare-and-set scheduled → sending, committed, so exactly one sweep (in any process) sends
    a row. Re-checks that it is still due: another sweep may have just tried and backed it off.
    claimed_at is the moment of THIS claim (not when the batch started): a long batch must not
    make its later rows look stuck to the reclaimer."""
    res = await db.execute(update(NotificationLog)
        .where(NotificationLog.id == log_id, NotificationLog.org_id == org_id, _due(now))
        .values(status="sending", claimed_at=now_utc()).execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


async def _hold_or_skip(db: AsyncSession, org_id: str, log, now: datetime) -> bool:
    """Checks made at the moment of sending, on a row this sweep has claimed. True when the row
    was settled here (skipped, or put back to wait) and must not be sent now."""
    if not log.recipient:
        log.status, log.claimed_at = "skipped", None
        return True
    # Tekly Sense agent messages: the agent must still be on (and the platform switch off),
    # win-back still needs marketing consent, and nothing goes outside opening hours.
    if (log.trigger_event or "").startswith("agent."):
        from backend.services import sense_agents
        verdict, why = await sense_agents.send_check(db, org_id, log, now)
        if verdict == "later":
            log.status, log.send_at, log.next_attempt_at, log.claimed_at = "scheduled", why, None, None
            return True
        if verdict == "stop":
            log.status, log.error, log.claimed_at = "skipped", why, None
            return True
    # Send-time status guard: a message queued because the job entered a status is only
    # still true while the job is in it.
    if log.expect_status and log.order_id:
        cur = (await db.execute(select(Order.status).where(
            Order.id == log.order_id, Order.org_id == org_id))).scalar_one_or_none()
        if cur != log.expect_status:
            log.status, log.claimed_at = "skipped", None
            log.error = (f"Job left status {log.expect_status!r} before this was sent"
                         + (f" (now {cur!r})" if cur else " (job no longer on file)"))[:500]
            return True
    # Consent is checked again at send time: a customer who replied STOP after this
    # message was queued must not receive it.
    if log.client_id:
        from backend.models import Client
        c = (await db.execute(select(Client).where(
            Client.id == log.client_id, Client.org_id == org_id))).scalar_one_or_none()
        if c is None or not channel_consented(c, log.channel):
            log.status, log.claimed_at = "skipped", None
            log.error = ("Client no longer on file" if c is None
                         else f"No client consent for {log.channel}")
            return True
    return False


def _record(log, result: dict) -> str:
    """Write one send's outcome on the row: sent, retrying (back to scheduled, later) or dead.
    The backoff runs from when this send finished, not from when the batch started."""
    log.attempts = (log.attempts or 0) + 1
    log.claimed_at = None
    if (result or {}).get("status") == "sent":
        log.status, log.error, log.next_attempt_at = "sent", None, None
        return "sent"
    err = _short((result or {}).get("error")) or "Delivery failed"
    log.last_error = err
    delay = None if is_permanent_error(err) else next_retry_delay(log.attempts)
    if delay is None:
        log.status, log.error, log.next_attempt_at = "dead", err, None
        return "dead"
    # Still "scheduled" (not "failed"): it is still owed, and a queued row keeps its live portal
    # link (services/notifications._freeze_log redacts finished ones).
    log.status, log.error = "scheduled", None
    log.next_attempt_at = now_utc() + timedelta(seconds=delay)
    return "retrying"


async def run_due(db: AsyncSession, *, org_id: str, now: Optional[datetime] = None,
                  limit: int = OUTBOX_BATCH) -> dict:
    """Outbox tick for one shop: reclaim stuck rows, then claim and send every due one, committing
    after each so a crash never re-sends what already went out."""
    now = now or now_utc()
    reclaimed = await _reclaim_stuck(db, org_id, now)
    due = (await db.execute(select(NotificationLog.id).where(
        NotificationLog.org_id == org_id, _due(now))
        .order_by(NotificationLog.send_at).limit(limit))).scalars().all()
    counts = {"sent": 0, "retrying": 0, "dead": 0}
    started = time.monotonic()
    for i, log_id in enumerate(due):
        if i and time.monotonic() - started > OUTBOX_BATCH_SECONDS:
            break                                     # out of time: the rest go next tick
        if not await _claim(db, org_id, log_id, now):
            continue                                  # another sweep has it
        log = (await db.execute(select(NotificationLog).where(
            NotificationLog.id == log_id, NotificationLog.org_id == org_id)
            .execution_options(populate_existing=True))).scalar_one()
        if await _hold_or_skip(db, org_id, log, now):
            await db.commit()
            continue
        # Sense emails carry List-Unsubscribe headers (and have no template, so they are plain
        # text either way); every other message passes its log so HTML-ness follows the template.
        extra = {"log": log}
        if (log.trigger_event or "").startswith("agent."):
            from backend.services.sense_agents import email_headers
            h = email_headers(log)
            extra = {"headers": h} if h else {}   # no template: plain text, nothing to look up
        if is_marketing_event(log.trigger_event):
            # Win-back / birthday: the marketing part of the operator's daily text budget. (Agent
            # texts carry no log here, so _send can't tell from it; everything else is transactional.)
            extra["transactional"] = False
        try:
            result = await asyncio.wait_for(_send(db, org_id, log.channel,
                recipient=log.recipient, subject=log.subject, body=log.body, **extra), SEND_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the rows behind it
            # Only the error type: a raw message could carry the recipient or provider details.
            result = {"status": "failed",
                      "error": "Timed out" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__}
            await db.rollback()                       # the claim is committed; drop anything half-done
            log = (await db.execute(select(NotificationLog).where(
                NotificationLog.id == log_id, NotificationLog.org_id == org_id)
                .execution_options(populate_existing=True))).scalar_one()
        # A worker that dies here (killed, cancelled) leaves the row "sending"; _reclaim_stuck retries it.
        outcome = _record(log, result)
        await db.commit()
        counts[outcome] += 1
        if outcome != "sent":
            log_.warning("notification %s (org %s, %s) %s after attempt %d: %s", log_id, org_id,
                         log.channel, "will retry" if outcome == "retrying" else "gave up",
                         log.attempts, log.last_error)
    return {"due": len(due), "sent": counts["sent"], "failed": counts["retrying"] + counts["dead"],
            "retrying": counts["retrying"], "dead": counts["dead"], "reclaimed": reclaimed}


# ── Account emails (password reset, email verification) ─────────────────────────────────────
# These don't go through the outbox: the body holds a one-time sign-in link whose token is only
# ever stored hashed, and the shop's message history is readable by staff, so the link must not
# be written to notification_logs. They also go out from the platform's mail account, not the
# shop's. Instead: the first try as before, then a bounded retry in the background on a
# transient failure, and every failure logged (never the link).
ACCOUNT_EMAIL_RETRY_S = (5, 30)
_ACCOUNT_EMAIL_TASKS: set = set()
_EMAIL_ADDR = re.compile(r"[^\s'\"<>]+@[^\s'\"<>]+")


def _mail_failed(res) -> Optional[str]:
    """The error of a failed send, else None (fakes and old callers may return nothing)."""
    if isinstance(res, dict) and res.get("status") not in (None, "sent"):
        # Scrub first, then shorten: shortening first can cut an address in half and leave the
        # local part where the pattern no longer sees it.
        return _short(_EMAIL_ADDR.sub("<address>", str(res.get("error") or ""))) or "Delivery failed"
    return None


async def _account_try(send, recipient, subject, body):
    try:
        return _mail_failed(await send(recipient, subject, body))
    except Exception as exc:  # noqa: BLE001 — a crashed send is a failed try, not a crashed request
        return type(exc).__name__


async def send_account_email(send, recipient: str, subject: str, body: str, *, what: str) -> None:
    """Send an account email with `send` (send_direct_email, looked up by the caller so tests can
    swap it). A transient failure is retried in the background (ACCOUNT_EMAIL_RETRY_S)."""
    err = await _account_try(send, recipient, subject, body)
    if err is None:
        return
    who = (recipient or "")[:3] + "…"
    if is_permanent_error(err) or not ACCOUNT_EMAIL_RETRY_S:
        log_.error("%s email to %s not sent: %s", what, who, err)
        return
    log_.warning("%s email to %s failed, will retry: %s", what, who, err)

    async def _retry():
        last = err
        for attempt, delay in enumerate(ACCOUNT_EMAIL_RETRY_S, start=2):
            await asyncio.sleep(delay)
            last = await _account_try(send, recipient, subject, body)
            if last is None:
                log_.info("%s email to %s sent on attempt %d", what, who, attempt)
                return
            if is_permanent_error(last):
                break
        log_.error("%s email to %s not sent after retrying: %s", what, who, last)

    task = asyncio.get_running_loop().create_task(_retry())
    _ACCOUNT_EMAIL_TASKS.add(task)          # kept referenced until done
    task.add_done_callback(_ACCOUNT_EMAIL_TASKS.discard)


@atexit.register
def warn_pending_account_emails() -> int:
    """At shutdown, say how many account-email retries were still waiting (they live in memory
    and are lost). Only a count: never an address or a link."""
    pending = sum(1 for t in _ACCOUNT_EMAIL_TASKS if not t.done())
    if pending:
        log_.warning("shutting down with %d account email retry(ies) not finished; "
                     "those emails were not sent", pending)
    return pending


# ── Build 85: appointment notifications ──────────────────────────────────────────
async def evaluate_appointment(db: AsyncSession, *, org_id: str, appointment_id: str, event: str,
                           now: Optional[datetime] = None) -> dict:
    """Fire `module="appointment"` rules on create / status change. The appointment module
    was declared back in build 80 but nothing evaluated it — this closes that loop."""
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return {"sent": 0, "scheduled": 0, "matched": 0}
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "appointment",
        NotificationRule.event == event,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return {"sent": 0, "scheduled": 0, "matched": 0}
    b = (await db.execute(select(Appointment).where(
        Appointment.id == appointment_id, Appointment.org_id == org_id))).scalar_one_or_none()
    if not b:
        return {"sent": 0, "scheduled": 0, "matched": 0}

    queued = scheduled = 0
    for rule in rules:
        if rule.location_id and rule.location_id != b.location_id:
            continue
        recipient = await _appointment_recipient(db, b, rule.channel)
        # Build 144bs: message reads correctly for confirm / reschedule / cancel
        when = b.scheduled_for.strftime('%Y-%m-%d %H:%M')
        if event == "changed":
            subject, body = "Appointment updated", f"Your appointment has been moved to {when}."
        elif event == "status_changed" and b.status == "canceled":
            subject, body = "Appointment cancelled", f"Your appointment on {when} has been cancelled."
        elif event == "status_changed" and b.status == "no_show":
            subject, body = "Missed appointment", f"We missed you at your {when} appointment — get in touch to rebook."
        elif event == "status_changed":
            subject, body = "Appointment update", f"Update on your {when} appointment: {b.status}."
        else:
            subject, body = "Your appointment", f"Your appointment is booked for {when}."
        send_at = now + timedelta(minutes=rule.delay_min or 0)
        if rule.working_hours_only:
            send_at = await _into_working_hours(db, org_id, b.location_id, send_at)

        log = NotificationLog(
            org_id=org_id, scope="client", channel=rule.channel, trigger_event=event,
            rule_id=rule.id, client_id=b.client_id, recipient=recipient,
            subject=subject, body=body, send_at=send_at)
        db.add(log)
        if not recipient:
            log.status = "skipped"
            log.error = f"No {rule.channel} on file"
        elif not await _appointment_consented(db, b, rule.channel):
            log.status = "skipped"
            log.error = f"No client consent for {rule.channel}"
        else:
            log.status = "scheduled"      # the outbox sweep sends it when due
            if send_at <= now:
                queued += 1
            else:
                scheduled += 1
    await db.commit()
    return {"sent": 0, "queued": queued, "scheduled": scheduled, "matched": len(rules)}


async def _appointment_consented(db: AsyncSession, b: Appointment, channel: str) -> bool:
    """Same consent gate as order notifications. A walk-in booking with no client record has
    nothing to check against and is allowed (they gave the number to be contacted about it)."""
    if not b.client_id:
        return True
    from backend.models import Client
    c = (await db.execute(select(Client).where(
        Client.id == b.client_id, Client.org_id == b.org_id))).scalar_one_or_none()
    return c is not None and channel_consented(c, channel)   # a removed client gets nothing


async def _appointment_recipient(db: AsyncSession, b: Appointment, channel: str) -> Optional[str]:
    if b.client_id:
        from backend.models import Client
        c = (await db.execute(select(Client).where(Client.id == b.client_id))).scalar_one_or_none()
        if c:
            return c.email if channel == "email" else c.phone
    return b.client_phone if channel != "email" else None


async def queue_appointment_reminders(db: AsyncSession, *, org_id: str,
                                  now: Optional[datetime] = None) -> int:
    """Appointment reminders: for each `reminder` appointment rule, queue a send for every
    appointment whose start is now inside the rule's lead time. Idempotent per rule+appointment."""
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return 0
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "appointment",
        NotificationRule.event == "reminder",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    queued = 0
    tz_cache: dict = {}
    for rule in rules:
        lead = timedelta(minutes=rule.reminder_offset_min or 0)
        rows = (await db.execute(select(Appointment).where(
            Appointment.org_id == org_id, Appointment.status == "booked",
            Appointment.scheduled_for > now - _TZ_SLACK,
            Appointment.scheduled_for <= now + lead + _TZ_SLACK))).scalars().all()
        for b in rows:
            if rule.location_id and rule.location_id != b.location_id:
                continue
            send_at = await _reminder_send_at(db, org_id, b.location_id, b.scheduled_for,
                                              lead, now, tz_cache)
            if send_at is None:
                continue
            # Any earlier row for this appointment's slot is a duplicate, failed ones included.
            dupe = (await db.execute(select(NotificationLog.id).where(
                NotificationLog.rule_id == rule.id,
                NotificationLog.client_id == b.client_id,
                NotificationLog.trigger_event == "reminder",
                NotificationLog.send_at == send_at).limit(1))).scalar_one_or_none()
            if dupe:
                continue
            recipient = await _appointment_recipient(db, b, rule.channel)
            db.add(NotificationLog(
                org_id=org_id, scope="client", channel=rule.channel,
                trigger_event="reminder", rule_id=rule.id, client_id=b.client_id,
                recipient=recipient, subject="Appointment reminder",
                body=(f"Reminder: your appointment is at "
                      f"{b.scheduled_for.strftime('%Y-%m-%d %H:%M')}."),
                send_at=send_at,
                status="scheduled" if recipient else "skipped",
                error=None if recipient else f"No {rule.channel} on file"))
            queued += 1
    await db.commit()
    return queued


# ── 144jb: Internal (staff) notifications for order Workflows ────────────────
async def notify_staff_order(db: AsyncSession, *, org_id: str, order_id: str, event: str,
                             status_key: Optional[str] = None,
                             actor_id: Optional[str] = None,
                             now: Optional[datetime] = None, commit: bool = True,
                             push_later: Optional[list] = None) -> int:
    """Fire `module="order"`, `scope="staff"` rules on an order event — Orderry's
    Internal › Workflows. Targets the people responsible for the job: the assigned
    technician and the order's manager. Never notifies the person who made the change.

    Channel `push`/`in_app` → in-app bell + web push (StaffNotification). `email`/`sms`
    → a NotificationLog row queued for the employee's email/phone (sent by the outbox sweep).
    Returns how many notifications were made (bell items + queued messages).

    push_later: collect the web pushes into this list instead of sending them, for a caller
    running inside a transaction; it sends them with push.dispatch_deferred after its commit.
    """
    from backend.models import Employee as _Emp
    from backend.services import push as _push
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "order",
        NotificationRule.scope == "staff", NotificationRule.event == event,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return 0
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not order:
        return 0

    sent = 0
    for rule in rules:
        if rule.record_type and getattr(order, "record_type", "work_order") != rule.record_type:
            continue
        if rule.trigger_status_key and rule.trigger_status_key != status_key:
            continue
        if rule.location_id and rule.location_id != order.location_id:
            continue
        targets = {order.assigned_to_id, order.manager_id}
        targets.discard(None)
        targets.discard(actor_id)          # don't notify whoever made the change
        if not targets:
            continue
        subject = f"{order.order_number}: {event.replace('_', ' ')}"
        body = (f"Work order {order.order_number} is now {order.status}."
                if status_key else f"Work order {order.order_number}: {event.replace('_', ' ')}.")
        for emp_id in targets:
            if rule.channel in ("push", "in_app"):
                await _push.notify(db, org_id=org_id, employee_id=emp_id,
                                   kind=f"workflow.{event}", title=subject, body=body,
                                   deep_link=f"/tech#/order/{order.id}", commit=False,
                                   deferred=push_later)
                sent += 1
                continue
            emp = (await db.execute(select(_Emp).where(_Emp.id == emp_id))).scalar_one_or_none()
            if not emp:
                continue
            recipient = emp.email if rule.channel == "email" else getattr(emp, "phone", None)
            log = NotificationLog(
                org_id=org_id, scope="employee", channel=rule.channel,
                trigger_event=f"workflow_{event}", rule_id=rule.id, order_id=order.id,
                employee_id=emp_id, recipient=recipient, subject=subject, body=body, send_at=now)
            db.add(log)
            if not recipient:
                log.status = "skipped"
                log.error = f"Employee has no {rule.channel}"
            else:
                log.status = "scheduled"      # the outbox sweep sends it
                sent += 1
    await (db.commit() if commit else db.flush())
    return sent


# ── 144ja: Contacts module — birthday / lifecycle notifications ──────────────
async def notify_contact_birthdays(db: AsyncSession, *, org_id: str,
                                   now: Optional[datetime] = None) -> int:
    """Fire `module="contact"`, `event="birthday"` rules for every client whose
    birthday is today. Orderry's Contacts notifications cover exactly this: a
    lifecycle greeting to the contact, not tied to any order.

    Idempotent per client+rule+year: a client who already has a birthday send this
    calendar year is skipped, so the daily sweep never double-greets. Returns how many
    greetings were queued (the outbox sweep sends them).
    """
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return 0
    from backend.models import Client, NotificationTemplate
    now = now or now_utc()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "contact",
        NotificationRule.event == "birthday",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return 0

    from backend.services.dates import day_start_utc, shop_tzname, zone
    tz = await shop_tzname(db, org_id)
    local = now.replace(tzinfo=timezone.utc).astimezone(zone(tz))
    today = f"{local.month:02d}-{local.day:02d}"   # the shop's calendar day, not UTC's
    clients = (await db.execute(select(Client).where(
        Client.org_id == org_id, Client.birthday.isnot(None),
        Client.blacklisted == False))).scalars().all()  # noqa: E712
    # The shop's year, as a UTC instant: a UTC year boundary inside the shop's 31 Dec / 1 Jan
    # would otherwise forget this year's greeting and send it twice.
    year_start = day_start_utc(local.date().replace(month=1, day=1), tz)
    sent = 0
    for rule in rules:
        tpl = None
        if rule.template_id:
            tpl = (await db.execute(select(NotificationTemplate).where(
                NotificationTemplate.id == rule.template_id))).scalar_one_or_none()
        for c in clients:
            bday = (c.birthday or "").strip()
            if len(bday) < 5 or bday[-5:] != today:
                continue
            dupe = (await db.execute(select(NotificationLog.id).where(
                NotificationLog.rule_id == rule.id, NotificationLog.client_id == c.id,
                NotificationLog.trigger_event == "birthday",
                NotificationLog.created_at >= year_start))).scalar_one_or_none()
            if dupe:
                continue
            first = (c.name or "").split()[0] if c.name else "there"
            if tpl:
                from backend.services import message_vars
                cmap = {"{Client Name}": c.name or "", "{First Name}": first,
                        "{Client First Name}": first, "{Client Email}": c.email or "",
                        "{Client Phone}": c.phone or ""}
                short = await message_vars.client_values(db, org_id, c, tpl.subject, tpl.body_template)
                subject = message_vars.apply(tpl.subject or "Happy birthday!", short, cmap)
                body = message_vars.apply(tpl.body_template or "", short, cmap)
            else:
                subject = "Happy birthday!"
                body = f"Happy birthday, {first}! Wishing you a great day from all of us."
            recipient = c.email if rule.channel == "email" else c.phone
            log = NotificationLog(
                org_id=org_id, scope="client", channel=rule.channel,
                trigger_event="birthday", template_id=tpl.id if tpl else None,
                rule_id=rule.id, client_id=c.id, recipient=recipient,
                subject=subject, body=body, send_at=now)
            db.add(log)
            if not recipient:
                log.status = "skipped"
                log.error = f"Client has no {rule.channel} on file"
            elif not channel_consented(c, rule.channel):
                log.status = "skipped"
                log.error = f"No client consent for {rule.channel}"
            else:
                log.status = "scheduled"      # the outbox sweep sends it
                sent += 1
    await db.commit()
    return sent


# ── Build 86: internal task notifications ────────────────────────────────────
# Tasks phase 0: task alerts land in the staff bell (StaffNotification) + Web Push, like missed
# calls and job assignments, in addition to any email/SMS rules the shop configured.
TASK_PUSH_KINDS = {"assigned": "task.assigned", "changed": "task.changed", "completed": "task.completed",
                   "overdue": "task.overdue"}
_TASK_BELL_TITLES = {"assigned": "New task for you", "changed": "Task updated",
                     "completed": "Task completed", "overdue": "Task overdue"}
TASK_LINK = "/tasks?task={id}"   # every task alert opens the task (bell row and push payload)


def task_people(task) -> set:
    """Everyone the task is assigned to: the primary owner and every extra assignee."""
    return {i for i in ([task.assigned_to_id] + list(task.assignee_ids or [])) if i}


def _task_targets(task, event: str, actor_id: Optional[str], recipients=None) -> set:
    """Who hears about `event`. `recipients` narrows "assigned" to the people just added, so an
    edit never re-announces an assignment. Never the person who acted."""
    if recipients is not None:
        targets = {i for i in recipients if i}
    elif event in ("assigned", "overdue"):
        targets = task_people(task)
    elif event in ("changed", "completed"):
        targets = task_people(task) | ({task.created_by_id} if task.created_by_id else set())
    else:
        targets = set()
    targets.discard(actor_id)
    return targets


def task_due_label(due_at, tzname: Optional[str]) -> str:
    """A task's due time as staff read it, on the shop's clock: "today 14:30", "12 Nov 14:30";
    a date-only due (23:59:59 shop time) has no time: "today", "12 Nov"."""
    from backend.services.dates import today_in, utc_to_wall
    wall = utc_to_wall(due_at, tzname)
    day = "today" if wall.date() == today_in(tzname) else f"{wall:%d %b}".lstrip("0")
    if (wall.hour, wall.minute, wall.second) == (23, 59, 59):
        return day
    return f"{day} {wall:%H:%M}"


async def notify_task_bell(db: AsyncSession, *, org_id: str, task, event: str,
                           actor_id: Optional[str] = None, recipients=None,
                           deferred: Optional[list] = None, tz: Optional[str] = None) -> int:
    """Staff bell + push for a task event. "changed" is collapsed: while someone still has an
    unread "Task updated" for this task, further edits and comments don't add another.
    `deferred`: collect the pushes there (push.notify) for the caller to send after its commit.
    `tz`: the shop's zone when the caller already has it (the overdue sweep)."""
    from backend.models import Employee as _Emp
    from backend.models import StaffNotification
    from backend.services.push import notify as push_notify
    kind = TASK_PUSH_KINDS.get(event)
    targets = _task_targets(task, event, actor_id, recipients)
    if not kind or not targets:
        return 0
    live = set((await db.execute(select(_Emp.id).where(
        _Emp.id.in_(targets), _Emp.org_id == org_id, _Emp.active == True))).scalars().all())  # noqa: E712
    link = TASK_LINK.format(id=task.id)
    if task.due_at and tz is None:
        from backend.services.dates import shop_tzname
        tz = await shop_tzname(db, org_id)
    body = (task.title or "")[:200] + (f" · due {task_due_label(task.due_at, tz)}" if task.due_at else "")
    sent = 0
    for emp_id in sorted(live):
        if event == "changed":
            pending = (await db.execute(select(StaffNotification.id).where(
                StaffNotification.employee_id == emp_id, StaffNotification.kind == kind,
                StaffNotification.deep_link == link, StaffNotification.read_at.is_(None),
                StaffNotification.dismissed_at.is_(None)).limit(1))).scalar_one_or_none()
            if pending:
                continue
        try:
            if await push_notify(db, org_id=org_id, employee_id=emp_id, kind=kind,
                                 title=_TASK_BELL_TITLES[event], body=body, deep_link=link,
                                 commit=False, deferred=deferred):
                sent += 1
        except Exception:  # noqa: BLE001 — one person's push must not stop the others
            logger.warning("task bell/push failed (task=%s employee=%s)", task.id, emp_id, exc_info=True)
    return sent


async def notify_task(db: AsyncSession, *, org_id: str, task, event: str,
                      actor_id: Optional[str] = None, recipients=None,
                      deferred: Optional[list] = None, tz: Optional[str] = None) -> int:
    """Notify the people involved in a task: every assignee, plus the author on changes and
    completion.

    Orderry's rule, which we follow: **you are never notified about something you did
    yourself.** Create a task for yourself and you get nothing; another employee
    creates one for you and you hear about it.

    Always goes to the staff bell + push (notify_task_bell). Shops can add email/SMS on top with
    `module="task"` rules, which resolve the recipient from the employee record.
    """
    from backend.models import Employee as _Emp

    try:
        await notify_task_bell(db, org_id=org_id, task=task, event=event, actor_id=actor_id,
                               recipients=recipients, deferred=deferred, tz=tz)
    except Exception:  # noqa: BLE001 — logged; the email/SMS rules still run
        logger.warning("task bell notification failed (task=%s event=%s)", task.id, event, exc_info=True)

    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "task",
        NotificationRule.event == event,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return 0

    targets = _task_targets(task, event, actor_id, recipients)
    if not targets:
        return 0

    sent = 0
    now = now_utc()
    if task.due_at and tz is None:
        from backend.services.dates import shop_tzname
        tz = await shop_tzname(db, org_id)
    due = f" Due {task_due_label(task.due_at, tz)}." if task.due_at else ""
    for rule in rules:
        for emp_id in sorted(targets):
            emp = (await db.execute(select(_Emp).where(
                _Emp.id == emp_id, _Emp.org_id == org_id))).scalar_one_or_none()
            if not emp:
                continue
            recipient = emp.email if rule.channel == "email" else getattr(emp, "phone", None)
            subject = f"Task {event}: {task.title}"
            body = f"Task “{task.title}” was {event}." + due
            send_at = now + timedelta(minutes=rule.delay_min or 0)
            if rule.working_hours_only:
                send_at = shift_into_working_hours(send_at)

            log = NotificationLog(
                org_id=org_id, scope="employee", channel=rule.channel,
                trigger_event=f"task_{event}", rule_id=rule.id, employee_id=emp_id,
                recipient=recipient, subject=subject, body=body, send_at=send_at)
            db.add(log)
            if not recipient:
                log.status = "skipped"
                log.error = f"Employee has no {rule.channel}"
            else:
                log.status = "scheduled"      # the outbox sweep sends it when due
                sent += 1 if send_at <= now else 0
    return sent


# ── Build 144ah — starter customer notifications ─────────────────────────────
# A new org otherwise has zero rules, so customers hear nothing on status change
# until staff build rules by hand. These three template-less rules give sensible
# out-of-the-box coverage (the engine renders the per-event default message):
# order received → status changed → completed. All client-facing, email channel.
DEFAULT_CLIENT_RULES = [
    {"event": "created", "channel": "email"},
    {"event": "status_changed", "channel": "email"},
    {"event": "closed", "channel": "email"},
]


async def seed_default_rules(db: AsyncSession, org_id: str) -> int:
    """Idempotent. Seed the starter client-facing order rules for ``org_id`` only when it
    has none yet (so re-running, or an org that already configured notifications, is never
    touched or duplicated). Caller commits. Returns the number of rules created."""
    existing = (await db.execute(select(func.count()).select_from(NotificationRule).where(
        NotificationRule.org_id == org_id,
        NotificationRule.module == "order",
        NotificationRule.scope == "client"))).scalar() or 0
    if existing:
        return 0
    for spec in DEFAULT_CLIENT_RULES:
        db.add(NotificationRule(
            org_id=org_id, module="order", scope="client",
            event=spec["event"], channel=spec["channel"], active=True))
    return len(DEFAULT_CLIENT_RULES)
