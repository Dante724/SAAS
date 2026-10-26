"""API v2: Lookups (locations, employees), contact search, order updates and status, order lines, invoice detail.

Routes register on backend.routers.v2.router, so they share its auth, limits and OpenAPI.

Same bar as the rest of v2: per-resource scopes (``locations``, ``employees``, ``contacts``,
``orders``, ``invoices``), bounded paging, length limits with unknown fields refused, ids from
another shop are 404, constraint failures 422, Idempotency-Key on POST, every write audited with
``via_api_key``, and cost fields only for keys holding ``costs:read``.

* Employees never expose contact details, pay, commission, tax ids or anything to do with signing in.
* Contact phone search uses the call-matching rules (services/call_contacts): formatting and
  country-code insensitive.
* A status change goes through the app's own path (routers/orders._apply_status_change) with an
  ``ApiActor`` in place of the employee, so every gate and side effect is the app's.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Query
from fastapi.routing import APIRoute
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.config import settings
from backend.db.database import get_db
from backend.models import (
    Client,
    Employee,
    Invoice,
    InvoiceOrder,
    Location,
    Order,
    OrderStatus,
    Organisation,
    Payment,
    Role,
)
from backend.routers import v1, v2
from backend.routers.v1 import _ERRORS, _NOT_FOUND, _WRITE_ERRORS, _In, _audit, _bounds, _idem_h, _idempotent, _must_exist, _page, _page_q, _size_q
from backend.routers.v2 import R, W, _contact, _costs, _paged, router
from backend.services import addresses
from backend.services import leads as lsvc
from backend.services.apikeys import ApiPrincipal

# Discovery (GET /api/v2) lists these too.
v2.RESOURCE_PATHS = tuple(dict.fromkeys(v2.RESOURCE_PATHS + ("locations", "employees", "orders", "invoices",
                                                              "appointments")))
v2._OPENAPI_DESCRIPTION += """
**Records.** Locations and employees (lookups), contact search by phone, email or text, work
orders (read, update, change status through the shop's own rules) and invoices (lines as printed,
balance, payments and refunds). Scope resources: `locations`, `employees`, `contacts`, `orders`,
`invoices`.
"""

_MAX_SCAN = 1000          # phone search: candidates compared in Python at most


def _iso(v) -> Optional[str]:
    return v.isoformat() if v else None


def _since(value: Optional[str], field: str) -> Optional[datetime]:
    """A ``*_since`` filter: ISO 8601 date or datetime. With an offset it's converted to UTC;
    without one it's taken as UTC (Tekly's stored timestamps are naive UTC)."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(422, f"{field} must be an ISO 8601 date or datetime, like 2026-10-25T18:00:00Z")
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _since_q(what: str):
    return Query(None, min_length=1, max_length=64,
                 description=f"Only records {what} at or after this time. ISO 8601, for example "
                             "`2026-10-25T18:00:00Z`. Without an offset it is taken as UTC.")


def _ok(example: dict, errors: dict) -> dict:
    """OpenAPI: a 200 with an example body, plus the error responses."""
    return {200: {"description": "OK", "content": {"application/json": {"example": example}}}, **errors}


_READ_ERRORS = {**_ERRORS, 422: {"model": v1.ErrorOut, "description": "Invalid query parameter."}}
_ONE_ERRORS = {**_ERRORS, **_NOT_FOUND}


# ── locations ────────────────────────────────────────────────────────────────
def _location(loc: Location) -> dict:
    return {"id": loc.id, "name": loc.name, "address": loc.address, "phone": loc.phone,
            "timezone": loc.timezone or "UTC", "active": bool(loc.active)}


_LOCATION_EX = {"id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "name": "Main Street", "address": "12 High St, Springfield",
                "phone": "+1 555 0100", "timezone": "America/New_York", "active": True}


@router.get("/locations", summary="List locations",
            description="The shop's locations (branches). Use an `id` as `location_id` when you create work "
                        "orders or appointments. Scope: `locations:read` (or `read`).",
            responses=_ok({"data": [_LOCATION_EX], "page": 1, "page_size": 25, "total": 1}, _READ_ERRORS))
async def list_locations(active: Optional[bool] = Query(None, description="Only active (`true`) or switched-off (`false`) locations."),
                         page: int = _page_q(), page_size: int = _size_q(),
                         db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("locations")):
    conds = [Location.active == active] if active is not None else []
    rows, total, page, size = await _paged(db, Location, p.org_id, page, page_size, *conds)
    return _page([_location(x) for x in rows], total, page, size)


@router.get("/locations/{lid}", summary="Get a location", responses=_ok({"data": _LOCATION_EX}, _ONE_ERRORS))
async def get_location(lid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("locations")):
    return {"data": _location(await lsvc.own(db, Location, lid, p.org_id, "Location"))}


# ── employees (directory only: never contact details, pay or sign-in data) ──
def _employee(e: Employee, role_name: Optional[str], mode: Optional[str]) -> dict:
    from backend.services.public_name import public_staff_name
    # An allow-list on purpose: email, phone, wage, commission, rates, tax id, PIN, password, MFA
    # and token data are never part of this object.
    return {"id": e.id, "name": e.name, "public_name": public_staff_name(e, mode), "role": role_name,
            "location_id": e.location_id, "active": bool(e.active)}


async def _employees_out(db: AsyncSession, org_id: str, rows: list[Employee]) -> list[dict]:
    role_ids = {e.role_id for e in rows if e.role_id}
    names = dict((await db.execute(select(Role.id, Role.name).where(
        Role.org_id == org_id, Role.id.in_(role_ids)))).all()) if role_ids else {}
    mode = (await db.execute(select(Organisation.public_employee_name).where(Organisation.id == org_id))).scalar()
    return [_employee(e, names.get(e.role_id), mode) for e in rows]


_EMPLOYEE_EX = {"id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10", "name": "Sam Ortega", "public_name": "Sam O.",
                "role": "Technician", "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "active": True}


@router.get("/employees", summary="List employees",
            description="The staff directory: name, the name customers see (`public_name`, from the shop's "
                        "**Name on public pages** setting), role and home location. Never email, phone, pay, "
                        "commission, tax ids or sign-in data. Scope: `employees:read` (or `read`).",
            responses=_ok({"data": [_EMPLOYEE_EX], "page": 1, "page_size": 25, "total": 1}, _READ_ERRORS))
async def list_employees(location_id: Optional[str] = Query(None, min_length=1, max_length=64,
                                                            description="Only employees whose home location is this one."),
                         active: Optional[bool] = Query(None, description="Only active (`true`) or deactivated (`false`) employees."),
                         page: int = _page_q(), page_size: int = _size_q(),
                         db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("employees")):
    conds = []
    if location_id is not None:
        conds.append(Employee.location_id == location_id)
    if active is not None:
        conds.append(Employee.active == active)
    rows, total, page, size = await _paged(db, Employee, p.org_id, page, page_size, *conds)
    return _page(await _employees_out(db, p.org_id, list(rows)), total, page, size)


@router.get("/employees/{eid}", summary="Get an employee", responses=_ok({"data": _EMPLOYEE_EX}, _ONE_ERRORS))
async def get_employee(eid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("employees")):
    e = await lsvc.own(db, Employee, eid, p.org_id, "Employee")
    return {"data": (await _employees_out(db, p.org_id, [e]))[0]}


# ── contacts: search (replaces v2's plain list) and update ───────────────────
def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _stripped_phone():
    """Client.phone with the usual formatting removed, as services/call_contacts.find_client does."""
    stored = Client.phone
    for ch in (" ", "-", "(", ")", ".", "/", "+"):
        stored = func.replace(stored, ch, "")
    return stored


_CONTACT_EX = {"id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31", "name": "Dana Whitfield", "type": "person",
               "phone": "+1 (555) 010-2233", "email": "dana@example.com", "address": "12 High St",
               "loyalty_tier": "standard", "balance": 0.0, "created_at": "2026-10-25T18:06:01",
               "address_parts": {"street": None, "city": None, "region": None, "postcode": None, "country": None}}

# v2.py registered a plain GET /contacts; this one (same name, same operationId) adds search.
router.routes[:] = [r for r in router.routes
                    if not (isinstance(r, APIRoute) and r.path == "/api/v2/contacts" and "GET" in r.methods)]


@router.get("/contacts", summary="List or search contacts",
            description="Newest first. Filters combine (all must match):\n\n"
                        "* `phone`: the same number however it's written. Spaces, dashes, brackets and a "
                        "leading `+`/`00` are ignored, and a number with a country code matches the same "
                        "number without it (`+1 (555) 010-2233` finds `5550102233`). Returns every match.\n"
                        "* `email`: exact, ignoring case.\n"
                        "* `q`: text in the name, phone or email (at least 2 characters).\n\n"
                        "Scope: `contacts:read` (or `read`).",
            responses=_ok({"data": [_CONTACT_EX], "page": 1, "page_size": 25, "total": 1}, _READ_ERRORS))
async def list_contacts(phone: Optional[str] = Query(None, min_length=1, max_length=40,
                                                     description="A phone number in any format (at least 4 digits)."),
                        email: Optional[str] = Query(None, min_length=3, max_length=254,
                                                     description="An email address (exact, case-insensitive)."),
                        q: Optional[str] = Query(None, min_length=2, max_length=100,
                                                 description="Text to find in the name, phone or email."),
                        page: int = _page_q(), page_size: int = _size_q(),
                        db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("contacts")):
    from backend.services.call_contacts import digits, same_number
    conds = []
    if email is not None:
        conds.append(func.lower(Client.email) == email.strip().lower())
    if q is not None:
        text = q.strip().lower()
        if len(text) < 2:
            raise HTTPException(422, "q must be at least 2 characters")
        alts = [func.lower(Client.name).like(_like(text), escape="\\"),
                func.lower(Client.email).like(_like(text), escape="\\"),
                Client.phone.like(_like(q.strip()), escape="\\")]
        qd = digits(q)
        if len(qd) >= 3:
            alts.append(_stripped_phone().like(_like(qd), escape="\\"))
        conds.append(or_(*alts))
    if phone is None:
        rows, total, page, size = await _paged(db, Client, p.org_id, page, page_size, *conds)
        return _page([_contact(c) for c in rows], total, page, size)

    # Phone: narrow in SQL on the trailing digits (find_client's candidate query), compare the
    # normalised numbers in Python, keep every match, newest first.
    d = digits(phone)
    if len(d) < 4:
        raise HTTPException(422, "phone must have at least 4 digits")
    page, size = _bounds(page, page_size)
    cands = (await db.execute(select(Client).where(
        Client.org_id == p.org_id, Client.phone.isnot(None), _stripped_phone().like(f"%{d[-7:]}%"), *conds)
        .order_by(desc(Client.created_at), desc(Client.id)).limit(_MAX_SCAN))).scalars().all()
    hits = [c for c in cands if same_number(c.phone, phone)]
    return _page([_contact(c) for c in hits[(page - 1) * size: page * size]], len(hits), page, size)


class ContactPatch(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "phone": "+1 555 010 2233", "email": "dana@example.com", "notes": "Prefers text messages"}]})
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=254)
    address: Optional[str] = Field(None, max_length=500)
    addr_street: Optional[str] = Field(None, max_length=200)
    addr_city: Optional[str] = Field(None, max_length=120)
    addr_region: Optional[str] = Field(None, max_length=120)
    addr_postcode: Optional[str] = Field(None, max_length=20)
    addr_country: Optional[str] = Field(None, max_length=3)
    notes: Optional[str] = Field(None, max_length=5000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v is not None else None

    @field_validator("email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        if not v1._EMAIL_RE.match(v.strip()):
            raise ValueError("not a valid email address")
        return v.strip()


@router.patch("/contacts/{cid}", summary="Update a contact",
              description="Only the fields you send change; send `null` to clear one (except `name`). Sending any "
                          "`addr_*` part rebuilds `address` from the parts. Fires `contact.updated`. "
                          "Scope: `contacts:write` (or `write`).",
              responses=_ok({"data": _CONTACT_EX}, {**_WRITE_ERRORS, **_NOT_FOUND}))
async def update_contact(cid: str, data: ContactPatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("contacts")):
    from backend.services.webhooks import emit_event
    c = await lsvc.own(db, Client, cid, p.org_id, "Contact")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(422, "name can't be cleared")
    if not changes:
        return {"data": _contact(c)}
    for k in ("name", "phone", "email", "address", "notes"):
        if k in changes:
            v = changes[k]
            setattr(c, k, (v.strip() or None) if isinstance(v, str) and k != "name" else v)
    addresses.apply(c, {k: changes[k] for k in addresses.PARTS if k in changes})
    await db.flush()
    await emit_event(db, p.org_id, "contact.updated", {"id": c.id})
    await _audit(db, p, "client.update", "client", c.id, fields=sorted(changes))
    await v1._commit(db)
    await db.refresh(c)
    return {"data": _contact(c)}


# ── orders (work orders; estimates are not here) ─────────────────────────────
_ORDER_EX = {
    "id": "a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d", "number": "WO-01042", "status": "in_repair",
    "status_name": "In repair", "status_group": "in_progress", "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
    "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "assigned_to_id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10",
    "manager_id": None, "priority": 0, "complaint": "Cracked screen",
    "device": {"id": "c51a7e2d-…", "asset_type": "Device", "brand": "Apple", "model": "iPhone 14",
               "serial": "F2LXK0Q1N72J", "imei": "356938035643809", "color": "Black"},
    "labour_total": 60.0, "parts_total": 89.0, "discount": 0.0, "tax": 0.0, "total": 149.0, "written_off": 0.0,
    "due_at": "2026-10-29T17:00:00", "scheduled_for": "2026-10-28T10:30:00", "scheduled_to": "2026-10-28T11:30:00",
    "duration_min": 60, "cancel_reason": None, "archived": False,
    "tracking_url": "https://shop.tekly.app/track/Xf3…",
    "created_at": "2026-10-25T18:06:01", "updated_at": "2026-10-26T09:12:44", "closed_at": None,
}
_ORDER_DETAIL_EX = {**_ORDER_EX, "lines": {"count": 2, "by_kind": {"service": 1, "product": 1},
                                           "url": "/api/v2/orders/a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d/lines"}}


async def _statuses(db: AsyncSession, org_id: str) -> dict:
    return {s.key: s for s in (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "work_order")
        .order_by(OrderStatus.sort_order))).scalars().all()}


def _device(dv) -> Optional[dict]:
    if dv is None:
        return None
    # Passcodes, photos and purchase prices stay in the app.
    return {"id": dv.id, "asset_type": dv.asset_type, "brand": dv.brand, "model": dv.model,
            "serial": dv.serial, "imei": dv.imei, "vin": dv.vin, "hin": dv.hin, "color": dv.color}


def _order(o: Order, statuses: dict) -> dict:
    st = statuses.get(o.status)
    return {
        "id": o.id, "number": o.order_number, "status": o.status,
        "status_name": st.name if st else o.status, "status_group": st.status_group if st else None,
        "client_id": o.client_id, "location_id": o.location_id, "assigned_to_id": o.assigned_to_id,
        "manager_id": o.manager_id, "priority": o.priority or 0, "complaint": o.complaint,
        "device": _device(o.device),
        "labour_total": o.labour_total, "parts_total": o.parts_total, "discount": o.discount, "tax": o.tax,
        "total": o.total, "written_off": o.written_off or 0.0,
        "due_at": _iso(o.due_at), "scheduled_for": _iso(o.scheduled_for), "scheduled_to": _iso(o.scheduled_to),
        "duration_min": o.duration_min, "cancel_reason": o.cancel_reason, "archived": bool(o.archived),
        "tracking_url": f"{settings.public_base}/track/{o.public_token}" if o.public_token else None,
        "created_at": _iso(o.created_at), "updated_at": _iso(o.updated_at), "closed_at": _iso(o.closed_at),
    }


async def _own_order(db: AsyncSession, org_id: str, oid: str, *opts, fresh: bool = False) -> Order:
    q = select(Order).where(Order.id == oid, Order.org_id == org_id,
                            Order.is_estimate == False)  # noqa: E712
    q = q.options(selectinload(Order.device), *opts)
    if fresh:   # re-read what a write just committed (the status change, a PATCH)
        q = q.execution_options(populate_existing=True)
    o = (await db.execute(q)).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, "Order not found")
    return o


async def _order_detail(db: AsyncSession, org_id: str, oid: str) -> dict:
    o = await _own_order(db, org_id, oid, selectinload(Order.lines), fresh=True)
    kinds: dict = {}
    for ln in o.lines:
        k = ln.kind or ln.line_type or "custom"
        kinds[k] = kinds.get(k, 0) + 1
    return {**_order(o, await _statuses(db, org_id)),
            "lines": {"count": len(o.lines), "by_kind": kinds, "url": f"/api/v2/orders/{o.id}/lines"}}


@router.get("/orders", summary="List work orders",
            description="Work orders, newest first (estimates aren't included). Filter by `status` (a status key), "
                        "`client_id`, `location_id`, `updated_since` and `created_since`. For a sync, page through "
                        "`updated_since` = your last run. Scope: `orders:read` (or `read`).",
            responses=_ok({"data": [_ORDER_EX], "page": 1, "page_size": 25, "total": 1}, _READ_ERRORS))
async def list_orders(status: Optional[str] = Query(None, min_length=1, max_length=50, description="A status key, e.g. `in_repair`."),
                      client_id: Optional[str] = Query(None, min_length=1, max_length=64),
                      location_id: Optional[str] = Query(None, min_length=1, max_length=64),
                      updated_since: Optional[str] = _since_q("changed"),
                      created_since: Optional[str] = _since_q("created"),
                      page: int = _page_q(), page_size: int = _size_q(),
                      db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("orders")):
    conds = [Order.is_estimate == False]  # noqa: E712
    if status is not None:
        conds.append(Order.status == status)
    if client_id is not None:
        conds.append(Order.client_id == client_id)
    if location_id is not None:
        conds.append(Order.location_id == location_id)
    if (u := _since(updated_since, "updated_since")) is not None:
        conds.append(Order.updated_at >= u)
    if (c := _since(created_since, "created_since")) is not None:
        conds.append(Order.created_at >= c)
    page, size = _bounds(page, page_size)
    base = [Order.org_id == p.org_id, *conds]
    total = (await db.execute(select(func.count()).select_from(Order).where(*base))).scalar() or 0
    rows = (await db.execute(select(Order).where(*base).options(selectinload(Order.device))
                             .order_by(desc(Order.created_at), desc(Order.id))
                             .limit(size).offset((page - 1) * size))).scalars().all()
    statuses = await _statuses(db, p.org_id)
    return _page([_order(o, statuses) for o in rows], total, page, size)


@router.get("/orders/{oid}", summary="Get a work order",
            description="The order with its device, the customer's tracking link (`tracking_url`) and a summary of "
                        "its lines (`GET /orders/{id}/lines` has them in full).",
            responses=_ok({"data": _ORDER_DETAIL_EX}, _ONE_ERRORS))
async def get_order(oid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("orders")):
    return {"data": await _order_detail(db, p.org_id, oid)}


def _line(ln, costs: bool) -> dict:
    from backend.routers.orders import _line_tax_amount
    out = {"id": ln.id, "kind": ln.kind or "custom", "line_type": ln.line_type, "description": ln.description,
           "qty": ln.qty, "unit_price": ln.unit_price, "discount": ln.discount or 0.0,
           "discount_type": ln.discount_type or "pct", "tax_rate": ln.tax_rate or 0.0,
           "tax_mode": ln.tax_mode or "imposed",
           "tax": _line_tax_amount(_LineView(ln)), "total": ln.total, "warranty_days": ln.warranty_days or 0}
    if costs:   # cost fields only for keys holding costs:read
        out["cost_price"] = ln.cost_price
    return out


class _LineView:
    """The numbers orders._line_tax_amount reads, with stored nulls as zero."""
    def __init__(self, ln):
        self.qty = float(ln.qty or 0)
        self.unit_price = float(ln.unit_price or 0)
        self.discount = float(ln.discount or 0)
        self.discount_type = ln.discount_type or "pct"
        self.tax_rate = float(ln.tax_rate or 0)
        self.tax_mode = ln.tax_mode or "imposed"


@router.get("/orders/{oid}/lines", summary="List a work order's lines",
            description="Every line on the job, in the order added. `kind` is `service`, `product`, `bundle` or "
                        "`custom`; `discount` is a percentage (`discount_type: pct`) or an amount (`fixed`); `tax` is "
                        "the tax in the line (`tax_mode: included`) or added on top (`imposed`). `cost_price` is "
                        "included only for keys with `costs:read`. Not paged (a job's lines).",
            responses=_ok({"data": [{"id": "e1…", "kind": "service", "line_type": "labour", "description": "Screen replacement",
                                     "qty": 1.0, "unit_price": 60.0, "discount": 0.0, "discount_type": "pct",
                                     "tax_rate": 0.0, "tax_mode": "imposed", "tax": 0.0, "total": 60.0,
                                     "warranty_days": 90}]}, _ONE_ERRORS))
async def list_order_lines(oid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("orders")):
    o = await _own_order(db, p.org_id, oid, selectinload(Order.lines))
    return {"data": [_line(ln, _costs(p)) for ln in o.lines]}   # as the app lists them


class OrderPatch(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "priority": 1, "assigned_to_id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10",
        "scheduled_for": "2026-10-28T10:30:00", "scheduled_to": "2026-10-28T11:30:00", "due_at": "2026-10-29T17:00:00Z"}]})
    complaint: Optional[str] = Field(None, max_length=5000)
    priority: Optional[int] = Field(None, ge=0, le=2, description="0 normal, 1 urgent, 2 VIP.")
    assigned_to_id: Optional[str] = Field(None, min_length=1, max_length=64, description="An employee in this shop, or null to unassign.")
    manager_id: Optional[str] = Field(None, min_length=1, max_length=64, description="An employee in this shop, or null.")
    due_at: Optional[str] = Field(None, min_length=1, max_length=64,
                                  description="ISO 8601. An offset is converted to UTC; without one it's taken as UTC.")
    scheduled_for: Optional[str] = Field(None, min_length=1, max_length=64,
                                         description="The shop's wall-clock time. With an offset it's converted to the location's clock.")
    scheduled_to: Optional[str] = Field(None, min_length=1, max_length=64, description="As scheduled_for.")
    location_id: Optional[str] = Field(None, min_length=1, max_length=64, description="Another location of this shop.")


async def _assignable(db: AsyncSession, org_id: str, emp_id: Optional[str], code: str, label: str) -> None:
    from backend.routers.orders import _assert_listed
    if emp_id is None:
        return
    await _must_exist(db, Employee, org_id, emp_id, "Employee")
    active = (await db.execute(select(Employee.active).where(Employee.id == emp_id))).scalar()
    if not active:
        raise HTTPException(422, "That employee is deactivated")
    await _assert_listed(db, org_id, emp_id, code, label)   # the app's rule for the Managers / Assignee lists


@router.patch("/orders/{oid}", summary="Update a work order",
              description="Only the fields you send change; `null` clears `complaint`, `assigned_to_id`, "
                          "`manager_id`, `due_at`, `scheduled_for` and `scheduled_to` (`priority` and `location_id` "
                          "can't be cleared). Every id must be this shop's (`404`), and an assignee or manager must "
                          "be someone the shop lists there (`422`). `scheduled_for` / `scheduled_to` are the shop's "
                          "wall-clock time. A closed or locked order can't be edited (`409`): reopen it with "
                          "`POST /orders/{id}/status`. To change the status, use that route. Fires `order.updated`. "
                          "Scope: `orders:write` (or `write`).",
              responses=_ok({"data": _ORDER_DETAIL_EX}, {**_WRITE_ERRORS, **_NOT_FOUND}))
async def update_order(oid: str, data: OrderPatch, db: AsyncSession = Depends(get_db),
                       p: ApiPrincipal = W("orders")):
    from backend.routers.orders import _is_closed
    from backend.services.dates import parse_wall_dt
    o = await _own_order(db, p.org_id, oid)
    sent = data.model_dump(exclude_unset=True)
    st = (await _statuses(db, p.org_id)).get(o.status)
    if st is not None and st.locks_edits:
        raise HTTPException(409, "This order is closed/locked and can't be edited. Reopen it to make changes.")
    if await _is_closed(db, p.org_id, o):
        raise HTTPException(409, "This order is closed. Reopen it (POST /orders/{id}/status) before editing it.")

    if sent.get("location_id") is not None:
        await _must_exist(db, Location, p.org_id, sent["location_id"], "Location")
    if "assigned_to_id" in sent:
        await _assignable(db, p.org_id, sent["assigned_to_id"], "display_in_assignees", "Assignee")
    if "manager_id" in sent:
        await _assignable(db, p.org_id, sent["manager_id"], "display_in_managers", "Managers")

    loc = sent.get("location_id") or o.location_id
    when = {}
    for k in ("scheduled_for", "scheduled_to"):
        if k in sent:
            when[k] = await parse_wall_dt(db, p.org_id, loc, sent[k], k) if sent[k] else None
    if "due_at" in sent:
        when["due_at"] = _since(sent["due_at"], "due_at") if sent["due_at"] else None
    start = when["scheduled_for"] if "scheduled_for" in when else o.scheduled_for
    end = when["scheduled_to"] if "scheduled_to" in when else o.scheduled_to
    if start and end and end < start:
        raise HTTPException(422, "scheduled_to must not be before scheduled_for")

    prev_assignee = o.assigned_to_id
    for k, v in sent.items():
        if k in ("priority", "location_id") and v is None:
            continue
        if k in when:
            v = when[k]
        elif k == "complaint" and isinstance(v, str):
            v = v.strip() or None
        setattr(o, k, v)
    await _audit(db, p, "order.update", "order", o.id, fields=sorted(sent))
    await v1._commit(db)
    if o.assigned_to_id and o.assigned_to_id != prev_assignee:
        try:   # the app tells a newly assigned technician (orders.assign_people)
            from backend.services.push import notify
            await notify(db, org_id=p.org_id, employee_id=o.assigned_to_id, kind="job.assigned",
                         title=f"New job {o.order_number}", body=f"You were assigned to {o.order_number}",
                         deep_link=f"/tech#/order/{o.id}")
        except Exception:  # noqa: BLE001
            pass
    return {"data": await _order_detail(db, p.org_id, o.id)}


class StatusIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"status": "ready", "comment": "Tested and packed"},
        {"status": "dropped", "cancel_reason": "Customer declined the quote"}]})
    status: str = Field(..., min_length=1, max_length=50, description="A work-order status key of this shop.")
    comment: Optional[str] = Field(None, max_length=5000, description="Saved with the change in the status history.")
    cancel_reason: Optional[str] = Field(None, max_length=120,
                                         description="Why the job was dropped; kept when the status is in the dropped group.")


@router.post("/orders/{oid}/status", summary="Change a work order's status",
             description="Runs the app's own status change, so everything the shop set up applies exactly as on "
                         "screen: status transfer rules (the ones for all roles), required comment / checklist / "
                         "payment, the close gates (required checklist items, signature, fields to fill before "
                         "closing, stock still to write off), the unpaid-balance write-off on a dropped job, "
                         "checklists that attach on the status, invoices, warranty, commissions, customer "
                         "notifications and webhooks (`order.status_changed`, `order.closed`).\n\n"
                         "A gate that stops the change is `409` with the shop's message; a status the shop "
                         "doesn't have is `422`. Send an `Idempotency-Key` to make retries safe. Returns `200`. "
                         "Scope: `orders:write` (or `write`).",
             responses=_ok({"data": {**_ORDER_DETAIL_EX, "status": "ready", "status_name": "Ready",
                                     "status_group": "ready"},
                            "change": {"from": "in_repair", "to": "ready", "is_closing": False}},
                           {**_WRITE_ERRORS, **_NOT_FOUND}))
async def change_order_status(oid: str, data: StatusIn, db: AsyncSession = Depends(get_db),
                              p: ApiPrincipal = W("orders"), idempotency_key: Optional[str] = _idem_h()):
    from backend.routers.orders import ApiActor, _apply_status_change
    o = await _own_order(db, p.org_id, oid)
    statuses = {k: s for k, s in (await _statuses(db, p.org_id)).items() if s.active}
    if data.status not in statuses:
        raise HTTPException(422, f"Unknown status '{data.status}'. This shop's statuses: {', '.join(statuses)}")

    async def run():
        order = await _own_order(db, p.org_id, o.id, selectinload(Order.client), selectinload(Order.assigned_to),
                                 selectinload(Order.manager), selectinload(Order.lines))
        prev = order.status
        try:
            res = await _apply_status_change(db, ApiActor(org_id=p.org_id, api_key_id=p.key_id), order,
                                             data.status, note=data.comment, reason=data.cancel_reason)
        except HTTPException as e:
            if e.status_code == 403:   # a transfer rule: a gate here, not a scope problem
                raise HTTPException(409, e.detail)
            raise
        return {"data": await _order_detail(db, p.org_id, order.id),
                "change": {"from": prev, "to": data.status, "is_closing": bool(res.get("is_closing"))}}
    return await _idempotent(db, p, idempotency_key, f"POST /v2/orders/{oid}/status", data, run, status=200)


class OrderCommentIn(_In):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"body": "Customer approved the battery quote by phone.", "visible_to_client": False}]})
    body: str = Field(..., min_length=1, max_length=5000, description="The comment text.")
    visible_to_client: Optional[bool] = Field(
        None, description="Show it on the customer's tracking page. Left out: the shop's default visibility.")


@router.post("/orders/{oid}/comments", status_code=201, summary="Add a comment to a work order",
             description="Adds a comment to the job, as staff can on screen. A client-visible comment shows on "
                         "the tracking page and runs the shop's \"comment added\" notifications. Use it before a "
                         "status that requires a comment. Send an `Idempotency-Key` to make retries safe. "
                         "Scope: `orders:write` (or `write`).",
             responses=_ok({"data": {"id": "c41e…", "order_id": "a83d5f2c-…",
                                     "body": "Customer approved the battery quote by phone.",
                                     "visible_to_client": False, "created_at": "2026-10-26T15:02:11Z"}},
                           {**_WRITE_ERRORS, **_NOT_FOUND}))
async def add_order_comment(oid: str, data: OrderCommentIn, db: AsyncSession = Depends(get_db),
                            p: ApiPrincipal = W("orders"), idempotency_key: Optional[str] = _idem_h()):
    from backend.models import OrderComment
    from backend.routers.feed import default_visibility
    from backend.routers.orders import notify_comment_added
    o = await _own_order(db, p.org_id, oid)

    async def run():
        visible = data.visible_to_client
        if visible is None:
            visible = await default_visibility(db, p.org_id, "comment") == "client"
        c = OrderComment(order_id=o.id, author_id=None, body=data.body.strip(), visible_to_client=bool(visible))
        db.add(c)
        await db.flush()
        await _audit(db, p, "order.comment", "order", o.id, comment_id=c.id, visible_to_client=bool(visible))
        await v1._commit(db)
        if visible:
            await notify_comment_added(db, p.org_id, o.id, actor_id=None)
        created = getattr(c, "created_at", None)
        return {"data": {"id": c.id, "order_id": o.id, "body": c.body, "visible_to_client": bool(visible),
                         "created_at": created.replace(microsecond=0).isoformat() + "Z" if created else None}}
    return await _idempotent(db, p, idempotency_key, f"POST /v2/orders/{oid}/comments", data, run)


# ── invoices ─────────────────────────────────────────────────────────────────
_INVOICE_EX = {"id": "0b7d…", "number": "INV-00031", "order_id": "a83d5f2c-…", "order_ids": ["a83d5f2c-…"],
               "client_id": "6f1c2a9d-…", "status": "partial", "display_mode": "detailed", "subtotal": 149.0,
               "tax": 0.0, "total": 149.0, "amount_paid": 100.0, "credited": 0.0, "balance_due": 49.0,
               "issued_at": "2026-10-26T09:00:00", "due_at": "2026-11-09T09:00:00", "created_at": "2026-10-26T09:00:00"}


async def _order_ids(db: AsyncSession, invs: list[Invoice]) -> dict:
    ids = [i.id for i in invs]
    out = {i.id: [] for i in invs}
    if ids:
        for iid, oid in (await db.execute(select(InvoiceOrder.invoice_id, InvoiceOrder.order_id)
                                          .where(InvoiceOrder.invoice_id.in_(ids))
                                          .order_by(InvoiceOrder.sort_order))).all():
            out[iid].append(oid)
    for i in invs:      # older invoices bill their primary order without a link row
        if i.order_id and i.order_id not in out[i.id]:
            out[i.id].insert(0, i.order_id)
    return out


def _invoice(i: Invoice, order_ids: list) -> dict:
    return {**v1._invoice(i), "order_ids": order_ids, "display_mode": i.display_mode or "detailed",
            "due_at": _iso(i.due_at), "created_at": _iso(i.created_at)}


@router.get("/invoices", summary="List invoices",
            description="Newest first. Filter by `status` (`draft`, `issued`, `partial`, `paid`, `cancelled`…), "
                        "`client_id`, `issued_since`, and `updated_since`: invoices created, issued, or with a "
                        "payment or refund recorded (on the invoice or the jobs it bills) since then. Scope: `invoices:read` (or `read`).",
            responses=_ok({"data": [_INVOICE_EX], "page": 1, "page_size": 25, "total": 1}, _READ_ERRORS))
async def list_invoices(status: Optional[str] = Query(None, min_length=1, max_length=30),
                        client_id: Optional[str] = Query(None, min_length=1, max_length=64),
                        updated_since: Optional[str] = _since_q("created, issued or paid"),
                        issued_since: Optional[str] = _since_q("issued"),
                        page: int = _page_q(), page_size: int = _size_q(),
                        db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("invoices")):
    conds = []
    if status is not None:
        conds.append(Invoice.status == status)
    if client_id is not None:
        conds.append(Invoice.client_id == client_id)
    if (s := _since(issued_since, "issued_since")) is not None:
        conds.append(Invoice.issued_at >= s)
    if (u := _since(updated_since, "updated_since")) is not None:
        paid = select(Payment.invoice_id).where(Payment.invoice_id.isnot(None), Payment.paid_at >= u)
        # refunds are often recorded on the job rather than the invoice
        refunded = select(Payment.order_id).where(Payment.invoice_id.is_(None), Payment.kind == "refund",
                                                  Payment.paid_at >= u)
        linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id.in_(refunded))
        conds.append(or_(Invoice.created_at >= u, Invoice.issued_at >= u, Invoice.id.in_(paid),
                         Invoice.order_id.in_(refunded), Invoice.id.in_(linked)))
    rows, total, page, size = await _paged(db, Invoice, p.org_id, page, page_size, *conds)
    oids = await _order_ids(db, list(rows))
    return _page([_invoice(i, oids[i.id]) for i in rows], total, page, size)


@router.get("/invoices/{iid}", summary="Get an invoice",
            description="The invoice with its lines as the customer sees them (`display_mode`: `detailed` lists "
                        "every line, `simplified` one line per job, `one_line` a single line), its totals, "
                        "`amount_paid`, `credited` (issued credit notes), `balance_due` and the work orders it "
                        "bills.",
            responses=_ok({"data": {**_INVOICE_EX, "lines": [
                {"description": "Screen replacement", "qty": 1.0, "unit_price": 60.0, "total": 60.0},
                {"description": "iPhone 14 screen", "qty": 1.0, "unit_price": 89.0, "total": 89.0}]}}, _ONE_ERRORS))
async def get_invoice(iid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("invoices")):
    from backend.services.invoicing import display_lines
    i = await lsvc.own(db, Invoice, iid, p.org_id, "Invoice")
    return {"data": {**_invoice(i, (await _order_ids(db, [i]))[i.id]), "lines": await display_lines(db, i)}}


@router.get("/invoices/{iid}/payments", summary="List an invoice's payments and refunds",
            description="Money recorded against the invoice (`kind`: `payment`, `prepayment` or `refund`), plus "
                        "refunds made on the work orders it bills. Amounts are positive; `kind` says the "
                        "direction. Oldest first. Not paged. Scope: `invoices:read` (or `read`).",
            responses=_ok({"data": [{"id": "5e2a…", "kind": "payment", "method": "card", "amount": 100.0,
                                     "reference": None, "paid_at": "2026-10-26T09:05:12", "invoice_id": "0b7d…",
                                     "order_id": "a83d5f2c-…"}]}, _ONE_ERRORS))
async def list_invoice_payments(iid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("invoices")):
    i = await lsvc.own(db, Invoice, iid, p.org_id, "Invoice")
    oids = (await _order_ids(db, [i]))[i.id]
    cond = Payment.invoice_id == i.id
    if oids:
        cond = or_(cond, (Payment.invoice_id.is_(None)) & (Payment.kind == "refund") & Payment.order_id.in_(oids))
    rows = (await db.execute(select(Payment).where(cond).order_by(Payment.paid_at, Payment.id).limit(1000))).scalars().all()
    # Cash accounts, shifts, who took it and card-reader charge ids stay in the app.
    return {"data": [{"id": x.id, "kind": x.kind, "method": x.method, "amount": abs(float(x.amount or 0)),
                      "reference": x.reference, "paid_at": _iso(x.paid_at), "invoice_id": x.invoice_id,
                      "order_id": x.order_id} for x in rows]}
