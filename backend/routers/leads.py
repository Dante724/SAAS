"""Inquiries / Leads pipeline (Orderry Lead Funnel).
A lead carries a response-time SLA. Winning a lead converts it into a real work
order, auto-creating the CRM client (matched by phone first) when needed.

WS-C: inquiry types (each with its own respond-by time), the device and serial, custom fields
(Settings → Forms, entity "lead"), the activity timeline (/api/feed/lead/{id}), a paged list for
Tekly Go, an idempotent create (Idempotency-Key) so Go can create offline and retry, and the
missed-call automation settings. The rules live in services/leads.py."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import AutomationSetting, Employee, Lead, LeadType
from backend.services import leads as svc
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/leads", tags=["leads"])

STATUSES = svc.STATUSES
LABELS = svc.LABELS


@router.get("/board")
async def leads_board(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Lead).where(Lead.org_id == current.org_id)
        .order_by(Lead.created_at.desc()).limit(2000))).scalars().all()
    cards = await svc.ser_many(db, rows)
    cols = [{"key": k, "label": LABELS[k], "cards": [c for c in cards if c["status"] == k]}
            for k in STATUSES]
    return {"columns": cols}


@router.get("")
async def list_leads(
    status: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=100),
    open_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Paged list, newest first (Tekly Go and the list view)."""
    cond = [Lead.org_id == current.org_id]
    if status:
        if status not in STATUSES:
            raise HTTPException(422, f"status must be one of {STATUSES}")
        cond.append(Lead.status == status)
    elif open_only:
        cond.append(Lead.status.in_(svc.OPEN))
    if q and q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(Lead.name.ilike(like), Lead.phone.ilike(like), Lead.email.ilike(like),
                        Lead.serial.ilike(like), Lead.inquiry.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(Lead).where(*cond))).scalar() or 0
    rows = (await db.execute(select(Lead).where(*cond).order_by(Lead.created_at.desc())
                             .limit(page_size).offset((page - 1) * page_size))).scalars().all()
    return {"items": await svc.ser_many(db, rows), "total": total, "page": page, "page_size": page_size}


# ── Inquiry types ────────────────────────────────────────────────────────────
def _ser_type(t: LeadType) -> dict:
    return {"id": t.id, "name": t.name, "sla_minutes": t.sla_minutes, "sort_order": t.sort_order,
            "active": t.active}


@router.get("/types")
async def list_types(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(LeadType).where(LeadType.org_id == current.org_id)
                             .order_by(LeadType.sort_order, LeadType.name))).scalars().all()
    return {"items": [_ser_type(t) for t in rows]}


class TypeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sla_minutes: int = Field(60, ge=1, le=60 * 24 * 30)
    sort_order: int = 0
    active: bool = True


class TypePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    sla_minutes: Optional[int] = Field(None, ge=1, le=60 * 24 * 30)
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@router.post("/types", status_code=201)
async def create_type(data: TypeIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    t = LeadType(org_id=current.org_id, name=data.name.strip(), sla_minutes=data.sla_minutes,
                 sort_order=data.sort_order, active=data.active)
    db.add(t)
    await db.commit()
    return _ser_type(t)


@router.patch("/types/{type_id}")
async def update_type(type_id: str, data: TypePatch, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    t = await svc.own(db, LeadType, type_id, current.org_id, "Inquiry type")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(t, k, v.strip() if isinstance(v, str) else v)
    await db.commit()
    return _ser_type(t)


@router.delete("/types/{type_id}")
async def delete_type(type_id: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    """Types in use are switched off (kept for history); unused ones are removed."""
    t = await svc.own(db, LeadType, type_id, current.org_id, "Inquiry type")
    used = (await db.execute(select(func.count()).select_from(Lead).where(
        Lead.org_id == current.org_id, Lead.lead_type_id == t.id))).scalar() or 0
    if used:
        t.active = False
    else:
        await db.delete(t)
    await db.commit()
    return {"ok": True, "archived": bool(used)}


# ── Missed call → inquiry settings ───────────────────────────────────────────
class MissedIn(BaseModel):
    enabled: bool
    merge_hours: int = Field(24, ge=0, le=24 * 14)
    sla_minutes: int = Field(30, ge=1, le=60 * 24 * 7)
    notify: bool = True
    fallback_employee_id: Optional[str] = None


@router.get("/missed-call-settings")
async def get_missed(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    return await svc.missed_settings(db, current.org_id)


@router.put("/missed-call-settings")
async def set_missed(data: MissedIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    if data.fallback_employee_id:
        await svc.own(db, Employee, data.fallback_employee_id, current.org_id, "Employee")
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == current.org_id,
        AutomationSetting.key == svc.MISSED_KEY))).scalar_one_or_none()
    if not s:
        s = AutomationSetting(org_id=current.org_id, key=svc.MISSED_KEY)
        db.add(s)
    s.enabled = data.enabled
    s.config = data.model_dump(exclude={"enabled"})
    await db.commit()
    return await svc.missed_settings(db, current.org_id)


# ── Inquiries ────────────────────────────────────────────────────────────────
class LeadIn(BaseModel):
    name: str = Field(..., max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    source: str = "phone"
    inquiry: Optional[str] = Field(None, max_length=4000)
    sla_minutes: int = Field(60, ge=1, le=60 * 24 * 30)  # used when no type is picked
    lead_type_id: Optional[str] = None
    manager_id: Optional[str] = None
    client_id: Optional[str] = None
    device_brand: Optional[str] = Field(None, max_length=100)
    device_model: Optional[str] = Field(None, max_length=100)
    serial: Optional[str] = Field(None, max_length=100)
    custom_data: Optional[dict] = None
    idempotency_key: Optional[str] = Field(None, max_length=64)


@router.post("", status_code=201)
async def create_lead(
    data: LeadIn,
    response: Response,
    idempotency_key: Optional[str] = Header(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    lead, created = await svc.create_lead(db, current.org_id, data.model_dump(), actor=current,
                                          idempotency_key=idempotency_key or data.idempotency_key)
    if not created:
        response.status_code = 200
    return await svc.ser_one(db, lead)


async def _get(db, current: Employee, lead_id: str) -> Lead:
    return await svc.own(db, Lead, lead_id, current.org_id, "Inquiry")


@router.get("/{lead_id}")
async def get_lead(lead_id: str, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(get_current_employee)):
    return await svc.ser_one(db, await _get(db, current, lead_id))


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    source: Optional[str] = None
    inquiry: Optional[str] = Field(None, max_length=4000)
    manager_id: Optional[str] = None
    lead_type_id: Optional[str] = None
    device_brand: Optional[str] = Field(None, max_length=100)
    device_model: Optional[str] = Field(None, max_length=100)
    serial: Optional[str] = Field(None, max_length=100)
    custom_data: Optional[dict] = None


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: str,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    lead = await _get(db, current, lead_id)
    lead = await svc.update_lead(db, current.org_id, lead, data.model_dump(exclude_unset=True), actor=current)
    return await svc.ser_one(db, lead)


@router.post("/{lead_id}/convert")
async def convert_lead(
    lead_id: str,
    is_estimate: bool = False,   # win as an estimate instead of a work order
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Win the lead: create (or link) the CRM client, attach the device, spawn a work order (or estimate)."""
    from backend.services.auth import assert_perm
    await assert_perm(db, current, "can_create_estimates" if is_estimate else "can_create_tickets")
    lead = await _get(db, current, lead_id)
    return await svc.convert_lead(db, current.org_id, lead, actor=current, is_estimate=is_estimate)
