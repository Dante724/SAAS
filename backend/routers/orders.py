import logging
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core import paging as _paging
from backend.db.database import get_db
from backend.services.dates import parse_dt, parse_wall_dt
from backend.models import (
    Client,
    DocumentSignature,
    Employee,
    Order,
    OrderComment,
    OrderLine,
    OrderStatus,
    OrderStatusLog,
    Organisation,
    StockLevel,
    StockMovement,
)
from backend.services import industry as _ind
from backend.services.auth import assert_perm, get_current_employee, has_perm, require_perm, record_scope
from backend.services.numbering import next_number

router = APIRouter(prefix="/api/orders", tags=["orders"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


async def unique_order_code(db: AsyncSession) -> str:
    """Build 144ei — a scannable code that is unique across EVERY work order in the
    whole system, not just within one org (Orderry semantics). A 12-digit numeric
    (EAN-style) generated and checked against orders.barcode globally; retried on the
    rare collision. A DB-wide unique index on orders.barcode is the hard guarantee;
    this just avoids handing the caller a value that's already taken."""
    for _ in range(25):
        # 12 digits, first digit 2 so it never starts with 0 and stays EAN-13-friendly
        code = "2" + "".join(secrets.choice("0123456789") for _ in range(11))
        exists = (await db.execute(select(Order.id).where(Order.barcode == code))).first()
        if not exists:
            return code
    # astronomically unlikely fallback: widen to 16 digits
    return "2" + "".join(secrets.choice("0123456789") for _ in range(15))


async def unique_public_token(db: AsyncSession) -> str:
    """144cu — a track-link token unique across EVERY order in the system, so a customer's
    public link can never collide with another order's. ~128 bits from secrets.token_urlsafe;
    checked against orders.public_token and retried on the (practically impossible) collision.
    A DB-wide unique index on orders.public_token is the hard guarantee."""
    for _ in range(25):
        tok = secrets.token_urlsafe(16)  # ~22 url-safe chars, fits the 40-char column
        exists = (await db.execute(select(Order.id).where(Order.public_token == tok))).first()
        if not exists:
            return tok
    return secrets.token_urlsafe(24)  # widen on the astronomically unlikely streak


# ── Schemas ──────────────────────────────────

# Line kind as the catalog/UI names it -> billing line_type. A custom line is billed as labour.
_LINE_TYPE_FROM_KIND = {"service": "labour", "custom": "labour", "product": "part"}


class LineIn(BaseModel):
    # labour|part|fee. Optional because the web "New work order" form only sends `kind`
    # (launch QA B1) — derived from kind in _fill_line_type below.
    line_type: Optional[str] = None
    order_item_id: Optional[str] = None             # several items per job: which item (null = whole job)
    description: str
    qty: float = 1.0
    unit_price: float = 0.0
    cost_price: float = 0.0
    kind: Optional[str] = None  # service|product|custom
    technician_id: Optional[str] = None
    work_share: float = 1.0
    booked_hours: float = 0.0                       # 144av #2 — flat-rate hrs billed
    co_technicians: Optional[list] = None           # 144av #3 — [{employee_id, share}]
    discount: float = 0.0
    discount_type: str = "pct"  # pct|fixed
    discount_paid_by: str = "client"  # client|company (company-paid keeps commissions whole)
    tax_id: Optional[str] = None
    tax_rate: float = 0.0
    tax_mode: str = "imposed"  # included|imposed
    warranty_days: int = 0
    ref_id: Optional[str] = None
    save_to_catalog: bool = False          # build 126: persist a custom line as a catalog item
    catalog_kind: Optional[str] = None     # service|product (when save_to_catalog)
    bundle_group_id: Optional[str] = None  # add this line INSIDE a kit already on the job

    # Security phase 3: negative quantities/prices/discounts and >100% discounts used to
    # create phantom stock and zero/negative totals that slipped past every guard.
    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_price", "cost_price", "discount")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("Prices, costs and discounts can't be negative")
        return v

    @field_validator("discount_type")
    @classmethod
    def _known_discount_type(cls, v):
        if v is not None and v not in ("pct", "fixed"):
            raise ValueError("discount_type must be 'pct' or 'fixed'")
        return v

    @model_validator(mode="after")
    def _pct_at_most_100(self):
        if (self.discount_type or "pct") == "pct" and (self.discount or 0) > 100:
            raise ValueError("A percentage discount can't exceed 100%")
        return self

    @model_validator(mode="after")
    def _fill_line_type(self):
        if not self.line_type:
            self.line_type = _LINE_TYPE_FROM_KIND.get((self.kind or "").lower())
            if not self.line_type:
                raise ValueError("line_type (labour|part|fee) or kind (service|product|custom) is required")
        return self

class OrderIn(BaseModel):
    client_id: Optional[str] = None
    device_id: Optional[str] = None
    location_id: Optional[str] = None   # Build 144df: default to the org's first location if omitted
    complaint: Optional[str] = None
    priority: int = 0
    assigned_to_id: Optional[str] = None
    manager_id: Optional[str] = None   # 144cp: WO manager/owner (Orderry parity)
    scheduled_for: Optional[str] = None
    scheduled_to: Optional[str] = None
    duration_min: int = 60
    resource_id: Optional[str] = None
    type_id: Optional[str] = None
    custom_data: Optional[dict] = None
    asset_serial: Optional[str] = None
    asset_imei: Optional[str] = None
    asset_vin: Optional[str] = None
    asset_hin: Optional[str] = None
    asset_brand: Optional[str] = None
    asset_image: Optional[str] = None   # 144dc: photo for the inline-created asset (data URL)
    asset_model: Optional[str] = None
    asset_type: Optional[str] = None
    asset_color: Optional[str] = None
    asset_condition: Optional[str] = None
    template_key: Optional[str] = None   # industry template; defaults to the org's active one
    asset_group_id: Optional[str] = None
    asset_custom_data: Optional[dict] = None
    intake_photos: Optional[List[str]] = None   # 144ak: media blob ids documenting condition at check-in
    is_estimate: bool = False
    currency_code: Optional[str] = None   # Build 122: per-document currency override
    lines: List[LineIn] = []

class RescheduleIn(BaseModel):
    scheduled_for: Optional[str] = None
    scheduled_to: Optional[str] = None
    duration_min: Optional[int] = None
    assigned_to_id: Optional[str] = None
    resource_id: Optional[str] = None

class StatusChange(BaseModel):
    status: str
    note: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=300)   # cancellation reason when moving into a dropped status

class CommentIn(BaseModel):
    body: str
    visible_to_client: bool = False

# ── Helpers ──────────────────────────────────

def _order_number(org_id: str, seq: int, is_estimate: bool = False) -> str:
    return f"{'E' if is_estimate else 'WO-'}{seq:05d}"

async def _apply_client_discount(db, org_id: str, client_id, li):
    """Orderry contact discounts: before pricing a line, apply the client's
    discount matrix — services vs materials, each a fixed percent OR a price
    type (personal price level). Manual discounts always win (never stomped)."""
    if not client_id or (getattr(li, "discount", 0) or 0) > 0:
        return
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        return
    if li.kind == "product":
        mode, value, ptid = client.disc_materials_mode, client.disc_materials_value, client.disc_materials_price_type_id
    elif li.kind == "service" or li.line_type == "labour":
        mode, value, ptid = client.disc_services_mode, client.disc_services_value, client.disc_services_price_type_id
    else:
        return
    if mode == "percent" and (value or 0) > 0:
        li.discount = value
        li.discount_type = "pct"
    elif mode == "price_type" and ptid:
        from backend.routers.pricing import resolve_price_for
        price = await resolve_price_for(db, org_id,
                                        kind=("product" if li.kind == "product" else "service"),
                                        ref_id=li.ref_id, price_type_id=ptid, qty=(li.qty or 1))
        if price is not None:
            li.unit_price = price


def _calc_line(line: "LineIn") -> float:
    """Orderry calc chain: base price -> discount -> tax.
    Discount can be percent or fixed. Tax can be imposed (added on top) or
    included (already in the price, so total is unchanged — extracted for reporting)."""
    base = line.qty * line.unit_price
    dtype = getattr(line, "discount_type", "pct")
    if dtype == "fixed":
        after_discount = max(base - line.discount, 0)
    else:
        after_discount = base * (1 - line.discount / 100)
    mode = getattr(line, "tax_mode", "imposed")
    if mode == "included":
        # price already contains tax; customer total does not change
        return q2(after_discount)
    return q2(after_discount * (1 + line.tax_rate / 100))


async def _auto_apply_tax(db, org_id: str, data: "LineIn") -> None:
    """When a line has no explicit tax, apply the org's default tax stack for the
    Tickets&Sales family (honouring product/category exceptions). Stamps a single
    effective rate/mode onto the line so existing per-line math keeps working."""
    if data.tax_id or (data.tax_rate or 0):
        return
    try:
        from backend.services.tax import resolve_taxes
    except Exception:
        return
    item_type = "product" if data.kind == "product" else ("service" if (data.kind == "service" or data.line_type == "labour") else None)
    res = await resolve_taxes(db, org_id, doc_family="tickets_sales", net_amount=100.0,
                              item_type=item_type, item_id=data.ref_id)
    if res.get("exempt"):
        data.tax_rate, data.tax_mode = 0.0, "imposed"
        return
    taxes = res.get("taxes") or []
    if taxes:
        # single-rate stamp: sum rates of the resolved stack, mode from the first
        data.tax_rate = q4(sum(t["rate"] or 0 for t in taxes))
        data.tax_mode = taxes[0]["mode"]
        data.tax_id = taxes[0]["tax_id"]


def _line_tax_amount(line: "LineIn") -> float:
    """The tax portion of a line, for the invoice totals block."""
    base = line.qty * line.unit_price
    dtype = getattr(line, "discount_type", "pct")
    after_discount = max(base - line.discount, 0) if dtype == "fixed" else base * (1 - line.discount / 100)
    rate = line.tax_rate / 100
    if getattr(line, "tax_mode", "imposed") == "included":
        # extract: tax = net - net/(1+rate)
        return q2(after_discount - after_discount / (1 + rate)) if rate else 0.0
    return q2(after_discount * rate)

def _order_totals(lines: list[OrderLine]):
    labour = sum(l.total for l in lines if l.line_type == "labour")
    parts = sum(l.total for l in lines if l.line_type == "part")
    fees = sum(l.total for l in lines if l.line_type == "fee")
    return labour, parts, fees, labour + parts + fees

def _intake_photos_payload(o: Order) -> list:
    """144dg — intake photos with their marked-up copy (if any). The original id/url is always the
    check-in shot; marked_id/url is the annotated derivative. Original is never replaced."""
    mk = getattr(o, "intake_markups", None) or {}
    out = []
    for b in (getattr(o, "intake_photos", None) or []):
        row = {"id": b, "url": f"/api/media/{b}"}
        m = mk.get(b)
        if m:
            row["marked_id"] = m
            row["marked_url"] = f"/api/media/{m}"
        out.append(row)
    return out


def _serialize_order_scalars(o: Order) -> dict:
    """Every field of a work order except its lines, comments and status history."""
    return {
        "id": o.id,
        "order_number": o.order_number,
        "status": o.status,
        "cancel_reason": getattr(o, "cancel_reason", None),
        "written_off": float(getattr(o, "written_off", 0) or 0),
        "continued_from_order_id": getattr(o, "continued_from_order_id", None),
        "priority": o.priority,
        "complaint": o.complaint,
        "diagnosis": o.diagnosis,
        "tech_notes": getattr(o, "tech_notes", None),
        "conclusion": getattr(o, "conclusion", None),
        "labour_total": o.labour_total,
        "parts_total": o.parts_total,
        "total": o.total,
        "prepayment_total": getattr(o, "prepayment_total", 0.0) or 0.0,
        "deposit_amount": getattr(o, "deposit_amount", 0.0) or 0.0,
        "currency_code": o.currency_code,      # Build 122: display currency + snapshot rate
        "fx_rate": o.fx_rate,
        "warranty_until": o.warranty_until.isoformat() if o.warranty_until else None,
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
        "scheduled_to": o.scheduled_to.isoformat() if o.scheduled_to else None,
        "resource_id": o.resource_id,
        "barcode": o.barcode,
        "due_at": o.due_at.isoformat() if o.due_at else None,
        "overdue_by_date": bool(o.due_at and not o.closed_at and o.due_at < datetime.utcnow()),
        "status_entered_at": o.status_entered_at.isoformat() if o.status_entered_at else None,
        "is_estimate": o.is_estimate,
        "estimate_approved_at": o.estimate_approved_at.isoformat() if o.estimate_approved_at else None,
        "converted_order_id": o.converted_order_id,
        "converted_from_id": o.converted_from_id,
        "duration_min": o.duration_min,
        "type_id": o.type_id,
        "template_key": getattr(o, "template_key", None),
        "custom_data": o.custom_data or {},
        "intake_photos": _intake_photos_payload(o),
        "client_msg_unread": getattr(o, "client_msg_unread", 0) or 0,
        "rec_unread": getattr(o, "rec_unread", 0) or 0,
        "public_token": o.public_token,
        "created_at": o.created_at.isoformat(),
        "closed_at": o.closed_at.isoformat() if o.closed_at else None,
        "client": {"id": o.client.id, "name": o.client.name, "phone": o.client.phone,
                   "balance": float(getattr(o.client, "balance", 0) or 0),
                   "watch_out": getattr(o.client, "watch_out", None)} if o.client else None,
        "assigned_to": {"id": o.assigned_to.id, "name": o.assigned_to.name} if o.assigned_to else None,
        "assigned_to_id": o.assigned_to_id,
        "manager": {"id": o.manager.id, "name": o.manager.name} if getattr(o, "manager", None) else None,
        "manager_id": getattr(o, "manager_id", None),
        "device": None,
    }


def _serialize_order(o: Order) -> dict:
    row = _serialize_order_scalars(o)
    row.update({
        "device": {"id": o.device.id, "brand": o.device.brand, "model": o.device.model, "serial": o.device.serial, "imei": o.device.imei, "vin": o.device.vin, "hin": o.device.hin, "asset_type": o.device.asset_type, "asset_label": _ind.asset_label(getattr(o.device, "template_key", None)), "image_data": getattr(o.device, "image_data", None), "watch_out": getattr(o.device, "watch_out", None), "custom_data": o.device.custom_data or {}} if o.device else None,
        "lines": [{"id": l.id, "line_type": l.line_type, "kind": l.kind, "description": l.description,
                   "qty": l.qty, "unit_price": l.unit_price, "cost_price": l.cost_price,
                   "discount": l.discount, "discount_type": l.discount_type, "discount_paid_by": l.discount_paid_by,
                   "tax_rate": l.tax_rate, "tax_mode": l.tax_mode, "technician_id": l.technician_id,
                   "work_share": getattr(l, "work_share", 1.0), "booked_hours": getattr(l, "booked_hours", 0) or 0,
                   "co_technicians": getattr(l, "co_technicians", None) or [],
                   "pending_stock": l.pending_stock or 0, "ref_id": l.ref_id, "total": l.total,
                   "order_item_id": getattr(l, "order_item_id", None),
                   "bundle_group_id": l.bundle_group_id, "bundle_name": l.bundle_name,
                   "bundle_print_mode": l.bundle_print_mode, "bundle_price_locked": l.bundle_price_locked,
                   "bundle_qty": l.bundle_qty} for l in o.lines],
        "comments": [{"id": c.id, "body": c.body, "visible_to_client": c.visible_to_client,
                      "created_at": c.created_at.isoformat()} for c in o.comments],
        "status_logs": [{"from": s.from_status, "to": s.to_status, "at": s.changed_at.isoformat(),
                         "note": s.note} for s in o.status_logs],
    })
    return row

# ── Routes ───────────────────────────────────

# Work-order list paging. Old clients asked for limit=1000 and paginated in the browser; a
# request above the page ceiling is clamped (not 422'd) so a cached bundle keeps working, and
# `total` tells it there is more. The validation bound still rejects absurd values.
ORDER_PAGE_MAX = 200
BOARD_COLUMN_MAX = 200


def _apply_order_filters(q, org_id: str, *, status=None, location_id=None, search=None,
                         type_id=None, assigned_to_id=None, priority=None, priority_min=None,
                         client_id=None, brand=None, model=None, is_estimate=None,
                         created_from=None, created_to=None, include_archived=False,
                         scheduled=None):
    """The work-order list filters, shared by the list, its facet counts and the board, so a
    total always counts exactly the rows the filters select. Device/client filters use
    subqueries (not joins) so rows never duplicate and the count stays a plain count."""
    from sqlalchemy import or_

    from backend.models import Device
    if is_estimate is not None:
        q = q.where(Order.is_estimate == is_estimate)
    if not include_archived:
        q = q.where(Order.archived == False)  # noqa: E712
    if status:
        statuses = [s for s in status.split(",") if s]
        if len(statuses) == 1:
            q = q.where(Order.status == statuses[0])
        elif statuses:
            q = q.where(Order.status.in_(statuses))
    if location_id:
        q = q.where(Order.location_id == location_id)
    if type_id:
        q = q.where(Order.type_id == type_id)
    if assigned_to_id == "__none":
        q = q.where(Order.assigned_to_id.is_(None))
    elif assigned_to_id:
        q = q.where(Order.assigned_to_id == assigned_to_id)
    if priority is not None:
        q = q.where(Order.priority == priority)
    if priority_min is not None:
        q = q.where(Order.priority >= priority_min)
    if client_id:
        q = q.where(Order.client_id == client_id)
    if scheduled == "yes":
        q = q.where(Order.scheduled_for.is_not(None))
    elif scheduled == "no":
        q = q.where(Order.scheduled_for.is_(None))
    if brand or model:
        dq = select(Device.id).where(Device.org_id == org_id)
        if brand:
            dq = dq.where(Device.brand.icontains(brand))
        if model:
            dq = dq.where(Device.model.icontains(model))
        q = q.where(Order.device_id.in_(dq))
    if created_from:
        q = q.where(Order.created_at >= parse_dt(created_from, "created_from"))
    if created_to:
        q = q.where(Order.created_at <= parse_dt(created_to, "created_to"))
    s = (search or "").strip()
    if s:
        # order number, client name/phone, or the device (brand, model, serial, IMEI)
        cq = select(Client.id).where(Client.org_id == org_id, or_(
            Client.name.icontains(s), Client.phone.contains(s)))
        dq = select(Device.id).where(Device.org_id == org_id, or_(
            Device.brand.icontains(s), Device.model.icontains(s),
            Device.serial.icontains(s), Device.imei.contains(s)))
        q = q.where(or_(Order.order_number.icontains(s),
                        Order.client_id.in_(cq), Order.device_id.in_(dq)))
    return q


def _device_image_url(device_id: Optional[str], has_image: bool) -> Optional[str]:
    return f"/api/assets/{device_id}/image" if (device_id and has_image) else None


def _serialize_order_row(o: Order, has_image: bool) -> dict:
    """List-row shape: every scalar field of the detail serializer, but no lines, comments or
    status history, and the asset photo as a URL instead of inline base64. The drawer loads
    GET /orders/{id} for the full record."""
    row = _serialize_order_scalars(o)
    if o.device:
        d = o.device
        row["device"] = {"id": d.id, "brand": d.brand, "model": d.model, "serial": d.serial,
                         "imei": d.imei, "vin": d.vin, "hin": d.hin, "asset_type": d.asset_type,
                         "asset_label": _ind.asset_label(getattr(d, "template_key", None)),
                         "image_url": _device_image_url(d.id, has_image),
                         "watch_out": getattr(d, "watch_out", None),
                         "custom_data": d.custom_data or {}}
    return row


async def _devices_with_images(db, device_ids) -> set:
    """Which of these devices carry a photo — one query, without reading the photo bytes."""
    from backend.models import Device
    ids = [i for i in set(device_ids) if i]
    if not ids:
        return set()
    return set((await db.execute(select(Device.id).where(
        Device.id.in_(ids), Device.image_data.is_not(None), Device.image_data != ""))).scalars().all())


def _order_list_options():
    from sqlalchemy.orm import defer

    from backend.models import Device
    return (selectinload(Order.client), selectinload(Order.device).options(defer(Device.image_data)),
            selectinload(Order.assigned_to), selectinload(Order.manager))


@router.get("")
async def list_orders(
    status: Optional[str] = None,
    location_id: Optional[str] = None,
    search: Optional[str] = None,
    q: Optional[str] = None,                  # alias of search (global search box sends q=)
    type_id: Optional[str] = None,
    assigned_to_id: Optional[str] = None,     # "__none" = unassigned
    priority: Optional[int] = None,
    priority_min: Optional[int] = None,       # 1 = urgent or VIP
    scheduled: Optional[str] = Query(None, pattern="^(yes|no)$"),
    client_id: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    is_estimate: Optional[bool] = None,   # Build 144di: None = unified list (both)
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    include_archived: bool = False,
    facets: bool = False,                     # also return status counts + assignees for the toolbar
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    limit = min(limit, ORDER_PAGE_MAX)
    from backend.services import scoping
    role = await scoping.role_for(db, current)
    flt = dict(location_id=location_id, search=search or q, type_id=type_id,
               assigned_to_id=assigned_to_id, priority=priority, priority_min=priority_min,
               client_id=client_id, brand=brand, model=model, is_estimate=is_estimate,
               created_from=created_from, created_to=created_to,
               include_archived=include_archived, scheduled=scheduled)

    def scoped(base):
        return scoping.scope_orders(base.where(Order.org_id == current.org_id), role, current)

    base = _apply_order_filters(scoped(select(Order)), current.org_id, status=status, **flt)
    orders = (await db.execute(base.options(*_order_list_options())
                               .order_by(Order.created_at.desc(), Order.id.desc())
                               .offset(skip).limit(limit))).scalars().all()
    # total = rows matching the same filters + record scope (it used to count the whole org)
    total = (await db.execute(_apply_order_filters(
        scoped(select(func.count(Order.id))), current.org_id, status=status, **flt))).scalar() or 0
    # Build 144ds — invoice status per order for the list column (batched, no N+1).
    from backend.models import Invoice as _Inv
    oids = [o.id for o in orders]
    inv_status: dict = {}
    if oids:
        for iid, ist, ipaid, itot in (await db.execute(select(
                _Inv.order_id, _Inv.status, _Inv.amount_paid, _Inv.total).where(
                _Inv.order_id.in_(oids)))).all():
            # collapse to the "most settled" status seen for the order
            cur = inv_status.get(iid)
            rank = {"paid": 3, "issued": 2, "draft": 1}
            s = "paid" if (itot and ipaid >= itot) else (ist or "draft")
            if cur is None or rank.get(s, 0) > rank.get(cur, 0):
                inv_status[iid] = s
    # 144de — checklists_pending per order for the amber list-row dot (batched, no N+1).
    pending_cl: set = set()
    if oids:
        from backend.models import OrderChecklistItem as _OCI
        for (oid,) in (await db.execute(select(_OCI.order_id).where(
                _OCI.order_id.in_(oids), _OCI.required == True, _OCI.done == False))).all():  # noqa: E712
            pending_cl.add(oid)
    with_img = await _devices_with_images(db, [o.device_id for o in orders])
    items = []
    for o in orders:
        row = _serialize_order_row(o, o.device_id in with_img)
        row["invoice_status"] = inv_status.get(o.id)  # None = not invoiced
        row["checklists_pending"] = o.id in pending_cl
        items.append(row)
    out = {"total": total, "items": items, "skip": skip, "limit": limit}
    if facets:
        out.update(await _order_facets(db, current, role, status, flt))
    return out


async def _order_facets(db, current, role, status, flt) -> dict:
    """Toolbar numbers for the paged list: per-status counts under every filter except status,
    the assignees present, and open work orders — all aggregates, no rows loaded."""
    from backend.services import scoping

    def scoped(base):
        return scoping.scope_orders(base.where(Order.org_id == current.org_id), role, current)

    counts = dict((await db.execute(_apply_order_filters(
        scoped(select(Order.status, func.count(Order.id))), current.org_id, **flt)
        .group_by(Order.status))).all())
    people = (await db.execute(
        scoped(select(Employee.id, Employee.name).select_from(Order)
               .join(Employee, Order.assigned_to_id == Employee.id))
        .where(Order.archived == False).distinct())).all()  # noqa: E712
    closed_keys = select(OrderStatus.key).where(
        OrderStatus.org_id == current.org_id, OrderStatus.record_type == "work_order",
        OrderStatus.status_group.in_(("closed", "dropped")))
    open_jobs = (await db.execute(scoped(select(func.count(Order.id))).where(
        Order.archived == False, Order.is_estimate == False,  # noqa: E712
        Order.status.not_in(closed_keys)))).scalar() or 0
    return {"status_counts": counts,
            "assignees": sorted(({"id": i, "name": n} for i, n in people), key=lambda a: (a["name"] or "").lower()),
            "open_jobs": open_jobs}


class BulkActionIn(BaseModel):
    order_ids: List[str]
    action: str            # set_status | assign | set_priority | create_invoice | group_invoice | archive | restore
    value: Optional[str] = None
    reason: Optional[str] = None   # 144-G: archive reason; also the cancellation reason for set_status
    refund: bool = True            # 144-G: refund payments when bulk-archiving


@router.post("/bulk")
async def bulk_action(
    data: BulkActionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if not data.order_ids:
        raise HTTPException(400, "No orders selected")
    # Bulk actions need the same permission as doing it to one order.
    _bulk_perm = {"assign": "can_assign_staff", "create_invoice": "can_create_invoices",
                  "group_invoice": "can_create_invoices"}.get(data.action)
    if _bulk_perm:
        await assert_perm(db, current, _bulk_perm)

    # 144-G: archive / restore are destructive — gate them (default-allow, like require_perm)
    # and run each through the single-order helper so refunds, commission reversal and audit
    # all happen exactly as they do for a one-off archive. One bad order is skipped, not fatal.
    if data.action in ("archive", "restore"):
        if current.role_id:
            from backend.models import Role
            _role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
            from backend.services.permissions import role_allows as _ra
            if _role is None or not _ra(_role, "can_delete_orders"):
                raise HTTPException(403, "Your role does not allow deleting orders")
        if data.action == "archive":
            await assert_perm(db, current, "can_delete_tickets")
        reason = (data.reason or "Bulk delete").strip()
        done, skipped = 0, []
        for oid in data.order_ids:
            try:
                if data.action == "archive":
                    await _archive_order(db, current, oid, reason, data.refund)
                else:
                    await _restore_order(db, current, oid)
                done += 1
            except HTTPException as e:
                skipped.append({"id": oid, "reason": e.detail})
        msg = f"{'Archived' if data.action == 'archive' else 'Restored'} {done} order{'s' if done != 1 else ''}"
        if skipped:
            msg += f", skipped {len(skipped)}"
        return {"ok": True, "updated": done, "skipped": skipped, "message": msg}

    # 144-P5 security: bulk set_status must NOT bypass the single-order gates. The old
    # path assigned ``o.status = data.value`` directly, skipping transition RBAC, the
    # completion + pending-stock-write-off gates, status_entered_at, audit, ledger,
    # commissions and notifications. Route each selected order through the SAME helper the
    # single-order endpoint uses. Best-effort per order: a blocked transition (or a
    # notification failure, already swallowed inside the helper) is recorded and skipped,
    # never fatal to the batch.
    if data.action == "set_status":
        if not data.value:
            raise HTTPException(400, "set_status requires a target status")
        # Reject arbitrary strings: the target must be a real, active status for THIS org
        # (or one of the built-in terminal keys the single path also accepts).
        from backend.models import OrderStatus as _OS
        _valid = set((await db.execute(select(_OS.key).where(
            _OS.org_id == current.org_id, _OS.active == True))).scalars().all())  # noqa: E712
        _valid |= {"new", "done", "closed", "delivered"}
        if data.value not in _valid:
            raise HTTPException(400, f"Unknown status '{data.value}'")
        done, skipped = 0, []
        for oid in data.order_ids:
            try:
                o = (await db.execute(
                    select(Order).where(Order.id == oid, Order.org_id == current.org_id)
                    .options(selectinload(Order.client), selectinload(Order.device),
                             selectinload(Order.assigned_to), selectinload(Order.manager),
                             selectinload(Order.lines))
                )).scalar_one_or_none()
                if not o:
                    skipped.append({"id": oid, "reason": "Order not found"})
                    continue
                await _apply_status_change(db, current, o, data.value, note="Bulk update",
                                           reason=getattr(data, "reason", None))
                done += 1
            except HTTPException as e:
                await db.rollback()
                skipped.append({"id": oid, "reason": e.detail})
        msg = f"Updated {done} order{'s' if done != 1 else ''}"
        if skipped:
            msg += f", skipped {len(skipped)}"
        return {"ok": True, "updated": done, "skipped": skipped, "message": msg}

    result = await db.execute(
        select(Order).where(Order.id.in_(data.order_ids), Order.org_id == current.org_id)
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
    )
    orders = result.scalars().all()
    # M3: a role limited to its own jobs can't reach colleagues' jobs through a bulk action
    # (each single-order route already enforces this via _guard_order_access).
    from backend.services import scoping as _scoping
    orders = [o for o in orders if await _scoping.can_view_order(db, current, o)]
    from backend.routers.feed import log_system_event
    n = 0
    for o in orders:
        if data.action == "group_invoice":
            continue                       # handled in a batch after the loop
        if data.action == "assign":
            o.assigned_to_id = data.value or None
        elif data.action == "set_priority":
            try:
                o.priority = int(data.value or 0)
            except (TypeError, ValueError):
                raise HTTPException(400, "priority must be a number")
        elif data.action == "create_invoice":
            from backend.models import Invoice
            # skip if order already has an invoice
            existing = (await db.execute(
                select(Invoice.id).where(Invoice.order_id == o.id).limit(1)
            )).scalar_one_or_none()
            if existing:
                continue
            inv = Invoice(
                invoice_number=await next_number(db, current.org_id, "invoice"),
                org_id=current.org_id, order_id=o.id, client_id=o.client_id,
                status="draft", subtotal=o.total or 0, total=o.total or 0,
                issued_at=datetime.utcnow(),
            )
            db.add(inv)
            await log_system_event(db, current.org_id, "order", o.id,
                                   f"Invoice {inv.invoice_number} created (bulk)")
        else:
            raise HTTPException(400, "Unknown action")
        n += 1

    if data.action == "group_invoice":
        # One invoice per client, combining all their selected orders — the "one bill for
        # several jobs" case. Orders must share a client; we group by client_id.
        from collections import defaultdict

        from backend.models import Invoice
        by_client: dict = defaultdict(list)
        for o in orders:
            if o.client_id:
                by_client[o.client_id].append(o)
        made = 0
        for client_id, grp in by_client.items():
            total = sum((o.total or 0) for o in grp)
            inv = Invoice(
                invoice_number=await next_number(db, current.org_id, "invoice"),
                org_id=current.org_id, order_id=grp[0].id, client_id=client_id,
                status="draft", subtotal=total, total=total, issued_at=datetime.utcnow())
            db.add(inv)
            for o in grp:
                await log_system_event(db, current.org_id, "order", o.id,
                                       f"Added to group invoice {inv.invoice_number}")
            made += 1
        await db.commit()
        return {"ok": True, "updated": made,
                "message": f"Created {made} group invoice{'s' if made != 1 else ''}"}

    await db.commit()
    return {"ok": True, "updated": n}


async def _order_side_effect(db: AsyncSession, order: Order, label: str, fn, *reload) -> None:
    """Run one best-effort side effect of saving an order and commit it. On failure log it, roll
    back just that work and reload the order (and `reload` objects, e.g. the signed-in employee),
    so the next side effect starts from a clean session."""
    order_id = order.id
    try:
        await fn()
        await db.commit()
    except Exception:  # noqa: BLE001 — the order is already saved; a side effect must not undo it
        logging.getLogger("tekly").exception("order %s saved, but %s failed", order_id, label)
        await db.rollback()
        for obj in (order, *reload):
            await db.refresh(obj)


@router.post("", status_code=201)
async def create_order(
    data: OrderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_tickets")),
):
    if data.is_estimate:
        await assert_perm(db, current, "can_create_estimates")
    await _assert_listed(db, current.org_id, data.manager_id, "display_in_managers", "Managers")
    await _assert_listed(db, current.org_id, data.assigned_to_id, "display_in_assignees", "Assignee")
    # Build 144df: location is optional from the client — default to the org's
    # first active location, creating a "Main" one if the org has none yet, so a
    # fresh shop can raise its first work order without a setup dead-end.
    if not data.location_id:
        from backend.models import Location
        loc = (await db.execute(
            select(Location).where(Location.org_id == current.org_id, Location.active == True)  # noqa: E712
            .order_by(Location.created_at))).scalars().first()
        if not loc:
            loc = Location(org_id=current.org_id, name="Main", timezone="UTC")
            db.add(loc)
            await db.flush()
        data.location_id = loc.id

    # Item 2: role location-scope enforcement (no-op for unscoped roles/owners).
    from backend.services.auth import check_location_access
    await check_location_access(db, current, data.location_id)

    # Fields the work-order form requires at intake (Settings → Forms → Customize).
    from backend.routers.forms import missing_at_intake
    _missing = await missing_at_intake(db, current.org_id, data.type_id, data)
    if _missing:
        raise HTTPException(422, f"Fill in {', '.join(_missing)} first.")

    # Industry template this order (and any asset auto-created with it) is stamped
    # with. Defaults to the org's active template; historical orders keep theirs.
    from backend.models import Organisation as _OrgRow
    from backend.services import industry as _ind
    _org_row = (await db.execute(select(_OrgRow).where(_OrgRow.id == current.org_id))).scalar_one_or_none()
    order_template_key = _ind.normalize_template_key(
        data.template_key or (_org_row.industry_template if _org_row else None))

    lines = []
    labour_total = 0.0
    parts_total = 0.0
    stock_writeoffs = []  # (part_id, qty, line_obj) to apply after order flush
    from backend.routers.pricing import evaluate_line_margin
    _perms_create = await _order_perms(db, current)
    _hide_cost = bool(current.role_id) and not _perms_create.get("can_view_purchase_price")
    _fixed_cost = not _hide_cost and not await has_perm(db, current, "can_set_cost_price")
    for li in data.lines:
        await _assert_line_rights(db, current, li)
        if _hide_cost or _fixed_cost:
            li.cost_price = await _catalog_cost(db, current.org_id, li.kind, li.ref_id)
        await _apply_client_discount(db, current.org_id, data.client_id, li)
        await _guard_discount_approval(db, current.org_id, _perms_create, li.discount, li.discount_type, is_owner=not current.role_id,
                                          base=(li.qty or 0) * (li.unit_price or 0))
        # 144ha — margin-floor guardrail on create (block a below-floor line)
        _mg = await evaluate_line_margin(
            db, current.org_id, kind=li.kind, ref_id=li.ref_id, unit_price=li.unit_price or 0,
            qty=li.qty or 0, discount=li.discount or 0, discount_type=li.discount_type or "pct",
            cost_price=li.cost_price)
        if _mg["blocked"]:
            raise HTTPException(409, f"'{li.description}': margin {_mg['margin_pct']}% is below the "
                                     f"{_mg['floor']}% floor. Lower the discount or raise the price.")
        total = _calc_line(li)
        ol = OrderLine(line_type=li.line_type, description=li.description,
                       qty=li.qty, unit_price=li.unit_price, cost_price=li.cost_price,
                       kind=li.kind, discount=li.discount, discount_type=li.discount_type, discount_paid_by=li.discount_paid_by,
                       tax_id=li.tax_id, tax_rate=li.tax_rate, tax_mode=li.tax_mode,
                       technician_id=li.technician_id, work_share=li.work_share, warranty_days=li.warranty_days,
                       total=total, ref_id=li.ref_id)
        lines.append(ol)
        if li.line_type == "labour":
            labour_total += total
        elif li.line_type == "part":
            parts_total += total
            # Estimates never deduct stock (Orderry: estimate = order w/o stock impact)
            if li.kind == "product" and li.ref_id and not data.is_estimate:
                stock_writeoffs.append((li.ref_id, li.qty, ol))
        else:  # fee and any other line types count toward labour-side total
            labour_total += total

    # Resolve the work order type's initial status and default asset type
    initial_status = "draft" if data.is_estimate else "new"
    type_asset_type = None
    if data.is_estimate:
        # estimates use the estimate status set; default to its New-group status
        from backend.models import OrderStatus as _OS
        # A shop may have several New-group estimate statuses: take the default, then the first.
        edflt = (await db.execute(select(_OS).where(
            _OS.org_id == current.org_id, _OS.is_estimate == True, _OS.status_group == "new", _OS.active == True)  # noqa: E712
            .order_by(_OS.is_default.desc(), _OS.sort_order, _OS.id).limit(1))).scalars().first()
        if edflt:
            initial_status = edflt.key
    if data.type_id and not data.is_estimate:
        from backend.models import WorkOrderType
        wot = (await db.execute(select(WorkOrderType).where(WorkOrderType.id == data.type_id, WorkOrderType.org_id == current.org_id))).scalar_one_or_none()
        if wot:
            initial_status = wot.initial_status or "new"
            type_asset_type = wot.default_asset_type
    # fall back to the org's default status for the matching document type
    if initial_status == "new":
        from backend.models import OrderStatus as _OS
        dflt = (await db.execute(select(_OS).where(
            _OS.org_id == current.org_id, _OS.is_default == True, _OS.active == True,  # noqa: E712
            _OS.record_type == ("estimate" if data.is_estimate else "work_order")))).scalars().first()
        if dflt:
            initial_status = dflt.key
        elif data.is_estimate:
            initial_status = "est_draft"

    # Auto-create an asset (device) if asset details given and no device_id
    device_id = data.device_id
    if not device_id and (data.asset_serial or data.asset_imei or data.asset_vin or data.asset_hin or data.asset_brand or data.asset_model or data.asset_image or data.asset_color or data.asset_condition) and data.client_id:
        from backend.models import Device
        new_asset = Device(
            org_id=current.org_id,
            client_id=data.client_id, owner_type="client",
            asset_type=data.asset_type or type_asset_type or "Device",
            brand=data.asset_brand, model=data.asset_model,
            serial=data.asset_serial, imei=data.asset_imei,
            vin=data.asset_vin, hin=data.asset_hin,
            color=data.asset_color, condition=data.asset_condition,
            template_key=order_template_key, asset_group_id=data.asset_group_id,
            custom_data=data.asset_custom_data or {},
            image_data=data.asset_image,
        )
        from backend.services import asset_catalog as _cat
        from backend.services import identifiers as _ids
        _ids.tidy_device(new_asset)
        new_asset.brand, new_asset.model = await _cat.canonical(
            db, current.org_id, order_template_key, new_asset.brand, new_asset.model)
        db.add(new_asset)
        await db.flush()
        device_id = new_asset.id

    # Item 1: optionally forbid an asset being on more than one open ticket.
    if device_id and not data.is_estimate:
        from backend.models import Organisation as _Org
        _org = (await db.execute(select(_Org).where(_Org.id == current.org_id))).scalar_one_or_none()
        if _org and _org.asset_one_open_ticket:
            _open = (await db.execute(
                select(Order.id).join(
                    OrderStatus,
                    (OrderStatus.key == Order.status) & (OrderStatus.org_id == Order.org_id),
                    isouter=True)
                .where(Order.org_id == current.org_id, Order.device_id == device_id,
                       ((OrderStatus.is_terminal == False) | (OrderStatus.id.is_(None))))  # noqa: E712
            )).first()
            if _open:
                raise HTTPException(409, "This asset is already used in an open ticket")
    # order has no one assigned yet.
    assigned_to_id = data.assigned_to_id
    manager_id = data.manager_id
    if (not assigned_to_id or not manager_id) and data.client_id:
        from backend.models import Client as _Client
        cl = (await db.execute(select(_Client).where(_Client.id == data.client_id, _Client.org_id == current.org_id))).scalar_one_or_none()
        if cl and cl.manager_id:
            if not assigned_to_id:
                assigned_to_id = cl.manager_id
            if not manager_id:          # 144cp: default the WO manager to the client's account manager
                manager_id = cl.manager_id

    # Build 122: resolve the document's display currency (override → client pref → base)
    # and snapshot the rate so later rate changes never alter this document.
    from backend.services import currency as _cur
    _cinfo = await _cur.resolve_for_client(db, current.org_id, data.client_id, data.currency_code)
    _intake = await _clean_intake_photos(db, current.org_id, data.intake_photos)

    order = Order(
        order_number=("E" + (await next_number(db, current.org_id, "order"))[3:]) if data.is_estimate else await next_number(db, current.org_id, "order"),
        barcode=await unique_order_code(db),   # 144ei — globally-unique scannable code from birth
        public_token=await unique_public_token(db),   # 144cu — globally-unique track link from birth
        org_id=current.org_id,
        client_id=data.client_id,
        device_id=device_id,
        location_id=data.location_id,
        priority=data.priority,
        complaint=data.complaint,
        assigned_to_id=assigned_to_id,
        manager_id=manager_id,
        scheduled_for=(await parse_wall_dt(db, current.org_id, data.location_id, data.scheduled_for, "scheduled_for")
                       if data.scheduled_for else None),
        scheduled_to=(await parse_wall_dt(db, current.org_id, data.location_id, data.scheduled_to, "scheduled_to")
                      if data.scheduled_to else None),
        duration_min=data.duration_min or 60,
        resource_id=data.resource_id or None,
        type_id=data.type_id,
        template_key=order_template_key,
        custom_data=data.custom_data or {},
        intake_photos=_intake,
        status=initial_status,
        is_estimate=data.is_estimate,
        labour_total=labour_total,
        parts_total=parts_total,
        total=labour_total + parts_total,
        currency_code=_cinfo["code"],
        fx_rate=_cinfo["rate"],
        lines=lines,
    )
    db.add(order)
    await db.flush()
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.create",
                 entity_type="order", entity_id=order.id,
                 diff={"order_number": order.order_number, "location_id": data.location_id})   # assigns order.id before we reference it
    log = OrderStatusLog(order_id=order.id, from_status=None, to_status=initial_status, changed_by_id=current.id)
    db.add(log)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id, "Work order created")

    # write off stock for product lines; if insufficient, deduct what's available
    # Permission gate (Orderry): adding more than is in stock ("add without
    # deduction") requires the can_add_without_stock role permission.
    perms = {}
    if current.role_id:
        from backend.models import Role
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        from backend.services.permissions import effective_permissions as _eff
        perms = _eff(role) if role else {c: False for c in _all_codes()}
    can_add_without_stock = bool(perms.get("can_add_without_stock", True))  # default allow unless explicitly false

    # write off products from stock at order creation; if not enough on hand
    # and record the shortfall as pending_stock (Orderry's "add without deduction")
    from backend.services import reservations as _rsv
    _allow_override = _can_override_reservation(perms, current)
    for part_id, qty, line_obj in stock_writeoffs:
        sl = (await db.execute(
            select(StockLevel).where(StockLevel.part_id == part_id, StockLevel.location_id == data.location_id).with_for_update()
        )).scalar_one_or_none()
        # 144at — reservation-aware: a new WO can take only FREE stock (+ override for others' holds).
        g = await _rsv.guard_and_draw(
            db, org_id=current.org_id, part_id=part_id, location_id=data.location_id,
            order_id=order.id, qty=qty, actor_id=current.id, allow_override=_allow_override,
            taken_label=order.order_number)
        if g["blocked"] > 0:
            raise HTTPException(409, f"{g['blocked']:g} unit(s) of a part are reserved for another job. "
                                     f"A manager with override permission must approve using them.")
        deducted = g["take"]
        shortfall = g["pending"]
        if shortfall > 0 and not can_add_without_stock:
            raise HTTPException(
                403,
                "Not enough free stock and you don't have permission to add products "
                "without deduction. Reduce the quantity or receive more stock."
            )
        if sl:
            await _atomic_add(db, sl, "qty_available", -(deducted), round_to=None)   # atomic
        line_obj.pending_stock = shortfall
        if deducted > 0:
            db.add(StockMovement(
                part_id=part_id, location_id=data.location_id,
                movement_type="use", qty=-deducted, ref_type="order", ref_id=order.id,
            ))
            from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
            await _alerts.maybe_low_stock_by_id(db, current.org_id, part_id)
            await log_system_event(db, current.org_id, "order", order.id,
                                   f"Stock written off: {deducted:g} unit(s)")
        if shortfall > 0:
            await log_system_event(db, current.org_id, "order", order.id,
                                   f"Added without deduction: {shortfall:g} unit(s) pending stock")

    await db.commit()
    await db.refresh(order)

    # Best-effort side effects. Each one commits on its own; a failure is logged and rolled
    # back so the hooks after it still work (R1: they used to be `except: pass`, and one DB error
    # left the session broken for every later hook).
    async def upsells():
        # 144gp Phase B — intake custom fields can auto-draft upsells (shared rule surface).
        from backend.routers.forms import sync_order_field_upsells
        await sync_order_field_upsells(db, current.org_id, order, current)

    async def checklists():
        # E4 — a new order enters its first status: attach the checklists set to attach on it.
        from backend.routers.checklists import auto_attach_for_status
        from backend.routers.feed import log_system_event as _log_ev
        for _title in await auto_attach_for_status(db, order, order.status, author_id=current.id):
            await _log_ev(db, current.org_id, "order", order.id, f"Checklist attached: {_title}")

    async def webhook():
        from backend.services.webhooks import emit_event
        await emit_event(db, current.org_id, "order.created", {
            "id": order.id, "order_number": order.order_number,
            "status": order.status, "total": order.total})

    async def commissions():
        # Payroll: order_created commissions to the creator (idempotent).
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).commissions_for_order_create(order, current.id)

    async def notify_technician():
        # Notify the assigned technician (in-app + push) on a fresh assignment.
        from backend.services.push import notify
        await notify(db, org_id=current.org_id, employee_id=order.assigned_to_id,
                     kind="job.assigned", title=f"New job {order.order_number}",
                     body=(order.complaint or "")[:140], deep_link=f"/tech#/order/{order.id}")

    created_matched = 0

    async def notification_rules():
        # Phase 1: fire "order created" notifications (real work orders only).
        # 144jc: run the rules engine first (client + staff), fall back to the legacy
        # template dispatch only when no rule matched — same convergence as status change.
        nonlocal created_matched
        from backend.services import notification_rules as _nr
        _cres = await _nr.evaluate(db, org_id=current.org_id, order_id=order.id, event="created")
        created_matched = (_cres or {}).get("matched", 0)
        await _nr.notify_staff_order(db, org_id=current.org_id, order_id=order.id,
                                     event="created", actor_id=current.id)

    async def legacy_notifications():
        from backend.services.notifications import dispatch_order_notifications
        await dispatch_order_notifications(
            db, org_id=current.org_id, order_id=order.id, trigger_event="order_created")

    async def automations():
        # Build 144w: workflow automations for order-created.
        from backend.services import automation as _auto
        await _auto.run(db, current.org_id, "order_created", order=order)

    await _order_side_effect(db, order, "upsell drafting", upsells, current)
    await _order_side_effect(db, order, "checklist attach", checklists, current)
    if not order.is_estimate:
        await _order_side_effect(db, order, "order.created webhook", webhook, current)
    await _order_side_effect(db, order, "creation commissions", commissions, current)
    if order.assigned_to_id and order.assigned_to_id != current.id:
        await _order_side_effect(db, order, "technician notification", notify_technician, current)
    if not order.is_estimate:
        await _order_side_effect(db, order, "notification rules", notification_rules, current)
        if created_matched == 0:
            await _order_side_effect(db, order, "order notifications", legacy_notifications, current)
    await _order_side_effect(db, order, "automations", automations, current)

    result = await db.execute(
        select(Order)
        .where(Order.id == order.id)
        .options(selectinload(Order.client), selectinload(Order.device),
                 selectinload(Order.assigned_to), selectinload(Order.manager), selectinload(Order.lines),
                 selectinload(Order.comments), selectinload(Order.status_logs))
    )
    return _serialize_order(result.scalar_one())

@router.get("/board")
async def board(
    is_estimate: bool = False,
    location_id: Optional[str] = None,        # build 138: scope the board to a branch
    search: Optional[str] = None,
    assigned_to_id: Optional[str] = None,     # "__none" = unassigned
    priority_min: Optional[int] = None,
    scheduled: Optional[str] = Query(None, pattern="^(yes|no)$"),
    per_column: int = Query(50, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Kanban board: open orders grouped by status GROUP (Orderry board view).
    Columns follow status groups in canonical order; each card carries enough
    to render + drag. Drag → PATCH /status applies transfer rules + triggers.

    Load-bounded: only open (non-archived, not closed/dropped) orders become cards, at most
    `per_column` per column (newest first); every column and status still gets its full
    count from one aggregate, and `truncated` says a column has more than it shows. Cards
    carry the asset photo as a URL, never inline."""
    per_column = min(per_column, BOARD_COLUMN_MAX)
    # Make sure the org has its default statuses, so the board is never blank on
    # first visit (the statuses list endpoint does the same on-demand seeding).
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, current.org_id)
    statuses = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id,
        OrderStatus.record_type == ("estimate" if is_estimate else "work_order"))
        .order_by(OrderStatus.sort_order))).scalars().all()
    grp_of = {s.key: s.status_group for s in statuses}
    # representative status key per group (first by sort order) for drop targets
    group_order = ["new", "in_progress", "pending", "ready", "closed", "dropped"]
    group_label = {"new": "New", "in_progress": "In progress", "pending": "Pending",
                   "ready": "Ready", "closed": "Done", "dropped": "Dropped"}
    statuses_by_group = {}
    for s in statuses:
        statuses_by_group.setdefault(s.status_group, []).append(
            {"key": s.key, "name": s.name, "color": s.color})

    # record-level scoping: a role scoped to "own" only sees its own board cards
    # (same rule as the list — the board must never leak others' work).
    from backend.services import scoping
    _brole = await scoping.role_for(db, current)

    def _scoped(base):
        base = scoping.scope_orders(base.where(Order.org_id == current.org_id), _brole, current)
        return _apply_order_filters(base, current.org_id, is_estimate=is_estimate,
                                    location_id=location_id, search=search,
                                    assigned_to_id=assigned_to_id, priority_min=priority_min,
                                    scheduled=scheduled)

    status_counts = dict((await db.execute(_scoped(select(Order.status, func.count(Order.id)))
                                           .group_by(Order.status))).all())
    group_counts: dict = {}
    for st, n in status_counts.items():
        g = grp_of.get(st, "new")
        group_counts[g] = group_counts.get(g, 0) + n

    now = datetime.utcnow()
    known = list(grp_of)
    cards_by_group = {g: [] for g in group_order}
    for g in group_order:
        if g in ("closed", "dropped") or not group_counts.get(g):
            continue
        keys = [k for k, gg in grp_of.items() if gg == g]
        cond = Order.status.in_(keys) if keys else None
        if g == "new":   # an unknown status lands in New, as before
            from sqlalchemy import or_, true
            unknown = Order.status.not_in(known) if known else true()
            cond = or_(cond, unknown) if cond is not None else unknown
        if cond is None:
            continue
        rows = (await db.execute(_scoped(select(Order)).where(cond)
                                 .options(*_order_list_options())
                                 .order_by(Order.created_at.desc(), Order.id.desc())
                                 .limit(per_column))).scalars().all()
        with_img = await _devices_with_images(db, [o.device_id for o in rows])
        for o in rows:
            cards_by_group[g].append({
                "id": o.id, "order_number": o.order_number, "status": o.status,
                "priority": o.priority, "total": o.total,
                "client_name": o.client.name if o.client else None,
                "client_id": o.client_id,
                # A device may have a serial but no brand/model (checked in by serial only),
                # so guard against None — concatenating None here 500'd the whole board.
                "device": (f"{o.device.brand or ''} {o.device.model or ''}".strip() or None)
                          if o.device else None,
                "device_image_url": _device_image_url(o.device_id, o.device_id in with_img),
                "assigned_to_id": o.assigned_to_id,
                "assigned_to_name": o.assigned_to.name if o.assigned_to else None,
                "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
                # flag: scheduled but still in New group → needs attention (Orderry)
                "needs_attention": bool(o.scheduled_for and g == "new"),
                "overdue": bool(o.due_at and o.due_at < now),
                "client_msg_unread": getattr(o, "client_msg_unread", 0) or 0,
                "rec_unread": getattr(o, "rec_unread", 0) or 0,
            })

    columns = []
    for g in group_order:
        if g in statuses_by_group:  # only show groups that have statuses
            cards = cards_by_group.get(g, [])
            columns.append({
                "group": g, "label": group_label.get(g, g),
                "statuses": statuses_by_group[g],
                "cards": cards,
                "count": group_counts.get(g, 0),
                "truncated": group_counts.get(g, 0) > len(cards),
            })
    return {"columns": columns, "status_counts": status_counts, "per_column": per_column}


@router.get("/archived")
async def list_archived_orders(
    limit: int = _paging.limit_query(100),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Archived (soft-deleted) work orders for the owner, with who archived them,
    when, and why. These never appear in the normal work-order lists."""
    rows = (await db.execute(
        select(Order).where(Order.org_id == current.org_id, Order.archived == True)  # noqa: E712
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
        .order_by(Order.archived_at.desc()).limit(_paging.clamp(limit)))).scalars().all()
    emp_names = {}
    ids = [o.archived_by_id for o in rows if o.archived_by_id]
    if ids:
        for e in (await db.execute(select(Employee).where(Employee.id.in_(ids)))).scalars().all():
            emp_names[e.id] = e.name
    return {"total": len(rows), "items": [{
        "id": o.id, "order_number": o.order_number, "status": o.status,
        "client": o.client.name if o.client else None,
        "total": o.total,
        "archive_reason": o.archive_reason,
        "archived_at": o.archived_at.isoformat() if o.archived_at else None,
        "archived_by": emp_names.get(o.archived_by_id),
    } for o in rows]}


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.client), selectinload(Order.device),
                 selectinload(Order.assigned_to), selectinload(Order.manager), selectinload(Order.lines),
                 selectinload(Order.comments), selectinload(Order.status_logs))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    from backend.services import scoping
    if not await scoping.can_view_order(db, current, order):
        raise HTTPException(403, "You can only open work orders assigned to you")
    data = _serialize_order(order)
    if not await has_perm(db, current, "can_view_item_prices"):
        for ln in data["lines"]:
            for k in LINE_PRICE_KEYS:
                ln.pop(k, None)
    # 144dd — checklists_pending drives the amber Checklists-tab dot: True iff a REQUIRED checklist
    # item is still undone (same definition as the completion gate). No required-undone item → no dot.
    from backend.models import OrderChecklistItem as _OCI
    data["checklists_pending"] = bool((await db.execute(select(_OCI.id).where(
        _OCI.order_id == order.id, _OCI.required == True, _OCI.done == False))).first())  # noqa: E712
    # 144dd — payment_due drives the Invoices-tab "Due" badge: an ISSUED (non-draft/cancelled)
    # invoice on this order still has a balance. No issued-unpaid invoice → no badge.
    from backend.models import Invoice as _Inv
    from sqlalchemy import case as _case
    _owed = _Inv.total - _Inv.amount_paid - _Inv.credited     # per invoice, never below 0
    _due = (await db.execute(select(func.coalesce(func.sum(_case((_owed > 0, _owed), else_=0)), 0)).where(
        _Inv.order_id == order.id,
        _Inv.status.notin_(["draft", "cancelled", "canceled"])))).scalar() or 0
    data["payment_due"] = q2(float(_due)) > 0
    # 144bj: prior-visit count for this device (repeat-repair signal on the WO card).
    if order.device_id and data.get("device"):
        vc = (await db.execute(select(func.count()).select_from(Order).where(
            Order.device_id == order.device_id, Order.org_id == current.org_id,
            Order.id != order.id))).scalar() or 0
        data["device"]["visit_count"] = int(vc)
    # Build 144be: staff-reply toggle so the WO modal can show/hide the reply box.
    from backend.models import Organisation as _Org
    _org = (await db.execute(select(_Org).where(_Org.id == current.org_id))).scalar_one_or_none()
    data["allow_staff_reply"] = getattr(_org, "oe_allow_staff_reply", True) if _org else True
    # Build 122: attach the display-currency symbol so the UI can render converted amounts.
    if order.currency_code:
        from backend.services import currency as _cur
        _ci = await _cur.rate_for_code(db, current.org_id, order.currency_code)
        data["currency_symbol"] = _ci["symbol"]
        if not order.fx_rate:
            data["fx_rate"] = _ci["rate"]
    # SLA state for the current status (OrderStatus.time_limit_min vs status_entered_at)
    if order.status_entered_at and not order.closed_at:
        st = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == current.org_id, OrderStatus.key == order.status,
            OrderStatus.is_estimate == bool(order.is_estimate)))).scalars().first()
        mins = int((datetime.utcnow() - order.status_entered_at).total_seconds() // 60)
        data["minutes_in_status"] = mins
        data["status_time_limit_min"] = st.time_limit_min if st else None
        data["status_sla_overdue"] = bool(st and st.time_limit_min and mins > st.time_limit_min)
    # Resolve the linked converted order's number both ways for the UI banner.
    link_ids = [i for i in (order.converted_order_id, order.converted_from_id) if i]
    if link_ids:
        nums = dict((r.id, r.order_number) for r in (await db.execute(
            select(Order).where(Order.id.in_(link_ids)))).scalars().all())
        data["converted_order_number"] = nums.get(order.converted_order_id)
        data["converted_from_number"] = nums.get(order.converted_from_id)
    return data

@router.get("/timing/flags")
async def timing_flags(
    ids: Optional[str] = None,   # comma-separated order ids (the visible page); omitted = every open order
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Batch stall flags for open (non-terminal) orders — one shared dwell-stats
    pass, so the WO list can show a stalled badge without an N+1."""
    from backend.services.cycle_time import compute_dwell_stats, get_order_timing
    from backend.services import scoping  # 144ay fix: was referenced below but never imported here
    stats = await compute_dwell_stats(db, current.org_id, is_estimate=False)
    terminal_keys = {s.key for s in (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.is_terminal == True))).scalars().all()}  # noqa: E712
    _fq = select(Order).where(
        Order.org_id == current.org_id, Order.is_estimate == False,  # noqa: E712
        Order.status.notin_(terminal_keys or {"__none__"}))
    _fq = scoping.scope_orders(_fq, await scoping.role_for(db, current), current)  # flags only for orders I can see
    if ids:
        _fq = _fq.where(Order.id.in_([i for i in ids.split(",") if i][:ORDER_PAGE_MAX]))
    orders = (await db.execute(_fq)).scalars().all()
    flags = {}
    for o in orders:
        t = await get_order_timing(db, o, stats)
        if t["stalled"] or t["predicted_ready_at"]:
            flags[o.id] = {"stalled": t["stalled"], "predicted_ready_at": t["predicted_ready_at"],
                           "over_by_min": t["over_by_min"]}
    return {"items": flags}


@router.get("/{order_id}/timing")
async def order_timing(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Cycle-time card for one order: elapsed vs typical for its bucket, stall
    flag, and predicted ready-time (with explicit confidence)."""
    from backend.services.cycle_time import get_order_timing
    from backend.services import scoping  # 144ay fix: referenced below but never imported here
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    if not await scoping.can_view_order(db, current, o):
        raise HTTPException(403, "You can only open work orders assigned to you")
    return await get_order_timing(db, o)


@router.patch("/{order_id}/status")
async def change_status(
    order_id: str,
    data: StatusChange,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.client), selectinload(Order.device),
                 selectinload(Order.assigned_to), selectinload(Order.manager), selectinload(Order.lines))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    return await _apply_status_change(db, current, order, data.status, note=data.note, reason=data.reason)


async def _apply_status_change(db, current, order, new_status, note=None, reason=None):
    """Shared gate + side-effect flow for a single-step status change, used by the
    single-order PATCH endpoint and the bulk set_status path so both enforce the same
    transition RBAC, completion + pending-stock-write-off gates, status_entered_at,
    audit, ledger, commissions and (best-effort) notifications. The original endpoint
    body is preserved verbatim; it is simply reached via ``data`` rebuilt from the
    arguments so both callers share one code path."""
    data = StatusChange(status=new_status, note=note, reason=reason)
    await _guard_order_access(db, current, order)
    if data.status != order.status and await _is_closed(db, current.org_id, order):
        await assert_perm(db, current, "can_edit_closed_ticket")   # reopening counts as editing it

    # Resolve the target status from the configurable status table, scoped to this
    # record's pipeline. .first() (not scalar_one_or_none) so legacy duplicate keys
    # across record types can never crash the status change.
    from backend.models import Invoice, OrderStatus
    _rt = "estimate" if order.is_estimate else "work_order"
    target = (await db.execute(
        select(OrderStatus).where(OrderStatus.org_id == current.org_id,
                                  OrderStatus.key == data.status,
                                  OrderStatus.active == True,  # noqa: E712
                                  OrderStatus.record_type == _rt)
        .order_by(OrderStatus.sort_order)
    )).scalars().first()
    is_closing = target.is_terminal if target else (data.status in ("done", "closed", "delivered"))
    # Dropped jobs (the customer never collected, refused the repair…): a reason is required,
    # the finished-work checks below are skipped and what's still owed is written off.
    is_dropping = bool(target and (target.status_group or "") == "dropped" and not order.is_estimate)

    # Phase B.7: enforce status transfer rules (allowed from->to for this role)
    from backend.routers.statuses import is_transition_allowed
    if not await is_transition_allowed(db, current.org_id, current.role_id, order.status, data.status):
        raise HTTPException(403, f"You can't move this order from '{order.status}' to '{data.status}'.")
    if is_dropping and data.status != order.status and not ((data.reason or "").strip() or order.cancel_reason):
        raise HTTPException(422, "Choose a reason for dropping this job.")

    # 144gr — Smart Fields: a custom field value can block entering a status.
    if target and order.status != data.status:
        from backend.routers.forms import status_gate_block
        _block = await status_gate_block(db, current.org_id, order, data.status)
        if _block:
            raise HTTPException(409, _block)

    # 144gj — Orderry requirement gates on ENTERING the target status.
    if target and order.status != data.status:
        from backend.models import OrderChecklistItem, OrderComment
        if target.require_comment:
            n = (await db.execute(select(func.count()).select_from(OrderComment).where(
                OrderComment.order_id == order.id))).scalar() or 0
            if not n:
                raise HTTPException(409, f"'{target.name}' requires a comment on the order first.")
        if target.require_checklist:
            items = (await db.execute(select(OrderChecklistItem).where(
                OrderChecklistItem.order_id == order.id))).scalars().all()
            undone = [c for c in items if not c.done]
            if undone:
                raise HTTPException(409, f"'{target.name}' requires all {len(items)} checklist item(s) done ({len(undone)} left).")
        if target.require_payment:
            from backend.services.order_money import balance_due as _due
            if await _due(db, order) > 0:          # payments, refunds and credit notes all count
                raise HTTPException(409, f"'{target.name}' requires the order to be fully paid first.")

    # Completion gate: when closing, enforce required checklist items + (optional)
    # customer signature. Only bites when the work has required checklist items or
    # the order's type opts in, so other closes are unaffected.
    if is_closing and not is_dropping:
        from backend.models import OrderChecklistItem, WorkOrderType
        req_items = (await db.execute(select(OrderChecklistItem).where(
            OrderChecklistItem.order_id == order.id,
            OrderChecklistItem.required == True))).scalars().all()  # noqa: E712
        wtype = None
        if order.type_id:
            wtype = (await db.execute(select(WorkOrderType).where(
                WorkOrderType.id == order.type_id))).scalar_one_or_none()
        needs_checklist = bool(req_items) or bool(wtype and wtype.requires_checklist)
        undone = [c for c in req_items if not c.done]
        if needs_checklist and undone:
            raise HTTPException(409, f"Cannot complete: {len(undone)} required checklist item(s) not done.")
        if wtype and wtype.requires_signature and not order.signature:
            raise HTTPException(409, "Cannot complete: customer signature required.")
        # Fields the form marks "Before closing" (Settings → Forms → Customize).
        if order.status != data.status:
            from backend.routers.forms import missing_before_close
            _missing = await missing_before_close(db, current.org_id, order)
            if _missing:
                raise HTTPException(409, f"Fill in before closing: {', '.join(_missing)}.")

    # Orderry rule: cannot close an order with products not yet written off from stock
    if is_closing and not is_dropping:
        pending = sum((l.pending_stock or 0) for l in order.lines)
        if pending > 0:
            raise HTTPException(
                409,
                f"Cannot close: {pending:g} unit(s) still pending stock write-off. "
                f"Receive the stock or reduce the quantity first."
            )
    prev = order.status
    prev_terminal = (await db.execute(select(OrderStatus.is_terminal).where(
        OrderStatus.org_id == current.org_id, OrderStatus.key == prev,
        OrderStatus.record_type == _rt))).scalars().first() or False
    _prev_status = order.status
    order.status = data.status
    if target and (target.status_group or "") == "dropped" and (data.reason or "").strip():
        order.cancel_reason = data.reason.strip()[:120]
    # Write off (or, on reopen / a normal close, undo) what the customer still owes.
    from backend.services.audit import record as _audit_wo
    from backend.services.order_money import balance_due as _owed
    _was_written_off = q2(float(order.written_off or 0))
    order.written_off = 0.0
    if is_dropping:
        order.written_off = await _owed(db, order)
        # Writing off what a customer owes is a money decision, not just a status change.
        if order.written_off > _was_written_off and not await has_perm(db, current, "can_view_finance"):
            raise HTTPException(403, f"This job still has {order.written_off:.2f} owed. Only someone who can use "
                                     "finance can drop it and write that off — ask a manager.")
    if q2(float(order.written_off or 0)) != _was_written_off:
        await _audit_wo(db, org_id=current.org_id, actor_id=current.id, action="order.write_off",
                        entity_type="order", entity_id=order.id,
                        diff={"amount": q2(float(order.written_off or 0)), "previous": _was_written_off,
                              "reason": order.cancel_reason if is_dropping else None})
    order.status_entered_at = datetime.utcnow()
    from backend.services.audit import record as _audit_sc
    await _audit_sc(db, org_id=current.org_id, actor_id=current.id, action="order.status_change",
                    entity_type="order", entity_id=order.id, diff={"status": [_prev_status, data.status]})   # for status time-limit tracking
    if is_closing:
        order.closed_at = datetime.utcnow()
        # Build 144dr — auto-draft an invoice on close (org opt-in). Never blocks
        # the status change if it fails. A dropped job isn't billed.
        if not order.is_estimate and not is_dropping:
            try:
                from backend.services.invoicing import auto_draft_on_close
                await auto_draft_on_close(db, order)
            except Exception:  # noqa: BLE001
                pass
        # Phase E groundwork: activate warranty on close from the longest line warranty
        try:
            wdays = max([(getattr(l, "warranty_days", 0) or 0) for l in order.lines] or [0])
            if wdays:
                from datetime import timedelta as _td
                # Item 4: warranty start honors org policy (closure date vs order creation date)
                _org = (await db.execute(select(Organisation).where(
                    Organisation.id == current.org_id))).scalar_one_or_none()
                _from = order.closed_at
                if _org and getattr(_org, "warranty_count_from", "closure") == "created" and order.created_at:
                    _from = order.created_at
                order.warranty_until = (_from + _td(days=wdays)).date()
        except Exception:
            pass
    log = OrderStatusLog(order_id=order.id, from_status=prev, to_status=data.status,
                         changed_by_id=current.id, note=data.note)
    db.add(log)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Status changed: {prev} \u2192 {data.status}")
    # E4 — checklists set to attach on this status (after the gates above, so they never block
    # entering it; the next require_checklist / close gate waits for them).
    if prev != data.status:
        from backend.routers.checklists import auto_attach_for_status
        for _title in await auto_attach_for_status(db, order, data.status, author_id=current.id):
            await log_system_event(db, current.org_id, "order", order.id, f"Checklist attached: {_title}")

    # Phase B.7: asset auto-movement between zones driven by the status
    if target and target.moves_asset_to and order.device_id:
        from backend.models import AssetMovement, Device
        dev = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none()
        from_zone = getattr(dev, "asset_type", None) if dev else None
        db.add(AssetMovement(org_id=current.org_id, asset_id=order.device_id,
                             from_zone=from_zone, to_zone=target.moves_asset_to,
                             order_id=order.id, status_key=data.status))
        await log_system_event(db, current.org_id, "order", order.id,
                               f"Asset moved to {target.moves_asset_to}")

    # Per-status trigger: auto-create invoice
    if target and target.triggers_invoice:
        existing_inv = (await db.execute(select(Invoice.id).where(Invoice.order_id == order.id).limit(1))).scalar_one_or_none()
        if not existing_inv:
            _inv = Invoice(invoice_number=await next_number(db, current.org_id, "invoice"), org_id=current.org_id,
                           order_id=order.id, client_id=order.client_id, status="draft",
                           subtotal=order.total or 0, total=order.total or 0, issued_at=datetime.utcnow())
            db.add(_inv)
            await log_system_event(db, current.org_id, "order", order.id, "Invoice auto-created on status change")
            # Build 144z: mirror the new invoice (and its customer) to QuickBooks if connected.
            try:
                await db.flush()
                from backend.services import qbo as _qbo
                await _qbo.autosync(db, current.org_id,
                                    [("contact", order.client_id), ("invoice", _inv.id)])
            except Exception:
                pass

    # Customer status messages come only from the notification rules engine (run after
    # commit, below). OrderStatus.triggers_notification is retired: see
    # services/legacy_status_notifs.py for how flagged statuses became rules.
    # Build 144w: workflow automations for status_changed (create task / assign /
    # tag / webhook). Order.status is already the new value here.
    try:
        from backend.services import automation as _auto
        await _auto.run(db, current.org_id, "status_changed", order=order)
    except Exception:
        pass
    # Build 90: the asset follows the job — check-in → "Pending repair", assignment →
    # the technician's warehouse, ready → "Ready for pickup", close → back to the client.
    try:
        from backend.services import warehouses as _wh
        await _wh.on_order_status(db, current.org_id, order, data.status, is_closing=is_closing)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("tekly").exception("asset auto-move failed for order %s", order.id)

    # Build 124: unified contact ledger — the receivable tracks the order's closed state.
    # We reconcile the net 'charge' rows to the order total when it closes, and back to
    # zero when it leaves a terminal state. Posting only the delta makes this idempotent:
    # re-closing, reopening, or editing the total never double-bills the client. It keys
    # off is_closing (which has its own fallback) rather than prev_terminal, so it works
    # even for the built-in 'done/closed/delivered' statuses that aren't in OrderStatus.
    if not order.is_estimate and order.client_id:
        from backend.services.ledger import order_charge_net, post_contact_ledger
        _net = await order_charge_net(db, order.client_id, order.id)
        # A dropped job is charged only what wasn't written off, so it leaves no debt.
        _target = q2(float(order.total or 0) - float(order.written_off or 0)) if is_closing else 0.0
        _delta = q2(_target - _net)
        if _delta:
            _note = (f"Order {order.order_number} ready" if is_closing
                     else f"Order {order.order_number} reopened")
            await post_contact_ledger(
                db, current.org_id, order.client_id, "charge", _delta,
                note=_note, ref_type="order", ref_id=order.id, created_by_id=current.id)

    # Mandate 1: payroll commissions. Accrue when the order enters a terminal
    # (closed) state; claw back when it leaves one (reopen) or is an estimate.
    # Runs before the commit below so the status, the client ledger and the commissions save
    # together: accruing after it left a closed order with no commissions if accrual failed.
    if not order.is_estimate:
        from backend.services.payroll import CommissionEngine
        eng = CommissionEngine(db, current.org_id)
        if is_closing and not prev_terminal:
            await db.refresh(order, ["lines"])
            await eng.accrue_for_order_close(order)
        elif prev_terminal and not is_closing:
            await eng.clawback_for_order_reopen(order)
        else:
            await db.refresh(order, ["lines"])
            await eng.accrue_on_status(order, order.status)

    await db.commit()

    # Customer/staff messages go out only AFTER the status change is committed — sending them
    # inside the transaction meant a failed commit still texted the customer about a change
    # that never happened.
    try:
        from backend.services import notification_rules as _nr
        _ev = "closed" if is_closing else "status_changed"
        await _nr.evaluate(db, org_id=current.org_id, order_id=order.id,
                           event=_ev, status_key=data.status)
        # 144jb: Internal › Workflows — notify the assignee + manager (staff-scoped rules).
        await _nr.notify_staff_order(db, org_id=current.org_id, order_id=order.id,
                                     event=_ev, status_key=data.status, actor_id=current.id)
    except Exception:  # noqa: BLE001
        pass
    # No legacy fallback: the old "templates when no rule matched and the status has
    # triggers_notification" path is gone so one setting (Settings › Notifications) decides
    # what the customer gets. Flagged statuses were converted to equivalent per-status rules.
    await db.commit()   # persist the notification log rows written above

    # Notify the assigned technician their job's status moved (unless they did it).
    if order.assigned_to_id and order.assigned_to_id != current.id:
        try:
            from backend.services.push import notify
            await notify(db, org_id=current.org_id, employee_id=order.assigned_to_id,
                         kind="schedule.changed", title=f"{order.order_number} → {data.status}",
                         body="Status updated", deep_link=f"/tech#/order/{order.id}")
        except Exception:
            pass

    try:
        from backend.services.webhooks import emit_event
        await emit_event(db, current.org_id, "order.status_changed", {
            "id": order.id, "order_number": order.order_number,
            "from": prev, "to": data.status})
        if is_closing:   # WS-C: registered and documented, but never fired before
            await emit_event(db, current.org_id, "order.closed", {
                "id": order.id, "order_number": order.order_number, "status": data.status,
                "is_estimate": bool(order.is_estimate)})
        await db.commit()
    except Exception:
        pass

    # Phase E: if this status is the configured feedback trigger, create a
    # pending Feedback row and fire a notification with the rating link.
    if target:
        from backend.models import Feedback, FeedbackSettings
        fs = (await db.execute(select(FeedbackSettings).where(
            FeedbackSettings.org_id == current.org_id, FeedbackSettings.active == True,  # noqa: E712
            FeedbackSettings.trigger_status_id == target.id))).scalar_one_or_none()
        if fs:
            fb = Feedback(org_id=current.org_id, order_id=order.id, client_id=order.client_id, system=fs.system)
            db.add(fb)
            from backend.routers.feed import log_system_event
            await log_system_event(db, current.org_id, "order", order.id, "Feedback request sent to customer")
            await db.commit()

    # Estimate auto-conversion: entering a status flagged converts_to_order
    # turns the estimate into a work order (Orderry "Order created" status).
    converted_id = None
    if order.is_estimate and target and getattr(target, "converts_to_order", False) and not order.converted_order_id:
        converted_id = await _convert_estimate(db, current, order)

    # tell the frontend whether to open the payment window (per-status trigger)
    wants_payment = bool(target.triggers_payment) if target else is_closing
    return {"ok": True, "status": data.status, "triggers_payment": wants_payment,
            "is_closing": is_closing, "converted_order_id": converted_id}


async def _convert_estimate(db, current, estimate):
    """Convert an estimate into a work order IN PLACE (Build 144di, Orderry-style):
    the same record flips ``is_estimate`` off, gets a fresh WO number, keeps its
    lines and history, and now deducts stock. Irreversible. Returns the id."""
    from backend.models import OrderLine, OrderStatus, StockLevel, StockMovement
    old_number = estimate.order_number
    # initial work-order status (org default or "new")
    init = "new"
    dflt = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.is_default == True,
        OrderStatus.active == True, OrderStatus.record_type == "work_order"))).scalars().first()  # noqa: E712
    if dflt:
        init = dflt.key
    # renumber E -> WO and flip type on the SAME record
    estimate.order_number = await next_number(db, current.org_id, "order")
    estimate.is_estimate = False
    estimate.status = init
    # deduct stock now (the estimate never did)
    src_lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == estimate.id))).scalars().all()
    for l in src_lines:
        if l.kind == "product" and l.ref_id:
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == l.ref_id, StockLevel.location_id == estimate.location_id).with_for_update())).scalar_one_or_none()
            if sl:
                deduct = min(l.qty, max(sl.qty_available, 0))
                await _atomic_add(db, sl, "qty_available", -(deduct), round_to=None)   # atomic
                l.pending_stock = l.qty - deduct
                # log the ACTUAL deducted quantity, negative — matches every other
                # consumption movement; was qty=+l.qty which corrupted movement-sum reports.
                db.add(StockMovement(part_id=l.ref_id, location_id=estimate.location_id,
                                     movement_type="use", qty=-deduct, ref_type="order", ref_id=estimate.id))
    db.add(OrderStatusLog(order_id=estimate.id, from_status=None, to_status=init,
                          changed_by_id=current.id, note=f"Converted from estimate {old_number}"))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", estimate.id,
                           f"Converted from estimate {old_number} to work order {estimate.order_number}")
    await db.commit()
    return estimate.id


@router.post("/{order_id}/convert")
async def convert_estimate_endpoint(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Manually convert an estimate into a work order."""
    est = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)))).scalar_one_or_none()
    if not est:
        raise HTTPException(404, "Estimate not found")
    if not est.is_estimate:
        raise HTTPException(400, "This is already a work order")
    if est.converted_order_id:
        raise HTTPException(400, "Already converted")
    new_id = await _convert_estimate(db, current, est)
    return {"ok": True, "order_id": new_id}


class SwitchTypeIn(BaseModel):
    type_id: str


class OrderCurrencyIn(BaseModel):
    currency_code: Optional[str] = None   # None → back to org base


@router.patch("/{order_id}/currency")
async def change_order_currency(
    order_id: str,
    data: OrderCurrencyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Change a document's display currency and re-snapshot the rate. Stored money stays in
    base; only the display currency + rate change."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    from backend.services import currency as _cur
    info = await _cur.resolve_for_client(db, current.org_id, order.client_id, data.currency_code)
    order.currency_code = info["code"]
    order.fx_rate = info["rate"]
    await db.commit()
    return {"ok": True, "currency_code": info["code"], "fx_rate": info["rate"], "symbol": info["symbol"]}


async def _guard_order_access(db, current, order):
    """403 if a record-scoped role (order_scope="own") tries to act on an order that
    isn't theirs. Mirrors the read guard on GET /{id}, closing the write-path IDOR:
    a scoped tech who knows an id still can't mutate someone else's order."""
    from backend.services import scoping as _scoping
    if not await _scoping.can_view_order(db, current, order):
        raise HTTPException(403, "You can only act on work orders assigned to you")


class DepositIn(BaseModel):
    amount: Optional[float] = None   # absolute deposit in base currency
    percent: Optional[float] = None  # or a % of the current total


async def _clean_intake_photos(db, org_id: str, ids) -> list:
    """Keep only media blob ids that actually belong to this org (can't reference another tenant's
    blob), de-duplicated, order-preserved, capped so the list can't grow unbounded."""
    ids = [i for i in (ids or []) if i][:40]
    if not ids:
        return []
    from backend.models import MediaBlob
    owned = set((await db.execute(select(MediaBlob.id).where(
        MediaBlob.id.in_(ids), MediaBlob.org_id == org_id))).scalars().all())
    seen, out = set(), []
    for i in ids:
        if i in owned and i not in seen:
            seen.add(i); out.append(i)
    return out


class IntakePhotosIn(BaseModel):
    photos: List[str] = []   # the full desired list of media blob ids (replaces the current set)


@router.patch("/{order_id}/intake-photos")
async def set_intake_photos(
    order_id: str,
    data: IntakePhotosIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Replace a work order's intake (condition) photos with the given media blob ids. Used to add
    or remove photos after the order was created; the blobs are uploaded first via POST /api/media."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    order.intake_photos = await _clean_intake_photos(db, current.org_id, data.photos)
    # 144dg — drop markups whose original photo is no longer on the order
    order.intake_markups = {k: v for k, v in (order.intake_markups or {}).items() if k in order.intake_photos}
    await db.commit()
    return {"ok": True, "intake_photos": _intake_photos_payload(order)}


class MarkupIn(BaseModel):
    original_id: str   # the intake photo being marked up (stays untouched)
    marked_id: str     # the uploaded annotated copy (POST /api/media first)


@router.patch("/{order_id}/intake-markups")
async def set_intake_markup(
    order_id: str,
    data: MarkupIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144dg — attach a marked-up copy to an intake photo, keeping the untouched original on file."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if data.original_id not in (order.intake_photos or []):
        raise HTTPException(400, "That photo isn't on this order")
    if not await _clean_intake_photos(db, current.org_id, [data.marked_id]):
        raise HTTPException(400, "Marked image not found")
    mk = dict(order.intake_markups or {})
    mk[data.original_id] = data.marked_id
    order.intake_markups = mk
    await db.commit()
    return {"ok": True, "intake_photos": _intake_photos_payload(order)}


@router.patch("/{order_id}/deposit")
async def set_deposit(
    order_id: str,
    data: DepositIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Set the requested deposit on an order/estimate. Pass amount OR percent (of total).
    The client pays it through the normal pay-link (POST /api/payments/checkout with this amount)."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if data.percent is not None:
        if data.percent < 0 or data.percent > 100:
            raise HTTPException(400, "percent must be 0–100")
        amount = q2((order.total or 0.0) * data.percent / 100.0)
    elif data.amount is not None:
        amount = q2(data.amount)
    else:
        raise HTTPException(400, "Provide amount or percent")
    if amount < 0:
        raise HTTPException(400, "deposit cannot be negative")
    order.deposit_amount = amount
    await db.commit()
    return {"ok": True, "deposit_amount": amount}


@router.patch("/{order_id}/type")
async def switch_type(
    order_id: str,
    data: SwitchTypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Switch a work order's type anytime. If the current status isn't valid for
    the new type's status set, the order is moved to that type's initial status."""
    from backend.models import OrderStatus, WorkOrderType
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    wot = (await db.execute(select(WorkOrderType).where(WorkOrderType.id == data.type_id, WorkOrderType.org_id == current.org_id))).scalar_one_or_none()
    if not wot:
        raise HTTPException(404, "Type not found")
    order.type_id = data.type_id
    remapped = None
    # if the type defines an initial status and the order is still in a 'new' group, align it
    if wot.initial_status:
        cur = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == current.org_id, OrderStatus.key == order.status))).scalars().first()
        if cur and cur.status_group == "new" and order.status != wot.initial_status:
            order.status = wot.initial_status
            order.status_entered_at = datetime.utcnow()
            remapped = wot.initial_status
            db.add(OrderStatusLog(order_id=order.id, from_status=cur.key, to_status=wot.initial_status,
                                  changed_by_id=current.id, note="Type switched"))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id, f"Type switched to {wot.name}")
    await db.commit()
    return {"ok": True, "type_id": data.type_id, "status": order.status, "remapped_to": remapped}

@router.post("/{order_id}/comments", status_code=201)
async def add_comment(
    order_id: str,
    data: CommentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    _co = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not _co:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, _co)
    comment = OrderComment(order_id=order_id, author_id=current.id,
                           body=data.body, visible_to_client=data.visible_to_client)
    db.add(comment)
    await db.commit()
    # Fire comment_added notifications (only for client-visible comments).
    # 144jc: rules engine first (client + staff), legacy template fallback.
    if data.visible_to_client:
        c_matched = 0
        try:
            from backend.services import notification_rules as _nr
            _cr = await _nr.evaluate(db, org_id=current.org_id, order_id=order_id, event="comment_added")
            c_matched = (_cr or {}).get("matched", 0)
            await _nr.notify_staff_order(db, org_id=current.org_id, order_id=order_id,
                                         event="comment_added", actor_id=current.id)
        except Exception:  # noqa: BLE001
            pass
        if c_matched == 0:
            from backend.services.notifications import dispatch_order_notifications
            await dispatch_order_notifications(
                db, org_id=current.org_id, order_id=order_id, trigger_event="comment_added")
    return {"ok": True}


class SignatureIn(BaseModel):
    signature: str                 # data URL / base64 PNG
    note: Optional[str] = None     # e.g. "Device collected by customer"


@router.post("/{order_id}/signature")
async def capture_signature(
    order_id: str,
    data: SignatureIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Capture a customer signature in-shop (e.g. on device pickup/handover).

    Stores it on the order and writes a signature item to the activity timeline.
    """
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if not data.signature:
        raise HTTPException(422, "signature is required")
    order.signature = data.signature
    from backend.models import EventFeedItem
    db.add(EventFeedItem(
        org_id=current.org_id, entity_type="order", entity_id=order.id,
        item_type="signature", media_data=data.signature, media_name="signature.png",
        body=data.note or "Customer signature captured", author_name=current.name,
    ))
    await db.commit()
    return {"ok": True}


@router.get("/{order_id}/incoming-po")
async def order_incoming_po(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Parts this work order is waiting on from a supplier — POs raised for it
    (source_order_id) or whose lines are reserved for it (reserved_order_id), that
    haven't fully arrived yet. Powers the 'Waiting on PO' badge on the WO."""
    from sqlalchemy import or_

    from backend.models import Part, POLine, PurchaseOrder, Supplier
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    open_states = ("draft", "ordered", "partial")
    # POs raised for this WO, or with any line reserved for it — scoped to org via supplier
    # De-duplicate on the id in a subquery: SELECT DISTINCT over whole PO rows fails on Postgres
    # (purchase_orders has JSON columns, which have no equality operator).
    po_ids = (select(PurchaseOrder.id)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .outerjoin(POLine, POLine.po_id == PurchaseOrder.id)
        .where(Supplier.org_id == current.org_id, PurchaseOrder.status.in_(open_states),
               or_(PurchaseOrder.source_order_id == order_id, POLine.reserved_order_id == order_id))
        .distinct())
    pos = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids))
                            .order_by(PurchaseOrder.po_number))).scalars().all()
    part_names = {p.id: p.name for p in (await db.execute(
        select(Part).where(Part.org_id == current.org_id))).scalars().all()}
    out = []
    for po in pos:
        lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
        rel = [l for l in lines if l.reserved_order_id == order_id] or lines
        out.append({"po_id": po.id, "po_number": po.po_number, "status": po.status,
                    "expected_at": po.expected_at.isoformat() if po.expected_at else None,
                    "parts": [{"part": part_names.get(l.part_id, l.part_id),
                               "outstanding": max((l.qty_ordered or 0) - (l.qty_received or 0), 0)} for l in rel]})
    return {"items": out}


@router.get("/{order_id}/purchase-orders")
async def order_purchase_orders(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Every PO linked to this job — open and finished — line by line, for the work order's
    "Parts & purchase orders" panel (ADR-144po)."""
    from backend.models import Part, POLine, Supplier
    from backend.services.po_links import pos_for_order
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    pos = await pos_for_order(db, current.org_id, order_id)
    sup_names = {s.id: s.name for s in (await db.execute(select(Supplier).where(
        Supplier.id.in_({p.supplier_id for p in pos})))).scalars().all()} if pos else {}
    out = []
    for po in pos:
        lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
        rel = [l for l in lines if l.reserved_order_id == order_id] or lines
        names = {p.id: p.name for p in (await db.execute(select(Part).where(
            Part.id.in_({l.part_id for l in rel})))).scalars().all()} if rel else {}
        out.append({
            "po_id": po.id, "po_number": po.po_number, "status": po.status,
            "workflow_status": po.workflow_status, "supplier": sup_names.get(po.supplier_id),
            "raised_here": po.source_order_id == order_id,
            "ordered_at": po.ordered_at.isoformat() if po.ordered_at else None,
            "expected_at": po.expected_at.isoformat() if po.expected_at else None,
            "received_at": po.received_at.isoformat() if po.received_at else None,
            "lines": [{"part": names.get(l.part_id, "Part"), "ordered": l.qty_ordered or 0,
                       "received": l.qty_received or 0,
                       "outstanding": max((l.qty_ordered or 0) - (l.qty_received or 0), 0)} for l in rel],
        })
    return {"items": out}


@router.get("/{order_id}/labor")
async def order_labor(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Total labor time logged against this order (from the time clock)."""
    from backend.models import TimeEntry
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    entries = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == current.org_id, TimeEntry.order_id == order_id))).scalars().all()
    from datetime import datetime as _dt
    # 144v: enrich with employee names + line label so the WO "Time & people" panel
    # can show who's on the job, on which line, and running vs total per person.
    emp_names = {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id))).scalars().all()}
    line_desc = {l.id: l.description for l in (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order_id))).scalars().all()}

    def _sec(e):  # live seconds for a still-running entry
        if e.ended_at:
            return int(e.duration_sec or 0)
        return max(0, int((_dt.utcnow() - e.started_at).total_seconds())) if e.started_at else 0

    total = sum(_sec(e) for e in entries)
    running = any(e.ended_at is None for e in entries)
    by_emp: dict = {}
    for e in entries:
        b = by_emp.setdefault(e.employee_id, {"employee_id": e.employee_id, "employee_name": emp_names.get(e.employee_id, "—"), "total_seconds": 0, "running": False})
        b["total_seconds"] += _sec(e)
        if e.ended_at is None:
            b["running"] = True
    # 144av #2 / 144ax — actual clocked seconds per line (shared defn in services.labour so the
    # inline widget and the Labour-efficiency report never drift). Running timers count live.
    from backend.services.labour import is_job_actual
    by_line: dict = {}
    for e in entries:
        lid = getattr(e, "order_line_id", None)
        if not lid or not is_job_actual(e):
            continue
        bl = by_line.setdefault(lid, {"order_line_id": lid, "total_seconds": 0, "running": False})
        bl["total_seconds"] += _sec(e)
        if e.ended_at is None:
            bl["running"] = True
    return {"order_id": order_id, "total_seconds": int(total), "running": running,
            "by_employee": list(by_emp.values()), "by_line": list(by_line.values()),
            "entries": [{"id": e.id, "employee_id": e.employee_id, "employee_name": emp_names.get(e.employee_id, "—"),
                         "category": e.category, "order_line_id": getattr(e, "order_line_id", None),
                         "line_description": line_desc.get(getattr(e, "order_line_id", None)),
                         "duration_sec": _sec(e), "running": e.ended_at is None,
                         "started_at": e.started_at.isoformat() if e.started_at else None,
                         "ended_at": e.ended_at.isoformat() if e.ended_at else None} for e in entries]}


@router.get("/{order_id}/invoice-preview")
async def invoice_preview(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """A read-only preview of what the customer owes — no DB writes."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines), selectinload(Order.client))
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    lines = [{"description": l.description, "qty": l.qty, "unit_price": l.unit_price,
              "line_type": l.line_type, "total": q2((l.qty or 0) * (l.unit_price or 0))}
             for l in order.lines]
    from backend.services.order_money import balance_due
    balance = await balance_due(db, order)
    return {
        "order_id": order.id, "order_number": order.order_number,
        "client": order.client.name if order.client else "Walk-in",
        "lines": lines,
        "labour_total": order.labour_total, "parts_total": order.parts_total,
        "total": order.total, "prepaid": order.prepayment_total, "balance": balance,
    }


@router.post("/{order_id}/payment-link", status_code=201)
async def payment_link(
    order_id: str,
    regenerate: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Create a payment intent for the order's outstanding balance and return a
    shareable link + QR code. Uses the live gateway's checkout URL when
    configured, else a hosted public pay page (simulated gateway).

    A2: generate-once — if a live (non-paid, non-revoked) link already exists for
    this order, return it instead of minting a new URL. Pass regenerate=true to
    revoke the old one and issue a fresh link."""
    await assert_perm(db, current, "can_create_payment_links")
    from backend.config import settings
    from backend.models import PaymentIntent
    from backend.services import payments as pay
    from backend.services.qr import qr_svg_data_uri
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    from backend.services.order_money import balance_due
    balance = await balance_due(db, order)   # net of every payment, not just prepayments
    if balance <= 0:
        raise HTTPException(400, "Nothing to collect: order balance is zero.")
    # reuse an existing live link unless regenerating
    existing = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.org_id == current.org_id, PaymentIntent.order_id == order_id,
        PaymentIntent.status.in_(("created", "viewed", "pending"))
    ).order_by(PaymentIntent.created_at.desc()))).scalars().first()
    # A live link is only reused while it still asks for what's owed: after a part payment or a
    # change to the order it would charge the old amount, so it's revoked and re-issued.
    if existing and not regenerate and abs(float(existing.amount or 0) - float(balance)) < 0.005:
        base0 = (settings.PUBLIC_BASE_URL or "").rstrip("/")
        rel0 = existing.checkout_url if (existing.provider != "simulated" and existing.checkout_url) else f"/pay/{existing.id}"
        url0 = (base0 + rel0) if (base0 and rel0.startswith("/")) else rel0
        return {"intent_id": existing.id, "amount": existing.amount, "currency": existing.currency,
                "gateway": existing.provider, "url": url0, "qr_svg": qr_svg_data_uri(url0),
                "status": existing.status, "reused": True}
    if existing:
        from backend.services.order_money import revoke_pay_link
        await revoke_pay_link(db, existing)   # stale or regenerated: close it at the provider too
    try:
        intent = await pay.create_intent(db, org_id=current.org_id, order_id=order_id,
                                         amount=balance, account_id=None)   # org base currency
    except RuntimeError as e:
        raise HTTPException(502, f"Gateway error: {e}")
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    # Live gateways return their own hosted checkout URL; the simulated gateway
    # uses our public pay page.
    is_live = intent.provider != "simulated"
    rel = intent.checkout_url if (is_live and intent.checkout_url) else f"/pay/{intent.id}"
    url = (base + rel) if (base and rel.startswith("/")) else rel
    return {"intent_id": intent.id, "amount": balance, "currency": intent.currency,
            "gateway": intent.provider, "url": url, "qr_svg": qr_svg_data_uri(url),
            "status": intent.status, "reused": False}


class CompleteIn(BaseModel):
    status: Optional[str] = None       # terminal status to move to (default: first terminal)
    signature: Optional[str] = None    # optionally capture a signature first


@router.post("/{order_id}/complete")
async def complete_order(
    order_id: str,
    data: CompleteIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """One-call completion for the mobile app: optionally capture a signature,
    then move to a terminal status. The completion gate (required checklist +
    signature) is enforced inside change_status, so this can return 409."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if data.signature:
        order.signature = data.signature
        from backend.models import EventFeedItem
        db.add(EventFeedItem(org_id=current.org_id, entity_type="order", entity_id=order.id,
                             item_type="signature", media_data=data.signature,
                             media_name="signature.png", body="Signature captured at completion",
                             author_name=current.name))
        await db.commit()
    target = data.status
    if not target:
        term = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == current.org_id, OrderStatus.is_terminal == True,  # noqa: E712
            OrderStatus.record_type == "work_order").order_by(OrderStatus.sort_order))).scalars().first()
        if not term:
            raise HTTPException(409, "No terminal status configured to complete into.")
        target = term.key
    return await change_status(order_id, StatusChange(status=target), db, current)


class NotifyIn(BaseModel):
    channel: str = "email"          # email|sms
    subject: Optional[str] = None
    body: str


@router.post("/{order_id}/notify")
async def notify_order_customer(
    order_id: str, data: NotifyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_send_sms")),
):
    """Message button: email/SMS the customer about this order. Delivery goes
    through the configured channel adapter (a real gateway is required to
    actually send; the simulated adapter records the attempt)."""
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    client = None
    if o.client_id:
        client = (await db.execute(select(Client).where(Client.id == o.client_id))).scalar_one_or_none()
    recipient = None
    if client:
        recipient = client.email if data.channel == "email" else client.phone
    if not recipient:
        raise HTTPException(422, f"No {data.channel} on file for this customer")
    delivery = "simulated"
    try:
        from backend.config import settings as _settings
        from backend.services.notifications import _adapter_for, notif_creds, try_oauth_email
        result = None
        if data.channel == "email":
            # Prefer the shop's connected Gmail/Outlook account; fall back to SMTP.
            result = await try_oauth_email(db, current.org_id,
                                           recipient=recipient, subject=data.subject, body=data.body)
            if result is not None:
                delivery = "oauth"
        if result is None:
            _creds = await notif_creds(db, current.org_id, data.channel)
            result = await _adapter_for(data.channel, _creds).send(
                recipient=recipient, subject=data.subject, body=data.body)
            delivery = "smtp" if (data.channel == "email" and _settings.SMTP_HOST) else "simulated"
    except Exception:
        pass   # seam: simulated/absent gateway
    return {"ok": True, "channel": data.channel, "recipient": recipient, "delivery": delivery}


class DuplicateOrderIn(BaseModel):
    # Optional scheduling for the new copy. "Schedule from" / "Schedule to" map to
    # the order's scheduled_for / scheduled_to columns. Omit both to create an
    # unscheduled draft (the historical behaviour).
    scheduled_for: Optional[str] = None
    scheduled_to: Optional[str] = None


@router.post("/{order_id}/duplicate", status_code=201)
async def duplicate_order(
    order_id: str,
    data: DuplicateOrderIn = DuplicateOrderIn(),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Clone a work order into a new editable draft: same client, asset (device),
    location, priority, complaint, type, industry template and all service/product
    lines. Payments, invoices, signature and status history are NOT copied — the
    copy starts fresh so it can be re-scheduled and worked. Scheduling is optional:
    pass ``scheduled_for`` / ``scheduled_to`` to book the copy straight onto the
    calendar, or omit them for an unscheduled draft. Stock is not deducted here
    (that happens when the copy is actually worked)."""
    sched_for = await parse_wall_dt(db, current.org_id, None, data.scheduled_for, "scheduled_for") if data.scheduled_for else None
    sched_to = await parse_wall_dt(db, current.org_id, None, data.scheduled_to, "scheduled_to") if data.scheduled_to else None
    if sched_for and sched_to and sched_to < sched_for:
        raise HTTPException(400, "Schedule to must be on or after schedule from")
    src = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Order not found")
    await assert_perm(db, current, "can_create_estimates" if src.is_estimate else "can_create_tickets")

    number = await next_number(db, current.org_id, "order")
    if src.is_estimate:
        number = "E" + number[3:]

    new_lines = []
    labour = parts = 0.0
    import uuid as _uuid
    _groups: dict = {}   # each kit on the copy gets its own group id
    for l in src.lines:
        _gid = (_groups.setdefault(l.bundle_group_id, str(_uuid.uuid4())) if l.bundle_group_id else None)
        nl = OrderLine(
            bundle_group_id=_gid, bundle_name=l.bundle_name, bundle_print_mode=l.bundle_print_mode,
            bundle_price_locked=l.bundle_price_locked, bundle_qty=l.bundle_qty,
            bundle_std_price=l.bundle_std_price,
            line_type=l.line_type, ref_id=l.ref_id, description=l.description,
            qty=l.qty, unit_price=l.unit_price, cost_price=l.cost_price, kind=l.kind,
            technician_id=l.technician_id, work_share=l.work_share,
            warranty_days=l.warranty_days, discount=l.discount, discount_type=l.discount_type,
            discount_paid_by=l.discount_paid_by, tax_id=l.tax_id, tax_rate=l.tax_rate,
            tax_mode=l.tax_mode, total=l.total, pending_stock=0.0)
        new_lines.append(nl)
        if l.line_type == "labour":
            labour += l.total
        elif l.line_type == "part":
            parts += l.total
        else:
            labour += l.total

    initial_status = "draft" if src.is_estimate else "new"
    dup = Order(
        order_number=number, org_id=current.org_id,
        public_token=await unique_public_token(db),   # 144cu — fresh unique track link, never reuse the source's
        client_id=src.client_id, device_id=src.device_id, location_id=src.location_id,
        priority=src.priority, complaint=src.complaint, diagnosis=src.diagnosis,
        assigned_to_id=src.assigned_to_id, type_id=src.type_id,
        template_key=src.template_key, custom_data=dict(src.custom_data or {}),
        status=initial_status, is_estimate=src.is_estimate,
        scheduled_for=sched_for, scheduled_to=sched_to,
        labour_total=labour, parts_total=parts, total=labour + parts,
        lines=new_lines,
    )
    db.add(dup)
    await db.flush()
    db.add(OrderStatusLog(order_id=dup.id, from_status=None, to_status=initial_status, changed_by_id=current.id))
    from backend.routers.feed import log_system_event
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.duplicate",
                 entity_type="order", entity_id=dup.id,
                 diff={"from": src.order_number, "to": dup.order_number})
    await log_system_event(db, current.org_id, "order", dup.id,
                           f"Duplicated from {src.order_number}")
    await db.commit()
    return {"ok": True, "id": dup.id, "order_number": dup.order_number,
            "from_order_number": src.order_number, "line_count": len(new_lines),
            "scheduled_for": dup.scheduled_for.isoformat() if dup.scheduled_for else None,
            "scheduled_to": dup.scheduled_to.isoformat() if dup.scheduled_to else None}


@router.post("/{order_id}/public-link")
async def ensure_public_link(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Return the public tracking link for an order, minting a cryptographically
    secure token the first time it's requested. Older or seeded orders may not
    have a token yet; this makes "Copy public link" always work. The token is
    unguessable (``secrets.token_urlsafe``) so the link can be shared with the
    customer without exposing the order id."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if not order.public_token:
        order.public_token = await unique_public_token(db)   # 144cu — collision-checked
        await db.commit()
    return {"token": order.public_token, "path": f"/track/{order.public_token}"}


@router.post("/{order_id}/public-link/rotate")
async def rotate_public_link(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """C: mint a fresh public token, invalidating every previously-shared link for
    this order (improves on Orderry, whose copied links can't be revoked)."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    order.public_token = await unique_public_token(db)   # 144cu — collision-checked
    await db.commit()
    return {"token": order.public_token, "path": f"/track/{order.public_token}", "rotated": True}


@router.post("/{order_id}/public-link/revoke")
async def revoke_public_link(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """C: kill the public link entirely — the track page 404s until a new link is minted."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    order.public_token = None
    await db.commit()
    return {"ok": True, "revoked": True}


class CreatePoIn(BaseModel):
    supplier_id: str
    auto_send: bool = False        # mark ordered + notify supplier immediately
    only_shortfall: bool = True    # only order what's short on stock (else full line qty)


@router.post("/{order_id}/create-po", status_code=201)
async def create_po_from_order(
    order_id: str,
    data: CreatePoIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Raise a purchase order for the parts this work order needs but doesn't have
    in stock. Product lines with no catalog part are turned into real products
    first (so stock can be tracked), the PO is linked back to this work order, and
    — with auto_send — it's marked ordered and the supplier is notified."""
    from backend.models import Part, POLine, PurchaseOrder, StockLevel, Supplier

    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    supplier = (await db.execute(select(Supplier).where(
        Supplier.id == data.supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    needed = []          # (part_id, qty, unit_cost, description)
    created_parts = []
    for l in order.lines:
        if l.line_type != "part" or l.kind != "product":
            continue
        # how many are short: prefer the recorded pending_stock; else compute
        if l.pending_stock and l.pending_stock > 0:
            short = l.pending_stock
        elif data.only_shortfall and l.ref_id:
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == l.ref_id, StockLevel.location_id == order.location_id))).scalar_one_or_none()
            avail = (sl.qty_available if sl else 0) or 0
            short = max(l.qty - max(avail, 0), 0)
        else:
            short = l.qty
        if short <= 0:
            continue
        part_id = l.ref_id
        if not part_id:
            # custom product not in the catalog yet — create it so it can be tracked
            part = Part(org_id=current.org_id, name=l.description or "Part",
                        cost_price=l.cost_price or 0.0, sell_price=l.unit_price or 0.0)
            db.add(part)
            await db.flush()
            part_id = part.id
            l.ref_id = part_id            # link the line so future deductions work
            if not order.is_estimate:
                l.pending_stock = short   # nothing was taken from stock for it yet: owed on arrival
            created_parts.append({"id": part.id, "name": part.name})
        needed.append((part_id, short, l.cost_price or 0.0, l.description))

    if not needed:
        raise HTTPException(400, "Nothing to order — all parts on this work order are in stock")

    total = sum(qty * cost for _, qty, cost, _ in needed)
    po = PurchaseOrder(
        supplier_id=supplier.id, location_id=order.location_id,
        org_id=current.org_id, po_number=await next_number(db, current.org_id, "po"),
        source_order_id=order.id,
        status="ordered" if data.auto_send else "draft",
        total=total, ordered_at=datetime.utcnow() if data.auto_send else None)
    db.add(po)
    await db.flush()
    for part_id, qty, cost, _desc in needed:
        db.add(POLine(po_id=po.id, part_id=part_id, qty_ordered=qty, unit_cost=cost))

    from backend.routers.feed import log_system_event
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.create_po",
                 entity_type="order", entity_id=order.id,
                 diff={"po_number": po.po_number, "supplier": supplier.name, "lines": len(needed)})
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Purchase order {po.po_number} raised ({len(needed)} part(s)) for {supplier.name}")
    if data.auto_send:
        # simulate sending the PO to the supplier (real transport wired in integrations)
        import logging
        logging.getLogger(__name__).info("PO %s marked sent to supplier %s (%d line(s), simulated)",
                                         po.po_number, supplier.id, len(needed))
    await db.commit()
    return {"ok": True, "po_id": po.id, "po_number": po.po_number, "status": po.status,
            "supplier": supplier.name, "line_count": len(needed), "total": total,
            "created_parts": created_parts, "sent": data.auto_send,
            "source_order": order.order_number}


# ── 144as — reserve on-hand stock for a work order (a timed hold) ───────────────
# Complements create-po: a PO covers what's *short*, a reservation earmarks what's already
# *in stock* so another job can't grab it while this one waits. Orderry/Tekmetric both let you
# reserve parts to a job; we add an auto-expiring hold so stale reservations free themselves.
class ReserveForOrderIn(BaseModel):
    hold_days: int = 7


@router.post("/{order_id}/reserve-stock", status_code=201)
async def reserve_stock_for_order(
    order_id: str,
    data: ReserveForOrderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_reserve_stock")),
):
    """Reserve the in-stock parts this work order needs, for `hold_days`. Reserves only up to
    the quantity each product line still needs that isn't already held for this job, and never
    more than is free at the order's location. Returns what was held and what was short."""
    from datetime import timedelta
    from backend.models import Part, StockLevel, StockReservation

    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    hold_days = max(1, min(int(data.hold_days or 7), 180))

    # how much is already actively held for this order, per part (don't double-reserve)
    held: dict[str, float] = {}
    existing = (await db.execute(select(StockReservation).where(
        StockReservation.org_id == current.org_id, StockReservation.order_id == order_id,
        StockReservation.released == False))).scalars().all()  # noqa: E712
    now = datetime.utcnow()
    for r in existing:
        if r.expires_at and r.expires_at < now:
            continue
        held[r.part_id] = held.get(r.part_id, 0) + (r.qty or 0)

    # roll up the quantity each catalog part is needed in, across this order's product lines
    need: dict[str, float] = {}
    for l in order.lines:
        if l.line_type != "part" or l.kind != "product" or not l.ref_id:
            continue
        need[l.ref_id] = need.get(l.ref_id, 0) + (l.qty or 0)

    reserved, short = [], []
    part_names = {p.id: p.name for p in (await db.execute(
        select(Part).where(Part.org_id == current.org_id))).scalars().all()}
    for part_id, qty_needed in need.items():
        want = qty_needed - held.get(part_id, 0)      # still to hold for this job
        if want <= 0:
            continue
        sl = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part_id, StockLevel.location_id == order.location_id))).scalar_one_or_none()
        free = ((sl.qty_available or 0) - (sl.qty_reserved or 0)) if sl else 0
        take = min(want, max(free, 0))
        if take <= 0:
            short.append({"part": part_names.get(part_id, part_id), "needed": want, "free": max(free, 0)})
            continue
        sl.qty_reserved = (sl.qty_reserved or 0) + take
        db.add(StockReservation(org_id=current.org_id, part_id=part_id, location_id=order.location_id,
                                order_id=order_id, qty=take,
                                expires_at=now + timedelta(days=hold_days)))
        reserved.append({"part": part_names.get(part_id, part_id), "qty": take})
        if take < want:
            short.append({"part": part_names.get(part_id, part_id), "needed": want - take, "free": 0})

    if reserved:
        from backend.routers.feed import log_system_event
        await log_system_event(db, current.org_id, "order", order_id,
                               f"Reserved {sum(r['qty'] for r in reserved):g} unit(s) of stock for {hold_days} day(s)")
    await db.commit()
    return {"ok": True, "reserved": reserved, "short": short, "hold_days": hold_days}


@router.get("/{order_id}/reservations")
async def order_reservations(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Active (not released, not expired) stock holds for this work order."""
    from backend.models import Part, StockReservation
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    now = datetime.utcnow()
    rows = (await db.execute(select(StockReservation).where(
        StockReservation.org_id == current.org_id, StockReservation.order_id == order_id,
        StockReservation.released == False))).scalars().all()  # noqa: E712
    part_names = {p.id: p.name for p in (await db.execute(
        select(Part).where(Part.org_id == current.org_id))).scalars().all()}
    out = []
    for r in rows:
        if r.expires_at and r.expires_at < now:
            continue
        out.append({"id": r.id, "part": part_names.get(r.part_id, r.part_id), "qty": r.qty,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None})
    return {"items": out}


async def _archive_order(db, current, order_id, reason, do_refund):
    # Row lock (B1): two archive-with-refund requests at once both saw the same unrefunded
    # balance and refunded it twice.
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .with_for_update())).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if order.archived:
        raise HTTPException(400, "Order is already archived")

    from backend.models import Account, Payment, Refund

    # Issued credit notes on this order's invoices put credit on the client's ledger; archiving
    # zeroes the order's charge, so leaving them would hand the client phantom credit. They are
    # canceled first, the same rule as deleting an invoice.
    from backend.models import CreditNote, Invoice as _Inv, InvoiceOrder as _IO
    _inv_ids = select(_Inv.id).where(_Inv.org_id == current.org_id, (_Inv.order_id == order_id) | _Inv.id.in_(
        select(_IO.invoice_id).where(_IO.order_id == order_id)))
    _notes = (await db.execute(select(CreditNote.number).where(
        CreditNote.invoice_id.in_(_inv_ids), CreditNote.status == "issued"))).scalars().all()
    if _notes:
        raise HTTPException(409, f"This order's invoice has issued credit notes ({', '.join(sorted(_notes))}). "
                                 "Cancel them first, then archive the order.")
    # Card money taken on a reader goes back on the reader (Card reader payments › Refund), not as
    # a cash refund here — booking it here too would let the reader refund it a second time.
    from backend.services.order_money import reader_charge_in_flight, unrefunded_reader_money
    if await reader_charge_in_flight(db, order_id):
        raise HTTPException(409, "A card reader charge for this order is still in progress. "
                                 "Finish or cancel it on the reader first.")
    if do_refund and await unrefunded_reader_money(db, order_id) > 0:
        raise HTTPException(409, "This order was paid on a card reader. Refund the card payment on the "
                                 "reader first (Card reader payments › Refund), then archive the order.")

    # A3: deletion dependency chain — never orphan money. Compute net unrefunded
    # payments first; if there is money on the order and the caller did not ask to
    # refund it, block with a clear message instead of silently detaching it.
    # Only real cash/card payments are refundable here — payments settled on the client's own
    # balance (method="balance") never put cash in the till, so refunding them as cash would hand
    # the customer money they never paid. (Those settle by reversing the ledger, handled elsewhere.)
    _pays = (await db.execute(select(Payment).where(
        Payment.order_id == order_id, Payment.kind.in_(["payment", "prepayment"]),
        Payment.method != "balance"))).scalars().all()
    _refunded_already = (await db.execute(select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
        Payment.order_id == order_id, Payment.kind == "refund"))).scalar() or 0.0
    _net = q2(sum(p.amount for p in _pays) - abs(_refunded_already))
    if _net > 0 and not do_refund:
        raise HTTPException(409, "This order has payments on it. Delete with refund, "
                                 "or void the payments first.")

    refunded = 0.0
    if do_refund:
        pays = _pays
        net_paid = _net
        if net_paid > 0:
            acct = None
            # refund to the account the payments used, else the first company account
            acct_ids = [p.account_id for p in pays if p.account_id]
            if acct_ids:
                acct = (await db.execute(select(Account).where(Account.id == acct_ids[0]))).scalar_one_or_none()
            if not acct:
                acct = (await db.execute(select(Account).where(
                    Account.org_id == current.org_id, Account.active == True))).scalars().first()  # noqa: E712
            refund = Refund(org_id=current.org_id, order_id=order_id,
                            account_id=acct.id if acct else None, amount=net_paid, reason=reason)
            db.add(refund)
            await db.flush()
            from backend.routers.cash import open_drawer_shift
            _rp = Payment(order_id=order_id, client_id=order.client_id, method="refund",
                          kind="refund", amount=net_paid, account_id=acct.id if acct else None,
                          # cash handed back from a till comes out of that drawer's count (B10)
                          shift_id=await open_drawer_shift(db, current.org_id, acct),
                          received_by_id=current.id, reference=f"Archive refund: {reason}"[:200])
            db.add(_rp)
            await db.flush()
            refund.payment_id = _rp.id
            if order.client_id:
                # The cash goes back, so the client's ledger must show it — otherwise the reversed
                # charge leaves them with phantom store credit equal to what they were refunded.
                from backend.services.ledger import post_contact_ledger
                await post_contact_ledger(db, current.org_id, order.client_id, "refund", abs(net_paid),
                                          note=f"Refund on archived order {order.order_number}",
                                          ref_type="refund", ref_id=refund.id, created_by_id=current.id)
            if acct:
                from backend.routers.cash import post_transaction
                await post_transaction(db, current.org_id, acct.id, "refund", -net_paid,
                                       comment=f"Refund on archived order {order.order_number}: {reason}",
                                       ref_type="refund", ref_id=refund.id, employee_id=current.id)
            refunded = net_paid

    order.archived = True
    order.archive_reason = reason
    order.archived_at = datetime.utcnow()
    order.archived_by_id = current.id
    # Zero out any outstanding charge this order posted to the client ledger — an archived order
    # must not leave a phantom receivable (mirrors the reopen path: target charge = 0).
    if not order.is_estimate and order.client_id:
        from backend.services.ledger import order_charge_net, post_contact_ledger
        _net = await order_charge_net(db, order.client_id, order.id)
        if q2(_net):
            await post_contact_ledger(
                db, current.org_id, order.client_id, "charge", q2(-_net),
                note=f"Order {order.order_number} archived", ref_type="order", ref_id=order.id,
                created_by_id=current.id)
    # Return consumed stock to inventory — archiving an order must not silently lose the parts it
    # deducted (reuses the same reversal the delete-line/reopen paths use). Safe against re-run:
    # archiving an already-archived order is rejected above.
    if not order.is_estimate:
        from backend.models import OrderLine
        _lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
        for _l in _lines:
            if _l.kind == "product" and _l.ref_id:
                _ded = (_l.qty or 0) - (_l.pending_stock or 0)
                if _ded > 0:
                    await _reverse_fulfillment(db, order, _l.ref_id, _ded)
                    # The parts are back on the shelf: the line no longer holds stock. Without this,
                    # restore -> archive (or delete the line) returned the same parts again.
                    _l.pending_stock = _l.qty or 0
    if not order.is_estimate:
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).reverse_for_deletion(order)
    from backend.routers.feed import log_system_event
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.archive",
                 entity_type="order", entity_id=order.id,
                 diff={"reason": reason, "refunded": refunded})
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Order archived: {reason}" + (f" (refunded {refunded})" if refunded else ""))
    await db.commit()
    return {"ok": True, "id": order.id, "archived": True, "reason": reason, "refunded": refunded}


class ArchiveIn(BaseModel):
    reason: str
    refund: bool = True


@router.post("/{order_id}/archive")
async def archive_order(
    order_id: str,
    data: ArchiveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_orders")),
):
    """Soft-delete a work order: refund any payments (with a reason) and move it to
    the archive. It stays visible to the owner in the archive with the reason, and
    is hidden from normal lists — it is never hard-deleted. (144-G: gated by
    can_delete_orders — default-allow, so only roles that explicitly deny it are blocked.)"""
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(422, "A reason is required to archive an order")
    return await _archive_order(db, current, order_id, reason, data.refund)


async def _restore_order(db, current, order_id):
    """144-G: reverse a soft-delete — un-hide the order and clear the archive stamp.
    Does NOT re-accrue commission reversed at archive time (a manager re-closes the
    order to re-earn it); un-deleting should never silently double-pay.
    ponytail: restore un-hides only, re-close to re-accrue commission."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    if not order.archived:
        raise HTTPException(400, "Order is not archived")
    order.archived = False
    order.archive_reason = None
    order.archived_at = None
    order.archived_by_id = None
    from backend.routers.feed import log_system_event
    from backend.services.audit import record as _audit
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.restore",
                 entity_type="order", entity_id=order.id, diff={"archived": False})
    await log_system_event(db, current.org_id, "order", order.id, "Order restored from archive")
    await db.commit()
    return {"ok": True, "id": order.id, "archived": False}


@router.post("/{order_id}/restore")
async def restore_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_orders")),
):
    """Bring a soft-deleted work order back into the active lists."""
    return await _restore_order(db, current, order_id)


@router.delete("/{order_id}")
async def delete_order(
    order_id: str,
    reason: Optional[str] = None,
    refund: bool = True,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_orders")),
):
    """Deleting a work order archives it (soft-delete) rather than destroying it,
    so finance/audit history is preserved and the owner can see why it was removed.
    Refunds any payments by default."""
    await assert_perm(db, current, "can_delete_tickets")
    return await _archive_order(db, current, order_id, (reason or "Deleted").strip(), refund)


@router.get("/sla/breaches")
async def sla_breaches(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Work orders that have exceeded their current status's time limit
    (OrderStatus.time_limit_min). Powers the SLA widget and is fed by the
    periodic status-sla-sweep background job."""
    from backend.services.sla import find_breaches
    items = await find_breaches(db, current.org_id)
    return {"total": len(items), "items": items}


class TransferOrderIn(BaseModel):
    location_id: str


@router.post("/{order_id}/transfer")
async def transfer_order(
    order_id: str,
    data: TransferOrderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_transfer_tickets")),
):
    """Build 75 — move a work order to another of the org's locations."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    from backend.models import Location
    loc = (await db.execute(select(Location).where(
        Location.id == data.location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not loc:
        raise HTTPException(404, "Target location not found")
    prev = order.location_id
    if prev == data.location_id:
        return {"ok": True, "location_id": data.location_id, "unchanged": True}
    order.location_id = data.location_id
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Transferred to location {loc.name}")
    await db.commit()
    return {"ok": True, "location_id": data.location_id, "from_location_id": prev}


@router.get("/scheduler/free-slots")
async def scheduler_free_slots(
    date_str: str,
    duration_min: int = 60,
    employee_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 74 — first-available search. Returns open windows of at least
    ``duration_min`` on a given day for a technician and/or resource, honouring the
    employee's planned shift bounds (WorkSchedule) and existing scheduled orders.
    Falls back to business hours 09:00–17:00 when there's no planned shift."""
    try:
        day = datetime.fromisoformat(date_str).date()
    except Exception:
        raise HTTPException(400, "Invalid date")
    if duration_min <= 0:
        raise HTTPException(400, "duration_min must be positive")

    # working window from the employee's planned shift, else default business hours
    win_start, win_end = 540, 1020  # 09:00–17:00
    if employee_id:
        from backend.models import WorkSchedule
        sched = (await db.execute(select(WorkSchedule).where(
            WorkSchedule.org_id == current.org_id, WorkSchedule.employee_id == employee_id,
            WorkSchedule.day == day))).scalars().all()
        shift = next((s for s in sched if s.kind == "shift"), None)
        if any(s.kind in ("day_off", "vacation", "sick") for s in sched) and not shift:
            return {"date": date_str, "slots": [], "reason": "employee off this day"}
        if shift:
            win_start, win_end = shift.start_min, shift.end_min

    # existing scheduled orders for this employee/resource on the day → busy blocks
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = datetime(day.year, day.month, day.day, 23, 59, 59)
    q = select(Order).where(
        Order.org_id == current.org_id, Order.scheduled_for.isnot(None),
        Order.scheduled_for >= start, Order.scheduled_for <= end, Order.archived == False)  # noqa: E712
    if employee_id:
        q = q.where(Order.assigned_to_id == employee_id)
    if resource_id:
        q = q.where(Order.resource_id == resource_id)
    orders = (await db.execute(q)).scalars().all()

    def _mins(dt):
        return dt.hour * 60 + dt.minute

    busy = []
    for o in orders:
        s = _mins(o.scheduled_for)
        e = _mins(o.scheduled_to) if o.scheduled_to else s + (o.duration_min or 60)
        busy.append((s, min(e, 24 * 60)))

    # Build 92: appointments occupy the same people as work orders do. Leaving them out
    # meant the scheduler would happily offer a slot that an appointment already owned.
    from backend.models import Appointment as _Bk
    bq = select(_Bk).where(
        _Bk.org_id == current.org_id, _Bk.scheduled_for >= start,
        _Bk.scheduled_for <= end, _Bk.status.in_(["booked", "done"]))
    if employee_id:
        bq = bq.where(_Bk.employee_id == employee_id)
    for b in (await db.execute(bq)).scalars().all():
        s = _mins(b.scheduled_for)
        busy.append((s, min(s + (b.duration_min or 60), 24 * 60)))
    busy.sort()

    # sweep the working window, collecting gaps ≥ duration
    slots = []
    cursor = win_start
    for bs, be in busy:
        if bs > cursor and bs - cursor >= duration_min:
            slots.append((cursor, bs))
        cursor = max(cursor, be)
    if win_end - cursor >= duration_min:
        slots.append((cursor, win_end))

    def _hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    return {"date": date_str, "duration_min": duration_min,
            "slots": [{"start": _hhmm(s), "end": _hhmm(e),
                       "start_iso": datetime(day.year, day.month, day.day, s // 60, s % 60).isoformat()}
                      for s, e in slots]}


@router.get("/scheduler/day")
async def scheduler_day(
    date_str: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Return scheduled orders for one day, grouped by technician."""
    try:
        day = datetime.fromisoformat(date_str).date()
    except Exception:
        raise HTTPException(400, "Invalid date")
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = datetime(day.year, day.month, day.day, 23, 59, 59)

    result = await db.execute(
        select(Order)
        .where(
            Order.org_id == current.org_id,
            Order.scheduled_for != None,  # noqa: E711
            Order.scheduled_for >= start,
            Order.scheduled_for <= end,
        )
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
        .order_by(Order.scheduled_for)
    )
    orders = result.scalars().all()

    items = []
    for o in orders:
        items.append({
            "id": o.id,
            "order_number": o.order_number,
            "status": o.status,
            "priority": o.priority,
            "total": o.total,
            "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
            "scheduled_to": o.scheduled_to.isoformat() if o.scheduled_to else None,
            "duration_min": o.duration_min,
            "resource_id": o.resource_id,
            "client_name": o.client.name if o.client else None,
            "assigned_to_id": o.assigned_to_id,
            "assigned_to_name": o.assigned_to.name if o.assigned_to else None,
            "kind": "order",
        })

    # Build 92: appointments share the calendar with work orders. They're returned in the
    # same list, tagged `kind`, so the scheduler shows one honest picture of the day
    # rather than two views that each omit half of it.
    from backend.models import Appointment as _Bk, Client as _Cl, Employee as _Emp
    bq = select(_Bk).where(
        _Bk.org_id == current.org_id, _Bk.scheduled_for >= start, _Bk.scheduled_for <= end,
        _Bk.status.in_(["booked", "done"]))
    appointments = (await db.execute(bq)).scalars().all()
    if appointments:
        cnames = {c.id: c.name for c in (await db.execute(select(_Cl).where(
            _Cl.org_id == current.org_id))).scalars().all()}
        enames = {e.id: e.name for e in (await db.execute(select(_Emp).where(
            _Emp.org_id == current.org_id))).scalars().all()}
        for b in appointments:
            ends = b.scheduled_for + timedelta(minutes=b.duration_min or 60)
            items.append({
                "id": b.id,
                "order_number": None,
                "status": b.status,
                "priority": 0,
                "total": 0,
                "scheduled_for": b.scheduled_for.isoformat(),
                "scheduled_to": ends.isoformat(),
                "duration_min": b.duration_min,
                "resource_id": None,
                "client_name": b.client_name or cnames.get(b.client_id),
                "assigned_to_id": b.employee_id,
                "assigned_to_name": enames.get(b.employee_id),
                "kind": "appointment",
                "order_id": b.order_id,          # set once it's been converted
            })
    items.sort(key=lambda x: x["scheduled_for"] or "")
    return {"date": date_str, "items": items}


@router.get("/scheduler/range")
async def scheduler_range(
    from_str: str,
    to_str: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Return scheduled orders between two dates (inclusive). Used by week/month views."""
    try:
        d_from = datetime.fromisoformat(from_str).date()
        d_to = datetime.fromisoformat(to_str).date()
    except Exception:
        raise HTTPException(400, "Invalid date")
    start = datetime(d_from.year, d_from.month, d_from.day, 0, 0, 0)
    end = datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59)

    result = await db.execute(
        select(Order)
        .where(
            Order.org_id == current.org_id,
            Order.scheduled_for != None,  # noqa: E711
            Order.scheduled_for >= start,
            Order.scheduled_for <= end,
        )
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
        .order_by(Order.scheduled_for)
    )
    orders = result.scalars().all()
    items = [{
        "id": o.id, "order_number": o.order_number, "status": o.status,
        "priority": o.priority, "total": o.total,
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
        "scheduled_to": o.scheduled_to.isoformat() if o.scheduled_to else None,
        "duration_min": o.duration_min,
        "resource_id": o.resource_id,
        "client_name": o.client.name if o.client else None,
        "assigned_to_id": o.assigned_to_id,
        "assigned_to_name": o.assigned_to.name if o.assigned_to else None,
    } for o in orders]
    return {"from": from_str, "to": to_str, "items": items}




class OrderCodeIn(BaseModel):
    barcode: str = ""


@router.get("/code/generate")
async def generate_order_code(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144ei — a fresh code guaranteed not to collide with any existing work
    order anywhere. Powers the Order code modal's Generate button."""
    return {"barcode": await unique_order_code(db)}


@router.patch("/{order_id}/code")
async def set_order_code(
    order_id: str,
    data: OrderCodeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144eg/144ei — set the order's scannable code (Orderry "Order code").
    Stored on order.barcode, which drives the {Barcode}/{QR Code} print variables and
    the /by-barcode scanner lookup, so screen + print + scan all match. The code is
    GLOBALLY unique: a value already used by any other work order (in any org) is
    rejected, and clearing it regenerates a fresh unique code rather than leaving the
    order without one."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    want = (data.barcode or "").strip()[:100]
    if not want:
        order.barcode = await unique_order_code(db)   # never leave a WO codeless
    else:
        clash = (await db.execute(select(Order.id).where(
            Order.barcode == want, Order.id != order.id))).first()
        if clash:
            raise HTTPException(409, "That code is already used by another work order")
        order.barcode = want
    await db.commit()
    return {"ok": True, "barcode": order.barcode, "order_number": order.order_number}


class CustomDataIn(BaseModel):
    custom_data: dict = {}


@router.patch("/{order_id}/custom-data")
async def save_custom_data(
    order_id: str,
    data: CustomDataIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Edit the work order's own form fields (Passcode, Accessories…) after intake — the job
    card is where "Before closing" fields get filled. Only this type's fields are kept; fields
    locked after create keep their first value."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    from backend.models import CustomField
    fields = (await db.execute(select(CustomField).where(
        CustomField.org_id == current.org_id, CustomField.type_id == order.type_id,
        CustomField.entity_type == "order"))).scalars().all() if order.type_id else []
    cur = dict(order.custom_data or {})
    for f in fields:
        if f.field_type == "header" or f.field_key not in data.custom_data:
            continue
        if f.lock_after_create and cur.get(f.field_key) not in (None, "", []):
            continue
        cur[f.field_key] = data.custom_data[f.field_key]
    order.custom_data = cur
    from backend.routers.forms import sync_order_field_upsells
    await sync_order_field_upsells(db, current.org_id, order, actor=current)
    await db.commit()
    return {"ok": True, "custom_data": cur}


class NotesIn(BaseModel):
    tech_notes: Optional[str] = None
    conclusion: Optional[str] = None


@router.patch("/{order_id}/notes")
async def save_notes(
    order_id: str,
    data: NotesIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144bj — technician's working notes + conclusion / client recommendations."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(order, k, v)
    await db.commit()
    return {"ok": True}


@router.patch("/{order_id}/schedule")
async def reschedule(
    order_id: str,
    data: RescheduleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    # Build 144ai: distinguish "field omitted" from "field explicitly null" so the
    # reschedule dialog can clear a schedule by sending scheduled_for: null.
    sent = data.model_fields_set
    if "scheduled_for" in sent:
        order.scheduled_for = (await parse_wall_dt(db, current.org_id, order.location_id, data.scheduled_for, "scheduled_for")
                               if data.scheduled_for else None)
    elif data.scheduled_for is not None:
        order.scheduled_for = await parse_wall_dt(db, current.org_id, order.location_id, data.scheduled_for, "scheduled_for")
    if "scheduled_to" in sent:
        order.scheduled_to = (await parse_wall_dt(db, current.org_id, order.location_id, data.scheduled_to, "scheduled_to")
                              if data.scheduled_to else None)
    elif data.scheduled_to is not None:
        order.scheduled_to = await parse_wall_dt(db, current.org_id, order.location_id, data.scheduled_to, "scheduled_to")
    if data.duration_min is not None:
        order.duration_min = data.duration_min
    if data.assigned_to_id is not None:
        order.assigned_to_id = data.assigned_to_id or None
    if data.resource_id is not None:
        order.resource_id = data.resource_id or None
    await db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    assigned_to_id: Optional[str] = None
    manager_id: Optional[str] = None


@router.patch("/{order_id}/assign")
async def assign_people(
    order_id: str,
    data: AssignIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_assign_staff")),
):
    """144cp — set the WO manager and/or assigned specialist. Only the fields
    present in the request are changed; empty string clears the field."""
    result = await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    sent = data.model_fields_set
    if "manager_id" in sent:
        await _assert_listed(db, current.org_id, data.manager_id, "display_in_managers", "Managers")
    if "assigned_to_id" in sent:
        await _assert_listed(db, current.org_id, data.assigned_to_id, "display_in_assignees", "Assignee")
    notify_id = None
    if "assigned_to_id" in sent:
        new_assignee = data.assigned_to_id or None
        if new_assignee and new_assignee != order.assigned_to_id:
            notify_id = new_assignee
        order.assigned_to_id = new_assignee
    if "manager_id" in sent:
        order.manager_id = data.manager_id or None
    await db.commit()
    if notify_id and notify_id != current.id:
        try:
            from backend.services.push import notify   # was never imported: assignees got no alert
            await notify(db, org_id=current.org_id, employee_id=notify_id,
                         kind="job.assigned", title=f"New job {order.order_number}",
                         body=f"You were assigned to {order.order_number}",
                         deep_link=f"/tech#/order/{order.id}")
        except Exception:
            pass
    return {"ok": True}


@router.post("/{order_id}/lines/{line_id}/receive-stock")
async def receive_pending_stock(
    order_id: str,
    line_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Write off the pending (not-yet-deducted) stock for a product line,
    once it is available. Deducts whatever it can and reduces pending."""
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    line = (await db.execute(
        select(OrderLine).where(OrderLine.id == line_id, OrderLine.order_id == order_id)
    )).scalar_one_or_none()
    if not line:
        raise HTTPException(404, "Line not found")
    if (line.pending_stock or 0) <= 0:
        return {"ok": True, "pending": 0}

    sl = (await db.execute(
        select(StockLevel).where(StockLevel.part_id == line.ref_id, StockLevel.location_id == order.location_id).with_for_update()
    )).scalar_one_or_none()
    available = sl.qty_available if sl else 0
    deduct = min(line.pending_stock, max(available, 0))
    if deduct <= 0:
        raise HTTPException(409, "No stock available to write off yet")
    await _atomic_add(db, sl, "qty_available", -(deduct), round_to=None)   # atomic
    line.pending_stock -= deduct
    db.add(StockMovement(
        part_id=line.ref_id, location_id=order.location_id,
        movement_type="use", qty=-deduct, ref_type="order", ref_id=order.id,
    ))
    from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
    await _alerts.maybe_low_stock_by_id(db, current.org_id, line.ref_id)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Pending stock written off: {deduct:g} unit(s)")
    await db.commit()
    return {"ok": True, "pending": line.pending_stock}


# ── Line management on an existing order (Orderry: add/edit/delete until closed) ──

async def _log_amount_change(db, org_id, order, prev_total):
    """144bi — record an amount-changed event (before → after) on the activity feed,
    so staff see totals move like Orderry's rail. No-op when the total is unchanged."""
    new_total = q2(float(order.total or 0))
    prev_total = q2(float(prev_total or 0))
    if new_total == prev_total:
        return
    from backend.routers.feed import log_system_event
    await log_system_event(db, org_id, "order", order.id,
                           f"Amount changed: {prev_total:.2f} → {new_total:.2f}")


async def _recalc_order(db, order):
    """Recompute an order's labour/parts/total from its lines."""
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    labour = sum(l.total for l in lines if l.line_type == "labour")
    parts = sum(l.total for l in lines if l.line_type == "part")
    fees = sum(l.total for l in lines if l.line_type == "fee")
    order.labour_total = labour + fees
    order.parts_total = parts
    total = labour + parts + fees
    if total < 0:
        # Discounts left behind after the work they paid for was removed: the job is worth 0, and
        # labour + parts still add up to it (discount lines sit in labour_total).
        order.labour_total = labour + fees - total
    # Item 3: optional whole-document rounding (nearest 0.05). Off by default.
    _org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    if _org and getattr(_org, "rounding_enabled", False):
        total = q2(round(total / 0.05) * 0.05)
    # Never below zero: discount lines (gift card, coupon, loyalty) left behind after the lines
    # they paid for were removed made the total negative, which then read as an "overpayment" and
    # could be refunded in cash (audit 2026-10-29, H5).
    order.total = max(total, 0)   # (rounding can't push a 0 below zero; kept for clarity)


def _all_codes():
    from backend.services.permissions import ALL_CODES
    return ALL_CODES


async def _order_perms(db, current):
    """The role's effective permissions ({code: bool} for every catalogue code); {} = owner."""
    perms = {}
    if current.role_id:
        from backend.models import Role
        from backend.services.permissions import effective_permissions
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        perms = effective_permissions(role) if role else {c: False for c in _all_codes()}
    return perms


async def _guard_discount_approval(db, org_id, perms, discount, discount_type, is_owner=False,
                                   base: float | None = None):
    """144hd — a discount above the org threshold needs approval rights.
    Default-allow (matching the app's RBAC): blocked only when a role explicitly
    sets can_approve_discount to False. Owners (no role) always pass. A fixed-amount
    discount is judged as its percentage of the line (base = qty x unit price): skipping
    fixed discounts let any role give a 100% discount without approval."""
    if is_owner or (discount or 0) <= 0:
        return
    if (discount_type or "pct") == "fixed":
        discount = 100.0 if not base or base <= 0 else min(100.0, float(discount) / float(base) * 100)
    if perms.get("can_approve_discount") is not False:
        return
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    thr = float(getattr(org, "discount_approval_pct", 0) or 0)
    if thr <= 0 or (discount or 0) <= thr:
        return
    raise HTTPException(403, f"Discounts over {thr:g}% need manager approval.")


async def _guard_editable(db, order, current):
    """Block edits if the order's current status locks edits (e.g. closed). A closed order
    that doesn't lock edits can still only be changed by roles with "Edit the closed ticket"."""
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.key == order.status))).scalars().first()
    if st and st.locks_edits:
        raise HTTPException(409, "This order is closed/locked and can't be edited. Reopen it to make changes.")
    if await _is_closed(db, current.org_id, order):
        await assert_perm(db, current, "can_edit_closed_ticket")


async def _is_closed(db, org_id, order) -> bool:
    """Is the order sitting in a closed (terminal) status right now?"""
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.key == order.status,
        OrderStatus.is_estimate == bool(order.is_estimate)))).scalars().first()
    if st:
        return st.status_group == "closed" or bool(st.is_terminal)
    return bool(order.closed_at)


async def _assert_listed(db, org_id, employee_id, code, list_name) -> None:
    """Only people whose role shows them in the Managers / Assignee list can be picked there."""
    if not employee_id:
        return
    emp = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.org_id == org_id))).scalar_one_or_none()
    if emp and not await has_perm(db, emp, code):
        raise HTTPException(422, f"{emp.name} isn't in the {list_name} list.")


async def _assert_line_rights(db, current, li) -> None:
    """Rights a new line needs on top of adding it: an item from outside the catalog, and a
    discount type other than the default percentage."""
    if not li.ref_id and li.line_type != "fee" and li.kind != "bundle":
        product = li.line_type == "part" or li.kind == "product" or li.catalog_kind == "product"
        await assert_perm(db, current, "can_add_custom_product" if product else "can_add_custom_service")
    if (li.discount or 0) > 0 and (li.discount_type or "pct") != "pct":
        await assert_perm(db, current, "can_set_discount_type")


# What "View prices of labors, services, products" hides on a work order's lines.
LINE_PRICE_KEYS = ("unit_price", "total", "discount", "discount_type")


async def _validate_line_assignment(db, org_id, *, technician_id=None, co_technicians=None):
    """144aw — guard per-line labour attribution before it reaches the payroll engine:
    every technician id (primary + split) must belong to THIS org, and split shares must
    sum to ~100%. Prevents over/under-paying commission and orphan/foreign ledger rows."""
    ids = set()
    if technician_id:
        ids.add(technician_id)
    if co_technicians:
        cleaned = [c for c in co_technicians if isinstance(c, dict) and c.get("employee_id")]
        total = sum(float(c.get("share") or 0) for c in cleaned)
        if cleaned and abs(total - 1.0) > 0.001:
            raise HTTPException(422, f"Split shares must add up to 100% (they total {round(total * 100)}%).")
        ids.update(c["employee_id"] for c in cleaned)
    if ids:
        found = set((await db.execute(select(Employee.id).where(
            Employee.org_id == org_id, Employee.id.in_(ids)))).scalars().all())
        if ids - found:
            raise HTTPException(422, "A selected technician doesn't belong to this organization.")



async def _catalog_cost(db, org_id, kind, ref_id) -> float:
    """The catalog cost of the service/product a line points at (0 when it points at nothing)."""
    if not ref_id or kind not in ("service", "product"):
        return 0.0
    from backend.models import Part, Service as _Svc
    model = _Svc if kind == "service" else Part
    row = (await db.execute(select(model.cost_price).where(model.id == ref_id, model.org_id == org_id))).scalar_one_or_none()
    return float(row or 0)

async def _catalog_price(db, org_id, kind, ref_id) -> float:
    """What the catalog charges for a service/product on a work order."""
    from backend.models import Part, Service as _Svc
    if kind == "service":
        row = (await db.execute(select(_Svc.price).where(_Svc.id == ref_id, _Svc.org_id == org_id))).scalar_one_or_none()
        return float(row or 0)
    p = (await db.execute(select(Part).where(Part.id == ref_id, Part.org_id == org_id))).scalar_one_or_none()
    return float(((p.repair_price or 0) or p.sell_price or 0) if p else 0)

@router.post("/{order_id}/lines", status_code=201)
async def add_line(
    order_id: str,
    data: LineIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Add a service/product/fee to an existing order (Orderry Services & Products tab)."""
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_editable(db, order, current)
    if (data.discount or 0) > 0:
        await assert_perm(db, current, "can_set_discount")
    await _assert_line_rights(db, current, data)
    await _validate_line_assignment(db, current.org_id, technician_id=data.technician_id, co_technicians=data.co_technicians)
    if data.order_item_id:
        from backend.services import multi_asset as _ma
        await _ma.require_enabled(db, current.org_id)
        await _ma.item_for_order(db, current.org_id, order.id, data.order_item_id)
    # 144ax G1 — snapshot the catalog service's standard duration as the line's booked hours
    # (minutes → hours), so booked-vs-actual has a baseline without the tech hand-typing it.
    # Snapshot, not a live link: editing the catalog service later won't rewrite this line.
    if not data.booked_hours and data.kind == "service" and data.ref_id:
        from backend.models import Service as _Svc
        svc = (await db.execute(select(_Svc).where(
            _Svc.id == data.ref_id, _Svc.org_id == current.org_id))).scalar_one_or_none()
        if svc and svc.duration_min:
            data.booked_hours = q2((svc.duration_min / 60.0) * (data.qty or 1))
            # 144ax G3 — flat-rate labour: bill booked hours × rate. We sync qty to the booked
            # hours and let the existing qty×unit_price math price it — the money engine is
            # untouched, so non-flat-rate lines (the default) are byte-identical to before.
            if svc.flat_rate and data.booked_hours > 0:
                data.qty = data.booked_hours

    perms = await _order_perms(db, current)
    # Someone who can't see costs never saw the catalog cost, so whatever they sent is ignored
    # and the line takes the catalog's cost (0 for a custom line).
    if current.role_id and not perms.get("can_view_purchase_price"):
        data.cost_price = await _catalog_cost(db, current.org_id, data.kind, data.ref_id)
    elif not await has_perm(db, current, "can_set_cost_price"):
        data.cost_price = await _catalog_cost(db, current.org_id, data.kind, data.ref_id)   # not theirs to set
    # Someone who can't see item prices picked without one: a catalog item is billed at its price.
    if data.ref_id and data.kind in ("service", "product") and not await has_perm(db, current, "can_view_item_prices"):
        data.unit_price = await _catalog_price(db, current.org_id, data.kind, data.ref_id)

    # Build 126 (Orderry parity): a custom line can be saved to the catalog as a reusable
    # service or product, or kept as a one-off for this order only. When saving, we create
    # the catalog record and re-point this line at it so it behaves like a picked item.
    if data.save_to_catalog and (data.kind in (None, "custom", "fee") or data.line_type == "fee"):
        ckind = data.catalog_kind or "service"
        if ckind == "product":
            from backend.models import Part
            part = Part(org_id=current.org_id, name=data.description,
                        sell_price=data.unit_price or 0, cost_price=data.cost_price or 0,
                        warranty_days=data.warranty_days or 0)
            db.add(part)
            await db.flush()
            data.kind, data.line_type, data.ref_id = "product", "part", part.id
        else:
            from backend.models import Service as _Svc
            svc = _Svc(org_id=current.org_id, name=data.description,
                       price=data.unit_price or 0, cost_price=data.cost_price or 0,
                       warranty_days=data.warranty_days or 0)
            db.add(svc)
            await db.flush()
            data.kind, data.line_type, data.ref_id = "service", "labour", svc.id

    if data.kind == "service" and perms.get("can_add_services") is False:
        raise HTTPException(403, "You don't have permission to add services")
    if data.kind == "product" and perms.get("can_add_products") is False:
        raise HTTPException(403, "You don't have permission to add products")

    # ── Bundle: one pick explodes into component lines (Orderry Bundles).
    #    Fixed-price bundles scale each component proportionally so the sum
    #    lands on the bundle price; component products deduct stock as usual. ──
    if data.kind == "bundle" and data.ref_id:
        from backend.models import Bundle, BundleComponent, Part
        from backend.models import Service as _Svc
        bundle = (await db.execute(select(Bundle).where(
            Bundle.id == data.ref_id, Bundle.org_id == current.org_id))).scalar_one_or_none()
        if not bundle:
            raise HTTPException(404, "Bundle not found")
        comps = (await db.execute(select(BundleComponent).where(
            BundleComponent.bundle_id == bundle.id))).scalars().all()
        if not comps:
            raise HTTPException(400, "This bundle has no components")
        # 144db — resolve device-variant product slots against the WO's device (once)
        _kit_device = None
        if order.device_id:
            from backend.models import Device as _Dev
            _kit_device = (await db.execute(select(_Dev).where(
                _Dev.id == order.device_id, _Dev.org_id == current.org_id))).scalar_one_or_none()
        # standard price per component. each entry: (comp, eff_ref_id, name, price, ltype, kind)
        std = []
        for c in comps:
            if c.ref_type == "product":
                item = (await db.execute(select(Part).where(Part.id == c.ref_id, Part.org_id == current.org_id)
                        .options(selectinload(Part.stock_levels)))).scalar_one_or_none()
                if getattr(c, "device_variant", False) and item is not None and _kit_device is not None:
                    item = await _resolve_variant_part(db, current.org_id, item, _kit_device)
                std.append((c, item.id if item else c.ref_id, item.name if item else "Product",
                            item.sell_price if item else 0, "part", "product"))
            else:
                item = (await db.execute(select(_Svc).where(_Svc.id == c.ref_id, _Svc.org_id == current.org_id))).scalar_one_or_none()
                std.append((c, c.ref_id, item.name if item else "Service", item.price if item else 0, "labour", "service"))
        # Every line from this kit is tagged with one group id (kit1p01) so customer documents can
        # print the kit as one line. A fixed-price kit locks its total for the group and spreads it
        # across the lines with the rounding remainder on one of them, so it adds up exactly.
        import uuid as _uuid

        from backend.services import kit_print
        group_id = str(_uuid.uuid4())
        locked = q2((bundle.price or 0) * data.qty) if bundle.price_mode == "fixed" else None
        if locked is not None and not kit_print.price_ok(locked):
            raise HTTPException(400, "That many kits comes to more than one job can hold")
        made = []
        for c, eff_ref_id, name, price, ltype, kind in std:
            li = LineIn(line_type=ltype, kind=kind, ref_id=eff_ref_id,
                        description=f"{name} ({bundle.name})",
                        qty=c.qty * data.qty, unit_price=q2(price))
            t = _calc_line(li)
            # The part's catalog cost, like any picked line, so profit reports see the kit's cost.
            nl = OrderLine(order_id=order_id, line_type=li.line_type, kind=li.kind, ref_id=li.ref_id,
                           description=li.description, qty=li.qty, unit_price=li.unit_price,
                           cost_price=await _catalog_cost(db, current.org_id, kind, eff_ref_id),
                           technician_id=data.technician_id, order_item_id=data.order_item_id, total=t,
                           bundle_group_id=group_id, bundle_name=bundle.name,
                           bundle_print_mode=kit_print.norm_mode(bundle.print_mode),
                           bundle_price_locked=locked, bundle_qty=data.qty, bundle_std_price=q2(price))
            db.add(nl)
            await db.flush()
            if kind == "product" and not order.is_estimate:
                await _maybe_deduct(db, order, nl, li.qty, perms, current)
            made.append(nl.id)
        await _respread_kit(db, order_id, group_id)
        await _recalc_order(db, order)
        await db.commit()
        return {"ok": True, "bundle": bundle.name, "lines_created": len(made), "bundle_group_id": group_id}

    # A line added INSIDE a kit already on this job joins its group: it prints with the kit, and a
    # fixed kit price is re-spread so the customer's price for the kit doesn't change.
    kit_group = None
    if data.bundle_group_id:
        kit_group = (await db.execute(select(OrderLine).where(
            OrderLine.order_id == order_id, OrderLine.bundle_group_id == data.bundle_group_id)
            .limit(1))).scalars().first()
        if not kit_group:
            raise HTTPException(404, "That kit isn't on this job")
        if data.kind not in ("service", "product") or not data.ref_id:
            raise HTTPException(400, "Only catalog services and products can go inside a kit. Add anything else as an extra.")
        await _guard_kit_change(db, current, order_id, kit_group.bundle_group_id, adds_value=True)
        # A line inside a kit takes the kit's item, and its weight is the catalog price, never a
        # price the client sent (it decides how the kit's locked total is shared out).
        data.order_item_id = kit_group.order_item_id

    # Merge: if the same product already exists on the order, just bump qty (Orderry)
    if data.kind == "product" and data.ref_id:
        # A kit's lines are never merged into: an extra must stay a line of its own.
        existing = (await db.execute(select(OrderLine).where(
            OrderLine.order_id == order_id, OrderLine.ref_id == data.ref_id, OrderLine.kind == "product",
            OrderLine.bundle_group_id.is_(None)).limit(1))).scalars().first()
        if existing and not kit_group:
            existing.qty += data.qty
            existing.total = q2(existing.qty * existing.unit_price *
                                   (1 - (existing.discount or 0)/100 if existing.discount_type != "fixed" else 1))
            # re-run proper calc
            tmp = LineIn(line_type=existing.line_type, description=existing.description, qty=existing.qty,
                         unit_price=existing.unit_price, cost_price=existing.cost_price or 0,
                         discount=existing.discount or 0, discount_type=existing.discount_type or "pct",
                         tax_rate=existing.tax_rate or 0, tax_mode=existing.tax_mode or "imposed")
            existing.total = _calc_line(tmp)
            await _maybe_deduct(db, order, existing, data.qty, perms, current)
            await _recalc_order(db, order)
            await db.commit()
            return {"ok": True, "line_id": existing.id, "merged": True}

    await _apply_client_discount(db, current.org_id, order.client_id, data)
    await _auto_apply_tax(db, current.org_id, data)
    await _guard_discount_approval(db, current.org_id, perms, data.discount, data.discount_type, is_owner=not current.role_id,
                                   base=(data.qty or 0) * (data.unit_price or 0))
    # 144gy — margin-floor guardrail: block a line discounted below the org's minimum.
    from backend.routers.pricing import evaluate_line_margin
    _mg = await evaluate_line_margin(
        db, current.org_id, kind=data.kind, ref_id=data.ref_id, unit_price=data.unit_price,
        qty=data.qty, discount=data.discount or 0, discount_type=data.discount_type or "pct",
        cost_price=data.cost_price)
    if _mg["blocked"]:
        raise HTTPException(409, f"Margin {_mg['margin_pct']}% is below the {_mg['floor']}% floor. "
                                 f"Lower the discount or raise the price.")
    total = _calc_line(data)
    line = OrderLine(
        order_id=order_id, line_type=data.line_type, kind=data.kind, ref_id=data.ref_id,
        description=data.description, qty=data.qty, unit_price=data.unit_price,
        cost_price=data.cost_price, discount=data.discount, discount_type=data.discount_type, discount_paid_by=data.discount_paid_by,
        tax_id=data.tax_id, tax_rate=data.tax_rate, tax_mode=data.tax_mode,
        technician_id=data.technician_id, work_share=data.work_share, warranty_days=data.warranty_days,
        booked_hours=data.booked_hours or 0, co_technicians=data.co_technicians or [], total=total,
        order_item_id=data.order_item_id,
    )
    if kit_group:
        line.bundle_group_id, line.bundle_name = kit_group.bundle_group_id, kit_group.bundle_name
        line.bundle_print_mode, line.bundle_qty = kit_group.bundle_print_mode, kit_group.bundle_qty
        line.bundle_price_locked = kit_group.bundle_price_locked
        line.bundle_std_price = q2(await _catalog_price(db, current.org_id, data.kind, data.ref_id))
        line.tax_id, line.tax_rate, line.tax_mode = kit_group.tax_id, kit_group.tax_rate, kit_group.tax_mode
    db.add(line)
    await db.flush()
    # deduct stock for products (estimates never deduct)
    if data.kind == "product" and data.ref_id and not order.is_estimate:
        await _maybe_deduct(db, order, line, data.qty, perms, current)
    _prev_total = float(order.total or 0)
    if kit_group:
        line.total = _calc_line(_line_as_in(line))
        await _respread_kit(db, order_id, kit_group.bundle_group_id, actor=current,
                            change=f"Added {data.description}")
    await _recalc_order(db, order)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id, f"Added {data.kind or data.line_type}: {data.description}")
    await _log_amount_change(db, current.org_id, order, _prev_total)
    await db.commit()
    resp = {"ok": True, "line_id": line.id}
    if _mg["below_floor"] and _mg["guard"] == "warn":   # 144gy warn: allow but flag
        resp["margin_warning"] = f"Low margin: {_mg['margin_pct']}% (floor {_mg['floor']}%)."
    return resp


async def _reverse_fulfillment(db, order, part_id, qty):
    """Unwind a fulfillment: return qty to warehouse stock, release serialized
    units allocated to this order, and restore FIFO cost layers (newest first,
    capped at each layer's received qty). Mirrors _maybe_deduct."""
    if not part_id or qty <= 0:
        return
    # Return stock to the location it was actually deducted from (recorded on the "use"
    # movement), not order.location_id — those differ after a work order is transferred to
    # another location, and returning to the new location would corrupt per-location on-hand.
    dloc = (await db.execute(select(StockMovement.location_id).where(
        StockMovement.ref_id == order.id, StockMovement.part_id == part_id,
        StockMovement.movement_type == "use").order_by(StockMovement.created_at.desc()).limit(1))).scalar_one_or_none()
    loc_id = dloc or order.location_id
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == part_id, StockLevel.location_id == loc_id))).scalar_one_or_none()
    if sl:
        await _atomic_add(db, sl, "qty_available", qty, round_to=None)   # atomic
    else:
        db.add(StockLevel(part_id=part_id, location_id=loc_id,
                          qty_available=qty, qty_reserved=0))
    db.add(StockMovement(part_id=part_id, location_id=loc_id,
                         movement_type="return", qty=qty, ref_type="order", ref_id=order.id))
    from backend.models import SerializedItem, StockItemBatch
    # Release serialized units (newest allocated first)
    serials = (await db.execute(select(SerializedItem).where(
        SerializedItem.org_id == order.org_id, SerializedItem.part_id == part_id,
        SerializedItem.order_id == order.id, SerializedItem.status == "allocated_to_order")
        .order_by(SerializedItem.created_at.desc()).limit(int(qty)))).scalars().all()
    for s in serials:
        s.status = "in_stock"
        s.order_id = None
    # Restore cost layers (approximate without a per-line consumption journal:
    # newest layer first, never exceeding what that layer originally received)
    from decimal import Decimal as _D
    remaining = _D(str(qty))
    layers = (await db.execute(select(StockItemBatch).where(
        StockItemBatch.org_id == order.org_id, StockItemBatch.part_id == part_id)
        .order_by(StockItemBatch.received_at.desc()))).scalars().all()
    for layer in layers:
        if remaining <= 0:
            break
        consumed = _D(str(layer.qty_received or 0)) - _D(str(layer.qty_remaining or 0))
        if consumed <= 0:
            continue
        back = min(remaining, consumed)
        layer.qty_remaining = _D(str(layer.qty_remaining or 0)) + back
        remaining -= back


def _can_override_reservation(perms, current) -> bool:
    """Admin-override is opt-in (default-deny), unlike the rest of the default-allow catalog:
    only the owner (no role) or a role with can_override_reservation explicitly enabled may
    consume stock reserved for another job."""
    return (not getattr(current, "role_id", None)) or (perms.get("can_override_reservation") is True)


async def _resolve_variant_part(db, org_id, configured, device):
    """144db — device-aware job-kit slot. Given the configured fallback part and the WO's device,
    return the in-category part that FITS the device, preferring one with stock. No device, no
    category, or no fit → the configured part (the safe default)."""
    from backend.models import Part
    from backend.services.compatibility import part_fits
    if not configured or not device or not getattr(configured, "category_id", None):
        return configured
    if not (device.brand or device.model):
        return configured
    pool = (await db.execute(select(Part).where(
        Part.org_id == org_id, Part.category_id == configured.category_id,
        Part.active == True).options(selectinload(Part.stock_levels)))).scalars().all()  # noqa: E712
    fits = [p for p in pool if part_fits(p.compatibility_json, device.brand, device.model)]
    if not fits:
        return configured
    # prefer a fitting part that has stock, else the first fitting one (stable by name)
    fits.sort(key=lambda p: p.name or "")
    with_stock = [p for p in fits if sum((sl.qty_available or 0) for sl in (p.stock_levels or [])) > 0]
    return (with_stock or fits)[0]


async def _maybe_deduct(db, order, line, qty, perms, current):
    """Deduct qty of a product line from stock; mark true shortfall as pending.
    144at — reservation-aware: a job may take only FREE stock + its own reservation. Units
    reserved for a *different* job are blocked (409) unless the actor can override, in which case
    they're drawn from those holds FIFO and audited."""
    from backend.services import reservations as _rsv
    allow_override = _can_override_reservation(perms, current)
    g = await _rsv.guard_and_draw(
        db, org_id=order.org_id, part_id=line.ref_id, location_id=order.location_id,
        order_id=order.id, qty=qty, actor_id=current.id, allow_override=allow_override,
        taken_label=order.order_number)
    if g["blocked"] > 0:
        raise HTTPException(409, f"{g['blocked']:g} unit(s) of this part are reserved for another job. "
                                 f"A manager with override permission must approve using them.")
    deducted = g["take"]
    shortfall = g["pending"]
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == line.ref_id, StockLevel.location_id == order.location_id))).scalar_one_or_none()
    if shortfall > 0 and perms.get("can_add_without_stock") is False:
        raise HTTPException(403, "Not enough free stock and you can't add without deduction.")
    if sl and deducted > 0:
        await _atomic_add(db, sl, "qty_available", -(deducted), round_to=None)   # atomic
        if True:
            db.add(StockMovement(part_id=line.ref_id, location_id=order.location_id,
                                 movement_type="use", qty=-deducted, ref_type="order", ref_id=order.id))
            from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
            await _alerts.maybe_low_stock_by_id(db, order.org_id, line.ref_id)
            # ── FIFO cost layers: if this part has batches, consume them oldest-first
            #    and stamp the exact weighted COGS onto the line. ──
            from backend.models import StockItemBatch
            has_batches = (await db.execute(select(StockItemBatch.id).where(
                StockItemBatch.org_id == order.org_id, StockItemBatch.part_id == line.ref_id,
                StockItemBatch.qty_remaining > 0).limit(1))).scalar_one_or_none()
            if has_batches:
                from decimal import Decimal as _D

                from backend.services.serialized import consume_fifo_lifo
                try:
                    res_cost = await consume_fifo_lifo(
                        db, org_id=order.org_id, part_id=line.ref_id,
                        qty=_D(str(deducted)), method="fifo")
                    # exact unit COGS for the consumed units
                    line.cost_price = res_cost["avg_unit_cost"]
                except HTTPException:
                    pass  # batches short — warehouse qty already handled the deduction
            # ── Serialized units: allocate in-stock serials onto this order. ──
            n_serial = int(deducted)
            if n_serial > 0:
                from backend.services.serialized import allocate_serialized_item
                for _ in range(n_serial):
                    try:
                        await allocate_serialized_item(
                            db, org_id=order.org_id, part_id=line.ref_id, order_id=order.id)
                    except HTTPException:
                        break  # not a serialized part (or none left) — fine
    line.pending_stock = (line.pending_stock or 0) + shortfall


def _line_as_in(line) -> "LineIn":
    return LineIn(line_type=line.line_type, description=line.description or "", qty=line.qty,
                  unit_price=line.unit_price or 0, cost_price=line.cost_price or 0,
                  discount=line.discount or 0, discount_type=line.discount_type or "pct",
                  tax_rate=line.tax_rate or 0, tax_mode=line.tax_mode or "imposed")


async def _respread_kit(db, order_id, group_id, *, actor=None, change: str = ""):
    """Fixed-price kit: the customer's price for the kit stays put. Re-divide the locked total
    across the lines still in the kit, in proportion to their standard price (kit_print.spread).
    A kit priced as the sum of its parts has no lock and is left alone. When a person's change
    caused it (actor), the old and new line prices are audited."""
    from backend.services import kit_print
    members = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order_id, OrderLine.bundle_group_id == group_id)
        .order_by(OrderLine.id))).scalars().all()
    locked = next((m.bundle_price_locked for m in members if m.bundle_price_locked is not None), None)
    if locked is None:
        return
    moved = {}
    for m, unit, adjust in kit_print.spread(locked, list(members)):
        before = float(m.unit_price or 0)
        m.unit_price = unit
        m.bundle_price_locked = locked
        m.total = q2(_calc_line(_line_as_in(m)) + adjust)
        if before != unit:
            moved[m.description or m.id] = [before, unit]
    if actor is not None and moved:
        from backend.services.audit import record as _audit
        await _audit(db, org_id=actor.org_id, actor_id=actor.id, action="order.kit_respread",
                     entity_type="order", entity_id=order_id,
                     diff={"kit": members[0].bundle_name if members else None, "group_id": group_id,
                           "change": change[:300], "kit_price": locked, "unit_prices": moved})


async def _kit_invoice_statuses(db, order_id, members) -> list[str]:
    """Statuses of the live (not cancelled) invoices that bill this kit's lines: a partial invoice
    listing one of them, or an invoice for the whole job (one that lists no job lines of its own)."""
    from backend.models import Invoice, InvoiceLine, InvoiceOrder
    from backend.services.invoicing import VOID_STATUSES
    ids = [m.id for m in members]
    out = list((await db.execute(select(Invoice.status).join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id).where(
        InvoiceLine.order_line_id.in_(ids), Invoice.status.notin_(VOID_STATUSES)))).scalars().all())
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order_id)
    partial = select(InvoiceLine.invoice_id).where(InvoiceLine.order_line_id.isnot(None))
    out += list((await db.execute(select(Invoice.status).where(
        (Invoice.order_id == order_id) | Invoice.id.in_(linked),
        Invoice.id.notin_(partial), Invoice.status.notin_(VOID_STATUSES)))).scalars().all())
    return [x or "draft" for x in out]


async def _guard_kit_change(db, current, order_id, group_id, *, adds_value: bool):
    """What may change inside a kit on a job, before anything is written.
    - Lines billed on a live partial invoice are frozen: re-sharing the kit price would rewrite them.
    - In a fixed-price kit, more parts (or more of a part) at the same price is a giveaway:
      it needs the kit-price permission. Fewer parts keeps the customer's price; anyone may.
    - In a kit priced as the sum of its parts that prints as one line, adding a line puts a charge
      under the kit's name: it needs the kit print permission."""
    from backend.services import kit_print
    members = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order_id, OrderLine.bundle_group_id == group_id))).scalars().all()
    if not members:
        return
    if await _kit_invoice_statuses(db, order_id, members):
        raise HTTPException(409, "Part of this kit is already on an invoice. Cancel that invoice to change the kit.")
    locked = any(m.bundle_price_locked is not None for m in members)
    if adds_value and locked and not await has_perm(db, current, "can_change_kit_price"):
        raise HTTPException(403, "This kit has a fixed price, so adding to it gives parts away. "
                                 "Add it as an extra, or ask a manager to change the kit.")
    if (adds_value and not locked and kit_print.norm_mode(members[0].bundle_print_mode) != kit_print.ITEMIZED
            and not await has_perm(db, current, "can_change_kit_print")):
        raise HTTPException(403, "This kit prints as one line, so the customer wouldn't see this charge. "
                                 "Add it as an extra, or ask a manager.")


class KitUpdate(BaseModel):
    print_mode: Optional[str] = None     # itemized | one_line | one_line_includes (this job only)
    price: Optional[float] = None        # a new fixed total for this kit on this job
    reason: Optional[str] = None         # required with price; kept in the audit log



@router.patch("/{order_id}/kits/{group_id}")
async def update_kit(
    order_id: str,
    group_id: str,
    data: KitUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """One kit on one job: how it prints for the customer, and (manager) its locked price.
    Both changes are audited; money stays on the kit's real lines."""
    from backend.services import kit_print
    from backend.services.audit import record as _audit
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_editable(db, order, current)
    members = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order_id, OrderLine.bundle_group_id == group_id))).scalars().all()
    if not members:
        raise HTTPException(404, "That kit isn't on this job")
    name = members[0].bundle_name
    if data.print_mode is not None:
        await assert_perm(db, current, "can_change_kit_print")
        if data.print_mode not in kit_print.PRINT_MODES:
            raise HTTPException(400, "Choose One line, One line and what's included, or Item by item")
        before = kit_print.norm_mode(members[0].bundle_print_mode)
        if before != data.print_mode and any(
                st != "draft" for st in await _kit_invoice_statuses(db, order_id, members)):
            raise HTTPException(409, "This kit is on an invoice the customer already has, so it keeps printing "
                                     "the same way. Print an itemised copy instead.")
        if before != data.print_mode:
            for m in members:
                m.bundle_print_mode = data.print_mode
            await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.kit_print_mode",
                         entity_type="order", entity_id=order.id,
                         diff={"kit": name, "group_id": group_id, "print_mode": [before, data.print_mode]})
    if data.price is not None:
        await assert_perm(db, current, "can_change_kit_price")
        if not kit_print.price_ok(data.price):     # checked here: a NaN can't be echoed in a 422
            raise HTTPException(400, "Enter a kit price of 0 or more")
        reason = (data.reason or "").strip()
        if len(reason) < 3:
            raise HTTPException(400, "Say why the kit price is changing")
        await _guard_kit_change(db, current, order_id, group_id, adds_value=False)
        before = members[0].bundle_price_locked
        if before is None:
            before = q2(sum(float(m.qty or 0) * float(m.unit_price or 0) for m in members))
        new = q2(data.price)
        for m in members:
            m.bundle_price_locked = new
        _prev_total = float(order.total or 0)
        await db.flush()
        await _respread_kit(db, order_id, group_id)
        await _recalc_order(db, order)
        await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.kit_price_override",
                     entity_type="order", entity_id=order.id,
                     diff={"kit": name, "group_id": group_id, "price": [before, new], "reason": reason[:500]})
        await _log_amount_change(db, current.org_id, order, _prev_total)
    await db.commit()
    return {"ok": True, "order_total": order.total, "print_mode": kit_print.norm_mode(members[0].bundle_print_mode),
            "price": members[0].bundle_price_locked}


class LineUpdate(BaseModel):
    order_item_id: Optional[str] = None              # several items per job ("" / null = whole job)
    qty: Optional[float] = None
    unit_price: Optional[float] = None
    cost_price: Optional[float] = None
    discount: Optional[float] = None
    discount_type: Optional[str] = None
    description: Optional[str] = None
    technician_id: Optional[str] = None
    work_share: Optional[float] = None               # 144av #3
    booked_hours: Optional[float] = None             # 144av #2
    co_technicians: Optional[list] = None            # 144av #3


    # Security phase 3: negative quantities/prices/discounts and >100% discounts used to
    # create phantom stock and zero/negative totals that slipped past every guard.
    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_price", "cost_price", "discount")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("Prices, costs and discounts can't be negative")
        return v

    @field_validator("discount_type")
    @classmethod
    def _known_discount_type(cls, v):
        if v is not None and v not in ("pct", "fixed"):
            raise ValueError("discount_type must be 'pct' or 'fixed'")
        return v

    @model_validator(mode="after")
    def _pct_at_most_100(self):
        if (self.discount_type or "pct") == "pct" and (self.discount or 0) > 100:
            raise ValueError("A percentage discount can't exceed 100%")
        return self

@router.patch("/{order_id}/lines/{line_id}")
async def update_line(
    order_id: str,
    line_id: str,
    data: LineUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Edit qty/price/cost/discount of a line; totals auto-recalc (Orderry)."""
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_editable(db, order, current)
    line = (await db.execute(select(OrderLine).where(OrderLine.id == line_id, OrderLine.order_id == order_id))).scalar_one_or_none()
    if not line:
        raise HTTPException(404, "Line not found")
    payload = data.model_dump(exclude_unset=True)
    if current.role_id and not (await _order_perms(db, current)).get("can_view_purchase_price"):
        payload.pop("cost_price", None)
    if "cost_price" in payload and float(payload["cost_price"] or 0) != float(line.cost_price or 0):
        await assert_perm(db, current, "can_set_cost_price")
    if "discount_type" in payload and (payload["discount_type"] or "pct") != (line.discount_type or "pct"):
        await assert_perm(db, current, "can_set_discount_type")
    # Changing what the customer pays needs the matching right (a technician can't zero a job).
    if "discount" in payload and (payload["discount"] or 0) != (line.discount or 0):
        await assert_perm(db, current, "can_set_discount")
    if "unit_price" in payload and payload["unit_price"] is not None and float(payload["unit_price"]) != float(line.unit_price or 0):
        if line.bundle_group_id and line.bundle_price_locked is not None:
            raise HTTPException(409, "This line is part of a fixed-price kit. Change the kit's price instead.")
        await assert_perm(db, current, "can_edit_sale_price")
    if "order_item_id" in payload:
        payload["order_item_id"] = payload["order_item_id"] or None
        if payload["order_item_id"]:
            from backend.services import multi_asset as _ma
            await _ma.require_enabled(db, current.org_id)
            await _ma.item_for_order(db, current.org_id, order.id, payload["order_item_id"])
    # 144aw — validate labour attribution against the org + 100% split before persisting.
    if "co_technicians" in payload or "technician_id" in payload:
        await _validate_line_assignment(
            db, current.org_id,
            technician_id=payload.get("technician_id", line.technician_id),
            co_technicians=payload.get("co_technicians", line.co_technicians))
    old_qty = line.qty or 0
    if line.bundle_group_id and "qty" in payload and (payload["qty"] or 0) != old_qty:
        await _guard_kit_change(db, current, order_id, line.bundle_group_id,
                                adds_value=(payload["qty"] or 0) > old_qty)
    if line.bundle_group_id and "order_item_id" in payload and payload["order_item_id"] != line.order_item_id:
        raise HTTPException(409, "A kit's lines stay with the kit. Move the whole kit, or remove this line and add it again.")
    for k, v in payload.items():
        setattr(line, k, v)
    # Stock follows the qty change for product lines on real orders (Orderry).
    if (line.kind == "product" and line.ref_id and not order.is_estimate
            and "qty" in payload and (line.qty or 0) != old_qty):
        delta = (line.qty or 0) - old_qty
        if delta > 0:
            perms = {}
            if current.role_id:
                from backend.models import Role
                from backend.services.permissions import effective_permissions as _eff
                role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
                perms = _eff(role) if role else {c: False for c in _all_codes()}
            await _maybe_deduct(db, order, line, delta, perms, current)
        else:
            dec = -delta
            from_pending = min(dec, line.pending_stock or 0)
            line.pending_stock = (line.pending_stock or 0) - from_pending
            physical = dec - from_pending
            if physical > 0:
                await _reverse_fulfillment(db, order, line.ref_id, physical)
    # 144hd — discount-approval gate on edit
    await _guard_discount_approval(db, current.org_id, await _order_perms(db, current),
                                   line.discount, line.discount_type, is_owner=not current.role_id,
                                   base=(line.qty or 0) * (line.unit_price or 0))
    # 144ha — margin-floor guardrail also on edit (can't cheap-add then discount here)
    from backend.routers.pricing import evaluate_line_margin
    _mg = await evaluate_line_margin(
        db, current.org_id, kind=line.kind, ref_id=line.ref_id, unit_price=line.unit_price or 0,
        qty=line.qty or 0, discount=line.discount or 0, discount_type=line.discount_type or "pct",
        cost_price=line.cost_price)
    if _mg["blocked"]:
        raise HTTPException(409, f"Margin {_mg['margin_pct']}% is below the {_mg['floor']}% floor. "
                                 f"Lower the discount or raise the price.")
    tmp = LineIn(line_type=line.line_type, description=line.description or "", qty=line.qty,
                 unit_price=line.unit_price, cost_price=line.cost_price or 0,
                 discount=line.discount or 0, discount_type=line.discount_type or "pct",
                 tax_rate=line.tax_rate or 0, tax_mode=line.tax_mode or "imposed")
    line.total = _calc_line(tmp)
    if line.bundle_group_id and line.bundle_price_locked is not None:
        # Any edit re-shares the locked price, so its last-cent correction isn't lost.
        change = (f"{line.description}: qty {old_qty:g} → {line.qty:g}" if (line.qty or 0) != old_qty
                  else f"{line.description} edited")
        await _respread_kit(db, order_id, line.bundle_group_id, actor=current, change=change)
    await _recalc_order(db, order)
    await db.commit()
    resp = {"ok": True, "total": line.total, "order_total": order.total}
    if _mg["below_floor"] and _mg["guard"] == "warn":
        resp["margin_warning"] = f"Low margin: {_mg['margin_pct']}% (floor {_mg['floor']}%)."
    return resp


@router.delete("/{order_id}/lines/{line_id}")
async def delete_line(
    order_id: str,
    line_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Remove a line; return any deducted stock (Orderry)."""
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_editable(db, order, current)
    line = (await db.execute(select(OrderLine).where(OrderLine.id == line_id, OrderLine.order_id == order_id))).scalar_one_or_none()
    if not line:
        raise HTTPException(404, "Line not found")
    if line.bundle_group_id:
        await _guard_kit_change(db, current, order_id, line.bundle_group_id, adds_value=False)
    # return previously-deducted stock for product lines (incl. serials + cost layers)
    if line.kind == "product" and line.ref_id and not order.is_estimate:
        deducted = (line.qty or 0) - (line.pending_stock or 0)
        if deducted > 0:
            await _reverse_fulfillment(db, order, line.ref_id, deducted)
    _prev_total = float(order.total or 0)
    _kit = line.bundle_group_id
    await db.delete(line)
    await db.flush()
    if _kit:
        await _respread_kit(db, order_id, _kit, actor=current, change=f"Removed {line.description}")
    await _recalc_order(db, order)
    await _log_amount_change(db, current.org_id, order, _prev_total)
    await db.commit()
    return {"ok": True, "order_total": order.total}
from sqlalchemy import or_ as _or

from backend.models import SavedFilter
from backend.services.money import q2, q4


class SavedFilterIn(BaseModel):
    name: str
    params: dict
    is_shared: bool = False

@router.get("/filters/saved")
async def list_saved_filters(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(
        select(SavedFilter).where(
            SavedFilter.org_id == current.org_id,
            _or(SavedFilter.employee_id == current.id, SavedFilter.is_shared == True),  # noqa: E712
        ).order_by(SavedFilter.name)
    )).scalars().all()
    return {"items": [
        {"id": f.id, "name": f.name, "params": f.params, "is_shared": f.is_shared,
         "mine": f.employee_id == current.id}
        for f in rows
    ]}

@router.post("/filters/saved", status_code=201)
async def create_saved_filter(
    data: SavedFilterIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.is_shared:
        await assert_perm(db, current, "can_manage_shared_filters")
    f = SavedFilter(org_id=current.org_id, employee_id=current.id,
                    name=data.name, params=data.params, is_shared=data.is_shared)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return {"id": f.id, "name": f.name, "params": f.params, "is_shared": f.is_shared, "mine": True}

@router.delete("/filters/saved/{filter_id}")
async def delete_saved_filter(
    filter_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    f = (await db.execute(select(SavedFilter).where(
        SavedFilter.id == filter_id, SavedFilter.org_id == current.org_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "Filter not found")
    if f.employee_id != current.id and not f.is_shared:
        raise HTTPException(403, "Not allowed")
    if f.is_shared:
        await assert_perm(db, current, "can_manage_shared_filters")
    await db.delete(f)
    await db.commit()
    return {"ok": True}


# ── 144ig — client e-signature certificates (staff side) ─────────────────────
@router.get("/{order_id}/signatures")
async def list_order_signatures(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Signed-approval records for this order (estimate + work order), newest first."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    rows = (await db.execute(select(DocumentSignature).where(
        DocumentSignature.order_id == order_id).order_by(DocumentSignature.signed_at.desc()))).scalars().all()
    return {"items": [{
        "id": s.id, "doc_type": s.doc_type, "decision": s.decision, "signer_name": s.signer_name,
        "signed_at": s.signed_at.isoformat() if s.signed_at else None,
        "doc_total": s.doc_total, "currency_code": s.currency_code,
        "signer_ip": s.signer_ip, "doc_hash": s.doc_hash,
        "has_drawn": bool(s.signature_png),
    } for s in rows]}


@router.get("/{order_id}/certificate.pdf")
async def order_certificate(
    order_id: str,
    signature_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Download the signed-approval certificate PDF (latest signature, or a specific one)."""
    from fastapi import Response
    from backend.services.signcert import build_certificate
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    q = select(DocumentSignature).where(DocumentSignature.order_id == order_id)
    if signature_id:
        q = q.where(DocumentSignature.id == signature_id)
    sig = (await db.execute(q.order_by(DocumentSignature.signed_at.desc()))).scalars().first()
    if not sig:
        raise HTTPException(404, "No signed document for this order")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    pdf = build_certificate(sig, order, org)
    fname = f"approval-{order.order_number or order_id[:8]}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{order_id}/comeback")
async def comeback_check(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_comeback_alerts")),
):
    """Comeback Shield (144bl), first slice: is this device back soon after a prior visit? Flags a
    likely comeback so the shop double-checks before promising. Window = 45 days before this order."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if not order.device_id:
        return {"is_comeback": False, "count": 0}
    ref = order.created_at or datetime.utcnow()
    window_start = ref - timedelta(days=45)
    priors = (await db.execute(
        select(Order).where(
            Order.org_id == current.org_id, Order.device_id == order.device_id,
            Order.id != order.id, Order.created_at <= ref, Order.created_at >= window_start)
        .order_by(Order.created_at.desc()))).scalars().all()
    if not priors:
        return {"is_comeback": False, "count": 0}
    last = priors[0]
    days = (ref - last.created_at).days if last.created_at else None
    return {"is_comeback": True, "count": len(priors), "days_since": days,
            "prior_order_id": last.id, "prior_order_number": last.order_number}


class ThreadMsgIn(BaseModel):
    body: str


@router.get("/{order_id}/thread")
async def order_thread(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144cq — the customer conversation for a work order: staff client-visible messages merged with
    the customer's replies, chronologically. Reading it clears the unread badge."""
    from backend.models import OrderComment, EventFeedItem
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    comments = (await db.execute(select(OrderComment).where(
        OrderComment.order_id == order.id, OrderComment.visible_to_client == True))).scalars().all()  # noqa: E712
    names = {}
    ids = {c.author_id for c in comments if c.author_id}
    if ids:
        for e in (await db.execute(select(Employee).where(Employee.id.in_(ids)))).scalars().all():
            names[e.id] = e.name
    msgs = [{"id": c.id, "from": "staff", "who": names.get(c.author_id, "Staff"),
             "body": c.body, "at": c.created_at.isoformat()} for c in comments]
    evs = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.org_id == current.org_id, EventFeedItem.entity_type == "order",
        EventFeedItem.entity_id == order.id, EventFeedItem.item_type == "client_message"))).scalars().all()
    msgs += [{"id": e.id, "from": "customer", "who": e.author_name or "Customer",
              "body": e.body, "at": e.created_at.isoformat()} for e in evs]
    msgs.sort(key=lambda m: m["at"])
    if order.client_msg_unread:
        order.client_msg_unread = 0
        await db.commit()
    return {"items": msgs}


@router.post("/{order_id}/thread", status_code=201)
async def order_thread_post(
    order_id: str,
    data: ThreadMsgIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Staff posts a message the customer sees on their status page (a client-visible comment)."""
    from backend.models import OrderComment
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(400, "Message is empty")
    c = OrderComment(order_id=order.id, author_id=current.id, body=body[:2000], visible_to_client=True)
    db.add(c)
    await db.commit()
    return {"id": c.id, "from": "staff", "who": current.name, "body": c.body, "at": c.created_at.isoformat()}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
