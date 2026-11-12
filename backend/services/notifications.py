"""Notification delivery engine (Phase 1 — deliverability).

On a trigger event (order_created, status_changed, status_changed_to:<key>,
comment_added) this finds matching NotificationTemplate rows, renders them with
the order's variables (reusing the document resolver), dispatches each via a
channel adapter, and writes a NotificationLog row for the communication history.

Delivery goes through a channel adapter. When provider credentials are configured
(SMTP for email, Twilio for SMS/WhatsApp) the matching real adapter sends for real
and records the provider result; when they are absent it falls back to a simulated
adapter that prints and marks the message sent in development, and fails (without logging the
body) when deployed. Either way a NotificationLog row
captures recipient, body, status (queued|sent|failed|skipped) and any error.
"""
import re
from typing import Optional

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import (
    NotificationLog,
    NotificationTemplate,
    Order,
)


def one_line(subject: Optional[str]) -> str:
    """An email subject with no CR/LF, so a value filled into it can't add mail headers."""
    import re
    return re.sub(r"[\r\n]+", " ", subject or "").strip()


# ── Notification log hooks ───────────────────────────────────────────────────
_PORTAL_TOKEN = re.compile(r"(/portal/)[A-Za-z0-9_-]{16,}")


def redact_portal_links(text):
    """A message as kept in the history: its portal link no longer carries the live token (the
    log is readable by staff who couldn't issue a link, and only hashes are stored otherwise)."""
    return _PORTAL_TOKEN.sub(r"\1…", text) if text and "/portal/" in text else text


async def log_is_html(db, log) -> bool:
    """HTML or plain for a logged message, decided from its template once and then kept."""
    if log.is_html is None:
        html = False
        if log.template_id:
            from backend.services.message_vars import is_html
            html = is_html((await db.execute(select(NotificationTemplate.body_template).where(
                NotificationTemplate.id == log.template_id))).scalar_one_or_none())
        log.is_html = html
    return bool(log.is_html)


@event.listens_for(NotificationLog, "before_insert")
@event.listens_for(NotificationLog, "before_update")
def _freeze_log(mapper, connection, target) -> None:
    if target.is_html is None:
        html = False
        if target.template_id:
            from backend.services.message_vars import is_html
            html = is_html(connection.execute(select(NotificationTemplate.body_template).where(
                NotificationTemplate.id == target.template_id)).scalar_one_or_none())
        target.is_html = html
    if target.status not in ("queued", "scheduled"):     # still to send: it needs the live link
        target.body = redact_portal_links(target.body)


# ── Channel adapters ─────────────────────────────────────────────────────────
class ChannelAdapter:
    """Base adapter. Real providers subclass and implement send(). `html` says whether an
    email body is HTML; it comes from the template (message_vars.is_html), never from the
    filled-in text. None (old callers) falls back to looking at the body."""
    name = "base"

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None, headers: Optional[dict] = None) -> dict:
        raise NotImplementedError


class SimulatedAdapter(ChannelAdapter):
    """Stand-in that 'delivers' by logging. Used when no provider is configured."""
    def __init__(self, channel: str):
        self.name = channel

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None, headers: Optional[dict] = None) -> dict:
        if not recipient:
            return {"status": "failed", "error": "No recipient address/number on file"}
        from backend.config import settings
        if settings.is_deployed:
            # A real deploy with no provider for this channel: nothing was delivered, so say so.
            # Never write the body here: it holds reset / verification / invite links.
            import logging
            logging.getLogger(__name__).error("No %s provider configured; message to %s not sent",
                                              self.name, recipient[:3] + "…")
            return {"status": "failed", "error": f"No {self.name} provider is configured"}
        # Development / tests: print it so a developer can follow the link locally.
        print(f"[notify:{self.name}:simulated] -> {recipient}: {(subject + ' | ') if subject else ''}{body[:120]}")
        return {"status": "sent", "error": None}


class SmtpEmailAdapter(ChannelAdapter):
    """Real email delivery over SMTP (works with SendGrid/Mailgun/SES SMTP relays).
    smtplib is blocking, so it runs in a worker thread to keep the event loop free."""
    name = "email"

    def __init__(self, creds: Optional[dict] = None):
        self.creds = creds or {}   # 144jn: this shop's own SMTP settings

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   attachment: Optional[tuple] = None, html: Optional[bool] = None,
                   headers: Optional[dict] = None) -> dict:
        """``attachment`` is an optional (filename, bytes, mimetype), e.g. an invoice PDF. ``html``
        says whether the body is HTML (from the template); None means guess from the body.
        ``headers`` are extra mail headers (e.g. List-Unsubscribe)."""
        if not recipient:
            return {"status": "failed", "error": "No email address on file"}
        import asyncio

        from backend.config import settings
        c = self.creds
        host = c.get("host") or settings.SMTP_HOST
        port = int(c.get("port") or settings.SMTP_PORT or 587)
        user = c.get("user") or settings.SMTP_USER
        password = c.get("password") or settings.SMTP_PASSWORD
        from_addr = c.get("from") or settings.SMTP_FROM or user
        use_tls = c.get("use_tls") if c.get("use_tls") is not None else settings.SMTP_USE_TLS
        use_tls = str(use_tls).lower() in ("1", "true", "yes", "on") if isinstance(use_tls, str) else bool(use_tls)
        # A shop's own SMTP server is a host we connect to for them: it must be public, and we
        # connect to the address that was checked (as webhooks do). The operator's is trusted.
        connect_to = host
        if host and host != settings.SMTP_HOST:
            from backend.core.ssrf import UnsafeURLError, public_ip_for
            try:
                connect_to = await asyncio.to_thread(public_ip_for, host, port)
            except UnsafeURLError:
                return {"status": "failed", "error": "SMTP: the server must be a public address"}

        def _blocking() -> dict:
            import re
            import smtplib
            from email.mime.text import MIMEText
            # 144jd: email templates can be rich HTML. The caller says which (from the
            # template); the body check is only for callers that don't.
            is_html = html if html is not None else bool(re.search(r"<[a-zA-Z/][^>]*>", body or ""))
            msg = MIMEText(body, "html" if is_html else "plain")
            if attachment:
                from email.mime.application import MIMEApplication
                from email.mime.multipart import MIMEMultipart
                fname, data, mime = attachment
                outer = MIMEMultipart()
                outer.attach(msg)
                part = MIMEApplication(data, _subtype=(mime or "application/octet-stream").split("/")[-1])
                part.add_header("Content-Disposition", "attachment", filename=fname)
                outer.attach(part)
                msg = outer
            msg["Subject"] = one_line(subject) or "Update on your order"
            msg["From"] = from_addr
            msg["To"] = recipient
            for hk, hv in (headers or {}).items():   # e.g. List-Unsubscribe on Tekly Sense emails
                msg[hk] = hv
            try:
                with smtplib.SMTP(timeout=10) as s:
                    s.connect(connect_to, port)
                    # starttls() checks the certificate against _host, which connect() set to the
                    # address; put the real name back so verification uses the name.
                    s._host = host
                    if use_tls:
                        s.starttls()
                    if user:
                        s.login(user, password)
                    s.send_message(msg)
                return {"status": "sent", "error": None}
            except Exception as exc:  # provider/network failure -> recorded, never raised
                return {"status": "failed", "error": f"SMTP: {exc}"}

        return await asyncio.to_thread(_blocking)


OPERATOR_TEXT_CAP_MESSAGE = ("Daily text limit reached on Tekly's shared number — connect your own Twilio "
                             "account in Settings › Integrations to send more.")
# notif_creds() tags the creds it returns with these; _adapter_for() strips them off again.
_ORG_KEY = "_org_id"
_CAP_KEY = "_operator_daily_cap"   # texts this kind of send may take today (cap, or cap - reserve)
_DB_KEY = "_db"                    # the caller's session: the shop's day, and the count on SQLite


async def reserve_operator_text(db, org_id: str, limit: int) -> bool:
    """Count one text against the shop's budget on the operator's Twilio account for the shop's
    current day; False (nothing counted) once `limit` texts have gone out today.

    One conditional upsert, atomic on Postgres and SQLite (>= 3.35):
    INSERT … ON CONFLICT (org_id, day) DO UPDATE SET sent = sent + 1 WHERE sent < :limit RETURNING
    sent. On Postgres it runs and commits on its own connection, so the count survives a caller that
    rolls back after the text went out, and the row lock is not held across the Twilio call. SQLite
    (dev / tests) has one writer: a second connection would wait on the caller's own write lock, so
    there it runs in the caller's session."""
    if limit <= 0:
        return False
    from backend.models import OperatorTextUsage
    from backend.services.dates import shop_today
    day = await shop_today(db, org_id)
    t = OperatorTextUsage.__table__
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(t).values(org_id=org_id, day=day, sent=1)
    stmt = stmt.on_conflict_do_update(index_elements=[t.c.org_id, t.c.day], set_={"sent": t.c.sent + 1},
                                      where=t.c.sent < limit).returning(t.c.sent)
    if db.bind.dialect.name == "postgresql":
        async with db.bind.connect() as conn:
            got = (await conn.execute(stmt)).scalar_one_or_none()
            await conn.commit()
    else:
        got = (await db.execute(stmt)).scalar_one_or_none()
    return got is not None


class TwilioAdapter(ChannelAdapter):
    """Real SMS / WhatsApp delivery via the Twilio REST API (async httpx).

    `daily_cap` (with `org_id`) is set when the shop texts through the OPERATOR's Twilio account:
    sends past it are refused with OPERATOR_TEXT_CAP_MESSAGE (toll fraud / SMS pumping guard)."""
    def __init__(self, channel: str, creds: Optional[dict] = None, *, org_id: Optional[str] = None,
                 daily_cap: Optional[int] = None, db=None):
        self.name = channel  # 'sms' or 'whatsapp'
        self.creds = creds or {}   # 144jn: this shop's own Twilio account
        self.org_id = org_id
        self.daily_cap = daily_cap
        self.db = db

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None, headers: Optional[dict] = None) -> dict:
        if not recipient:
            return {"status": "failed", "error": "No phone number on file"}
        from backend.config import settings
        c = self.creds
        sid = c.get("account_sid") or settings.TWILIO_ACCOUNT_SID
        token = c.get("auth_token") or settings.TWILIO_AUTH_TOKEN
        is_wa = self.name == "whatsapp"
        from_addr = (c.get("whatsapp_from") or settings.TWILIO_WHATSAPP_FROM) if is_wa else (c.get("from") or settings.TWILIO_FROM)
        to_addr = f"whatsapp:{recipient}" if is_wa and not recipient.startswith("whatsapp:") else recipient
        text = ((subject + "\n\n") if subject else "") + body
        from backend.services.twilio_voice import sms_refusal
        refused = sms_refusal(c, whatsapp=is_wa)   # S7: no billed texts outside production
        if refused:
            return {"status": "failed", "error": refused}
        if self.daily_cap is not None and self.org_id and self.db is not None:
            # Counted before the send (a Twilio error still used a slot): at worst an undercount
            # of the shop's budget, never an overrun of the operator's bill.
            if not await reserve_operator_text(self.db, self.org_id, self.daily_cap):
                # Reported, never raised: automatic notifications log a failed send and move on;
                # the manual send endpoints turn `capped` into a 429.
                return {"status": "failed", "error": OPERATOR_TEXT_CAP_MESSAGE, "capped": True}
        try:
            import httpx
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, data={"From": from_addr, "To": to_addr, "Body": text},
                    auth=(sid, token))
            if resp.status_code in (200, 201):
                return {"status": "sent", "error": None}
            return {"status": "failed", "error": f"Twilio {resp.status_code}: {resp.text[:160]}"}
        except Exception as exc:  # never raised into the caller
            return {"status": "failed", "error": f"Twilio: {exc}"}


def channel_consented(client, channel: str) -> bool:
    """N-GAP-2 — has this client consented to be contacted on this channel?

    email → consent_email; sms/whatsapp → consent_phone. We gate on an *explicit*
    opt-out only: a False blocks the send, while None/True allows it — so turning
    consent enforcement on never silently mutes the whole existing client base
    (Orderry defaults consent from Settings > General; a null here means
    "not specified", which we treat as permitted).
    ponytail: explicit-False gate; tighten to require True if a market demands
    hard opt-in.
    """
    if client is None:
        return True
    field = "consent_email" if (channel or "").lower() == "email" else "consent_phone"
    return getattr(client, field, None) is not False


def _adapter_for(channel: str, creds: Optional[dict] = None) -> ChannelAdapter:
    """Return a real provider adapter when credentials are configured, else the
    simulated (logged) adapter. `creds` are THIS shop's own keys (per-tenant);
    when omitted we fall back to the global env (operator default).

    144jn: pass per-org creds so each shop sends through its OWN provider account
    — no shared Stripe/Twilio/SMTP between tenants.
    """
    from backend.config import settings
    ch = (channel or "").lower()
    c = dict(creds or {})
    org_id, daily_cap, db = c.pop(_ORG_KEY, None), c.pop(_CAP_KEY, None), c.pop(_DB_KEY, None)
    if ch == "email":
        host = c.get("host") or settings.SMTP_HOST
        from_ok = c.get("from") or c.get("user") or settings.SMTP_FROM or settings.SMTP_USER
        if host and from_ok:
            return SmtpEmailAdapter(c)
    if ch in ("sms", "whatsapp"):
        sid = c.get("account_sid") or settings.TWILIO_ACCOUNT_SID
        token = c.get("auth_token") or settings.TWILIO_AUTH_TOKEN
        if sid and token:
            return TwilioAdapter(ch, c, org_id=org_id, daily_cap=daily_cap, db=db)
    return SimulatedAdapter(channel)


async def notif_creds(db, org_id: str, channel: str, *, transactional: bool = False) -> Optional[dict]:
    """This shop's own credentials for `channel`'s provider (or the env defaults).

    `transactional`: the text is about the customer's own job or booking (status updates,
    reminders, a message from the job page). Those may use the whole daily cap on the operator's
    Twilio account; everything else (campaigns, chats, birthday / win-back, test sends) stops
    OPERATOR_SMS_RESERVE_PCT short of it. Defaults to False so a new caller can't eat the reserve.
    None for a channel with no provider (email→smtp, sms/whatsapp→twilio)."""
    ch = (channel or "").lower()
    provider = "smtp" if ch == "email" else ("twilio" if ch in ("sms", "whatsapp") else None)
    if not provider:
        return None
    from backend.services import org_creds
    creds = dict(await org_creds.effective(db, org_id, provider))
    if provider == "twilio":
        # Tag for TwilioAdapter: which shop is sending, and its daily cap when the keys are the
        # operator's shared account (None on the shop's own account).
        creds[_ORG_KEY] = org_id
        cap = await operator_text_cap(db, org_id, creds, transactional=transactional)
        if cap is not None:
            creds[_CAP_KEY] = cap
            creds[_DB_KEY] = db
    return creds


def uses_operator_twilio(creds: dict) -> bool:
    """The texts are billed to the OPERATOR's Twilio account: the shop has no account SID of its own
    (a shop that saved only a `from` number still sends on the server's SID and token)."""
    from backend.config import settings
    sid = settings.TWILIO_ACCOUNT_SID
    return bool(sid) and (creds.get("account_sid") or sid) == sid


async def operator_text_cap(db, org_id: str, creds: dict, *, transactional: bool = False) -> Optional[int]:
    """Texts per day this shop may send on the OPERATOR's Twilio account (the server env keys);
    None when the keys are the shop's own. Only an active (paying) subscription gets the full cap;
    trial and every other status (past_due, unpaid, suspended, trial_lost, churned) get the trial
    cap. Non-transactional sends stop OPERATOR_SMS_RESERVE_PCT short of it."""
    from backend.config import settings
    if not uses_operator_twilio(creds):
        return None
    from backend.models import Organisation
    status = (await db.execute(select(Organisation.sub_status).where(
        Organisation.id == org_id))).scalar_one_or_none()
    cap = int(settings.OPERATOR_SMS_DAILY_CAP if status == "active" else settings.OPERATOR_SMS_DAILY_CAP_TRIAL)
    if transactional:
        return cap
    pct = min(max(int(settings.OPERATOR_SMS_RESERVE_PCT), 0), 100)
    return cap - cap * pct // 100


async def send_direct_email(recipient: str, subject: str, body: str) -> dict:
    """Build 144cb — fire a one-off transactional email (password reset, email
    verification, teammate invite) through the same adapter seam as order
    notifications. With SMTP configured it delivers for real; otherwise it logs
    (dev) so the flow is testable without a mail server."""
    return await _adapter_for("email").send(recipient=recipient, subject=subject, body=body, html=False)


# ── Engine ───────────────────────────────────────────────────────────────────
async def dispatch_order_notifications(db: AsyncSession, *, org_id: str, order_id: str,
                                       trigger_event: str, status_key: Optional[str] = None,
                                       commit: bool = True) -> int:
    """Queue every active template that matches this event for this order (the "legacy" path,
    the only one on plans without notification rules, e.g. Solo).

    trigger_event is one of: 'order_created', 'status_change', 'comment_added'.
    For status changes we also match templates targeting the specific status_key.

    Audit NTF-02: this used to call Twilio/SMTP inside the request, before the caller's commit,
    with no retry. It now writes outbox rows (status 'scheduled', due now) exactly like the rules
    engine, and the outbox sweep delivers them: retries with backoff, send-time consent and STOP
    re-check, crash reclaim. Nothing here calls a provider. commit=False leaves the rows in the
    caller's transaction (audit WO-08). Returns how many messages were queued.
    """
    templates = (await db.execute(select(NotificationTemplate).where(
        NotificationTemplate.org_id == org_id,
        NotificationTemplate.active == True))).scalars().all()  # noqa: E712

    def _matches(t: NotificationTemplate) -> bool:
        te = (t.trigger_event or "").lower()
        if trigger_event == "order_created":
            return te in ("order_created", "created")
        if trigger_event == "comment_added":
            return te in ("comment_added", "order_comment_added")
        if trigger_event == "status_change":
            # generic "any status change" OR a template scoped to this exact status
            if te in ("status_change", "order_status_changed", "status_changed"):
                if t.trigger_status:  # scoped to a specific status
                    return t.trigger_status == status_key
                return True
        return False

    matched = [t for t in templates if _matches(t)]
    if not matched:
        return 0

    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == org_id)
        .options(selectinload(Order.lines), selectinload(Order.client),
                 selectinload(Order.device), selectinload(Order.assigned_to)))).scalar_one_or_none()
    if not order:
        return 0

    # Reuse the document variable resolver so notification + document variables match.
    from backend.routers.documents import _resolve
    from backend.routers.feed import log_system_event
    from backend.services import message_vars
    from backend.services.dates import now_utc
    from backend.services.notification_rules import with_opt_out
    variables = await _resolve(db, org_id, order)
    if isinstance(variables, tuple):   # build 144s made _resolve return (doc_map, rows)
        variables = variables[0]
    client = order.client
    now = now_utc()

    queued = 0
    for t in matched:
        subject, body = await message_vars.render_for_order(
            db, org_id, order, t.subject or None, t.body_template or "", doc_vars=variables)
        recipient = None
        if client:
            recipient = client.email if t.channel == "email" else client.phone
        body = await with_opt_out(db, org_id, order.client_id, t.channel, body)
        log = NotificationLog(
            org_id=org_id, scope="client", channel=t.channel, trigger_event=trigger_event,
            template_id=t.id, order_id=order.id, client_id=order.client_id,
            recipient=recipient, subject=subject, body=body, send_at=now)
        if not recipient:
            log.status = "skipped"
            log.error = f"Client has no {t.channel} on file"
        elif not channel_consented(client, t.channel):
            log.status = "skipped"
            log.error = f"No client consent for {t.channel}"
        else:
            log.status = "scheduled"      # the outbox sweep sends it (services/notification_rules.run_due)
            queued += 1
        db.add(log)
        # Mirror into the order event feed (the communication history trail).
        verb = "queued" if log.status == "scheduled" else "skipped (no contact or consent)"
        await log_system_event(db, org_id, "order", order.id,
                               f"{t.channel.upper()} notification {verb} to client")

    await (db.commit() if commit else db.flush())
    return queued

async def try_oauth_email(db, org_id, *, recipient, subject, body, html: bool = False,
                          message_id: Optional[str] = None):
    """A6 seam: if the org has a connected Gmail/Outlook account, send through it
    and return the result; otherwise return None so the caller falls back to SMTP
    or the simulated adapter. ``message_id`` (outbox sends, audit NTF-05) is passed on so a
    resend of the same message carries the same Message-ID."""
    try:
        from sqlalchemy import select

        from backend.models import EmailConnection
        from backend.services.email_oauth import send_via_connection
        conn = (await db.execute(select(EmailConnection).where(
            EmailConnection.org_id == org_id, EmailConnection.connected == True))).scalars().first()  # noqa: E712
        if not conn:
            return None
        # prefer the default account
        conns = (await db.execute(select(EmailConnection).where(
            EmailConnection.org_id == org_id, EmailConnection.connected == True))).scalars().all()  # noqa: E712
        conn = next((c for c in conns if c.is_default), conn)
        if message_id:
            return await send_via_connection(conn, recipient=recipient, subject=subject, body=body,
                                             html=html, message_id=message_id)
        return await send_via_connection(conn, recipient=recipient, subject=subject, body=body, html=html)
    except Exception:
        return None
