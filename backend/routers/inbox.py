"""Build 104 — the unified inbox.

Every customer message — WhatsApp, SMS, email, Facebook/Instagram DM — lands in one
stream, threaded per customer, so the front desk stops tab-hopping between apps. A
thread can be replied to, assigned, snoozed/closed, and turned into a work order or an
invoice in one step.

Inbound messages arrive through a single provider-agnostic webhook: the channel adapters
(Twilio, Meta, etc.) live at the edge and all normalise to the same shape, so the core
never learns the quirks of any one provider. Outbound send is stubbed at the transport
boundary — it records the message and hands off to whatever the org has wired — because
the interesting logic (threading, assignment, conversion) is provider-independent.
"""
from datetime import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.webhook_security import verify_org_webhook
from backend.db.database import get_db
from backend.models import Client, Employee, InboxMessage, InboxThread
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

CHANNELS = ("whatsapp", "sms", "email", "facebook", "instagram", "call")


def _ser_thread(t: InboxThread, last: Optional[str] = None) -> dict:
    return {"id": t.id, "channel": t.channel, "client_id": t.client_id,
            "subject": t.subject, "handle": t.handle, "status": t.status,
            "unread": t.unread, "assigned_to_id": t.assigned_to_id,
            "order_id": t.order_id, "last_message_at": t.last_message_at.isoformat(),
            "preview": last}


@router.get("/threads")
async def list_threads(
    status: str = "open",
    channel: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(InboxThread).where(InboxThread.org_id == current.org_id)
    if status != "all":
        q = q.where(InboxThread.status == status)
    if channel:
        q = q.where(InboxThread.channel == channel)
    if search:
        q = q.where(or_(InboxThread.subject.icontains(search),
                        InboxThread.handle.icontains(search)))
    threads = (await db.execute(q.order_by(InboxThread.last_message_at.desc())
                                .limit(200))).scalars().all()
    # One query for all previews instead of one per thread (avoids N+1): pull the latest
    # message per thread via a correlated max-timestamp subquery.
    previews: dict[str, str] = {}
    if threads:
        tids = [t.id for t in threads]
        latest = (select(InboxMessage.thread_id,
                         func.max(InboxMessage.created_at).label("mx"))
                  .where(InboxMessage.thread_id.in_(tids))
                  .group_by(InboxMessage.thread_id)).subquery()
        rows = (await db.execute(
            select(InboxMessage.thread_id, InboxMessage.body)
            .join(latest, (InboxMessage.thread_id == latest.c.thread_id)
                  & (InboxMessage.created_at == latest.c.mx)))).all()
        for tid, body in rows:
            previews.setdefault(tid, (body or "")[:80])
    out = [_ser_thread(t, previews.get(t.id, "")) for t in threads]
    unread = (await db.execute(select(func.count()).select_from(InboxThread).where(
        InboxThread.org_id == current.org_id, InboxThread.unread == True,  # noqa: E712
        InboxThread.status == "open"))).scalar() or 0
    return {"items": out, "unread_count": unread}


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, thread_id)
    if t.unread:                       # opening a thread clears its unread flag
        t.unread = False
        await db.commit()
    msgs = (await db.execute(select(InboxMessage).where(
        InboxMessage.thread_id == thread_id)
        .order_by(InboxMessage.created_at))).scalars().all()
    return {"thread": _ser_thread(t),
            "messages": [{"id": m.id, "direction": m.direction, "body": m.body,
                          "media_url": m.media_url, "author_id": m.author_id,
                          "created_at": m.created_at.isoformat()} for m in msgs]}


class InboundIn(BaseModel):
    channel: str
    handle: str                        # sender phone / email / social handle
    body: Optional[str] = None
    media_url: Optional[str] = None
    external_thread_id: Optional[str] = None
    external_message_id: Optional[str] = None
    subject: Optional[str] = None


@router.post("/webhook/{org_id}", status_code=201)
async def inbound_webhook(
    org_id: str, data: InboundIn, request: Request,
    db: AsyncSession = Depends(get_db),
):
    await verify_org_webhook(db, org_id, request)  # Build 110: token-gated
    """Provider-agnostic inbound. Adapters normalise any channel to this shape.

    Threading rule: match an existing open thread by (channel, external id) or by
    (channel, handle); otherwise open a new one. Matching on the handle is what keeps a
    customer's third message in the same conversation as their first, even when the
    provider does not supply a stable thread id.
    """
    if data.channel not in CHANNELS:
        raise HTTPException(422, f"Unknown channel “{data.channel}”")

    # Match any recent thread on this channel+handle, including closed ones: a customer
    # replying to a closed conversation should reopen it, not silently start a new one.
    q = select(InboxThread).where(
        InboxThread.org_id == org_id, InboxThread.channel == data.channel)
    if data.external_thread_id:
        q = q.where(InboxThread.external_id == data.external_thread_id)
    else:
        q = q.where(InboxThread.handle == data.handle)
    thread = (await db.execute(q.order_by(InboxThread.last_message_at.desc()))).scalars().first()

    if not thread:
        # try to attach a known client by phone/email
        client = (await db.execute(select(Client).where(
            Client.org_id == org_id,
            or_(Client.phone == data.handle, Client.email == data.handle)))).scalars().first()
        thread = InboxThread(
            org_id=org_id, channel=data.channel, external_id=data.external_thread_id,
            handle=data.handle, subject=data.subject,
            client_id=client.id if client else None)
        db.add(thread)
        await db.flush()

    msg = InboxMessage(org_id=org_id, thread_id=thread.id, direction="inbound",
                       body=data.body, media_url=data.media_url,
                       external_id=data.external_message_id)
    db.add(msg)
    thread.unread = True
    thread.status = "open"             # a new inbound reopens a snoozed thread
    thread.last_message_at = _dt.utcnow()
    await db.commit()
    return {"thread_id": thread.id, "message_id": msg.id}


class ReplyIn(BaseModel):
    body: str
    media_url: Optional[str] = None


@router.post("/threads/{thread_id}/reply", status_code=201)
async def reply(
    thread_id: str, data: ReplyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Record and dispatch an outbound reply. Transport is delegated to the channel
    adapter configured for the org; here we persist the message and mark the thread
    handled. The reply is attributed to the sending employee for accountability."""
    t = await _load(db, current, thread_id)
    msg = InboxMessage(org_id=current.org_id, thread_id=t.id, direction="outbound",
                       body=data.body, media_url=data.media_url, author_id=current.id)
    db.add(msg)
    t.unread = False
    t.last_message_at = _dt.utcnow()
    # Build 144af: actually dispatch through the channel provider (WhatsApp / Messenger
    # / Instagram / SMS). Gated — 'simulated' when the provider isn't configured.
    from backend.services import messaging as _msg
    res = await _msg.send(t.channel, t.handle, data.body, await _msg.creds_for(db, current.org_id))
    msg.delivered = res["status"] in ("sent", "simulated")
    await db.commit()
    return {"message_id": msg.id, "dispatched": res["status"] != "skipped", "status": res["status"]}


class AssignIn(BaseModel):
    assigned_to_id: Optional[str] = None


@router.patch("/threads/{thread_id}")
async def update_thread(
    thread_id: str,
    status: Optional[str] = None,
    data: Optional[AssignIn] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, thread_id)
    if status:
        if status not in ("open", "snoozed", "closed"):
            raise HTTPException(422, "status must be open|snoozed|closed")
        t.status = status
    if data and data.assigned_to_id is not None:
        t.assigned_to_id = data.assigned_to_id or None
    await db.commit()
    return _ser_thread(t)


class NewThreadIn(BaseModel):
    channel: str
    handle: str
    client_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None


@router.post("/threads", status_code=201)
async def create_thread(
    data: NewThreadIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Start a new outbound conversation on any channel. If a matching open thread already
    exists for this channel+handle, reuse it rather than fork a duplicate. Links to a known
    client by id or by matching the handle against phone/email."""
    if data.channel not in CHANNELS:
        raise HTTPException(422, f"Unknown channel “{data.channel}”")

    existing = (await db.execute(select(InboxThread).where(
        InboxThread.org_id == current.org_id, InboxThread.channel == data.channel,
        InboxThread.handle == data.handle, InboxThread.status != "closed"))).scalars().first()
    thread = existing
    if not thread:
        client_id = data.client_id
        if not client_id:
            c = (await db.execute(select(Client).where(
                Client.org_id == current.org_id,
                or_(Client.phone == data.handle, Client.email == data.handle)))).scalars().first()
            client_id = c.id if c else None
        thread = InboxThread(org_id=current.org_id, channel=data.channel, handle=data.handle,
                             subject=data.subject, client_id=client_id, unread=False)
        db.add(thread)
        await db.flush()

    if data.body:
        msg = InboxMessage(org_id=current.org_id, thread_id=thread.id, direction="outbound",
                           body=data.body, author_id=current.id)
        db.add(msg)
        thread.last_message_at = _dt.utcnow()
    await db.commit()
    return {"thread_id": thread.id}


@router.post("/threads/{thread_id}/to-order", status_code=201)
async def thread_to_order(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Turn a conversation into a work order — the whole point of a unified inbox is that
    a chat becomes a job without re-keying the customer. Links back so the thread shows
    it produced an order, and reuses the thread's client (creating one if the message
    came from an unknown handle)."""
    t = await _load(db, current, thread_id)
    if t.order_id:
        raise HTTPException(409, "This conversation already has a work order")

    client_id = t.client_id
    if not client_id:
        client = Client(org_id=current.org_id,
                        name=t.handle or "Inbox customer",
                        phone=t.handle if (t.channel in ("whatsapp", "sms", "call")) else None,
                        email=t.handle if t.channel == "email" else None)
        db.add(client)
        await db.flush()
        client_id = client.id
        t.client_id = client_id

    from backend.models import Location, Order
    from backend.routers.orders import _order_number
    loc = (await db.execute(select(Location).where(
        Location.org_id == current.org_id).order_by(Location.id).limit(1))).scalar_one_or_none()
    seq = (await db.execute(select(func.count()).select_from(Order).where(
        Order.org_id == current.org_id))).scalar() or 0
    order = Order(org_id=current.org_id, client_id=client_id,
                  location_id=loc.id if loc else None,
                  order_number=_order_number(current.org_id, seq + 1),
                  complaint=f"From {t.channel} conversation",
                  status="new")
    db.add(order)
    await db.flush()
    t.order_id = order.id
    await db.commit()
    return {"order_id": order.id, "order_number": order.order_number}


async def _load(db: AsyncSession, current: Employee, thread_id: str) -> InboxThread:
    t = (await db.execute(select(InboxThread).where(
        InboxThread.id == thread_id,
        InboxThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Conversation not found")
    return t


# ── Build 144af: Meta webhook (Messenger / Instagram / WhatsApp Cloud) ──────────
async def _ingest_normalised(db, org_id: str, m: dict) -> bool:
    """Insert one normalised inbound message into the inbox, matching or opening a
    thread. Returns True if a new message was stored (False when de-duped)."""
    if m.get("external_message_id"):
        dup = (await db.execute(select(InboxMessage).where(
            InboxMessage.org_id == org_id,
            InboxMessage.external_id == m["external_message_id"]))).scalars().first()
        if dup:
            return False
    q = select(InboxThread).where(InboxThread.org_id == org_id, InboxThread.channel == m["channel"])
    if m.get("external_thread_id"):
        q = q.where(InboxThread.external_id == m["external_thread_id"])
    else:
        q = q.where(InboxThread.handle == m["handle"])
    thread = (await db.execute(q.order_by(InboxThread.last_message_at.desc()))).scalars().first()
    if not thread:
        client = (await db.execute(select(Client).where(
            Client.org_id == org_id,
            or_(Client.phone == m["handle"], Client.email == m["handle"])))).scalars().first()
        thread = InboxThread(org_id=org_id, channel=m["channel"], external_id=m.get("external_thread_id"),
                             handle=m["handle"], subject=m.get("subject"),
                             client_id=client.id if client else None)
        db.add(thread)
        await db.flush()
    db.add(InboxMessage(org_id=org_id, thread_id=thread.id, direction="inbound",
                        body=m.get("body", ""), external_id=m.get("external_message_id")))
    thread.unread = True
    thread.status = "open"
    thread.last_message_at = _dt.utcnow()
    return True


@router.get("/webhook/meta/{org_id}", include_in_schema=False)
async def meta_verify(org_id: str, request: Request):
    """Meta subscription handshake: echo hub.challenge when the verify token matches."""
    from fastapi.responses import PlainTextResponse

    from backend.services.messaging import verify_subscription
    q = request.query_params
    challenge = verify_subscription(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    if challenge is None:
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@router.post("/webhook/meta/{org_id}", status_code=201)
async def meta_inbound(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Inbound Messenger / Instagram / WhatsApp Cloud events → inbox threads.
    De-duped by provider message id. When META_APP_SECRET is configured the request
    is authenticated via the X-Hub-Signature-256 header (fail-closed); only when the
    secret is unset (dev) are unsigned posts accepted."""
    import hashlib
    import hmac

    from backend.config import settings
    from backend.services.messaging import parse_meta_webhook

    # Read the raw body once: it's needed for both signature verification and parsing.
    raw = await request.body()
    if settings.META_APP_SECRET:  # fail-closed once a secret exists
        presented = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            settings.META_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not presented or not hmac.compare_digest(presented, expected):
            raise HTTPException(403, "Invalid or missing X-Hub-Signature-256")
    try:
        import json as _json
        payload = _json.loads(raw.decode() or "{}")
    except Exception:
        payload = {}
    ingested = 0
    for m in parse_meta_webhook(payload):
        if await _ingest_normalised(db, org_id, m):
            ingested += 1
    await db.commit()
    return {"ingested": ingested}
