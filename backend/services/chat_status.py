"""Chat inbox status rules (C3) and the one inbound path every provider uses (C4).

Statuses, set by message direction:
  new           a conversation nobody from the shop has answered yet
  reply_needed  the customer wrote again after the shop replied
  replied       the shop wrote last
  completed     done; replied threads complete on their own after 24h of quiet

A new customer message reopens a completed thread. Spam threads keep their messages but sit in
the Spam folder and never notify anyone.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ChatMessage, ChatThread, Client
from backend.services.dates import now_utc

STATUSES = ("new", "reply_needed", "replied", "completed")
AUTO_COMPLETE_AFTER = timedelta(hours=24)
# Channels a provider webhook may post. "call" is accepted for old integrations of the
# retired /api/inbox hook, which took it.
CHANNELS = ("whatsapp", "sms", "email", "facebook", "instagram", "call")


def mark_inbound(t: ChatThread, now: datetime | None = None) -> None:
    now = now or now_utc()
    t.last_inbound_at = now
    t.last_message_at = now
    if t.status in ("replied", "completed"):
        t.status = "reply_needed" if t.last_outbound_at else "new"
    elif t.status not in STATUSES:
        t.status = "new"


def mark_outbound(t: ChatThread, now: datetime | None = None) -> None:
    now = now or now_utc()
    t.last_outbound_at = now
    t.last_message_at = now
    t.status = "replied"


async def link_client(db: AsyncSession, org_id: str, handle: str | None):
    """Orderry rule: ambiguous numbers resolve to the most recent record. Email handles
    match the client's email."""
    if not handle:
        return None
    if "@" in handle:
        return (await db.execute(select(Client).where(Client.org_id == org_id, Client.email == handle)
                                 .order_by(Client.created_at.desc()))).scalars().first()
    # Same number rule as calls (D2): formatting-insensitive and country-code aware.
    from backend.services.call_contacts import find_client
    return await find_client(db, org_id, handle, newest=True)


async def ingest_inbound(db: AsyncSession, org_id: str, *, channel: str, handle: str, body: str,
                         name: str | None = None, external_id: str | None = None):
    """Store one inbound message, opening the thread if needed. Returns (thread, message), or
    (thread, None) when this provider message id was already stored (webhook retry).
    The caller commits, then calls `notify_inbound`. Long names / handles are cut to the
    column sizes so a provider can't make the insert fail.

    A suspended (or switched-off) shop takes nothing in (services/org_live): this returns
    (None, None) exactly like a provider retry, so every webhook still answers 2xx (the provider
    stops retrying) but nothing is stored and nobody is alerted. The one exception is a carrier
    STOP / START by text, which still sets the customer's consent (compliance, like unsubscribe
    links) so a resumed shop never texts someone who opted out meanwhile."""
    from backend.services.org_live import org_id_is_live
    if not await org_id_is_live(db, org_id):
        await _consent_keyword_while_closed(db, org_id, channel, handle, body)
        return None, None
    handle = (handle or "")[:160]
    name = (name or "")[:200] or None
    external_id = (external_id or "")[:160] or None
    if external_id:
        dup = (await db.execute(select(ChatMessage.id).join(ChatThread, ChatThread.id == ChatMessage.thread_id)
                                .where(ChatThread.org_id == org_id, ChatMessage.external_id == external_id)
                                .limit(1))).scalar()
        if dup:
            return None, None
    thread = (await db.execute(select(ChatThread).where(
        ChatThread.org_id == org_id, ChatThread.channel == channel,
        ChatThread.contact_phone == handle).order_by(ChatThread.created_at.desc()))).scalars().first()
    if not thread:
        client = await link_client(db, org_id, handle)
        thread = ChatThread(org_id=org_id, channel=channel, contact_phone=handle,
                            contact_name=name or (client.name if client else None),
                            client_id=client.id if client else None, status="new", is_spam=False)
        db.add(thread)
        await db.flush()
    msg = ChatMessage(thread_id=thread.id, direction="in", body=body or "", status="received",
                      external_id=external_id)
    if external_id:
        # A concurrent retry may have stored it between the check above and here.
        try:
            async with db.begin_nested():
                db.add(msg)
        except IntegrityError:
            return None, None
    else:
        db.add(msg)
    mark_inbound(thread)
    return thread, msg


async def _consent_keyword_while_closed(db: AsyncSession, org_id: str, channel: str,
                                        handle: str | None, body: str | None) -> None:
    """STOP / START by text for a shop that isn't live: update consent only, and commit (callers
    treat this as a retry and commit nothing)."""
    if channel not in ("sms", "whatsapp"):
        return
    from backend.routers.chats import OPT_IN_WORDS, OPT_OUT_WORDS
    kw = (body or "").strip().upper().rstrip(".!")
    if kw not in OPT_OUT_WORDS and kw not in OPT_IN_WORDS:
        return
    c = await link_client(db, org_id, (handle or "")[:160])
    if c:
        c.consent_phone = kw in OPT_IN_WORDS
        await db.commit()


async def notify_inbound(db: AsyncSession, org_id: str, thread: ChatThread, body: str) -> None:
    """Fire a `chat.message` notification to the technician most likely to own this
    conversation: the one assigned to the client's most recent open order. Spam threads and
    threads with no such technician notify nobody."""
    if thread.is_spam or not thread.client_id:
        return
    from backend.models import Order
    o = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.client_id == thread.client_id,
        Order.assigned_to_id.isnot(None), Order.closed_at.is_(None))
        .order_by(Order.created_at.desc()))).scalars().first()
    if not o or not o.assigned_to_id:
        return
    try:
        from backend.services.push import notify
        await notify(db, org_id=org_id, employee_id=o.assigned_to_id, kind="chat.message",
                     title=f"New message · {o.order_number}",
                     body=(body or "")[:140], deep_link=f"/tech#/order/{o.id}")
    except Exception:  # noqa: BLE001 — a push failure never loses the message
        pass


async def autocomplete_replied(db: AsyncSession, now: datetime | None = None) -> int:
    """Complete every replied thread that has been quiet for 24h (all orgs). Returns the count."""
    cutoff = (now or now_utc()) - AUTO_COMPLETE_AFTER
    res = await db.execute(update(ChatThread).where(
        ChatThread.status == "replied", ChatThread.last_outbound_at.isnot(None),
        ChatThread.last_outbound_at <= cutoff).values(status="completed")
        .execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount or 0
