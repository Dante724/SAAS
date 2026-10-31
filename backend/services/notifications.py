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
from typing import Optional

from sqlalchemy import select
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


# ── Channel adapters ─────────────────────────────────────────────────────────
class ChannelAdapter:
    """Base adapter. Real providers subclass and implement send(). `html` says whether an
    email body is HTML; it comes from the template (message_vars.is_html), never from the
    filled-in text. None (old callers) falls back to looking at the body."""
    name = "base"

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None) -> dict:
        raise NotImplementedError


class SimulatedAdapter(ChannelAdapter):
    """Stand-in that 'delivers' by logging. Used when no provider is configured."""
    def __init__(self, channel: str):
        self.name = channel

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None) -> dict:
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
                   html: Optional[bool] = None) -> dict:
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

        def _blocking() -> dict:
            import re
            import smtplib
            from email.mime.text import MIMEText
            # 144jd: email templates can be rich HTML. The caller says which (from the
            # template); the body check is only for callers that don't.
            is_html = html if html is not None else bool(re.search(r"<[a-zA-Z/][^>]*>", body or ""))
            msg = MIMEText(body, "html" if is_html else "plain")
            msg["Subject"] = one_line(subject) or "Update on your order"
            msg["From"] = from_addr
            msg["To"] = recipient
            try:
                with smtplib.SMTP(host, port, timeout=10) as s:
                    if use_tls:
                        s.starttls()
                    if user:
                        s.login(user, password)
                    s.send_message(msg)
                return {"status": "sent", "error": None}
            except Exception as exc:  # provider/network failure -> recorded, never raised
                return {"status": "failed", "error": f"SMTP: {exc}"}

        return await asyncio.to_thread(_blocking)


class TwilioAdapter(ChannelAdapter):
    """Real SMS / WhatsApp delivery via the Twilio REST API (async httpx)."""
    def __init__(self, channel: str, creds: Optional[dict] = None):
        self.name = channel  # 'sms' or 'whatsapp'
        self.creds = creds or {}   # 144jn: this shop's own Twilio account

    async def send(self, *, recipient: str, subject: Optional[str], body: str,
                   html: Optional[bool] = None) -> dict:
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
    c = creds or {}
    if ch == "email":
        host = c.get("host") or settings.SMTP_HOST
        from_ok = c.get("from") or c.get("user") or settings.SMTP_FROM or settings.SMTP_USER
        if host and from_ok:
            return SmtpEmailAdapter(c)
    if ch in ("sms", "whatsapp"):
        sid = c.get("account_sid") or settings.TWILIO_ACCOUNT_SID
        token = c.get("auth_token") or settings.TWILIO_AUTH_TOKEN
        if sid and token:
            return TwilioAdapter(ch, c)
    return SimulatedAdapter(channel)


async def notif_creds(db, org_id: str, channel: str) -> Optional[dict]:
    """This shop's effective creds for a notification channel (email→smtp,
    sms/whatsapp→twilio); None for anything else."""
    ch = (channel or "").lower()
    provider = "smtp" if ch == "email" else ("twilio" if ch in ("sms", "whatsapp") else None)
    if not provider:
        return None
    from backend.services import org_creds
    return await org_creds.effective(db, org_id, provider)


async def send_direct_email(recipient: str, subject: str, body: str) -> dict:
    """Build 144cb — fire a one-off transactional email (password reset, email
    verification, teammate invite) through the same adapter seam as order
    notifications. With SMTP configured it delivers for real; otherwise it logs
    (dev) so the flow is testable without a mail server."""
    return await _adapter_for("email").send(recipient=recipient, subject=subject, body=body, html=False)


# ── Engine ───────────────────────────────────────────────────────────────────
async def dispatch_order_notifications(db: AsyncSession, *, org_id: str, order_id: str,
                                       trigger_event: str, status_key: Optional[str] = None):
    """Find + send every template that matches this event for this order.

    trigger_event is one of: 'order_created', 'status_change', 'comment_added'.
    For status changes we also match templates targeting the specific status_key.
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
    from backend.services import message_vars
    variables = await _resolve(db, org_id, order)
    if isinstance(variables, tuple):   # build 144s made _resolve return (doc_map, rows)
        variables = variables[0]
    client = order.client

    sent = 0
    for t in matched:
        subject, body = await message_vars.render_for_order(
            db, org_id, order, t.subject or None, t.body_template or "", doc_vars=variables)
        recipient = None
        if client:
            recipient = client.email if t.channel == "email" else client.phone
        log = NotificationLog(
            org_id=org_id, scope="client", channel=t.channel, trigger_event=trigger_event,
            template_id=t.id, order_id=order.id, client_id=order.client_id,
            recipient=recipient, subject=subject, body=body, status="queued")
        db.add(log)

        if not recipient:
            log.status = "skipped"
            log.error = f"Client has no {t.channel} on file"
        elif not channel_consented(client, t.channel):
            log.status = "skipped"
            log.error = f"No client consent for {t.channel}"
        else:
            # Build 144ae: for email, prefer the shop's own connected Gmail/Outlook
            # account (sends *from* their address, threaded) and fall back to SMTP /
            # simulated only when no account is connected.
            result = None
            if t.channel == "email":
                result = await try_oauth_email(db, org_id, recipient=recipient, subject=subject, body=body)
            if result is None:
                _creds = await notif_creds(db, org_id, t.channel)
                result = await _adapter_for(t.channel, _creds).send(
                    recipient=recipient, subject=subject, body=body,
                    html=message_vars.is_html(t.body_template))
            log.status = result["status"]
            log.error = result.get("error")
            if log.status == "sent":
                sent += 1

        # Mirror into the order event feed (the communication history trail).
        from backend.routers.feed import log_system_event
        verb = {"sent": "sent", "skipped": "skipped (no contact)", "failed": "failed"}.get(log.status, log.status)
        await log_system_event(db, org_id, "order", order.id,
                               f"{t.channel.upper()} notification {verb} to client")

    await db.commit()
    return sent

async def try_oauth_email(db, org_id, *, recipient, subject, body):
    """A6 seam: if the org has a connected Gmail/Outlook account, send through it
    and return the result; otherwise return None so the caller falls back to SMTP
    or the simulated adapter."""
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
        return await send_via_connection(conn, recipient=recipient, subject=subject, body=body)
    except Exception:
        return None
