"""Chats — a unified inbox. Threads auto-link to CRM clients by phone (most recent record
wins, Orderry's rule). Outbound goes through the shop's provider (Twilio / WhatsApp Cloud /
Meta) and is simulated when no keys exist. A thread can convert into a Lead or a work order.

C3: every thread carries a status (new / reply_needed / replied / completed) that follows
the messages (services/chat_status.py), plus a Spam folder that notifies nobody.
C4: provider webhooks (Twilio, Meta, and the generic normalised hook) all land here; the old
/api/inbox/webhook URLs forward to these handlers.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ChatMessage, ChatThread, Employee, Lead
from backend.services import chat_status as cs
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/chats", tags=["chats"])


OPT_OUT_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT OUT"}
OPT_IN_WORDS = {"START", "UNSTOP", "YES", "OPTIN", "OPT IN"}
FOLDERS = ("inbox", "spam")


def _iso(d):
    return d.isoformat() if d else None


def _ser_thread(t: ChatThread, client_name=None, last_body=None) -> dict:
    return {"id": t.id, "channel": t.channel, "contact_phone": t.contact_phone,
            "contact_name": t.contact_name, "client_id": t.client_id,
            "client_name": client_name, "lead_id": t.lead_id, "order_id": t.order_id,
            "last_body": last_body, "last_message_at": _iso(t.last_message_at),
            "status": t.status or "new", "is_spam": bool(t.is_spam), "spam_at": _iso(t.spam_at),
            "last_inbound_at": _iso(t.last_inbound_at), "last_outbound_at": _iso(t.last_outbound_at)}


async def _load(db: AsyncSession, org_id: str, tid: str) -> ChatThread:
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == tid, ChatThread.org_id == org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    return t


@router.get("/threads")
async def list_threads(
    status: str = Query("all", description="all | new | reply_needed | replied | completed"),
    folder: str = Query("inbox", description="inbox | spam"),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if status != "all" and status not in cs.STATUSES:
        raise HTTPException(422, f"status must be all or one of {list(cs.STATUSES)}")
    if folder not in FOLDERS:
        raise HTTPException(422, "folder must be inbox or spam")
    from backend.models import Client
    q = select(ChatThread).where(ChatThread.org_id == current.org_id,
                                 ChatThread.is_spam == (folder == "spam"))
    if status != "all" and folder == "inbox":
        q = q.where(ChatThread.status == status)
    rows = (await db.execute(q.order_by(ChatThread.last_message_at.desc().nullslast())
                             .limit(200))).scalars().all()
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
    # Tab counts: one grouped query over the whole inbox, plus the spam folder size.
    counts = {s: 0 for s in cs.STATUSES}
    for s, n in (await db.execute(select(ChatThread.status, func.count()).where(
            ChatThread.org_id == current.org_id, ChatThread.is_spam == False)  # noqa: E712
            .group_by(ChatThread.status))).all():
        if s in counts:
            counts[s] = n
    counts["all"] = sum(counts[s] for s in cs.STATUSES)
    counts["spam"] = (await db.execute(select(func.count()).select_from(ChatThread).where(
        ChatThread.org_id == current.org_id, ChatThread.is_spam == True))).scalar() or 0  # noqa: E712
    return {"items": out, "counts": counts}


@router.get("/threads/{tid}/messages")
async def thread_messages(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _load(db, current.org_id, tid)
    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.thread_id == tid)
        .order_by(ChatMessage.created_at))).scalars().all()
    return {"items": [{"id": m.id, "direction": m.direction, "body": m.body,
                       "status": m.status, "created_at": _iso(m.created_at)}
                      for m in msgs]}


class ThreadPatch(BaseModel):
    status: Optional[str] = None      # new | reply_needed | replied | completed
    is_spam: Optional[bool] = None    # true = move to Spam, false = restore to the inbox


@router.patch("/threads/{tid}")
async def update_thread(
    tid: str, data: ThreadPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_chats")),
):
    """Set a thread's status by hand, or move it to / out of the Spam folder."""
    t = await _load(db, current.org_id, tid)
    if data.status is not None:
        if data.status not in cs.STATUSES:
            raise HTTPException(422, f"status must be one of {list(cs.STATUSES)}")
        t.status = data.status
    if data.is_spam is not None and bool(t.is_spam) != data.is_spam:
        t.is_spam = data.is_spam
        t.spam_at = datetime.utcnow() if data.is_spam else None
    await db.commit()
    return _ser_thread(t)


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
    client = await cs.link_client(db, current.org_id, data.contact_phone)
    t = ChatThread(org_id=current.org_id, channel=data.channel,
                   contact_phone=data.contact_phone,
                   contact_name=data.contact_name or (client.name if client else None),
                   client_id=client.id if client else None, status="new", is_spam=False,
                   last_message_at=datetime.utcnow())
    db.add(t)
    await db.flush()
    if data.first_message:
        db.add(ChatMessage(thread_id=t.id, direction="in", body=data.first_message,
                           status="received"))
        cs.mark_inbound(t)
    await db.commit()
    return _ser_thread(t, client.name if client else None)


class SendIn(BaseModel):
    body: str


async def _transport(db: AsyncSession, org_id: str, t: ChatThread, body: str) -> tuple[dict, bool]:
    """Send through the shop's provider for this channel. Returns (result, simulated)."""
    if t.channel in ("facebook", "instagram"):
        from backend.services import messaging
        res = await messaging.send(t.channel, t.contact_phone or "", body,
                                   await messaging.creds_for(db, org_id))
        simulated = res.get("status") == "simulated"
        return ({"status": "sent" if simulated else res.get("status"), "error": res.get("error")}, simulated)
    from backend.services.notifications import SimulatedAdapter, _adapter_for, notif_creds
    channel = t.channel if t.channel in ("sms", "whatsapp", "email") else "sms"
    adapter = _adapter_for(channel, await notif_creds(db, org_id, channel))
    res = await adapter.send(recipient=t.contact_phone or "", subject=None, body=body)
    return res, isinstance(adapter, SimulatedAdapter)


@router.post("/threads/{tid}/send", status_code=201)
async def send_message(
    tid: str, data: SendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current.org_id, tid)
    if not data.body.strip():
        raise HTTPException(400, "Message is empty")
    result, simulated = await _transport(db, current.org_id, t, data.body)
    m = ChatMessage(thread_id=tid, direction="out", body=data.body,
                    author_id=current.id, status=result.get("status") or "sent")
    db.add(m)
    if m.status == "failed":           # nothing reached the customer: not a reply
        t.last_message_at = datetime.utcnow()
    else:
        cs.mark_outbound(t)
    await db.commit()
    return {"id": m.id, "status": m.status, "simulated": simulated, "error": result.get("error"),
            "thread_status": t.status}


@router.post("/threads/{tid}/inbound", status_code=201)
async def receive_inbound(
    tid: str, data: SendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Log an inbound message by hand (a customer replied on another device, a demo)."""
    t = await _load(db, current.org_id, tid)
    db.add(ChatMessage(thread_id=tid, direction="in", body=data.body, status="received"))
    cs.mark_inbound(t)
    await db.commit()
    await cs.notify_inbound(db, current.org_id, t, data.body)
    return {"ok": True, "thread_status": t.status}


# ── Provider webhooks ─────────────────────────────────────────────────────────

@router.post("/webhook/twilio")
async def twilio_inbound(request: Request, db: AsyncSession = Depends(get_db),
                         org_id: str = Query(..., description="target org for this Twilio number")):
    """Live inbound webhook for Twilio SMS/WhatsApp. Twilio POSTs form-encoded
    fields (From, To, Body, ProfileName, MessageSid). We resolve/create the thread, link the
    CRM client by phone, and store the inbound message once per MessageSid. Returns empty TwiML."""
    form = await request.form()
    # Verify Twilio's X-Twilio-Signature (HMAC-SHA1 over the request URL + sorted form
    # params, base64) with this shop's auth token, else the operator-wide one. With no
    # token at all, unsigned posts are accepted only in development/test.
    from backend.config import settings as _settings
    from backend.core.webhook_security import provider_secrets, require_signature
    from backend.services.twilio_voice import request_urls, signature_ok
    secrets = await provider_secrets(db, org_id, "twilio", "auth_token", _settings.TWILIO_AUTH_TOKEN)
    params = [(k, str(v)) for k, v in form.multi_items()]
    presented = request.headers.get("X-Twilio-Signature", "")
    urls = request_urls(request)       # S4: also the public URL Twilio signed behind a proxy
    verified = any(signature_ok(s, presented, urls, params) for s in secrets)
    require_signature(secrets, verified, "Twilio", status=403)
    twiml = Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>",
                     media_type="application/xml")
    raw_from = (form.get("From") or "").strip()
    body = (form.get("Body") or "").strip()
    profile = (form.get("ProfileName") or "").strip() or None
    channel = "whatsapp" if raw_from.startswith("whatsapp:") else "sms"
    phone = raw_from.replace("whatsapp:", "")
    if not phone:
        raise HTTPException(400, "Missing sender")

    thread, msg = await cs.ingest_inbound(db, org_id, channel=channel, handle=phone, body=body,
                                          name=profile, external_id=(form.get("MessageSid") or None))
    if msg is None:                    # Twilio retry of a message we already stored
        return twiml
    # Carrier keywords: STOP turns automatic texts off for this customer, START turns them back on.
    kw = body.strip().upper().rstrip(".!")
    if kw in OPT_OUT_WORDS or kw in OPT_IN_WORDS:
        c = await cs.link_client(db, org_id, phone)
        if c:
            c.consent_phone = kw in OPT_IN_WORDS
            db.add(ChatMessage(thread_id=thread.id, direction="out", status="system",
                               body="Texts turned off by the customer (STOP)." if kw in OPT_OUT_WORDS
                               else "Texts turned back on by the customer (START)."))
    await db.commit()
    await cs.notify_inbound(db, org_id, thread, body)
    return twiml


@router.get("/webhook/meta/{org_id}", include_in_schema=False)
async def meta_verify(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Meta subscription handshake: echo hub.challenge when the verify token matches THIS
    shop's own token. The operator-wide token only counts for a shop without its own."""
    from fastapi.responses import PlainTextResponse

    from backend.config import settings
    from backend.services import org_creds
    from backend.services.messaging import verify_subscription
    own = (await org_creds.own(db, org_id, "meta")).get("verify_token")
    q = request.query_params
    challenge = verify_subscription(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"),
                                    [own] if own else [settings.META_VERIFY_TOKEN])
    if challenge is None:
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(challenge)


@router.post("/webhook/meta/{org_id}", status_code=201)
async def meta_inbound(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Inbound Messenger / Instagram / WhatsApp Cloud events → chat threads, de-duped by
    provider message id. Authenticated via X-Hub-Signature-256.

    S2 — the URL names the org, so the signature alone can't prove the tenant: one Meta app
    (the operator's) signs events for every shop it serves. So:
      * the shop's own app secret is always accepted; the operator-wide META_APP_SECRET only
        for a shop that registered its Page / Instagram / WhatsApp ids;
      * when ids are registered, only events addressed to them are ingested, so a delivery
        signed for one shop and replayed at another shop's URL stores nothing.
    With no secret at all, unsigned posts are accepted only in development/test."""
    import hashlib
    import hmac
    import json

    from backend.config import settings
    from backend.core.webhook_security import require_signature
    from backend.services import org_creds
    from backend.services.messaging import meta_account_ids, parse_meta_webhook

    raw = await request.body()       # needed for both the signature and parsing
    own = await org_creds.own(db, org_id, "meta")
    ids = meta_account_ids(own)
    secrets = [s for s in dict.fromkeys([own.get("app_secret"),
                                         settings.META_APP_SECRET if ids else None]) if s]
    presented = request.headers.get("X-Hub-Signature-256", "")
    verified = bool(presented) and any(hmac.compare_digest(
        presented, "sha256=" + hmac.new(s.encode(), raw, hashlib.sha256).hexdigest()) for s in secrets)
    if not secrets and settings.META_APP_SECRET:
        # The operator app is configured but this shop never registered its ids: refuse rather
        # than trust a delivery that could belong to any shop.
        raise HTTPException(403, "Register this shop's Meta Page / Instagram / WhatsApp ids first")
    require_signature(secrets, verified, "Meta", status=403)
    try:
        payload = json.loads(raw.decode() or "{}")
    except Exception:  # noqa: BLE001 — a malformed body ingests nothing
        payload = {}
    stored = []
    for m in parse_meta_webhook(payload):
        if ids and m.get("account_id") not in ids:
            continue                   # addressed to another shop's Page / number
        thread, msg = await cs.ingest_inbound(db, org_id, channel=m["channel"], handle=m["handle"],
                                              body=m.get("body") or "",
                                              external_id=m.get("external_message_id"))
        if msg is not None:
            stored.append((thread, m.get("body") or ""))
    await db.commit()
    for thread, body in stored:
        await cs.notify_inbound(db, org_id, thread, body)
    return {"ingested": len(stored)}


class InboundIn(BaseModel):
    channel: str = Field(max_length=20)
    handle: str = Field(max_length=160)          # sender phone / email / social handle
    body: Optional[str] = None
    name: Optional[str] = Field(None, max_length=200)
    external_message_id: Optional[str] = Field(None, max_length=160)


@router.post("/webhook/{org_id}", status_code=201)
async def generic_inbound(org_id: str, data: InboundIn, request: Request,
                          db: AsyncSession = Depends(get_db)):
    """Provider-agnostic inbound for anything without its own adapter: post a normalised
    message with the org's X-Webhook-Token. De-duped by external_message_id when given."""
    from backend.core.webhook_security import verify_org_webhook
    await verify_org_webhook(db, org_id, request)
    if data.channel not in cs.CHANNELS:
        raise HTTPException(422, f"Unknown channel “{data.channel}”")
    if not (data.handle or "").strip():
        raise HTTPException(422, "handle is required")
    thread, msg = await cs.ingest_inbound(db, org_id, channel=data.channel, handle=data.handle.strip(),
                                          body=data.body or "", name=data.name,
                                          external_id=data.external_message_id)
    if msg is None:
        dup = (await db.execute(select(ChatMessage).join(ChatThread, ChatThread.id == ChatMessage.thread_id)
                                .where(ChatThread.org_id == org_id,
                                       ChatMessage.external_id == data.external_message_id))).scalars().first()
        return {"thread_id": dup.thread_id if dup else None, "message_id": dup.id if dup else None,
                "duplicate": True}
    await db.commit()
    await cs.notify_inbound(db, org_id, thread, data.body or "")
    return {"thread_id": thread.id, "message_id": msg.id}


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
            existing = await cs.link_client(db, current.org_id, t.contact_phone)
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
