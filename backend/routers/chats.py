"""Chats — a unified inbox. Threads auto-link to CRM clients by phone (most
recent record wins, Orderry's rule). Outbound transport is a SIMULATED adapter
(logged, not actually transmitted) until Twilio/Meta keys exist — the same seam
as the notification engine. A thread can convert into a Lead in one click."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ChatMessage, ChatThread, Client, Employee, Lead
from backend.services.auth import get_current_employee


async def _notify_inbound_chat(db: AsyncSession, org_id: str, thread: "ChatThread", body: str) -> None:
    """Fire a `chat.message` notification to the technician most likely to own
    this conversation — the one assigned to the client's most recent active
    order. No such tech ⇒ no-op (nothing to route to)."""
    if not thread.client_id:
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
    except Exception:
        pass

router = APIRouter(prefix="/api/chats", tags=["chats"])


async def _link_client(db, org_id, phone):
    """Orderry rule: ambiguous numbers resolve to the most recent record."""
    if not phone:
        return None
    rows = (await db.execute(select(Client).where(
        Client.org_id == org_id, Client.phone == phone)
        .order_by(Client.created_at.desc()))).scalars().all()
    return rows[0] if rows else None


def _ser_thread(t: ChatThread, client_name=None, last_body=None, unread=0) -> dict:
    return {"id": t.id, "channel": t.channel, "contact_phone": t.contact_phone,
            "contact_name": t.contact_name, "client_id": t.client_id,
            "client_name": client_name, "lead_id": t.lead_id, "last_body": last_body,
            "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None}


@router.get("/threads")
async def list_threads(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(ChatThread).where(ChatThread.org_id == current.org_id)
        .order_by(ChatThread.last_message_at.desc().nullslast()).limit(200))).scalars().all()
    tids = [t.id for t in rows]
    cids = {t.client_id for t in rows if t.client_id}
    # batch the client names and the per-thread last message (was 2 queries per thread)
    names = dict((await db.execute(select(Client.id, Client.name).where(
        Client.id.in_(cids)))).all()) if cids else {}
    last_by: dict = {}
    if tids:
        sub = (select(ChatMessage.thread_id, func.max(ChatMessage.created_at).label("mx"))
               .where(ChatMessage.thread_id.in_(tids)).group_by(ChatMessage.thread_id)).subquery()
        lm = (await db.execute(select(ChatMessage).join(sub, and_(
            ChatMessage.thread_id == sub.c.thread_id, ChatMessage.created_at == sub.c.mx)))).scalars().all()
        for m in lm:
            last_by.setdefault(m.thread_id, m)
    out = [_ser_thread(t, names.get(t.client_id),
                       (last_by[t.id].body[:80] if last_by.get(t.id) and last_by[t.id].body else None))
           for t in rows]
    return {"items": out}


@router.get("/threads/{tid}/messages")
async def thread_messages(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.thread_id == tid)
        .order_by(ChatMessage.created_at))).scalars().all()
    return {"items": [{"id": m.id, "direction": m.direction, "body": m.body,
                       "status": m.status,
                       "created_at": m.created_at.isoformat() if m.created_at else None}
                      for m in msgs]}


class ThreadIn(BaseModel):
    channel: str = "whatsapp"
    contact_phone: str
    contact_name: Optional[str] = None
    first_message: Optional[str] = None  # optional inbound opener (demo/manual log)


@router.post("/threads", status_code=201)
async def create_thread(
    data: ThreadIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    client = await _link_client(db, current.org_id, data.contact_phone)
    t = ChatThread(org_id=current.org_id, channel=data.channel,
                   contact_phone=data.contact_phone,
                   contact_name=data.contact_name or (client.name if client else None),
                   client_id=client.id if client else None,
                   last_message_at=datetime.utcnow())
    db.add(t)
    await db.flush()
    if data.first_message:
        db.add(ChatMessage(thread_id=t.id, direction="in", body=data.first_message,
                           status="received"))
    await db.commit()
    return _ser_thread(t, client.name if client else None)


class SendIn(BaseModel):
    body: str


@router.post("/threads/{tid}/send", status_code=201)
async def send_message(
    tid: str, data: SendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    if not data.body.strip():
        raise HTTPException(400, "Message is empty")
    # Real transport via the Twilio adapter when keys exist; simulated otherwise.
    from backend.services.notifications import SimulatedAdapter, _adapter_for, notif_creds
    channel = t.channel if t.channel in ("sms", "whatsapp") else "sms"
    adapter = _adapter_for(channel, await notif_creds(db, current.org_id, channel))
    result = await adapter.send(recipient=t.contact_phone or "", subject=None, body=data.body)
    simulated = isinstance(adapter, SimulatedAdapter)
    m = ChatMessage(thread_id=tid, direction="out", body=data.body,
                    author_id=current.id, status=result.get("status", "sent"))
    db.add(m)
    t.last_message_at = datetime.utcnow()
    await db.commit()
    return {"id": m.id, "status": m.status, "simulated": simulated, "error": result.get("error")}


@router.post("/threads/{tid}/inbound", status_code=201)
async def receive_inbound(
    tid: str, data: SendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Log an inbound message (demo seam; a real webhook lands here later)."""
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    db.add(ChatMessage(thread_id=tid, direction="in", body=data.body, status="received"))
    t.last_message_at = datetime.utcnow()
    await db.commit()
    await _notify_inbound_chat(db, current.org_id, t, data.body)
    return {"ok": True}


@router.post("/webhook/twilio")
async def twilio_inbound(request: Request, db: AsyncSession = Depends(get_db),
                         org_id: str = Query(..., description="target org for this Twilio number")):
    """Live inbound webhook for Twilio SMS/WhatsApp. Twilio POSTs form-encoded
    fields (From, To, Body, ProfileName). We resolve/create the thread, link the
    CRM client by phone, and store the inbound message. Returns empty TwiML."""
    form = await request.form()
    # Build 110: verify Twilio's X-Twilio-Signature when an auth token is configured
    # (HMAC-SHA1 over the request URL + sorted form params, base64). Fail-closed —
    # same pattern as the Square/Stripe payment webhooks.
    from backend.config import settings as _settings
    if _settings.TWILIO_AUTH_TOKEN:
        import base64
        import hashlib
        import hmac as _hmac
        payload = str(request.url)
        for k in sorted(form.keys()):
            payload += k + str(form[k])
        expected = base64.b64encode(_hmac.new(
            _settings.TWILIO_AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()
        presented = request.headers.get("X-Twilio-Signature", "")
        if not _hmac.compare_digest(expected, presented):
            raise HTTPException(403, "Invalid Twilio signature")
    raw_from = (form.get("From") or "").strip()
    body = (form.get("Body") or "").strip()
    profile = (form.get("ProfileName") or "").strip() or None
    channel = "whatsapp" if raw_from.startswith("whatsapp:") else "sms"
    phone = raw_from.replace("whatsapp:", "")
    if not phone:
        raise HTTPException(400, "Missing sender")

    thread = (await db.execute(select(ChatThread).where(
        ChatThread.org_id == org_id, ChatThread.channel == channel,
        ChatThread.contact_phone == phone))).scalars().first()
    if not thread:
        client = await _link_client(db, org_id, phone)
        thread = ChatThread(org_id=org_id, channel=channel, contact_phone=phone,
                            contact_name=profile, client_id=client.id if client else None)
        db.add(thread)
        await db.flush()
    db.add(ChatMessage(thread_id=thread.id, direction="in", body=body, status="received"))
    thread.last_message_at = datetime.utcnow()
    await db.commit()
    await _notify_inbound_chat(db, org_id, thread, body)
    return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>",
                    media_type="application/xml")


@router.post("/threads/{tid}/to-lead", status_code=201)
async def thread_to_lead(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Convert the conversation into an Inquiry on the leads board (Orderry)."""
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    if t.lead_id:
        raise HTTPException(409, "This chat already has a lead")
    first_in = (await db.execute(select(ChatMessage).where(
        ChatMessage.thread_id == tid, ChatMessage.direction == "in")
        .order_by(ChatMessage.created_at).limit(1))).scalars().first()
    from datetime import timedelta
    lead = Lead(org_id=current.org_id, location_id=current.location_id,
                name=t.contact_name or t.contact_phone or "Chat contact",
                phone=t.contact_phone, source=t.channel,
                inquiry=(first_in.body if first_in else None),
                client_id=t.client_id, manager_id=current.id,
                sla_due_at=datetime.utcnow() + timedelta(minutes=60))
    db.add(lead)
    await db.flush()
    t.lead_id = lead.id
    await db.commit()
    return {"lead_id": lead.id}


@router.post("/threads/{tid}/to-order", status_code=201)
async def thread_to_order(
    tid: str,
    is_estimate: bool = False,   # additive: win the chat as an estimate instead of a work order
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 81 — turn a conversation straight into a work order.

    Creates the client from the chat contact if there isn't one yet, seeds the
    complaint from the customer's first inbound message, and links the thread to the
    order so the conversation stays attached to the job.
    """
    from backend.models import Client, Order, OrderStatus
    from backend.services.numbering import next_number

    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    if t.order_id:
        raise HTTPException(409, "This chat already has a work order")

    # reuse the linked client, else create one from the chat contact
    client_id = t.client_id
    if not client_id:
        existing = None
        if t.contact_phone:
            existing = (await db.execute(select(Client).where(
                Client.org_id == current.org_id,
                Client.phone == t.contact_phone))).scalars().first()
        if existing:
            client_id = existing.id
        else:
            c = Client(org_id=current.org_id,
                       name=t.contact_name or t.contact_phone or "Chat contact",
                       phone=t.contact_phone, source=t.channel)
            db.add(c)
            await db.flush()
            client_id = c.id
        t.client_id = client_id

    first_in = (await db.execute(select(ChatMessage).where(
        ChatMessage.thread_id == tid, ChatMessage.direction == "in")
        .order_by(ChatMessage.created_at).limit(1))).scalars().first()

    initial = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id,
        OrderStatus.record_type == ("estimate" if is_estimate else "work_order"),
        OrderStatus.is_default == True, OrderStatus.active == True))).scalars().first()  # noqa: E712

    order = Order(
        org_id=current.org_id, location_id=current.location_id,
        order_number=await next_number(db, current.org_id, "order"),
        client_id=client_id, assigned_to_id=current.id, is_estimate=is_estimate,
        status=initial.key if initial else "new",
        complaint=(first_in.body if first_in else None) or f"From {t.channel} chat",
    )
    db.add(order)
    await db.flush()
    t.order_id = order.id
    await db.commit()
    return {"order_id": order.id, "order_number": order.order_number, "client_id": client_id}
