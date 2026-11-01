"""Tekly Go: message the customer from your own phone (design §3, phase 1b).

The shop's saved templates are filled in on the server, using the same {Placeholders} as printed
documents, so a text from the phone reads the same as one from the web app. The phone then opens
its own SMS or WhatsApp app with the number and text ready, and the person taps Send there. A
phone app can't send silently, and that one tap is the best a PWA can do.

Because the send happens outside Tekly, we record a "handoff": what was opened, to whom, by whom.
When the person comes back and says it was sent, it goes into the job's history and into the
customer's portal thread, so the conversation is all in one place.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.database import get_db
from backend.models import (
    Client, ConsentLog, Employee, MessageHandoff, MessageTemplate, Order, OrderComment, Organisation,
)
from backend.services.auth import get_current_employee, record_scope, require_perm
from backend.services.phones import to_e164

router = APIRouter(prefix="/api/mobile", tags=["mobile-messages"], dependencies=[Depends(record_scope)])

CHANNELS = ("sms", "whatsapp")

# What a customer agrees to when they sign at drop-off, unless the shop wrote its own wording
# (Settings → Customer pages → Drop-off terms).
DEFAULT_DROPOFF_TERMS = (
    "I'm leaving this item for diagnosis and repair. I've removed or backed up my data; the shop "
    "isn't responsible for data loss. Repairs beyond the agreed estimate need my approval first. "
    "Items not collected within 90 days of the repair being ready may be disposed of."
)


@router.get("/dropoff-terms")
async def dropoff_terms(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The drop-off terms the customer reads and signs on the tablet at intake."""
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    custom = ((org.dropoff_terms if org else None) or "").strip()
    return {"terms": custom or DEFAULT_DROPOFF_TERMS, "custom": bool(custom), "shop": org.name if org else ""}


async def _order(db, current, order_id) -> Order:
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
         .options(selectinload(Order.client), selectinload(Order.lines), selectinload(Order.device),
                  selectinload(Order.assigned_to)))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    from backend.routers.orders import _guard_order_access
    await _guard_order_access(db, current, o)
    return o


async def _text_vars(db, org_id: str, o: Order, base_url: str = "") -> dict:
    """The document placeholders as plain text (barcodes, QR codes and tables are HTML: left out).
    Links come out relative ("/track/…") when PUBLIC_BASE_URL isn't set; a text needs a full link,
    so those get this server's own address."""
    from backend.routers.documents import _Html, _resolve
    res = await _resolve(db, org_id, o)
    doc = res[0] if isinstance(res, tuple) else res
    base = base_url.rstrip("/")
    out = {}
    for k, v in doc.items():
        if isinstance(v, _Html) or "<" in str(v):
            continue
        v = str(v)
        if base and v.startswith(("/track/", "/pay/", "/portal/", "/feedback/")):
            v = base + v
        out[k] = v
    return out


def _fill(body: str, vars: dict) -> str:
    out = body or ""
    for k, v in vars.items():
        out = out.replace(k, v)
    return out


def _consent(c: Optional[Client]) -> dict:
    return {
        "sms": bool(c) and c.consent_phone is not False,
        "whatsapp": None if not c else c.consent_whatsapp,     # None = not asked yet
    }


@router.get("/orders/{order_id}/compose")
async def compose_context(order_id: str, request: Request, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_send_sms"))):
    """Everything the message sheet needs in one trip: the customer's number in international form,
    their consent, and each SMS/WhatsApp template already filled in for this job."""
    o = await _order(db, current, order_id)
    org = await db.get(Organisation, current.org_id)
    c = o.client
    tpls = (await db.execute(select(MessageTemplate).where(
        MessageTemplate.org_id == current.org_id, MessageTemplate.channel.in_(["any", *CHANNELS]))
        .order_by(MessageTemplate.name))).scalars().all()
    vars_ = await _text_vars(db, current.org_id, o, str(request.base_url)) if tpls else {}
    return {
        "client": {"id": c.id, "name": c.name, "phone": c.phone} if c else None,
        "to_e164": to_e164(c.phone if c else None, getattr(org, "country", None)),
        "consent": _consent(c),
        "templates": [{"id": t.id, "name": t.name, "channel": t.channel, "body": _fill(t.body, vars_)} for t in tpls],
    }


class HandoffIn(BaseModel):
    order_id: str
    channel: str
    body: str
    template_id: Optional[str] = None


@router.post("/messages", status_code=201)
async def open_handoff(data: HandoffIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_send_sms"))):
    """Record that a text/WhatsApp was opened on the phone. Consent is checked here: no texts to a
    customer who said no, and WhatsApp only once they've said yes."""
    if data.channel not in CHANNELS:
        raise HTTPException(422, f"channel must be one of {CHANNELS}")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(422, "The message is empty")
    o = await _order(db, current, data.order_id)
    c = o.client
    if not c or not c.phone:
        raise HTTPException(422, "This customer has no phone number")
    consent = _consent(c)
    if data.channel == "sms" and not consent["sms"]:
        raise HTTPException(409, {"code": "consent_required", "channel": "sms",
                                  "message": "This customer asked not to be texted."})
    if data.channel == "whatsapp" and consent["whatsapp"] is not True:
        raise HTTPException(409, {"code": "consent_required", "channel": "whatsapp",
                                  "message": "Ask the customer if WhatsApp is OK first."})
    org = await db.get(Organisation, current.org_id)
    h = MessageHandoff(org_id=current.org_id, order_id=o.id, client_id=c.id, actor_id=current.id,
                       channel=data.channel, template_id=data.template_id, body=body[:2000],
                       to_number=to_e164(c.phone, getattr(org, "country", None)) or c.phone, state="opened")
    db.add(h)
    await db.commit()
    return {"id": h.id, "state": h.state}


class HandoffPatch(BaseModel):
    state: str          # sent | cancelled


@router.patch("/messages/{handoff_id}")
async def resolve_handoff(handoff_id: str, data: HandoffPatch, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_send_sms"))):
    """The person came back: they sent it, or they didn't. Only `sent` reaches the job history and
    the customer's portal thread, so the customer never sees a message that may not have gone."""
    if data.state not in ("sent", "cancelled"):
        raise HTTPException(422, "state must be sent or cancelled")
    h = (await db.execute(select(MessageHandoff).where(
        MessageHandoff.id == handoff_id, MessageHandoff.org_id == current.org_id))).scalar_one_or_none()
    if not h:
        raise HTTPException(404, "Message not found")
    if h.actor_id != current.id:
        raise HTTPException(403, "Only the person who opened this message can mark it")
    if h.state == data.state:
        return {"id": h.id, "state": h.state}            # a retry: nothing more to do
    if h.state != "opened":
        raise HTTPException(409, f"This message is already marked {h.state}")
    h.state, h.resolved_at = data.state, datetime.utcnow()
    if data.state == "sent" and h.order_id:
        how = "SMS" if h.channel == "sms" else "WhatsApp"
        # the portal thread (customer sees it as a message from the shop)
        db.add(OrderComment(order_id=h.order_id, author_id=current.id, body=h.body, visible_to_client=True))
        from backend.routers.feed import log_system_event
        await log_system_event(db, current.org_id, "order", h.order_id,
                               f"{current.name} sent a {how} from their phone to {h.to_number}")
    await db.commit()
    return {"id": h.id, "state": h.state}


class ConsentIn(BaseModel):
    granted: bool


@router.post("/clients/{client_id}/whatsapp-consent")
async def whatsapp_consent(client_id: str, data: ConsentIn, db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(require_perm("can_send_sms"))):
    """The customer said yes or no to WhatsApp (asked in person). Kept in the consent history."""
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Customer not found")
    c.consent_whatsapp = data.granted
    db.add(ConsentLog(org_id=current.org_id, client_id=c.id, channel="whatsapp", granted=data.granted,
                      source="mobile_verbal", actor_id=current.id))
    await db.commit()
    return {"ok": True, "consent": _consent(c)}


async def expire_open_handoffs(db) -> int:
    """Handoffs nobody answered within 24h become 'unknown' (scheduled hourly)."""
    rows = (await db.execute(select(MessageHandoff).where(
        MessageHandoff.state == "opened",
        MessageHandoff.created_at < datetime.utcnow() - timedelta(hours=24)))).scalars().all()
    for h in rows:
        h.state = "unknown"
    await db.commit()
    return len(rows)
