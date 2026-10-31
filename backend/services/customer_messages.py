"""One way to send a staff-written message to a customer (flow check G3 / R1 / I6).

Every "message the customer" action — the job's conversation box, the activity reply box,
publishing recommendations, Send estimate, Send invoice — goes through ``send_to_customer``:

  * picks the channel the customer prefers (see ``pick_channel``),
  * sends through this shop's own provider (connected Gmail/Outlook, SMTP or Twilio),
  * writes a NotificationLog row so it shows in the communication history,
  * and returns an honest ``delivery`` result. With no provider set up the simulated adapter
    "sends" (it prints in development), but the result says ``not_set_up`` instead of
    pretending the customer got it.

delivery = {status: sent | not_set_up | no_contact | failed, channel, message}
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Client, NotificationLog, NotificationRule
from backend.services.notifications import (
    SimulatedAdapter,
    _adapter_for,
    channel_consented,
    notif_creds,
)

CHANNELS = ("email", "sms")
_WORD = {"email": "email", "sms": "text"}


def _contact(client: Optional[Client], channel: str) -> Optional[str]:
    if not client:
        return None
    v = client.email if channel == "email" else client.phone
    return (v or "").strip() or None


def _usable(client: Optional[Client], channel: str) -> bool:
    return bool(_contact(client, channel)) and channel_consented(client, channel)


async def _email_connection(db: AsyncSession, org_id: str):
    from backend.models import EmailConnection
    return (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == org_id, EmailConnection.connected == True)  # noqa: E712
        .order_by(EmailConnection.is_default.desc()))).scalars().first()


async def provider_ready(db: AsyncSession, org_id: str, channel: str) -> bool:
    """Is a real provider set up for this channel (so a send can actually reach someone)?"""
    if channel == "email" and await _email_connection(db, org_id):
        return True
    return not isinstance(_adapter_for(channel, await notif_creds(db, org_id, channel)), SimulatedAdapter)


async def pick_channel(db: AsyncSession, org_id: str, client: Optional[Client],
                       requested: Optional[str] = None) -> Optional[str]:
    """The channel to use for this customer. An explicit email/sms request wins. Otherwise, in
    order: the channel of the shop's own "comment added" customer rule, the channel the customer
    last chatted on, then whichever of text/email has a provider set up, then whichever the
    customer has contact details for. None when the customer can't be reached at all."""
    req = (requested or "auto").lower()
    if req in CHANNELS:
        return req
    prefs: list[str] = []
    rule_ch = (await db.execute(select(NotificationRule.channel).where(
        NotificationRule.org_id == org_id, NotificationRule.module == "order",
        NotificationRule.scope == "client", NotificationRule.event == "comment_added",
        NotificationRule.active == True).limit(1))).scalar_one_or_none()  # noqa: E712
    if rule_ch in CHANNELS:
        prefs.append(rule_ch)
    if client:
        from backend.services.notification_rules import _latest_chat_channel
        chat = await _latest_chat_channel(db, org_id, client.id)
        if chat in CHANNELS:
            prefs.append(chat)
    for ch in prefs:
        if _usable(client, ch):
            return ch
    reachable = [ch for ch in ("sms", "email") if _usable(client, ch)]
    for ch in reachable:
        if await provider_ready(db, org_id, ch):
            return ch
    if reachable:
        return reachable[0]
    # Nothing usable: still name a channel when the customer has any contact, so the result can
    # say which one was refused (e.g. they opted out of texts).
    return next((ch for ch in ("sms", "email") if _contact(client, ch)), None)


def check_override(to: str, channel: Optional[str], client: Optional[Client]) -> None:
    """A typed-in recipient: one plain email address only. Texts go only to the number on file,
    so a customer who replied STOP can't be texted at another number and the shop's SMS account
    can't be pointed at arbitrary numbers."""
    import re

    from fastapi import HTTPException
    if channel == "sms":
        if to != _contact(client, "sms"):
            raise HTTPException(422, "Texts go to the customer's number on file. Update their phone number first.")
        return
    if not re.fullmatch(r"[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+", to) or len(to) > 200:
        raise HTTPException(422, "Enter one email address")


def delivery_message(status: str, channel: Optional[str], *, saved: bool, error: Optional[str] = None,
                     client: Optional[Client] = None) -> str:
    word = _WORD.get(channel or "", "message")
    if status == "sent":
        return f"Sent by {word} to the customer."
    lead = "Saved on the job, but not sent" if saved else "Not sent"
    if status == "not_set_up":
        return f"{lead}: set up email or text messages in Settings → Integrations."
    if status == "no_contact":
        if channel and _contact(client, channel) and not channel_consented(client, channel):
            return f"{lead}: the customer asked not to get {word}s."
        if channel and not _contact(client, channel):
            what = "email address" if channel == "email" else "mobile number"
            return f"{lead}: the customer has no {what}."
        return f"{lead}: the customer has no mobile number or email."
    return f"{lead}: {(error or 'the provider refused it')[:160]}"


async def send_to_customer(db: AsyncSession, *, org_id: str, client: Optional[Client], body: str,
                           subject: Optional[str] = None, channel: Optional[str] = None,
                           order_id: Optional[str] = None, trigger_event: str = "staff_message",
                           attachment: Optional[tuple] = None, saved: bool = True,
                           to: Optional[str] = None) -> dict:
    """Send ``body`` to the customer and log it. Never raises on a provider failure; the caller
    commits. ``attachment`` = (filename, bytes, mimetype), used for email only. ``to`` overrides
    the address/number (staff typed one in), which needs an explicit ``channel``."""
    to = (to or "").strip() or None
    ch = (channel if to and channel in CHANNELS else None) or await pick_channel(db, org_id, client, channel)
    if to:
        check_override(to, ch, client)
        if to == _contact(client, ch):
            to = None                   # the customer's own contact: their opt-out applies
    recipient = to or (_contact(client, ch) if ch else None)
    client_id = client.id if client else None
    if not ch or not recipient or (not to and not channel_consented(client, ch)):
        status, error = "no_contact", None
        if ch and client:
            db.add(NotificationLog(org_id=org_id, scope="client", channel=ch, trigger_event=trigger_event,
                                   order_id=order_id, client_id=client.id, recipient=recipient,
                                   subject=subject, body=body, status="skipped",
                                   error="No consent" if recipient else f"Client has no {ch} on file"))
        return {"status": status, "channel": ch,
                "message": delivery_message(status, ch, saved=saved, error=error, client=client)}

    if ch == "sms":
        from backend.services.notification_rules import with_opt_out
        body = await with_opt_out(db, org_id, client_id, ch, body)
    simulated = False
    try:
        result = None
        if ch == "email":
            conn = await _email_connection(db, org_id)
            if conn:
                from backend.services.email_oauth import send_via_connection
                result = await send_via_connection(conn, recipient=recipient, subject=subject, body=body,
                                                   attachment=attachment)
        if result is None:
            adapter = _adapter_for(ch, await notif_creds(db, org_id, ch))
            simulated = isinstance(adapter, SimulatedAdapter)
            if attachment and ch == "email" and not simulated:
                result = await adapter.send(recipient=recipient, subject=subject, body=body, attachment=attachment)
            else:
                result = await adapter.send(recipient=recipient, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 — a provider failure is reported, never raised
        result = {"status": "failed", "error": str(exc)[:200]}
    raw = result.get("status") or "failed"
    if simulated:
        status = "not_set_up"
    elif raw == "sent":
        status = "sent"
    else:
        status = "failed"
    db.add(NotificationLog(org_id=org_id, scope="client", channel=ch, trigger_event=trigger_event,
                           order_id=order_id, client_id=client_id, recipient=recipient, subject=subject,
                           body=body, status="sent" if status == "sent" else "failed",
                           error=None if status == "sent" else (result.get("error") or "No provider set up")))
    return {"status": status, "channel": ch,
            "message": delivery_message(status, ch, saved=saved, error=result.get("error"), client=client)}


def track_link(order) -> str:
    from backend.routers.documents import _track_url
    return _track_url(order)


async def notify_staff_about_customer(db: AsyncSession, order, *, kind: str, title: str, body: str) -> int:
    """Customer → shop (flow check G1): the customer approved/declined online or wrote on their
    page. Tell the shop owner, the job's manager and its assignee (in-app bell + push). Each
    person's muted kinds are respected. Never raises; the caller commits."""
    from backend.models import Employee, Organisation
    from backend.services.push import notify
    owner = (await db.execute(select(Organisation.owner_id).where(
        Organisation.id == order.org_id))).scalar_one_or_none()
    ids = {i for i in (owner, order.manager_id, order.assigned_to_id) if i}
    if not ids:
        return 0
    people = (await db.execute(select(Employee.id).where(
        Employee.id.in_(ids), Employee.org_id == order.org_id, Employee.active == True))).scalars().all()  # noqa: E712
    n = 0
    for emp_id in people:
        try:
            if await notify(db, org_id=order.org_id, employee_id=emp_id, kind=kind, title=title[:200],
                            body=body[:500], deep_link=f"/tech#/order/{order.id}", commit=False):
                n += 1
        except Exception:  # noqa: BLE001 — an alert must never break the customer's action
            import logging
            logging.getLogger("tekly").exception("staff alert failed for order %s", order.id)
    return n
