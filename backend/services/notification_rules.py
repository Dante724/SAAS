"""Build 80 — the external-notification rules engine.

A NotificationRule decides *whether and when* to send; a NotificationTemplate is
*what* gets sent. Rules support what Orderry's per-module notification settings do:

  * event triggers (created / status_changed / closed) scoped to a status and a location
  * **delayed sending** — e.g. ask for feedback 60 minutes after the job closes
  * **working-hours-only** — a send that would land at 23:10 is held to the next
    working morning instead of waking the customer
  * **appointment reminders** — fire N minutes *before* an order's scheduled_for

Delayed/reminder sends are written to NotificationLog with status='scheduled' and a
`send_at`. A worker tick (`run_due`) flushes the ones that have come due, so nothing
depends on a long-lived in-process timer.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import (
    Appointment,
    NotificationLog,
    NotificationRule,
    NotificationTemplate,
    Order,
)
from backend.services.notifications import _adapter_for, channel_consented, notif_creds, try_oauth_email


async def _send(db, org_id, channel, *, recipient, subject, body):
    """144jn: send through THIS shop's own provider credentials (per-tenant).

    Email goes out from the shop's connected Gmail/Outlook account when there is one
    (same as the old template path, build 144ae) and falls back to SMTP / simulated."""
    if channel == "email":
        result = await try_oauth_email(db, org_id, recipient=recipient, subject=subject, body=body)
        if result is not None:
            return result
    creds = await notif_creds(db, org_id, channel)
    return await _adapter_for(channel, creds).send(recipient=recipient, subject=subject, body=body)

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


async def _render(db: AsyncSession, org_id: str, order: Order, rule: NotificationRule):
    """Render the rule's template against the order's variables. Falls back to a
    plain status line when the rule has no template attached."""
    from backend.routers.documents import _apply, _resolve
    variables = await _resolve(db, org_id, order)
    if isinstance(variables, tuple):   # build 144s made _resolve return (doc_map, rows)
        variables = variables[0]
    tpl = None
    if rule.template_id:
        tpl = (await db.execute(select(NotificationTemplate).where(
            NotificationTemplate.id == rule.template_id))).scalar_one_or_none()
    if tpl:
        return _apply(tpl.subject or "", variables) if tpl.subject else None, \
               _apply(tpl.body_template or "", variables), tpl.id
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


async def evaluate(db: AsyncSession, *, org_id: str, order_id: str, event: str,
                   status_key: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """Run every matching rule for an order event. Immediate sends go out now;
    delayed / out-of-hours sends are queued with a due time."""
    now = now or datetime.utcnow()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == org_id)
        .options(selectinload(Order.client), selectinload(Order.lines),
                 selectinload(Order.device), selectinload(Order.assigned_to)))).scalar_one_or_none()
    if not order:
        return {"sent": 0, "scheduled": 0}

    matched = [r for r in rules if _rule_matches(r, event=event, status_key=status_key, order=order)]
    sent = scheduled = 0
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

        send_at = now + timedelta(minutes=rule.delay_min or 0)
        if rule.working_hours_only:
            s, e = await _location_window(db, order.location_id, send_at)
            send_at = shift_into_working_hours(send_at, s, e)

        log = NotificationLog(
            org_id=org_id, scope="client", channel=channel, trigger_event=event,
            template_id=tpl_id, rule_id=rule.id, order_id=order.id, client_id=order.client_id,
            recipient=recipient, subject=subject, body=body, send_at=send_at)
        db.add(log)

        if not recipient:
            log.status = "skipped"
            log.error = f"Client has no {channel} on file"
        elif not channel_consented(client, channel):
            log.status = "skipped"
            log.error = f"No client consent for {channel}"
        elif send_at <= now:
            result = await _send(db, org_id, channel, recipient=recipient, subject=subject, body=body)
            log.status = result["status"]
            log.error = result.get("error")
            sent += 1 if log.status == "sent" else 0
        else:
            log.status = "scheduled"      # a worker tick will flush it when due
            scheduled += 1
    await db.commit()
    return {"sent": sent, "scheduled": scheduled, "matched": len(matched)}


async def queue_reminders(db: AsyncSession, *, org_id: str,
                          now: Optional[datetime] = None) -> int:
    """For every `reminder` rule, queue a send for each order whose scheduled_for
    falls within the rule's lead time. Idempotent: an order already holding a
    scheduled/sent reminder for that rule is skipped."""
    now = now or datetime.utcnow()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.event == "reminder",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    queued = 0
    for rule in rules:
        lead = timedelta(minutes=rule.reminder_offset_min or 0)
        horizon = now + lead
        orders = (await db.execute(select(Order).where(
            Order.org_id == org_id, Order.scheduled_for.isnot(None),
            Order.scheduled_for > now, Order.scheduled_for <= horizon,
            Order.archived == False)  # noqa: E712
            .options(selectinload(Order.client), selectinload(Order.lines),
                     selectinload(Order.device), selectinload(Order.assigned_to)))).scalars().all()
        for order in orders:
            if rule.location_id and rule.location_id != order.location_id:
                continue
            dupe = (await db.execute(select(NotificationLog.id).where(
                NotificationLog.rule_id == rule.id, NotificationLog.order_id == order.id,
                NotificationLog.status.in_(["scheduled", "sent"])))).scalar_one_or_none()
            if dupe:
                continue
            subject, body, tpl_id = await _render(db, org_id, order, rule)
            client = order.client
            recipient = (client.email if rule.channel == "email" else client.phone) if client else None
            send_at = order.scheduled_for - lead
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


async def run_due(db: AsyncSession, *, org_id: str, now: Optional[datetime] = None) -> dict:
    """Worker tick: dispatch every scheduled notification whose send_at has passed."""
    now = now or datetime.utcnow()
    due = (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org_id, NotificationLog.status == "scheduled",
        NotificationLog.send_at.isnot(None), NotificationLog.send_at <= now))).scalars().all()
    sent = failed = 0
    from sqlalchemy import update as _update
    for log in due:
        if not log.recipient:
            log.status = "skipped"
            await db.commit()
            continue
        # Claim the row before sending and commit after each message: the old loop sent
        # everything and committed once at the end, so a crash mid-sweep re-sent every message
        # already delivered, and two runners could both send the same row. A row left in
        # "sending" by a crash is never re-sent automatically (no duplicates to customers).
        claimed = await db.execute(_update(NotificationLog)
            .where(NotificationLog.id == log.id, NotificationLog.status == "scheduled")
            .values(status="sending").execution_options(synchronize_session=False))
        await db.commit()
        if claimed.rowcount == 0:
            continue
        result = await _send(db, org_id, log.channel,
            recipient=log.recipient, subject=log.subject, body=log.body)
        log.status = result["status"]
        log.error = result.get("error")
        await db.commit()
        if log.status == "sent":
            sent += 1
        else:
            failed += 1
    return {"due": len(due), "sent": sent, "failed": failed}


# ── Build 85: appointment notifications ──────────────────────────────────────────
async def evaluate_appointment(db: AsyncSession, *, org_id: str, appointment_id: str, event: str,
                           now: Optional[datetime] = None) -> dict:
    """Fire `module="appointment"` rules on create / status change. The appointment module
    was declared back in build 80 but nothing evaluated it — this closes that loop."""
    now = now or datetime.utcnow()
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

    sent = scheduled = 0
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
            s, e = await _location_window(db, b.location_id, send_at)
            send_at = shift_into_working_hours(send_at, s, e)

        log = NotificationLog(
            org_id=org_id, scope="client", channel=rule.channel, trigger_event=event,
            rule_id=rule.id, client_id=b.client_id, recipient=recipient,
            subject=subject, body=body, send_at=send_at)
        db.add(log)
        if not recipient:
            log.status = "skipped"
            log.error = f"No {rule.channel} on file"
        elif send_at <= now:
            res = await _send(db, org_id, rule.channel, 
                recipient=recipient, subject=subject, body=body)
            log.status = res["status"]
            log.error = res.get("error")
            sent += 1 if log.status == "sent" else 0
        else:
            log.status = "scheduled"
            scheduled += 1
    await db.commit()
    return {"sent": sent, "scheduled": scheduled, "matched": len(rules)}


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
    now = now or datetime.utcnow()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "appointment",
        NotificationRule.event == "reminder",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    queued = 0
    for rule in rules:
        lead = timedelta(minutes=rule.reminder_offset_min or 0)
        rows = (await db.execute(select(Appointment).where(
            Appointment.org_id == org_id, Appointment.status == "booked",
            Appointment.scheduled_for > now,
            Appointment.scheduled_for <= now + lead))).scalars().all()
        for b in rows:
            if rule.location_id and rule.location_id != b.location_id:
                continue
            dupe = (await db.execute(select(NotificationLog.id).where(
                NotificationLog.rule_id == rule.id,
                NotificationLog.client_id == b.client_id,
                NotificationLog.trigger_event == "reminder",
                NotificationLog.status.in_(["scheduled", "sent"])))).scalar_one_or_none()
            if dupe:
                continue
            recipient = await _appointment_recipient(db, b, rule.channel)
            db.add(NotificationLog(
                org_id=org_id, scope="client", channel=rule.channel,
                trigger_event="reminder", rule_id=rule.id, client_id=b.client_id,
                recipient=recipient, subject="Appointment reminder",
                body=(f"Reminder: your appointment is at "
                      f"{b.scheduled_for.strftime('%Y-%m-%d %H:%M')}."),
                send_at=b.scheduled_for - lead,
                status="scheduled" if recipient else "skipped",
                error=None if recipient else f"No {rule.channel} on file"))
            queued += 1
    await db.commit()
    return queued


# ── 144jb: Internal (staff) notifications for order Workflows ────────────────
async def notify_staff_order(db: AsyncSession, *, org_id: str, order_id: str, event: str,
                             status_key: Optional[str] = None,
                             actor_id: Optional[str] = None,
                             now: Optional[datetime] = None) -> int:
    """Fire `module="order"`, `scope="staff"` rules on an order event — Orderry's
    Internal › Workflows. Targets the people responsible for the job: the assigned
    technician and the order's manager. Never notifies the person who made the change.

    Channel `push`/`in_app` → in-app bell + web push (StaffNotification). `email`/`sms`
    → the employee's email/phone via the usual adapter + a NotificationLog row.
    """
    from backend.models import Employee as _Emp
    from backend.services import push as _push
    now = now or datetime.utcnow()
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
                                   deep_link=f"/tech#/order/{order.id}", commit=False)
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
                res = await _send(db, org_id, rule.channel, 
                    recipient=recipient, subject=subject, body=body)
                log.status = res["status"]
                log.error = res.get("error")
                sent += 1 if log.status == "sent" else 0
    await db.commit()
    return sent


# ── 144ja: Contacts module — birthday / lifecycle notifications ──────────────
async def notify_contact_birthdays(db: AsyncSession, *, org_id: str,
                                   now: Optional[datetime] = None) -> int:
    """Fire `module="contact"`, `event="birthday"` rules for every client whose
    birthday is today. Orderry's Contacts notifications cover exactly this: a
    lifecycle greeting to the contact, not tied to any order.

    Idempotent per client+rule+year: a client who already has a birthday send this
    calendar year is skipped, so the daily sweep never double-greets.
    """
    from backend.models import Client, NotificationTemplate
    now = now or datetime.utcnow()
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "contact",
        NotificationRule.event == "birthday",
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return 0

    today = f"{now.month:02d}-{now.day:02d}"
    clients = (await db.execute(select(Client).where(
        Client.org_id == org_id, Client.birthday.isnot(None),
        Client.blacklisted == False))).scalars().all()  # noqa: E712
    year_start = datetime(now.year, 1, 1)
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
                from backend.routers.documents import _apply
                cmap = {"{Client Name}": c.name or "", "{First Name}": first,
                        "{Client First Name}": first, "{Client Email}": c.email or "",
                        "{Client Phone}": c.phone or ""}
                subject = _apply(tpl.subject or "Happy birthday!", cmap)
                body = _apply(tpl.body_template or "", cmap)
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
                res = await _send(db, org_id, rule.channel, 
                    recipient=recipient, subject=subject, body=body)
                log.status = res["status"]
                log.error = res.get("error")
                sent += 1 if log.status == "sent" else 0
    await db.commit()
    return sent


# ── Build 86: internal task notifications ────────────────────────────────────
async def notify_task(db: AsyncSession, *, org_id: str, task, event: str,
                      actor_id: Optional[str] = None) -> int:
    """Notify the people involved in a task (assignee + author).

    Orderry's rule, which we follow: **you are never notified about something you did
    yourself.** Create a task for yourself and you get nothing; another employee
    creates one for you and you hear about it.

    Targets employees, not clients — so these rules use `module="task"` and resolve the
    recipient from the employee record.
    """
    from backend.models import Employee as _Emp

    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "task",
        NotificationRule.event == event,
        NotificationRule.active == True))).scalars().all()  # noqa: E712
    if not rules:
        return 0

    # who cares about this event
    targets = set()
    if event in ("assigned", "changed") and task.assigned_to_id:
        targets.add(task.assigned_to_id)
    if event in ("changed", "completed") and task.created_by_id:
        targets.add(task.created_by_id)
    targets.discard(actor_id)          # never notify the person who did it
    if not targets:
        return 0

    sent = 0
    now = datetime.utcnow()
    for rule in rules:
        for emp_id in targets:
            emp = (await db.execute(select(_Emp).where(_Emp.id == emp_id))).scalar_one_or_none()
            if not emp:
                continue
            recipient = emp.email if rule.channel == "email" else getattr(emp, "phone", None)
            subject = f"Task {event}: {task.title}"
            body = (f"Task “{task.title}” was {event}."
                    + (f" Due {task.due_at:%Y-%m-%d %H:%M}." if task.due_at else ""))
            send_at = now + timedelta(minutes=rule.delay_min or 0)
            if rule.working_hours_only:
                send_at = shift_into_working_hours(send_at)

            log = NotificationLog(
                org_id=org_id, scope="employee", channel=rule.channel,
                trigger_event=f"task_{event}", rule_id=rule.id,
                recipient=recipient, subject=subject, body=body, send_at=send_at)
            db.add(log)
            if not recipient:
                log.status = "skipped"
                log.error = f"Employee has no {rule.channel}"
            elif send_at <= now:
                res = await _send(db, org_id, rule.channel, 
                    recipient=recipient, subject=subject, body=body)
                log.status = res["status"]
                log.error = res.get("error")
                sent += 1 if log.status == "sent" else 0
            else:
                log.status = "scheduled"
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
