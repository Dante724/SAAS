"""Public REST API v1 (Phase 2B, hardened in apih1).

Authenticated with a public API key (Authorization: Bearer rok_...). Org scope and
scopes (read / write / costs:read) come from the key. Responses use a consistent envelope:
  list:   {"data": [...], "page": n, "page_size": m, "total": t}
  single: {"data": {...}}
Covered resources: contacts, orders, estimates, products, appointments, invoices, credit notes,
time-entries. Reads need 'read', writes need 'write' (which implies read), and cost fields
appear only for keys holding 'costs:read'.

The machine-readable contract is GET /api/v1/openapi.json (served in production too, no key
needed). It is built from the routes in this file only, so internal routes never leak into it.
"""
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator
from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    ApiIdempotencyKey,
    Appointment,
    Client,
    Employee,
    Invoice,
    Location,
    Order,
    Part,
    Service,
    TimeEntry,
)
from backend.services import audit
from backend.services.apikeys import ApiPrincipal, require_scope
from backend.services.dates import parse_dt, parse_wall_dt
from backend.services.invoicing import balance_due as _inv_balance
from backend.services.numbering import next_number

# operationId = "v1_" + the handler name (readable ids for client generators instead of the
# FastAPI default "list_contacts_api_v1_contacts_get"; the prefix keeps them unique app-wide).
router = APIRouter(prefix="/api/v1", tags=["public-api-v1"],
                   generate_unique_id_function=lambda route: f"v1_{route.name}")

# v1 needs a broad scope: a key narrowed to one resource ("inquiries:read") is a v2 key.
READ = Depends(require_scope("read"))
WRITE = Depends(require_scope("write"))

API_VERSION = "1.0"
MAX_PAGE = 10_000
MAX_PAGE_SIZE = 100
IDEMPOTENCY_TTL = timedelta(hours=24)

# ── shared parameters ────────────────────────────────────────────────────────
_PAGE_DESC = f"1-based page number (1–{MAX_PAGE})."
_SIZE_DESC = f"Items per page (1–{MAX_PAGE_SIZE})."
_IDEM_DESC = ("Optional. A unique value (for example a UUID) for this write. Retrying with the same "
              "key and the same body within 24 hours returns the original response instead of "
              "creating a duplicate; the same key with a different body is refused with 422.")


def _page_q():
    return Query(1, ge=1, le=MAX_PAGE, description=_PAGE_DESC)


def _size_q():
    return Query(25, ge=1, le=MAX_PAGE_SIZE, description=_SIZE_DESC)


def _idem_h():
    return Header(None, alias="Idempotency-Key", min_length=1, max_length=255, description=_IDEM_DESC)


def _page(items, total, page, size):
    return {"data": items, "page": page, "page_size": size, "total": total}


# ── documented response shapes (OpenAPI only; handlers return plain dicts) ──
class ErrorOut(BaseModel):
    detail: Any = Field(..., description="A message, or for 422 a list of field errors.",
                        examples=["Contact not found"])


class ContactOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "6f1c…", "name": "Dana Whitfield", "type": "person", "phone": "+1 555 0100",
        "email": "dana@example.com", "address": "12 High St", "loyalty_tier": None, "balance": 0.0,
        "created_at": "2026-10-20T14:03:11"}]})
    id: str
    name: str
    type: Literal["person", "organization"] = Field(..., description="person or organization")
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    loyalty_tier: Optional[str] = None
    balance: Optional[float] = Field(None, description="What the contact owes (positive) or holds in credit.")
    created_at: Optional[str] = Field(None, description="ISO 8601, UTC.")


class OrderOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "a83d…", "number": "WO-1042", "status": "new", "is_estimate": False, "client_id": "6f1c…",
        "location_id": "2b9e…", "assigned_to_id": None, "complaint": "Cracked screen",
        "labour_total": 0.0, "parts_total": 0.0, "total": 0.0, "due_at": None, "scheduled_for": None,
        "created_at": "2026-10-20T14:05:42"}]})
    id: str
    number: str
    status: str = Field(..., description="The shop's own status key.")
    is_estimate: bool
    client_id: Optional[str] = None
    location_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    complaint: Optional[str] = None
    labour_total: float
    parts_total: float
    total: float
    due_at: Optional[str] = None
    scheduled_for: Optional[str] = Field(None, description="Shop wall-clock time, ISO 8601 without offset.")
    created_at: Optional[str] = None


class ProductOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "c51a…", "sku": "SCR-IP13", "mpn": None, "name": "iPhone 13 screen", "brand": "Apple",
        "category": "Screens", "sell_price": 129.0, "uom": "pcs", "barcode": None, "active": True}]})
    id: str
    sku: Optional[str] = None
    mpn: Optional[str] = None
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    sell_price: Optional[float] = None
    cost_price: Optional[float] = Field(None, description="Only present for keys with the costs:read scope.")
    uom: Optional[str] = None
    barcode: Optional[str] = None
    active: Optional[bool] = None


class AppointmentOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "e02f…", "client_id": None, "client_name": "Dana Whitfield", "service_id": None,
        "employee_id": None, "status": "confirmed", "scheduled_for": "2026-10-28T10:30:00",
        "duration_min": 60, "note": "Drop-off"}]})
    id: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    service_id: Optional[str] = None
    employee_id: Optional[str] = None
    status: str
    scheduled_for: Optional[str] = Field(None, description="Shop wall-clock time, ISO 8601 without offset.")
    duration_min: Optional[int] = None
    note: Optional[str] = None


class InvoiceOut(BaseModel):
    id: str
    number: Optional[str] = None
    order_id: Optional[str] = None
    client_id: Optional[str] = None
    status: str
    subtotal: float
    tax: float
    total: float
    amount_paid: float
    credited: float
    balance_due: float
    issued_at: Optional[str] = None


class CreditNoteLineOut(BaseModel):
    description: Optional[str] = None
    qty: float
    unit_price: float
    tax_rate: Optional[float] = None
    net: float
    tax: float
    total: float


class CreditNoteOut(BaseModel):
    id: str
    number: Optional[str] = None
    status: str
    client_id: Optional[str] = None
    invoice_id: Optional[str] = None
    reason: Optional[str] = None
    subtotal: float
    tax: float
    total: float
    issued_at: Optional[str] = None
    canceled_at: Optional[str] = None
    lines: list[CreditNoteLineOut]


class TimeEntryOut(BaseModel):
    id: str
    employee_id: str
    order_id: Optional[str] = None
    category: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_sec: Optional[int] = None
    billable: Optional[bool] = None
    state: Optional[str] = None
    cost_rate: Optional[float] = Field(None, description="Only present for keys with the costs:read scope.")
    billable_rate: Optional[float] = Field(None, description="Only present for keys with the costs:read scope.")


class DiscoveryOut(BaseModel):
    version: str
    org_id: str
    scopes: list[str]
    resources: dict[str, str]
    docs: str
    openapi: str


def _one(name: str, item: type) -> type:
    return create_model(name, data=(item, ...))


def _many(name: str, item: type) -> type:
    return create_model(name, data=(list[item], ...), page=(int, ...), page_size=(int, ...), total=(int, ...))


_ERRORS = {
    401: {"model": ErrorOut, "description": "Missing, invalid, expired or revoked API key."},
    403: {"model": ErrorOut, "description": "The key lacks the scope, the shop is suspended, or the call was not over HTTPS."},
    429: {"model": ErrorOut, "description": "Rate limit exceeded. See Retry-After."},
}
_NOT_FOUND = {404: {"model": ErrorOut, "description": "Not found in this shop."}}
_WRITE_ERRORS = {
    **_ERRORS,
    404: {"model": ErrorOut, "description": "A referenced id does not exist in this shop."},
    409: {"model": ErrorOut, "description": "Conflicts with existing data, or the same Idempotency-Key is still being processed."},
    422: {"model": ErrorOut, "description": "Invalid body, or an Idempotency-Key reused with a different body."},
}


def _doc(model: type, errors: dict, status: int = 200) -> dict:
    return {status: {"model": model}, **errors}


# ── discovery ────────────────────────────────────────────────────────────────
@router.get("", summary="Discover the API", responses=_doc(_one("DiscoveryResponse", DiscoveryOut), _ERRORS),
            description="Which shop the key belongs to, its scopes, and where each resource lives.")
async def index(principal: ApiPrincipal = READ):
    base = "/api/v1"
    return {"data": {
        "version": API_VERSION,
        "org_id": principal.org_id,
        "scopes": sorted(principal.scopes),
        "resources": {
            "contacts": f"{base}/contacts", "orders": f"{base}/orders",
            "estimates": f"{base}/estimates", "products": f"{base}/products",
            "appointments": f"{base}/appointments", "invoices": f"{base}/invoices", "credit_notes": f"{base}/credit-notes",
            "time_entries": f"{base}/time-entries"},
        "docs": f"{base}/openapi.json",
        "openapi": f"{base}/openapi.json",
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


def _product(p: Part, costs: bool = False) -> dict:
    out = {"id": p.id, "sku": p.sku, "mpn": p.mpn, "name": p.name, "brand": p.brand, "category": p.category,
           "sell_price": p.sell_price, "uom": p.uom, "barcode": p.barcode, "active": p.active}
    if costs:   # A2: cost fields only for keys holding costs:read
        out["cost_price"] = p.cost_price
    return out


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
            "lines": [{"description": ln.description, "qty": ln.qty, "unit_price": ln.unit_price,
                       "tax_rate": ln.tax_rate, "net": ln.net, "tax": ln.tax, "total": ln.total} for ln in c.lines]}


def _time_entry(e: TimeEntry, costs: bool = False) -> dict:
    out = {"id": e.id, "employee_id": e.employee_id, "order_id": e.order_id, "category": e.category,
           "started_at": e.started_at.isoformat() if e.started_at else None,
           "ended_at": e.ended_at.isoformat() if e.ended_at else None,
           "duration_sec": e.duration_sec, "billable": e.billable, "state": e.state}
    if costs:   # A2: pay and billing rates only for keys holding costs:read
        out["cost_rate"] = e.cost_rate
        out["billable_rate"] = e.billable_rate
    return out


# ── helpers ──────────────────────────────────────────────────────────────────
def _bounds(page: int, size: int) -> tuple[int, int]:
    """A1: clamp again here so a caller of _list can never ask for an unbounded page."""
    return min(max(1, int(page)), MAX_PAGE), min(max(1, int(size)), MAX_PAGE_SIZE)


async def _list(db, model, org_id, ser, page, size, extra=None, order_col=None):
    page, size = _bounds(page, size)
    q = select(model).where(model.org_id == org_id)
    cq = select(func.count()).select_from(model).where(model.org_id == org_id)
    if extra is not None:
        q = q.where(extra)
        cq = cq.where(extra)
    total = (await db.execute(cq)).scalar() or 0
    oc = order_col if order_col is not None else model.created_at
    rows = (await db.execute(q.order_by(desc(oc), desc(model.id)).limit(size).offset((page - 1) * size))).scalars().all()
    return _page([ser(r) for r in rows], total, page, size)


async def _must_exist(db: AsyncSession, model, org_id: str, rid: Optional[str], label: str) -> None:
    """A8: an id in a body must name a record in the caller's own shop (else 404, not a 500)."""
    if rid is None:
        return
    found = (await db.execute(select(model.id).where(model.id == rid, model.org_id == org_id))).scalar()
    if not found:
        raise HTTPException(404, f"{label} not found")


async def _commit(db: AsyncSession) -> None:
    """A8: a constraint failure the checks above didn't anticipate is the caller's 422, not a 500."""
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(422, "The request refers to a record that doesn't exist or conflicts with existing data")


async def _audit(db, principal: ApiPrincipal, action: str, entity_type: str, entity_id: str, **extra) -> None:
    """A9: every API write is in the shop's audit log, attributed to the key."""
    await audit.record(db, org_id=principal.org_id, actor_id=None, action=action,
                       entity_type=entity_type, entity_id=entity_id,
                       diff={"via_api_key": principal.key_id, **extra})


async def _idempotent(db: AsyncSession, principal: ApiPrincipal, idem_key: Optional[str], route: str,
                      payload: BaseModel, run: Callable[[], Awaitable[dict]], status: int = 201,
                      stored: Optional[Callable[[dict], dict]] = None) -> JSONResponse:
    """A5: Idempotency-Key on writes. The first request claims (org, key, Idempotency-Key) under a
    unique constraint, runs, and stores its response; a retry with the same body replays it, a
    different body is a 422, and a retry while the first is still running is a 409. A failed
    request releases its claim so it can be retried. Claims expire after 24 hours.

    `stored`, when given, is what gets kept for replays (v2 webhooks: the signing secret is shown
    once and never written to the replay store)."""
    if idem_key is None:
        return JSONResponse(status_code=status, content=jsonable_encoder(await run()))
    body_hash = hashlib.sha256(json.dumps({"route": route, "body": payload.model_dump(mode="json")},
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    now = datetime.utcnow()
    await db.execute(delete(ApiIdempotencyKey).where(
        ApiIdempotencyKey.org_id == principal.org_id, ApiIdempotencyKey.created_at < now - IDEMPOTENCY_TTL))

    def _find():
        return select(ApiIdempotencyKey).where(
            ApiIdempotencyKey.org_id == principal.org_id, ApiIdempotencyKey.key_id == principal.key_id,
            ApiIdempotencyKey.idem_key == idem_key)

    claim = ApiIdempotencyKey(org_id=principal.org_id, key_id=principal.key_id, idem_key=idem_key,
                              route=route, request_hash=body_hash, created_at=now)
    db.add(claim)
    try:
        await db.commit()
        claim_id = claim.id
    except IntegrityError:
        await db.rollback()
        claim_id = None
    if claim_id is None:
        prior = (await db.execute(_find().execution_options(populate_existing=True))).scalar_one_or_none()
        if prior is None:
            raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
        if prior.request_hash != body_hash:
            raise HTTPException(422, "This Idempotency-Key was already used with a different request")
        if prior.status_code is None or prior.response_body is None:
            raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
        return JSONResponse(status_code=prior.status_code, content=json.loads(prior.response_body),
                            headers={"Idempotency-Key": idem_key, "Idempotent-Replayed": "true"})
    try:
        body = jsonable_encoder(await run())
    except BaseException:
        await db.rollback()
        await db.execute(delete(ApiIdempotencyKey).where(ApiIdempotencyKey.id == claim_id))
        await db.commit()
        raise
    await db.execute(update(ApiIdempotencyKey).where(ApiIdempotencyKey.id == claim_id).values(
        status_code=status, response_body=json.dumps(stored(body) if stored else body)))
    await db.commit()
    return JSONResponse(status_code=status, content=body, headers={"Idempotency-Key": idem_key})


# ── input models (A6 / A7) ───────────────────────────────────────────────────
_ID = Field(None, min_length=1, max_length=64)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _In(BaseModel):
    # Unknown fields are refused, so a typo ("complain") is an error instead of silently dropped.
    model_config = ConfigDict(extra="forbid")


class ContactIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "name": "Dana Whitfield", "phone": "+1 555 0100", "email": "dana@example.com",
        "address": "12 High St", "client_type": "person"}]})
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=254, description="A valid email address.")
    address: Optional[str] = Field(None, max_length=500)
    client_type: Literal["person", "organization"] = "person"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v


class OrderIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "location_id": "2b9e…", "client_id": "6f1c…", "complaint": "Cracked screen", "duration_min": 60}]})
    client_id: Optional[str] = _ID
    location_id: str = Field(..., min_length=1, max_length=64)
    complaint: Optional[str] = Field(None, max_length=5000)
    assigned_to_id: Optional[str] = _ID
    duration_min: int = Field(60, ge=5, le=1440, description="Planned minutes (5–1440).")


class AppointmentIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "location_id": "2b9e…", "scheduled_for": "2026-10-28T10:30:00", "client_name": "Dana Whitfield",
        "duration_min": 60, "note": "Drop-off"}]})
    location_id: str = Field(..., min_length=1, max_length=64)
    scheduled_for: str = Field(..., min_length=1, max_length=64,
                               description="ISO 8601. With an offset it is converted to the location's clock; "
                                           "without one it is taken as the shop's wall-clock time.")
    client_name: Optional[str] = Field(None, max_length=200)
    client_id: Optional[str] = _ID
    service_id: Optional[str] = _ID
    employee_id: Optional[str] = _ID
    duration_min: int = Field(60, ge=5, le=1440, description="Minutes (5–1440).")
    note: Optional[str] = Field(None, max_length=5000)


class TimeEntryIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "employee_id": "9d0a…", "order_id": "a83d…", "started_at": "2026-10-20T09:00:00Z",
        "duration_min": 45, "billable": True}]})
    employee_id: str = Field(..., min_length=1, max_length=64)
    order_id: Optional[str] = _ID
    started_at: str = Field(..., min_length=1, max_length=64, description="ISO 8601.")
    duration_min: float = Field(..., gt=0, le=1440, description="Minutes, more than 0 and at most 1440 (one day).")
    billable: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=5000)


# ── contacts ─────────────────────────────────────────────────────────────────
@router.get("/contacts", summary="List contacts", responses=_doc(_many("ContactList", ContactOut), _ERRORS),
            description="Newest first.")
async def list_contacts(page: int = _page_q(), page_size: int = _size_q(),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Client, principal.org_id, _contact, page, page_size)


@router.get("/contacts/{cid}", summary="Get a contact",
            responses=_doc(_one("ContactResponse", ContactOut), {**_ERRORS, **_NOT_FOUND}))
async def get_contact(cid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    c = (await db.execute(select(Client).where(Client.id == cid, Client.org_id == principal.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    return {"data": _contact(c)}


@router.post("/contacts", status_code=201, summary="Create a contact",
             responses=_doc(_one("ContactCreated", ContactOut), _WRITE_ERRORS, 201),
             description="Needs the write scope. Send an Idempotency-Key to make retries safe.")
async def create_contact(data: ContactIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE,
                         idempotency_key: Optional[str] = _idem_h()):
    async def run():
        c = Client(org_id=principal.org_id, name=data.name, phone=data.phone, email=data.email,
                   address=data.address, client_type=data.client_type)
        db.add(c)
        await db.flush()
        await _audit(db, principal, "client.create", "client", c.id, name=c.name)
        await _commit(db)
        await db.refresh(c)
        return {"data": _contact(c)}
    return await _idempotent(db, principal, idempotency_key, "POST /contacts", data, run)


# ── orders + estimates ───────────────────────────────────────────────────────
@router.get("/orders", summary="List work orders", responses=_doc(_many("OrderList", OrderOut), _ERRORS),
            description="Work orders only (estimates are under /estimates). Newest first.")
async def list_orders(status: Optional[str] = Query(None, max_length=40, description="Exact status key."),
                      updated_since: Optional[str] = Query(
                          None, min_length=1, max_length=64,
                          description="Only orders changed at or after this time. ISO 8601; an offset is "
                                      "converted to UTC, without one it is taken as UTC."),
                      page: int = _page_q(), page_size: int = _size_q(),
                      db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    extra = (Order.is_estimate == False)  # noqa: E712
    if status:
        extra = extra & (Order.status == status)
    if updated_since is not None:
        try:
            since = datetime.fromisoformat(updated_since.strip())
        except ValueError:
            raise HTTPException(422, "updated_since must be an ISO 8601 date or datetime, like 2026-10-25T18:00:00Z")
        if since.tzinfo:
            since = since.astimezone(timezone.utc).replace(tzinfo=None)
        extra = extra & (Order.updated_at >= since)
    return await _list(db, Order, principal.org_id, _order, page, page_size, extra=extra)


@router.get("/orders/{oid}", summary="Get a work order",
            responses=_doc(_one("OrderResponse", OrderOut), {**_ERRORS, **_NOT_FOUND}))
async def get_order(oid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    o = (await db.execute(select(Order).where(Order.id == oid, Order.org_id == principal.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return {"data": _order(o)}


async def _initial_status(db: AsyncSession, org_id: str) -> str:
    """The shop's default work-order status, the same rule the app's own create uses."""
    from backend.models import OrderStatus
    dflt = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.is_default == True, OrderStatus.active == True,  # noqa: E712
        OrderStatus.record_type == "work_order"))).scalars().first()
    return dflt.key if dflt else "new"


@router.post("/orders", status_code=201, summary="Create a work order",
             responses=_doc(_one("OrderCreated", OrderOut), _WRITE_ERRORS, 201),
             description="Needs the write scope. Starts in the shop's default status. Every id must "
                         "belong to this shop (else 404). Fires the order.created webhook.")
async def create_order(data: OrderIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE,
                       idempotency_key: Optional[str] = _idem_h()):
    async def run():
        from backend.models import OrderStatusLog
        from backend.routers.orders import unique_order_code, unique_public_token
        from backend.services import currency as _cur
        org = principal.org_id
        from backend.services.billing import assert_can_add_job
        await assert_can_add_job(db, org)   # Solo: new jobs a month (a replayed key returns the first reply)
        await _must_exist(db, Location, org, data.location_id, "Location")
        await _must_exist(db, Client, org, data.client_id, "Contact")
        await _must_exist(db, Employee, org, data.assigned_to_id, "Employee")
        status = await _initial_status(db, org)
        cinfo = await _cur.resolve_for_client(db, org, data.client_id, None)
        o = Order(org_id=org, order_number=await next_number(db, org, "order"),
                  barcode=await unique_order_code(db), public_token=await unique_public_token(db),
                  client_id=data.client_id, location_id=data.location_id, complaint=data.complaint,
                  assigned_to_id=data.assigned_to_id, duration_min=data.duration_min, status=status,
                  currency_code=cinfo["code"], fx_rate=cinfo["rate"])
        db.add(o)
        await db.flush()
        db.add(OrderStatusLog(order_id=o.id, from_status=None, to_status=status, changed_by_id=None))
        await _audit(db, principal, "order.create", "order", o.id, order_number=o.order_number)
        from backend.routers.feed import log_system_event
        await log_system_event(db, org, "order", o.id, "Work order created through the API")
        await _commit(db)
        await db.refresh(o)
        try:
            from backend.routers.checklists import auto_attach_for_status
            await auto_attach_for_status(db, o, o.status, author_id=None)
            await db.commit()
        except Exception:  # noqa: BLE001 — never block creation on a checklist
            await db.rollback()
        try:
            from backend.services.webhooks import emit_event
            # Same payload as an order made in the app; "number" kept for existing receivers.
            await emit_event(db, org, "order.created", {"id": o.id, "number": o.order_number,
                                                        "order_number": o.order_number,
                                                        "status": o.status, "total": o.total})
            await db.commit()   # the delivery row is written after the record's own commit
        except Exception:  # noqa: BLE001
            pass
        await db.refresh(o)
        return {"data": _order(o)}
    return await _idempotent(db, principal, idempotency_key, "POST /orders", data, run)


@router.get("/estimates", summary="List estimates", responses=_doc(_many("EstimateList", OrderOut), _ERRORS))
async def list_estimates(page: int = _page_q(), page_size: int = _size_q(),
                         db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Order, principal.org_id, _order, page, page_size,
                       extra=(Order.is_estimate == True))  # noqa: E712


@router.get("/estimates/{eid}", summary="Get an estimate",
            responses=_doc(_one("EstimateResponse", OrderOut), {**_ERRORS, **_NOT_FOUND}))
async def get_estimate(eid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    o = (await db.execute(select(Order).where(
        Order.id == eid, Order.org_id == principal.org_id, Order.is_estimate == True))).scalar_one_or_none()  # noqa: E712
    if not o:
        raise HTTPException(404, "Estimate not found")
    return {"data": _order(o)}


# ── products / inventory ─────────────────────────────────────────────────────
@router.get("/products", summary="List products", responses=_doc(_many("ProductList", ProductOut), _ERRORS),
            description="cost_price is included only for keys with the costs:read scope.")
async def list_products(page: int = _page_q(), page_size: int = _size_q(),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    costs = principal.can_see_costs
    return await _list(db, Part, principal.org_id, lambda p: _product(p, costs), page, page_size)


@router.get("/products/{pid}", summary="Get a product",
            responses=_doc(_one("ProductResponse", ProductOut), {**_ERRORS, **_NOT_FOUND}))
async def get_product(pid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    p = (await db.execute(select(Part).where(Part.id == pid, Part.org_id == principal.org_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Product not found")
    return {"data": _product(p, principal.can_see_costs)}


# ── appointments ─────────────────────────────────────────────────────────────
@router.get("/appointments", summary="List appointments",
            responses=_doc(_many("AppointmentList", AppointmentOut), _ERRORS),
            description="Latest scheduled first.")
@router.get("/bookings", include_in_schema=False)   # old name the v1 docs used to promise
async def list_appointments(page: int = _page_q(), page_size: int = _size_q(),
                            db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Appointment, principal.org_id, _appointment, page, page_size,
                       order_col=Appointment.scheduled_for)


_BUSY = ("booked", "confirmed")


@router.post("/appointments", status_code=201, summary="Create an appointment",
             responses=_doc(_one("AppointmentCreated", AppointmentOut), _WRITE_ERRORS, 201),
             description="Needs the write scope. A time with an offset is converted to the location's "
                         "clock. An employee can't be double-booked (409). Fires appointment.created.")
@router.post("/bookings", status_code=201, include_in_schema=False)   # old name, kept as an alias
async def create_appointment(data: AppointmentIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE,
                             idempotency_key: Optional[str] = _idem_h()):
    async def run():
        org = principal.org_id
        await _must_exist(db, Location, org, data.location_id, "Location")
        await _must_exist(db, Client, org, data.client_id, "Contact")
        await _must_exist(db, Service, org, data.service_id, "Service")
        await _must_exist(db, Employee, org, data.employee_id, "Employee")
        # A13: the shop's wall clock for that location, like the app's own appointment create.
        start = await parse_wall_dt(db, org, data.location_id, data.scheduled_for, "scheduled_for")
        end = start + timedelta(minutes=data.duration_min)
        if data.employee_id:
            others = (await db.execute(select(Appointment).where(
                Appointment.org_id == org, Appointment.employee_id == data.employee_id,
                Appointment.status.in_(_BUSY)))).scalars().all()
            for o in others:
                o_end = o.scheduled_for + timedelta(minutes=o.duration_min or 60)
                if start < o_end and o.scheduled_for < end:
                    raise HTTPException(409, "That employee already has an appointment "
                                             f"{o.scheduled_for.strftime('%H:%M')}-{o_end.strftime('%H:%M')}")
        b = Appointment(org_id=org, location_id=data.location_id, scheduled_for=start,
                        client_name=data.client_name, client_id=data.client_id, service_id=data.service_id,
                        employee_id=data.employee_id, duration_min=data.duration_min, note=data.note,
                        status="confirmed")
        db.add(b)
        await db.flush()
        await _audit(db, principal, "appointment.create", "appointment", b.id,
                     scheduled_for=start.isoformat())
        try:
            await db.commit()
        except IntegrityError:   # the same slot was just taken (the app's race guard)
            await db.rollback()
            raise HTTPException(409, "That time slot was just taken — please pick another")
        await db.refresh(b)
        try:
            from backend.services.webhooks import emit_event
            await emit_event(db, org, "appointment.created", {"id": b.id})
            await db.commit()   # the delivery row is written after the record's own commit
        except Exception:  # noqa: BLE001
            pass
        await db.refresh(b)
        return {"data": _appointment(b)}
    return await _idempotent(db, principal, idempotency_key, "POST /appointments", data, run)


# ── invoices (read) ──────────────────────────────────────────────────────────
@router.get("/invoices", summary="List invoices", responses=_doc(_many("InvoiceList", InvoiceOut), _ERRORS))
async def list_invoices(page: int = _page_q(), page_size: int = _size_q(),
                        db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    return await _list(db, Invoice, principal.org_id, _invoice, page, page_size)


@router.get("/invoices/{iid}", summary="Get an invoice",
            responses=_doc(_one("InvoiceResponse", InvoiceOut), {**_ERRORS, **_NOT_FOUND}))
async def get_invoice(iid: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    i = (await db.execute(select(Invoice).where(Invoice.id == iid, Invoice.org_id == principal.org_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Invoice not found")
    return {"data": _invoice(i)}


# ── credit notes (read) ──────────────────────────────────────────────────────
@router.get("/credit-notes", summary="List credit notes",
            responses=_doc(_many("CreditNoteList", CreditNoteOut), _ERRORS))
async def list_credit_notes(page: int = _page_q(), page_size: int = _size_q(),
                            db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    from sqlalchemy.orm import selectinload

    from backend.models import CreditNote
    page, size = _bounds(page, page_size)
    cond = CreditNote.org_id == principal.org_id
    total = (await db.execute(select(func.count()).select_from(CreditNote).where(cond))).scalar() or 0
    rows = (await db.execute(select(CreditNote).where(cond).options(selectinload(CreditNote.lines))
                             .order_by(desc(CreditNote.created_at), desc(CreditNote.id))
                             .limit(size).offset((page - 1) * size))).scalars().all()
    return _page([_credit_note(r) for r in rows], total, page, size)


@router.get("/credit-notes/{cn_id}", summary="Get a credit note",
            responses=_doc(_one("CreditNoteResponse", CreditNoteOut), {**_ERRORS, **_NOT_FOUND}))
async def get_credit_note(cn_id: str, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    from sqlalchemy.orm import selectinload

    from backend.models import CreditNote
    c = (await db.execute(select(CreditNote).where(CreditNote.id == cn_id, CreditNote.org_id == principal.org_id)
                          .options(selectinload(CreditNote.lines)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Credit note not found")
    return {"data": _credit_note(c)}


# ── time entries ─────────────────────────────────────────────────────────────
@router.get("/time-entries", summary="List time entries",
            responses=_doc(_many("TimeEntryList", TimeEntryOut), _ERRORS),
            description="Voided entries are left out. cost_rate and billable_rate are included only "
                        "for keys with the costs:read scope.")
async def list_time_entries(order_id: Optional[str] = Query(None, max_length=64),
                            employee_id: Optional[str] = Query(None, max_length=64),
                            page: int = _page_q(), page_size: int = _size_q(),
                            db: AsyncSession = Depends(get_db), principal: ApiPrincipal = READ):
    extra = (TimeEntry.state != "void")
    if order_id:
        extra = extra & (TimeEntry.order_id == order_id)
    if employee_id:
        extra = extra & (TimeEntry.employee_id == employee_id)
    costs = principal.can_see_costs
    return await _list(db, TimeEntry, principal.org_id, lambda e: _time_entry(e, costs), page, page_size,
                       extra=extra, order_col=TimeEntry.started_at)


@router.post("/time-entries", status_code=201, summary="Log a time entry",
             responses=_doc(_one("TimeEntryCreated", TimeEntryOut), _WRITE_ERRORS, 201),
             description="Needs the write scope. duration_min must be more than 0 and at most 1440.")
async def create_time_entry(data: TimeEntryIn, db: AsyncSession = Depends(get_db), principal: ApiPrincipal = WRITE,
                            idempotency_key: Optional[str] = _idem_h()):
    async def run():
        org = principal.org_id
        emp = (await db.execute(select(Employee).where(
            Employee.id == data.employee_id, Employee.org_id == org))).scalar_one_or_none()
        if not emp:
            raise HTTPException(404, "Employee not found")
        await _must_exist(db, Order, org, data.order_id, "Order")
        start = parse_dt(data.started_at, "started_at")
        if start.tzinfo is not None:
            from datetime import timezone
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        end = start + timedelta(minutes=data.duration_min)
        cost = emp.hourly_cost_rate or (emp.wage_value if emp.wage_type == "hourly" else 0.0) or 0.0
        e = TimeEntry(org_id=org, employee_id=emp.id, order_id=data.order_id,
                      location_id=emp.location_id, category="job" if data.order_id else "other",
                      started_at=start, ended_at=end, duration_sec=int((end - start).total_seconds()),
                      billable=(data.billable if data.billable is not None else bool(data.order_id)),
                      cost_rate=float(cost), billable_rate=float(emp.billable_rate or cost),
                      note=data.note, source="manual", state="stopped")
        db.add(e)
        await db.flush()
        await _audit(db, principal, "time_entry.create", "time_entry", e.id,
                     employee_id=emp.id, duration_sec=e.duration_sec)
        await _commit(db)
        await db.refresh(e)
        return {"data": _time_entry(e, principal.can_see_costs)}
    return await _idempotent(db, principal, idempotency_key, "POST /time-entries", data, run)


# ── OpenAPI (A14) ────────────────────────────────────────────────────────────
_OPENAPI_DESCRIPTION = """\
The Tekly public API: read and create contacts, work orders, appointments and time entries, and
read estimates, products, invoices and credit notes.

**Authentication.** Send an API key as a bearer token: `Authorization: Bearer rok_…`. Create keys
in Settings → Integrations → API keys. HTTPS only.

**Scopes.** `read` for every GET, `write` for the POSTs (write includes read), and `costs:read` to
receive cost fields (product `cost_price`, time-entry `cost_rate` and `billable_rate`); without it
those fields are left out.

**Envelope.** A list is `{"data": [...], "page": n, "page_size": m, "total": t}` with `page` 1–10000
and `page_size` 1–100 (default 25). A single record is `{"data": {...}}`. Errors are
`{"detail": ...}`.

**Idempotency.** Every POST accepts an `Idempotency-Key` header. A retry with the same key and
body within 24 hours returns the original response (with `Idempotent-Replayed: true`).

**Rate limits.** Each key has its own budget per minute. Responses carry `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; a 429 carries `Retry-After` (seconds).
"""
_openapi_cache: Optional[dict] = None


def openapi_for(api_router: APIRouter, *, version: str, description: str, tag: str, tag_description: str) -> dict:
    """An OpenAPI document for one public router only (internal routes never leak into it)."""
    from fastapi.openapi.utils import get_openapi

    from backend.config import settings
    return get_openapi(
        title="Tekly API", version=version, openapi_version="3.1.0", description=description,
        routes=[r for r in api_router.routes if getattr(r, "include_in_schema", False)],
        servers=[{"url": settings.public_base}],
        tags=[{"name": tag, "description": tag_description}],
    )


def build_openapi() -> dict:
    """The v1-only OpenAPI document (only routes from this router that are in the schema)."""
    global _openapi_cache
    if _openapi_cache is None:
        _openapi_cache = openapi_for(router, version=API_VERSION, description=_OPENAPI_DESCRIPTION,
                                     tag="public-api-v1", tag_description="Tekly public API v1")
    return _openapi_cache


@router.get("/openapi.json", include_in_schema=False)
async def openapi_document():
    """Public, no key needed, served in production too."""
    return JSONResponse(build_openapi(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/docs", include_in_schema=False)
async def docs_redirect():
    # Discovery used to advertise /api/v1/docs, which 404'd. Send old links to the document.
    return RedirectResponse("/api/v1/openapi.json", status_code=307)
