"""B2 — audit trail read API + GDPR data export & erasure."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
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
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


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
    devices = (await db.execute(select(Device).where(Device.client_id == client_id))).scalars().all()
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.client_id == client_id))).scalars().all()

    await audit_record(db, org_id=current.org_id, actor_id=current.id,
                       action="gdpr.export", entity_type="client", entity_id=client_id)
    await db.commit()
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "client": {"id": cl.id, "name": cl.name,
                   "phone": getattr(cl, "phone", None), "email": getattr(cl, "email", None),
                   "address": getattr(cl, "address", None), "client_type": cl.client_type},
        "assets": [{"id": d.id, "type": d.asset_type, "brand": d.brand, "model": d.model,
                    "serial": d.serial, "vin": d.vin, "hin": d.hin, "imei": d.imei} for d in devices],
        "orders": [{"id": o.id, "order_number": o.order_number, "status": o.status,
                    "total": o.total, "created_at": o.created_at.isoformat() if o.created_at else None}
                   for o in orders],
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
    for f in ("phone", "email", "address", "notes"):
        if hasattr(cl, f):
            setattr(cl, f, None)
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
        "exported_at": datetime.utcnow().isoformat(),
        "organisation": {"id": current.org_id, "name": getattr(org, "name", None),
                         "country": getattr(org, "country", None)},
        "counts": {"clients": len(clients), "assets": len(devices), "orders": len(orders)},
        "clients": [{"id": c.id, "name": c.name, "email": getattr(c, "email", None),
                     "phone": getattr(c, "phone", None), "consent_email": getattr(c, "consent_email", None),
                     "consent_phone": getattr(c, "consent_phone", None)} for c in clients],
        "orders": [{"id": o.id, "order_number": o.order_number, "client_id": o.client_id,
                    "status": o.status, "total": o.total} for o in orders],
    }
