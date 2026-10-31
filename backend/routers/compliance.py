"""B2 — audit trail read API + GDPR data export & erasure."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    AuditLog,
    Client,
    ConsentLog,
    Device,
    Employee,
    Order,
    Organisation,
)
from backend.services.audit import record as audit_record
from backend.services.auth import require_perm, record_scope
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/compliance", tags=["compliance"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


# ── Audit log ────────────────────────────────────────────────────────────────
@router.get("/audit")
async def list_audit(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    q = select(AuditLog).where(AuditLog.org_id == current.org_id)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if action:
        q = q.where(AuditLog.action == action)
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    rows = (await db.execute(q.order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all()
    # resolve actor names
    names = {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id))).scalars().all()}
    return {"items": [{
        "id": r.id, "action": r.action, "entity_type": r.entity_type,
        "entity_id": r.entity_id, "actor_id": r.actor_id,
        "actor": names.get(r.actor_id, "—") if r.actor_id else "system",
        "diff": r.diff, "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


# ── GDPR: subject data export ────────────────────────────────────────────────
@router.get("/gdpr/client/{client_id}/export")
async def gdpr_export(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Portable copy of everything held about a data subject (the client): their
    profile, assets, and order history — GDPR Art. 15/20."""
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Client not found")
    from backend.models import (Appointment, Call, ChatMessage, ChatThread, Feedback, InboxMessage,
                                InboxThread, Invoice, Lead, LoyaltyLedger, NotificationLog, Payment)
    devices = (await db.execute(select(Device).where(Device.client_id == client_id))).scalars().all()
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.client_id == client_id))).scalars().all()

    async def rows(model, *conds, order=None):
        q = select(model).where(*conds)
        return (await db.execute(q.order_by(order) if order is not None else q)).scalars().all()

    def iso(v):
        return v.isoformat() if v else None

    invoices = await rows(Invoice, Invoice.org_id == current.org_id, Invoice.client_id == client_id)
    payments = await rows(Payment, Payment.client_id == client_id,
                          Payment.invoice_id.in_([i.id for i in invoices] or ["_"]) |
                          Payment.order_id.in_([o.id for o in orders] or ["_"]))
    messages = await rows(NotificationLog, NotificationLog.org_id == current.org_id,
                          NotificationLog.client_id == client_id, order=NotificationLog.created_at)
    consents = await rows(ConsentLog, ConsentLog.org_id == current.org_id,
                          ConsentLog.client_id == client_id, order=ConsentLog.created_at)
    appts = await rows(Appointment, Appointment.org_id == current.org_id, Appointment.client_id == client_id)
    feedback = await rows(Feedback, Feedback.org_id == current.org_id, Feedback.client_id == client_id)
    loyalty = await rows(LoyaltyLedger, LoyaltyLedger.org_id == current.org_id, LoyaltyLedger.client_id == client_id)
    chat_ids = [t.id for t in await rows(ChatThread, ChatThread.org_id == current.org_id,
                                          ChatThread.client_id == client_id)]
    inbox_ids = [t.id for t in await rows(InboxThread, InboxThread.org_id == current.org_id,
                                           InboxThread.client_id == client_id)]
    calls = await rows(Call, Call.org_id == current.org_id, Call.client_id == client_id)
    leads = await rows(Lead, Lead.org_id == current.org_id, Lead.client_id == client_id)
    chats = await rows(ChatMessage, ChatMessage.thread_id.in_(chat_ids or ["_"]), order=ChatMessage.created_at)
    inbox = await rows(InboxMessage, InboxMessage.thread_id.in_(inbox_ids or ["_"]), order=InboxMessage.created_at)

    await audit_record(db, org_id=current.org_id, actor_id=current.id,
                       action="gdpr.export", entity_type="client", entity_id=client_id)
    await db.commit()
    return {
        "exported_at": now_utc().isoformat(),
        "client": {"id": cl.id, "name": cl.name,
                   "phone": getattr(cl, "phone", None), "email": getattr(cl, "email", None),
                   "address": getattr(cl, "address", None), "client_type": cl.client_type},
        "assets": [{"id": d.id, "type": d.asset_type, "brand": d.brand, "model": d.model,
                    "serial": d.serial, "vin": d.vin, "hin": d.hin, "imei": d.imei} for d in devices],
        "orders": [{"id": o.id, "order_number": o.order_number, "status": o.status,
                    "total": o.total, "created_at": o.created_at.isoformat() if o.created_at else None}
                   for o in orders],
        # Art. 15 covers everything held about the subject, not just the profile.
        "profile_extra": {"billing_address": cl.billing_address, "birthday": iso(cl.birthday),
                          "tax_id": cl.tax_id, "discount_card": cl.discount_card,
                          "custom_data": cl.custom_data or {}, "consent_email": cl.consent_email,
                          "consent_phone": cl.consent_phone},
        "invoices": [{"number": i.invoice_number, "status": i.status, "total": i.total,
                      "amount_paid": i.amount_paid, "issued_at": iso(i.issued_at)} for i in invoices],
        "payments": [{"amount": p.amount, "method": p.method, "kind": p.kind, "paid_at": iso(p.paid_at)}
                     for p in payments],
        "messages_sent": [{"channel": m.channel, "recipient": m.recipient, "subject": m.subject,
                           "body": m.body, "status": m.status, "created_at": iso(m.created_at)} for m in messages],
        "conversations": [{"direction": m.direction, "body": m.body, "created_at": iso(m.created_at)}
                          for m in [*chats, *inbox]],
        "consent_history": [{"channel": c.channel, "granted": c.granted, "source": c.source,
                             "created_at": iso(c.created_at)} for c in consents],
        "appointments": [{"scheduled_for": iso(a.scheduled_for), "status": a.status, "note": a.note}
                         for a in appts],
        "feedback": [{"rating": f.rating, "comment": f.comment, "submitted_at": iso(f.submitted_at)}
                     for f in feedback],
        "loyalty": [{"points": x.points, "note": x.note, "created_at": iso(x.created_at)} for x in loyalty],
        "calls": [{"direction": c.direction, "from": c.from_number, "to": c.to_number, "status": c.status,
                   "duration_s": c.duration_s, "transcript": c.transcript, "summary": c.summary,
                   "recording_url": c.recording_url, "created_at": iso(getattr(c, "created_at", None))}
                  for c in calls],
        "inquiries": [{"name": x.name, "phone": x.phone, "email": x.email, "source": x.source,
                       "inquiry": x.inquiry, "status": x.status, "created_at": iso(x.created_at)} for x in leads],
    }


# ── GDPR: right to erasure (anonymize, preserve financial integrity) ─────────
@router.post("/gdpr/client/{client_id}/erase")
async def gdpr_erase(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """GDPR Art. 17 erasure. Financial/audit records must be retained, so we
    *anonymize* the subject's PII rather than hard-deleting rows: name becomes a
    tombstone, contact fields cleared, asset identifiers scrubbed. Orders keep
    their totals for accounting."""
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Client not found")

    cl.name = f"[erased {client_id[:8]}]"
    for f in ("phone", "email", "address", "notes", "billing_address", "birthday", "tax_id",
              "reg_no", "discount_card", "watch_out"):
        if hasattr(cl, f):
            setattr(cl, f, None)
    cl.custom_data = {}
    from backend.services import portal_links
    await portal_links.revoke_all(db, cl.id)   # old portal links must stop showing their history
    await _erase_related(db, current.org_id, client_id)
    scrubbed = 0
    for d in (await db.execute(select(Device).where(Device.client_id == client_id))).scalars().all():
        for f in ("serial", "vin", "hin", "imei", "passcode_enc", "color"):
            if hasattr(d, f) and getattr(d, f):
                setattr(d, f, None)
                scrubbed += 1
    # Build 144cs: an erasure implicitly withdraws all consent — log it.
    for ch in ("email", "phone", "marketing"):
        db.add(ConsentLog(org_id=current.org_id, client_id=client_id, channel=ch,
                          granted=False, source="erasure", actor_id=current.id))
    if hasattr(cl, "consent_email"):
        cl.consent_email = False
    if hasattr(cl, "consent_phone"):
        cl.consent_phone = False
    await audit_record(db, org_id=current.org_id, actor_id=current.id,
                       action="gdpr.erase", entity_type="client", entity_id=client_id,
                       diff={"pii_cleared": True, "asset_fields_scrubbed": scrubbed})
    await db.commit()
    return {"ok": True, "client_id": client_id, "asset_fields_scrubbed": scrubbed,
            "note": "PII anonymized; financial records retained for accounting."}


async def _erase_related(db, org_id: str, client_id: str) -> None:
    """Personal data about the subject outside the client row: message history, call
    recordings/transcripts, chat and inbox conversations, booking contact details, survey
    comments and the order signature image. Amounts, dates and statuses stay for accounting."""
    from backend.models import (Appointment, Call, ChatMessage, ChatThread, Feedback, InboxMessage,
                                InboxThread, Lead, NotificationLog)
    gone = "[erased]"
    await db.execute(update(NotificationLog).where(
        NotificationLog.org_id == org_id, NotificationLog.client_id == client_id)
        .values(recipient=None, subject=None, body=gone))
    # texts / WhatsApps staff sent from their own phones (Tekly Go)
    from backend.models import MessageHandoff
    await db.execute(update(MessageHandoff).where(
        MessageHandoff.org_id == org_id, MessageHandoff.client_id == client_id)
        .values(to_number=None, body=gone))
    await db.execute(update(Call).where(Call.org_id == org_id, Call.client_id == client_id)
                     .values(from_number=None, to_number=None, recording_url=None,
                             transcript=None, summary=None))
    chat_ids = select(ChatThread.id).where(ChatThread.org_id == org_id, ChatThread.client_id == client_id)
    await db.execute(update(ChatMessage).where(ChatMessage.thread_id.in_(chat_ids)).values(body=gone))
    await db.execute(update(ChatThread).where(ChatThread.org_id == org_id, ChatThread.client_id == client_id)
                     .values(contact_phone=None, contact_name=None))
    inbox_ids = select(InboxThread.id).where(InboxThread.org_id == org_id, InboxThread.client_id == client_id)
    await db.execute(update(InboxMessage).where(InboxMessage.thread_id.in_(inbox_ids))
                     .values(body=gone, media_url=None))
    await db.execute(update(InboxThread).where(InboxThread.org_id == org_id, InboxThread.client_id == client_id)
                     .values(handle=None, subject=None))
    await db.execute(update(Appointment).where(Appointment.org_id == org_id, Appointment.client_id == client_id)
                     .values(client_name=None, client_phone=None, note=None))
    await db.execute(update(Lead).where(Lead.org_id == org_id, Lead.client_id == client_id)
                     .values(name=gone, phone=None, email=None, inquiry=None))
    await db.execute(update(Feedback).where(Feedback.org_id == org_id, Feedback.client_id == client_id)
                     .values(comment=None))
    await db.execute(update(Order).where(Order.org_id == org_id, Order.client_id == client_id)
                     .values(signature=None))
    await _erase_media(db, org_id, client_id)


async def _erase_media(db, org_id: str, client_id: str) -> None:
    """Photos, signatures and files about the subject (P9): intake photos and markups, the
    e-signature images and signer details, photos/files on their jobs' and their own feed,
    recommendation photos and asset pictures. The stored bytes are destroyed, not just
    unlinked. Signature hashes, totals and dates stay as the record that something was signed.
    Documents (PDFs) are generated on request and never stored, so there is nothing to erase."""
    from backend.models import DocumentSignature, EventFeedItem, MediaBlob, Recommendation
    from backend.services import media as media_svc
    orders = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.client_id == client_id))).scalars().all()
    order_ids = [o.id for o in orders]
    blob_ids: set[str] = set()
    for o in orders:
        blob_ids.update(i for i in (o.intake_photos or []) if i)
        blob_ids.update(v for v in (o.intake_markups or {}).values() if v)
        if o.signature_blob_id:
            blob_ids.add(o.signature_blob_id)
        o.intake_photos, o.intake_markups, o.signature_blob_id = [], {}, None
    feed = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.org_id == org_id,
        EventFeedItem.item_type.in_(("photo", "signature", "file")),
        ((EventFeedItem.entity_type == "order") & EventFeedItem.entity_id.in_(order_ids or ["_"]))
        | ((EventFeedItem.entity_type == "client") & (EventFeedItem.entity_id == client_id))))).scalars().all()
    for f in feed:
        if f.media_blob_id:
            blob_ids.add(f.media_blob_id)
        f.media_data, f.media_blob_id, f.media_name = None, None, None
        f.body = "[erased]"
    if order_ids:
        await db.execute(update(DocumentSignature).where(
            DocumentSignature.org_id == org_id, DocumentSignature.order_id.in_(order_ids))
            .values(signature_png=None, signer_name=None, signer_ip=None, user_agent=None))
        await db.execute(update(Recommendation).where(
            Recommendation.org_id == org_id, Recommendation.order_id.in_(order_ids))
            .values(media_data=None))
    await db.execute(update(Device).where(Device.client_id == client_id).values(image_data=None))
    await db.flush()
    if blob_ids:
        blobs = (await db.execute(select(MediaBlob).where(
            MediaBlob.org_id == org_id, MediaBlob.id.in_(blob_ids)))).scalars().all()
        await media_svc.erase(db, blobs)


# ── GDPR: consent record (proof-of-consent, Art. 7) ──────────────────────────
class ConsentIn(BaseModel):
    channel: str          # email|phone|marketing
    granted: bool
    source: str = "staff"


@router.post("/gdpr/client/{client_id}/consent")
async def set_consent(
    client_id: str, data: ConsentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.channel not in ("email", "phone", "marketing"):
        raise HTTPException(400, "channel must be email|phone|marketing")
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Client not found")
    # keep the live flag and the permanent log in sync
    if data.channel == "email" and hasattr(cl, "consent_email"):
        cl.consent_email = data.granted
    elif data.channel == "phone" and hasattr(cl, "consent_phone"):
        cl.consent_phone = data.granted
    db.add(ConsentLog(org_id=current.org_id, client_id=client_id, channel=data.channel,
                      granted=data.granted, source=data.source, actor_id=current.id))
    await db.commit()
    return {"ok": True, "channel": data.channel, "granted": data.granted}


@router.get("/gdpr/client/{client_id}/consent")
async def consent_history(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    rows = (await db.execute(select(ConsentLog).where(
        ConsentLog.org_id == current.org_id, ConsentLog.client_id == client_id).order_by(
        ConsentLog.created_at.desc()))).scalars().all()
    return {"items": [{"channel": r.channel, "granted": r.granted, "source": r.source,
                       "actor_id": r.actor_id,
                       "at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


# ── GDPR: full-tenant data export (controller portability) ───────────────────
@router.get("/gdpr/export-org")
async def export_org(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """A portable JSON bundle of the tenant's own records — clients, assets and
    orders. Owner/settings-manager only. Audited."""
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    clients = (await db.execute(select(Client).where(Client.org_id == current.org_id))).scalars().all()
    devices = (await db.execute(select(Device).where(Device.org_id == current.org_id))).scalars().all()
    orders = (await db.execute(select(Order).where(Order.org_id == current.org_id))).scalars().all()
    await audit_record(db, org_id=current.org_id, actor_id=current.id,
                       action="gdpr.export_org", entity_type="organisation", entity_id=current.org_id)
    await db.commit()
    return {
        "exported_at": now_utc().isoformat(),
        "organisation": {"id": current.org_id, "name": getattr(org, "name", None),
                         "country": getattr(org, "country", None)},
        "counts": {"clients": len(clients), "assets": len(devices), "orders": len(orders)},
        "clients": [{"id": c.id, "name": c.name, "email": getattr(c, "email", None),
                     "phone": getattr(c, "phone", None), "consent_email": getattr(c, "consent_email", None),
                     "consent_phone": getattr(c, "consent_phone", None)} for c in clients],
        "orders": [{"id": o.id, "order_number": o.order_number, "client_id": o.client_id,
                    "status": o.status, "total": o.total} for o in orders],
    }


# ── Data retention ───────────────────────────────────────────────────────────
class RetentionIn(BaseModel):
    days: dict[str, Optional[int]] = {}     # {kind: days}; null or missing = keep for good


def _retention_out(org) -> dict:
    from backend.services import retention
    kept = retention.clean(org.retention_days)
    return {"kinds": [{"key": k, "label": label, "days": kept.get(k)} for k, label in retention.KINDS.items()],
            "min_days": retention.MIN_DAYS, "max_days": retention.MAX_DAYS}


@router.get("/retention")
async def get_retention(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """How long this shop keeps old records of each kind (none set = keep for good)."""
    org = await db.get(Organisation, current.org_id)
    return _retention_out(org)


@router.put("/retention")
async def set_retention(
    data: RetentionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.services import retention
    org = await db.get(Organisation, current.org_id)
    before = retention.clean(org.retention_days)
    org.retention_days = retention.clean(data.days) or None
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="settings.retention",
                       entity_type="organisation", entity_id=org.id,
                       diff={"before": before, "after": org.retention_days or {}})
    await db.commit()
    return _retention_out(org)
