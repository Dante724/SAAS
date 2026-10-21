"""Public REST API v1 (Phase 2B).

Authenticated with a public API key (Authorization: Bearer rok_...). Org scope and
scopes (read/write) come from the key. Responses use a consistent envelope:
  list:   {"data": [...], "page": n, "page_size": m, "total": t}
  single: {"data": {...}}
Covered resources: contacts, orders, estimates, products, appointments, invoices,
time-entries. Writes are gated by the 'write' scope.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_dt
from backend.models import (
    Appointment,
    Client,
    Employee,
    Invoice,
    Order,
    Part,
    TimeEntry,
)
from backend.services.apikeys import ApiPrincipal, get_api_principal, require_scope
from backend.services.numbering import next_number
from backend.services.invoicing import balance_due as _inv_balance

router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])

READ = Depends(get_api_principal)
WRITE = Depends(require_scope("write"))


def _page(items, total, page, size):
    return {"data": items, "page": page, "page_size": size, "total": total}


# ── discovery ────────────────────────────────────────────────────────────────
@router.get("")
async def index(principal: ApiPrincipal = READ):
    base = "/api/v1"
    return {"data": {
        "version": "1.0",
        "org_id": principal.org_id,
        "scopes": sorted(principal.scopes),
        "resources": {
            "contacts": f"{base}/contacts", "orders": f"{base}/orders",
            "estimates": f"{base}/estimates", "products": f"{base}/products",
            "appointments": f"{base}/appointments", "invoices": f"{base}/invoices", "credit_notes": f"{base}/credit-notes",
            "time_entries": f"{base}/time-entries"},
        "docs": "/api/v1/docs",
    }}


# ── serializers ──────────────────────────────────────────────────────────────
def _contact(c: Client) -> dict:
    return {"id": c.id, "name": c.name, "type": c.client_type, "phone": c.phone,
            "email": c.email, "address": c.address, "loyalty_tier": c.loyalty_tier,
            "balance": c.balance, "created_at": c.created_at.isoformat() if c.created_at else None}


def _order(o: Order) -> dict:
    return {"id": o.id, "number": o.order_number, "status": o.status, "is_estimate": o.is_estimate,
            "client_id": o.client_id, "location_id": o.location_id, "assigned_to_id": o.assigned_to_id,
            "complaint": o.complaint, "labour_total": o.labour_total, "parts_total": o.parts_total,
            "total": o.total, "due_at": o.due_at.isoformat() if o.due_at else None,
            "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
            "created_at": o.created_at.isoformat() if o.created_at else None}


def _product(p: Part) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "brand": p.brand, "category": p.category,
            "sell_price": p.sell_price, "cost_price": p.cost_price, "uom": p.uom,
            "barcode": p.barcode, "active": p.active}


def _appointment(b: Appointment) -> dict:
    return {"id": b.id, "client_id": b.client_id, "client_name": b.client_name,
            "service_id": b.service_id, "employee_id": b.employee_id, "status": b.status,
            "scheduled_for": b.scheduled_for.isoformat() if b.scheduled_for else None,
            "duration_min": b.duration_min, "note": b.note}


def _invoice(i: Invoice) -> dict:
    return {"id": i.id, "number": i.invoice_number, "order_id": i.order_id, "client_id": i.client_id,
            "status": i.status, "subtotal": i.subtotal, "tax": i.tax, "total": i.total,
            "amount_paid": i.amount_paid, "credited": i.credited or 0.0, "balance_due": _inv_balance(i),
            "issued_at": i.issued_at.isoformat() if i.issued_at else None}


def _credit_note(c) -> dict:
    return {"id": c.id, "number": c.number, "status": c.status, "client_id": c.client_id,
            "invoice_id": c.invoice_id, "reason": c.reason, "subtotal": c.subtotal, "tax": c.tax_total,
            "total": c.total, "issued_at": c.issued_at.isoformat() if c.issued_at else None,
            "canceled_at": c.canceled_at.isoformat() if c.canceled_at else None,
            "lines": [{"description": l.description, "qty": l.qty, "unit_price": l.unit_price,
                       "tax_rate": l.tax_rate, "net": l.net, "tax": l.tax, "total": l.total} for l in c.lines]}


def _time_entry(e: TimeEntry) -> dict:
    return {"id": e.id, "employee_id": e.employee_id, "order_id": e.order_id, "category": e.category,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "ended_at": e.ended_at.isoformat() if e.ended_at else None,
            "duration_sec": e.duration_sec, "billable": e.billable, "state": e.state,
            "cost_rate": e.cost_rate, "billable_rate": e.billable_rate}


# ── helper: paged query ──────────────────────────────────────────────────────
async def _list(db, model, org_id, ser, page, size, extra=None, order_col=None):
    q = select(model).where(model.org_id == org_id)
    cq = select(func.count()).select_from(model).where(model.org_id == org_id)
    if extra is not None:
        q = q.where(extra); cq = cq.where(extra)
    total = (await db.execute(cq)).scalar() or 0
    oc = order_col if order_col is not None else model.created_at
    rows = (await db.execute(q.order_by(desc(oc)).limit(size).offset((page - 1) * size))).scalars().all()
    return _page([ser(r) for r in rows], total, page, size)


# ── contacts ─────────────────────────────────────────────────────────────────
@router.get("/contacts")
async def list_contacts(page: int = 1, page_size: int = Query(25, le=100),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Client, principal.org_id, _contact, page, page_size)


@router.get("/contacts/{cid}")
async def get_contact(cid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    c = (await db.execute(select(Client).where(Client.id == cid, Client.org_id == principal.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    return {"data": _contact(c)}


class ContactIn(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    client_type: str = "person"


@router.post("/contacts", status_code=201)
async def create_contact(data: ContactIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE):
    c = Client(org_id=principal.org_id, name=data.name, phone=data.phone, email=data.email,
               address=data.address, client_type=data.client_type)
    db.add(c); await db.commit(); await db.refresh(c)
    return {"data": _contact(c)}


# ── orders + estimates ───────────────────────────────────────────────────────
@router.get("/orders")
async def list_orders(status: Optional[str] = None, page: int = 1, page_size: int = Query(25, le=100),
                      db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    extra = (Order.is_estimate == False)  # noqa: E712
    if status:
        extra = extra & (Order.status == status)
    return await _list(db, Order, principal.org_id, _order, page, page_size, extra=extra)


@router.get("/orders/{oid}")
async def get_order(oid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    o = (await db.execute(select(Order).where(Order.id == oid, Order.org_id == principal.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return {"data": _order(o)}


class OrderIn(BaseModel):
    client_id: Optional[str] = None
    location_id: str
    complaint: Optional[str] = None
    assigned_to_id: Optional[str] = None
    duration_min: int = 60


@router.post("/orders", status_code=201)
async def create_order(data: OrderIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE):
    o = Order(org_id=principal.org_id, order_number=await next_number(db, principal.org_id, "order"), client_id=data.client_id,
              location_id=data.location_id, complaint=data.complaint, assigned_to_id=data.assigned_to_id,
              duration_min=data.duration_min, status="new")
    db.add(o); await db.commit(); await db.refresh(o)
    try:
        from backend.services.webhooks import emit_event
        await emit_event(db, principal.org_id, "order.created", {"id": o.id, "number": o.order_number})
    except Exception:
        pass
    return {"data": _order(o)}


@router.get("/estimates")
async def list_estimates(page: int = 1, page_size: int = Query(25, le=100),
                         db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Order, principal.org_id, _order, page, page_size,
                       extra=(Order.is_estimate == True))  # noqa: E712


@router.get("/estimates/{eid}")
async def get_estimate(eid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    o = (await db.execute(select(Order).where(
        Order.id == eid, Order.org_id == principal.org_id, Order.is_estimate == True))).scalar_one_or_none()  # noqa: E712
    if not o:
        raise HTTPException(404, "Estimate not found")
    return {"data": _order(o)}


# ── products / inventory ─────────────────────────────────────────────────────
@router.get("/products")
async def list_products(page: int = 1, page_size: int = Query(25, le=100),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Part, principal.org_id, _product, page, page_size)


@router.get("/products/{pid}")
async def get_product(pid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    p = (await db.execute(select(Part).where(Part.id == pid, Part.org_id == principal.org_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Product not found")
    return {"data": _product(p)}


# ── appointments ─────────────────────────────────────────────────────────────────
@router.get("/appointments")
async def list_appointments(page: int = 1, page_size: int = Query(25, le=100),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Appointment, principal.org_id, _appointment, page, page_size,
                       order_col=Appointment.scheduled_for)


class AppointmentIn(BaseModel):
    location_id: str
    scheduled_for: str
    client_name: Optional[str] = None
    client_id: Optional[str] = None
    service_id: Optional[str] = None
    employee_id: Optional[str] = None
    duration_min: int = 60
    note: Optional[str] = None


@router.post("/appointments", status_code=201)
async def create_appointment(data: AppointmentIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE):
    b = Appointment(org_id=principal.org_id, location_id=data.location_id,
                scheduled_for=parse_dt(data.scheduled_for, "scheduled_for"), client_name=data.client_name,
                client_id=data.client_id, service_id=data.service_id, employee_id=data.employee_id,
                duration_min=data.duration_min, note=data.note, status="confirmed")
    db.add(b); await db.commit(); await db.refresh(b)
    try:
        from backend.services.webhooks import emit_event
        await emit_event(db, principal.org_id, "appointment.created", {"id": b.id})
    except Exception:
        pass
    return {"data": _appointment(b)}


# ── invoices (read) ──────────────────────────────────────────────────────────
@router.get("/invoices")
async def list_invoices(page: int = 1, page_size: int = Query(25, le=100),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Invoice, principal.org_id, _invoice, page, page_size)


@router.get("/invoices/{iid}")
async def get_invoice(iid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    i = (await db.execute(select(Invoice).where(Invoice.id == iid, Invoice.org_id == principal.org_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Invoice not found")
    return {"data": _invoice(i)}


# ── credit notes (read) ──────────────────────────────────────────────────────
@router.get("/credit-notes")
async def list_credit_notes(page: int = 1, page_size: int = Query(25, le=100),
                            db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    from sqlalchemy.orm import selectinload

    from backend.models import CreditNote
    size, page = max(1, page_size), max(1, page)
    cond = CreditNote.org_id == principal.org_id
    total = (await db.execute(select(func.count()).select_from(CreditNote).where(cond))).scalar() or 0
    rows = (await db.execute(select(CreditNote).where(cond).options(selectinload(CreditNote.lines))
                             .order_by(desc(CreditNote.created_at)).limit(size).offset((page - 1) * size))).scalars().all()
    return _page([_credit_note(r) for r in rows], total, page, size)


@router.get("/credit-notes/{cn_id}")
async def get_credit_note(cn_id: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    from sqlalchemy.orm import selectinload

    from backend.models import CreditNote
    c = (await db.execute(select(CreditNote).where(CreditNote.id == cn_id, CreditNote.org_id == principal.org_id)
                          .options(selectinload(CreditNote.lines)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Credit note not found")
    return {"data": _credit_note(c)}


# ── time entries ─────────────────────────────────────────────────────────────
@router.get("/time-entries")
async def list_time_entries(order_id: Optional[str] = None, employee_id: Optional[str] = None,
                            page: int = 1, page_size: int = Query(25, le=100),
                            db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    extra = (TimeEntry.state != "void")
    if order_id:
        extra = extra & (TimeEntry.order_id == order_id)
    if employee_id:
        extra = extra & (TimeEntry.employee_id == employee_id)
    return await _list(db, TimeEntry, principal.org_id, _time_entry, page, page_size,
                       extra=extra, order_col=TimeEntry.started_at)


class TimeEntryIn(BaseModel):
    employee_id: str
    order_id: Optional[str] = None
    started_at: str
    duration_min: float
    billable: Optional[bool] = None
    note: Optional[str] = None


@router.post("/time-entries", status_code=201)
async def create_time_entry(data: TimeEntryIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE):
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == principal.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    start = parse_dt(data.started_at, "started_at")
    end = start + timedelta(minutes=data.duration_min)
    cost = emp.hourly_cost_rate or (emp.wage_value if emp.wage_type == "hourly" else 0.0) or 0.0
    e = TimeEntry(org_id=principal.org_id, employee_id=emp.id, order_id=data.order_id,
                  location_id=emp.location_id, category="job" if data.order_id else "other",
                  started_at=start, ended_at=end, duration_sec=int((end - start).total_seconds()),
                  billable=(data.billable if data.billable is not None else bool(data.order_id)),
                  cost_rate=float(cost), billable_rate=float(emp.billable_rate or cost),
                  note=data.note, source="manual", state="stopped")
    db.add(e); await db.commit(); await db.refresh(e)
    return {"data": _time_entry(e)}
