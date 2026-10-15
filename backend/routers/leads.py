"""Inquiries / Leads pipeline (Orderry Lead Funnel).
A lead carries a response-time SLA. Winning a lead converts it into a real work
order, auto-creating the CRM client (matched by phone first) when needed."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Client, Employee, Lead, Order, OrderStatus, OrderStatusLog
from backend.services.auth import get_current_employee
from backend.services.numbering import next_number

router = APIRouter(prefix="/api/leads", tags=["leads"])

STATUSES = ["new", "in_progress", "won", "lost"]
LABELS = {"new": "New", "in_progress": "In progress", "won": "Won", "lost": "Lost"}


def _ser(l: Lead, order_number: Optional[str] = None) -> dict:
    overdue = bool(l.sla_due_at and l.status in ("new", "in_progress") and l.sla_due_at < datetime.utcnow())
    return {
        "id": l.id, "name": l.name, "phone": l.phone, "email": l.email, "source": l.source,
        "inquiry": l.inquiry, "status": l.status, "client_id": l.client_id,
        "manager_id": l.manager_id, "converted_order_id": l.converted_order_id,
        "converted_order_number": order_number,
        "sla_due_at": l.sla_due_at.isoformat() if l.sla_due_at else None,
        "sla_overdue": overdue,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


@router.get("/board")
async def leads_board(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Lead).where(Lead.org_id == current.org_id)
        .order_by(Lead.created_at.desc()))).scalars().all()
    # resolve converted order numbers in one query
    oids = [l.converted_order_id for l in rows if l.converted_order_id]
    nums = {}
    if oids:
        nums = dict((o.id, o.order_number) for o in (await db.execute(
            select(Order).where(Order.id.in_(oids)))).scalars().all())
    cols = [{"key": k, "label": LABELS[k],
             "cards": [_ser(l, nums.get(l.converted_order_id)) for l in rows if l.status == k]}
            for k in STATUSES]
    return {"columns": cols}


class LeadIn(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    source: str = "phone"
    inquiry: Optional[str] = None
    sla_minutes: int = 60  # respond-by SLA (Orderry: response-time limit per inquiry type)


@router.post("", status_code=201)
async def create_lead(
    data: LeadIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # If the phone matches an existing client, auto-link (Orderry chat-linking rule).
    client_id = None
    if data.phone:
        c = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.phone == data.phone))).scalars().first()
        client_id = c.id if c else None
    lead = Lead(org_id=current.org_id, location_id=current.location_id, name=data.name,
                phone=data.phone, email=data.email, source=data.source, inquiry=data.inquiry,
                client_id=client_id, manager_id=current.id,
                sla_due_at=datetime.utcnow() + timedelta(minutes=max(data.sla_minutes, 1)))
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return _ser(lead)


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    source: Optional[str] = None
    inquiry: Optional[str] = None
    manager_id: Optional[str] = None


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: str,
    data: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    lead = (await db.execute(select(Lead).where(
        Lead.id == lead_id, Lead.org_id == current.org_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    payload = data.model_dump(exclude_unset=True)
    if "status" in payload and payload["status"] not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    for k, v in payload.items():
        setattr(lead, k, v)
    await db.commit()
    return _ser(lead)


@router.post("/{lead_id}/convert")
async def convert_lead(
    lead_id: str,
    is_estimate: bool = False,   # win as an estimate instead of a work order (additive; default keeps old behavior)
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Win the lead: create (or link) the CRM client and spawn a work order (or estimate)."""
    lead = (await db.execute(select(Lead).where(
        Lead.id == lead_id, Lead.org_id == current.org_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    if lead.converted_order_id:
        raise HTTPException(409, "Lead is already converted")

    # 1) Resolve the client: linked > phone match > create new.
    client = None
    if lead.client_id:
        client = (await db.execute(select(Client).where(Client.id == lead.client_id))).scalar_one_or_none()
    if not client and lead.phone:
        client = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.phone == lead.phone))).scalars().first()
    if not client:
        client = Client(org_id=current.org_id, name=lead.name, phone=lead.phone,
                        email=lead.email, client_type="person", manager_id=lead.manager_id)
        db.add(client)
        await db.flush()
    lead.client_id = client.id

    # 2) Spawn the work order (or estimate) at its default status.
    init = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id,
        OrderStatus.record_type == ("estimate" if is_estimate else "work_order"),
        OrderStatus.is_default == True, OrderStatus.active == True))).scalars().first()  # noqa: E712
    order = Order(org_id=current.org_id, location_id=lead.location_id or current.location_id,
                  client_id=client.id, order_number=await next_number(db, current.org_id, "order"),
                  status=(init.key if init else "new"), is_estimate=is_estimate,
                  complaint=lead.inquiry or f"Inquiry from {lead.name}",
                  assigned_to_id=lead.manager_id or current.id)
    db.add(order)
    await db.flush()
    db.add(OrderStatusLog(order_id=order.id, from_status=None,
                          to_status=order.status, changed_by_id=current.id,
                          note=f"Created from inquiry ({lead.source})"))
    lead.status = "won"
    lead.converted_order_id = order.id
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Created from inquiry: {lead.name} ({lead.source})")
    await db.commit()
    return {"ok": True, "order_id": order.id, "order_number": order.order_number,
            "client_id": client.id}
