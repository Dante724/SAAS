"""Public REST API v2 (WS-C, hardened to the v1 bar when merged onto api/harden-and-docs).

Same keys, envelope, paging, errors and protections as v1 (docs/developers/), plus:
  * inquiries — list/get/create/update/convert to a job;  inquiry types — list;
  * tags — list/create/update/delete;  services — list/get/create/update;
  * bundles — list/get/create/update/delete;  products — list/get/create/update;
  * contacts — with structured addresses;  webhooks — manage subscriptions with the API.

Scopes. A key may be narrowed to one resource (``inquiries:read``, ``products:write``…); the
broad ``read`` / ``write`` cover every resource here except ``webhooks``, which always needs an
explicit ``webhooks:read`` / ``webhooks:write``. ``<resource>:write`` implies ``<resource>:read``.
Cost fields (service and product ``cost_price``) appear only for keys holding ``costs:read``.

Protections shared with v1 (routers/v1.py): bounded paging, length limits and unknown fields
refused, every referenced id checked against the key's shop (404), constraint failures as 422,
Idempotency-Key on every POST, every write audited with ``via_api_key``, no CORS and HTTPS only
when deployed (main.py / core.observability), and a v2-only OpenAPI at /api/v2/openapi.json.
"""
import json
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Bundle, Client, Lead, LeadType, Part, Service, Tag
from backend.routers import v1
from backend.routers.v1 import _In, _audit, _bounds, _idem_h, _idempotent, _must_exist, _page, _page_q, _size_q
from backend.services import addresses
from backend.services import leads as lsvc
from backend.services.apikeys import ApiPrincipal, any_api_key, require_scope

router = APIRouter(prefix="/api/v2", tags=["public-api-v2"],
                   generate_unique_id_function=lambda route: f"v2_{route.name}")

API_VERSION = "2.0"
_ID = Field(None, min_length=1, max_length=64)


def R(resource: str):
    return Depends(require_scope("read", resource))


def W(resource: str):
    return Depends(require_scope("write", resource))


async def _paged(db, model, org_id: str, page: int, size: int, *conds, order=None):
    page, size = _bounds(page, size)
    cond = [model.org_id == org_id, *conds]
    total = (await db.execute(select(func.count()).select_from(model).where(*cond))).scalar() or 0
    oc = order if order is not None else model.created_at
    rows = (await db.execute(select(model).where(*cond).order_by(desc(oc), desc(model.id))
                             .limit(size).offset((page - 1) * size))).scalars().all()
    return rows, total, page, size


async def _guard(db: AsyncSession, coro):
    """A constraint failure a core function didn't anticipate is the caller's 422, not a 500."""
    try:
        return await coro
    except IntegrityError:
        await db.rollback()
        raise HTTPException(422, "The request refers to a record that doesn't exist or conflicts with existing data")


async def _audited(db: AsyncSession, p: ApiPrincipal, action: str, entity_type: str, entity_id: str, **extra) -> None:
    """Audit a write that a shared core function already committed (A9: attributed to the key)."""
    await _audit(db, p, action, entity_type, entity_id, **extra)
    await db.commit()


def _costs(p: ApiPrincipal) -> bool:
    return p.can_see_costs


def _guard_cost_write(p: ApiPrincipal, data) -> None:
    """A key that can't read cost prices can't set them either."""
    if "cost_price" in data.model_fields_set and not _costs(p):
        raise HTTPException(403, "Setting cost_price needs the costs:read scope")


# ── discovery ────────────────────────────────────────────────────────────────
RESOURCE_PATHS = ("contacts", "inquiries", "inquiry_types", "tags", "services", "bundles", "products", "webhooks")


@router.get("", summary="Discover the API",
            description="Any valid key may call it. It shows only the key's own shop and scopes.")
async def index(p: ApiPrincipal = Depends(any_api_key)):
    base = "/api/v2"
    return {"data": {"version": API_VERSION, "org_id": p.org_id, "scopes": sorted(p.scopes),
                     "resources": {n: f"{base}/{n.replace('_', '-')}" for n in RESOURCE_PATHS},
                     "v1": "/api/v1", "docs": f"{base}/openapi.json", "openapi": f"{base}/openapi.json"}}


# ── contacts (with structured address) ───────────────────────────────────────
def _contact(c: Client) -> dict:
    return {**v1._contact(c), "address_parts": addresses.ser(c)}


class ContactIn(_In):
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=254)
    client_type: Literal["person", "organization"] = "person"
    address: Optional[str] = Field(None, max_length=500)
    addr_street: Optional[str] = Field(None, max_length=200)
    addr_city: Optional[str] = Field(None, max_length=120)
    addr_region: Optional[str] = Field(None, max_length=120)
    addr_postcode: Optional[str] = Field(None, max_length=20)
    addr_country: Optional[str] = Field(None, max_length=3)

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
        if not v1._EMAIL_RE.match(v.strip()):
            raise ValueError("not a valid email address")
        return v.strip()


@router.get("/contacts", summary="List contacts")
async def list_contacts(page: int = _page_q(), page_size: int = _size_q(), db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("contacts")):
    rows, total, page, size = await _paged(db, Client, p.org_id, page, page_size)
    return _page([_contact(c) for c in rows], total, page, size)


@router.get("/contacts/{cid}", summary="Get a contact")
async def get_contact(cid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("contacts")):
    return {"data": _contact(await lsvc.own(db, Client, cid, p.org_id, "Contact"))}


@router.post("/contacts", status_code=201, summary="Create a contact")
async def create_contact(data: ContactIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("contacts"),
                         idempotency_key: Optional[str] = _idem_h()):
    async def run():
        c = Client(org_id=p.org_id, name=data.name, phone=data.phone, email=data.email,
                   address=data.address, client_type=data.client_type)
        addresses.apply(c, {k: getattr(data, k) for k in addresses.PARTS if k in data.model_fields_set})
        db.add(c)
        await db.flush()
        await _audit(db, p, "client.create", "client", c.id, name=c.name)
        await v1._commit(db)
        await db.refresh(c)
        return {"data": _contact(c)}
    return await _idempotent(db, p, idempotency_key, "POST /v2/contacts", data, run)


# ── inquiries ────────────────────────────────────────────────────────────────
_Source = Literal["phone", "walk_in", "whatsapp", "sms", "email", "instagram", "facebook", "web", "api", "other"]
assert set(_Source.__args__) == set(lsvc.SOURCES)


def _custom_ok(v: Optional[dict]) -> Optional[dict]:
    if v is not None and (len(v) > 100 or len(json.dumps(v, default=str)) > 20_000):
        raise ValueError("custom_data is too large (at most 100 fields, 20,000 characters)")
    return v


class InquiryIn(_In):
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    source: _Source = "api"
    inquiry: Optional[str] = Field(None, max_length=4000)
    sla_minutes: int = Field(60, ge=1, le=60 * 24 * 30)
    lead_type_id: Optional[str] = _ID
    manager_id: Optional[str] = _ID
    client_id: Optional[str] = _ID
    device_brand: Optional[str] = Field(None, max_length=100)
    device_model: Optional[str] = Field(None, max_length=100)
    serial: Optional[str] = Field(None, max_length=100)
    custom_data: Optional[dict[str, Any]] = None

    @field_validator("custom_data")
    @classmethod
    def _custom(cls, v):
        return _custom_ok(v)


class InquiryPatch(_In):
    status: Optional[Literal["new", "in_progress", "won", "lost"]] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    source: Optional[_Source] = None
    inquiry: Optional[str] = Field(None, max_length=4000)
    manager_id: Optional[str] = _ID
    lead_type_id: Optional[str] = _ID
    device_brand: Optional[str] = Field(None, max_length=100)
    device_model: Optional[str] = Field(None, max_length=100)
    serial: Optional[str] = Field(None, max_length=100)
    custom_data: Optional[dict[str, Any]] = None

    @field_validator("custom_data")
    @classmethod
    def _custom(cls, v):
        return _custom_ok(v)


class ConvertIn(_In):
    is_estimate: bool = False


def _inq(p: ApiPrincipal, rows: list[dict]) -> list[dict]:
    """Without contacts:read a key doesn't learn which inquiries match an existing customer."""
    if p.allows("read", "contacts"):
        return rows
    return [{**r, "client_id": None} for r in rows]


@router.get("/inquiries", summary="List inquiries")
async def list_inquiries(status: Optional[Literal["new", "in_progress", "won", "lost"]] = Query(None),
                         page: int = _page_q(), page_size: int = _size_q(),
                         db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    rows, total, page, size = await _paged(db, Lead, p.org_id, page, page_size,
                                           *([Lead.status == status] if status else []))
    return _page(_inq(p, await lsvc.ser_many(db, rows)), total, page, size)


@router.get("/inquiries/{lid}", summary="Get an inquiry")
async def get_inquiry(lid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    return {"data": _inq(p, [await lsvc.ser_one(db, await lsvc.own(db, Lead, lid, p.org_id, "Inquiry"))])[0]}


@router.post("/inquiries", status_code=201, summary="Create an inquiry",
             description="Send an Idempotency-Key to make retries safe (a web form, say).")
async def create_inquiry(data: InquiryIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("inquiries"),
                         idempotency_key: Optional[str] = _idem_h()):
    async def run():
        body = data.model_dump()
        if not p.allows("read", "contacts"):
            body["client_id"] = None      # linking a contact needs contacts access (no probing by 404)
        lead, _created = await _guard(db, lsvc.create_lead(db, p.org_id, body, via="api"))
        await _audited(db, p, "lead.create", "lead", lead.id, name=lead.name, source=lead.source)
        return {"data": _inq(p, [await lsvc.ser_one(db, lead)])[0]}
    return await _idempotent(db, p, idempotency_key, "POST /v2/inquiries", data, run)


@router.patch("/inquiries/{lid}", summary="Update an inquiry")
async def update_inquiry(lid: str, data: InquiryPatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("inquiries")):
    lead = await lsvc.own(db, Lead, lid, p.org_id, "Inquiry")
    changes = data.model_dump(exclude_unset=True)
    lead = await _guard(db, lsvc.update_lead(db, p.org_id, lead, changes))
    await _audited(db, p, "lead.update", "lead", lead.id, fields=sorted(changes))
    return {"data": _inq(p, [await lsvc.ser_one(db, lead)])[0]}


@router.post("/inquiries/{lid}/convert", summary="Convert an inquiry to a job",
             description="Needs inquiries:write plus orders:write (estimates:write for an estimate) and "
                         "contacts:write, because converting creates the contact and the job.")
async def convert_inquiry(lid: str, data: Optional[ConvertIn] = None,
                          is_estimate: Optional[bool] = Query(None, description="Deprecated: send it in the body."),
                          db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("inquiries"),
                          idempotency_key: Optional[str] = _idem_h()):
    est = bool(is_estimate) if is_estimate is not None else bool(data and data.is_estimate)
    p.require("write", "estimates" if est else "orders")
    p.require("write", "contacts")
    lead = await lsvc.own(db, Lead, lid, p.org_id, "Inquiry")

    async def run():
        res = await _guard(db, lsvc.convert_lead(db, p.org_id, lead, is_estimate=est))
        await _audited(db, p, "lead.convert", "lead", lead.id, order_id=res.get("order_id"),
                       is_estimate=est)
        return {"data": res}
    return await _idempotent(db, p, idempotency_key, f"POST /v2/inquiries/{lid}/convert",
                             ConvertIn(is_estimate=est), run, status=200)


@router.get("/inquiry-types", summary="List inquiry types")
async def list_inquiry_types(db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    rows = (await db.execute(select(LeadType).where(LeadType.org_id == p.org_id, LeadType.active == True)  # noqa: E712
                             .order_by(LeadType.sort_order, LeadType.name).limit(500))).scalars().all()
    return {"data": [{"id": t.id, "name": t.name, "sla_minutes": t.sla_minutes} for t in rows]}


# ── tags ─────────────────────────────────────────────────────────────────────
class TagIn(_In):
    name: str = Field(..., min_length=1, max_length=40)
    color: Optional[str] = Field(None, max_length=20)


class TagPatch(_In):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    color: Optional[str] = Field(None, max_length=20)


def _tag(t: Tag) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color,
            "created_at": t.created_at.isoformat() if t.created_at else None}


@router.get("/tags", summary="List tags")
async def list_tags(page: int = _page_q(), page_size: int = _size_q(), db: AsyncSession = Depends(get_db),
                    p: ApiPrincipal = R("tags")):
    rows, total, page, size = await _paged(db, Tag, p.org_id, page, page_size)
    return _page([_tag(t) for t in rows], total, page, size)


@router.post("/tags", status_code=201, summary="Create a tag")
async def create_tag(data: TagIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags"),
                     idempotency_key: Optional[str] = _idem_h()):
    from backend.routers.tags import create_tag_core

    async def run():
        t = await _guard(db, create_tag_core(db, p.org_id, data.name, data.color))
        await _audited(db, p, "tag.create", "tag", t.id, name=t.name)
        await db.refresh(t)
        return {"data": _tag(t)}
    return await _idempotent(db, p, idempotency_key, "POST /v2/tags", data, run)


@router.patch("/tags/{tid}", summary="Update a tag")
async def update_tag(tid: str, data: TagPatch, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags")):
    from backend.routers.tags import get_tag, update_tag_core
    t = await _guard(db, update_tag_core(db, await get_tag(db, p.org_id, tid), data.name, data.color))
    await _audited(db, p, "tag.update", "tag", t.id, name=t.name)
    await db.refresh(t)
    return {"data": _tag(t)}


@router.delete("/tags/{tid}", summary="Delete a tag")
async def delete_tag(tid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags")):
    from backend.routers.tags import delete_tag_core, get_tag
    t = await get_tag(db, p.org_id, tid)
    name = t.name
    await _guard(db, delete_tag_core(db, t))
    await _audited(db, p, "tag.delete", "tag", tid, name=name)
    return {"data": {"id": tid, "deleted": True}}


# ── services ─────────────────────────────────────────────────────────────────
def _service(s: Service, costs: bool = False) -> dict:
    out = {"id": s.id, "name": s.name, "description": s.description, "category": s.category,
           "category_id": s.category_id, "price": s.price,
           "duration_min": s.duration_min, "warranty_days": s.warranty_days, "barcode": s.barcode,
           "unit": s.unit, "is_labor": s.is_labor, "bookable": s.bookable, "active": s.active}
    if costs:   # A2: cost fields only for keys holding costs:read
        out["cost_price"] = s.cost_price
    return out


class ServiceIn(_In):
    name: str = Field(..., min_length=1, max_length=300)
    description: str = Field("", max_length=2000)
    price: float = Field(0.0, ge=0, le=10_000_000)
    cost_price: float = Field(0.0, ge=0, le=10_000_000)
    duration_min: int = Field(60, ge=1, le=60 * 24)
    warranty_days: int = Field(0, ge=0, le=3650)
    barcode: Optional[str] = Field(None, max_length=100)
    unit: str = Field("pc", min_length=1, max_length=24)
    is_labor: bool = False
    bookable: bool = True
    active: bool = True


class ServicePatch(_In):
    name: Optional[str] = Field(None, min_length=1, max_length=300)
    description: Optional[str] = Field(None, max_length=2000)
    price: Optional[float] = Field(None, ge=0, le=10_000_000)
    cost_price: Optional[float] = Field(None, ge=0, le=10_000_000)
    duration_min: Optional[int] = Field(None, ge=1, le=60 * 24)
    warranty_days: Optional[int] = Field(None, ge=0, le=3650)
    barcode: Optional[str] = Field(None, max_length=100)
    unit: Optional[str] = Field(None, min_length=1, max_length=24)
    is_labor: Optional[bool] = None
    bookable: Optional[bool] = None
    active: Optional[bool] = None


@router.get("/services", summary="List services",
            description="cost_price is included only for keys with the costs:read scope.")
async def list_services(page: int = _page_q(), page_size: int = _size_q(), db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("services")):
    rows, total, page, size = await _paged(db, Service, p.org_id, page, page_size)
    return _page([_service(s, _costs(p)) for s in rows], total, page, size)


@router.get("/services/{sid}", summary="Get a service")
async def get_service(sid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("services")):
    return {"data": _service(await lsvc.own(db, Service, sid, p.org_id, "Service"), _costs(p))}


@router.post("/services", status_code=201, summary="Create a service")
async def create_service(data: ServiceIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("services"),
                         idempotency_key: Optional[str] = _idem_h()):
    _guard_cost_write(p, data)
    async def run():
        s = Service(org_id=p.org_id, **{**data.model_dump(), "name": data.name.strip()})
        db.add(s)
        await db.flush()
        await _audit(db, p, "service.create", "service", s.id, name=s.name)
        await v1._commit(db)
        await db.refresh(s)
        return {"data": _service(s, _costs(p))}
    return await _idempotent(db, p, idempotency_key, "POST /v2/services", data, run)


@router.patch("/services/{sid}", summary="Update a service")
async def update_service(sid: str, data: ServicePatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("services")):
    s = await lsvc.own(db, Service, sid, p.org_id, "Service")
    _guard_cost_write(p, data)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if v is not None or k == "barcode":
            setattr(s, k, v)
    await _audit(db, p, "service.update", "service", s.id, fields=sorted(changes))
    await v1._commit(db)
    await db.refresh(s)
    return {"data": _service(s, _costs(p))}


# ── bundles ──────────────────────────────────────────────────────────────────
class CompIn(_In):
    ref_type: Literal["service", "product"]
    ref_id: str = Field(..., min_length=1, max_length=64)
    qty: float = Field(1, gt=0, le=10_000)
    device_variant: bool = False


class BundleIn(_In):
    name: str = Field(..., min_length=1, max_length=200)
    price_mode: Literal["sum", "fixed"] = "sum"
    price: float = Field(0.0, ge=0, le=10_000_000)
    category_id: Optional[str] = _ID
    components: list[CompIn] = Field(default_factory=list, max_length=200)


async def _bundle_body(db, org_id: str, data: BundleIn):
    """The staff-screen model, with only the fields the caller sent (so an update leaves the
    rest alone), after checking every referenced id is this shop's (404 otherwise)."""
    from backend.models import Category
    from backend.routers.bundles import BundleIn as BundleBody
    await _must_exist(db, Category, org_id, data.category_id, "Category")
    for c in data.components:
        await _must_exist(db, Service if c.ref_type == "service" else Part, org_id, c.ref_id,
                          "Service" if c.ref_type == "service" else "Product")
    return BundleBody(**data.model_dump(exclude_unset=True))


@router.get("/bundles", summary="List bundles")
async def list_bundles(page: int = _page_q(), page_size: int = _size_q(), db: AsyncSession = Depends(get_db),
                       p: ApiPrincipal = R("bundles")):
    from backend.routers.bundles import _ser
    rows, total, page, size = await _paged(db, Bundle, p.org_id, page, page_size, Bundle.active == True)  # noqa: E712
    return _page([await _ser(db, b) for b in rows], total, page, size)


@router.get("/bundles/{bid}", summary="Get a bundle")
async def get_bundle(bid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("bundles")):
    from backend.routers.bundles import _ser, get_bundle as _get
    return {"data": await _ser(db, await _get(db, p.org_id, bid))}


@router.post("/bundles", status_code=201, summary="Create a bundle")
async def create_bundle(data: BundleIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("bundles"),
                        idempotency_key: Optional[str] = _idem_h()):
    from backend.routers.bundles import _ser, create_bundle_core

    async def run():
        b = await _guard(db, create_bundle_core(db, p.org_id, await _bundle_body(db, p.org_id, data)))
        await _audited(db, p, "bundle.create", "bundle", b.id, name=b.name)
        await db.refresh(b)
        return {"data": await _ser(db, b)}
    return await _idempotent(db, p, idempotency_key, "POST /v2/bundles", data, run)


@router.put("/bundles/{bid}", summary="Update a bundle",
            description="Fields left out keep their value; components, when sent, replace the list.")
async def update_bundle(bid: str, data: BundleIn, db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = W("bundles")):
    from backend.routers.bundles import _ser, get_bundle as _get, update_bundle_core
    b = await _get(db, p.org_id, bid)
    b = await _guard(db, update_bundle_core(db, b, await _bundle_body(db, p.org_id, data)))
    await _audited(db, p, "bundle.update", "bundle", b.id, name=b.name)
    await db.refresh(b)
    return {"data": await _ser(db, b)}


@router.delete("/bundles/{bid}", summary="Delete a bundle")
async def delete_bundle(bid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("bundles")):
    from backend.routers.bundles import delete_bundle_core, get_bundle as _get
    b = await _get(db, p.org_id, bid)
    if b.active:
        await _guard(db, delete_bundle_core(db, b))
        await _audited(db, p, "bundle.delete", "bundle", bid, name=b.name)
    return {"data": {"id": bid, "deleted": True}}


# ── products (writable in v2) ────────────────────────────────────────────────
class ProductIn(_In):
    name: str = Field(..., min_length=1, max_length=300)
    sku: Optional[str] = Field(None, max_length=100)
    mpn: Optional[str] = Field(None, max_length=100)
    brand: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    uom: str = Field("pcs", min_length=1, max_length=10)
    sell_price: float = Field(0.0, ge=0, le=10_000_000)
    cost_price: float = Field(0.0, ge=0, le=10_000_000)
    active: bool = True


class ProductPatch(_In):
    name: Optional[str] = Field(None, min_length=1, max_length=300)
    sku: Optional[str] = Field(None, max_length=100)
    mpn: Optional[str] = Field(None, max_length=100)
    brand: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    uom: Optional[str] = Field(None, min_length=1, max_length=10)
    sell_price: Optional[float] = Field(None, ge=0, le=10_000_000)
    cost_price: Optional[float] = Field(None, ge=0, le=10_000_000)
    active: Optional[bool] = None


async def _save_product(db, p: ApiPrincipal, row: Part, event: str, action: str, **extra) -> dict:
    from backend.services.webhooks import emit_event
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Another product already uses this part number (MPN)")
    await emit_event(db, row.org_id, event, {"id": row.id, "sku": row.sku, "name": row.name})
    await _audit(db, p, action, "part", row.id, name=row.name, **extra)
    await v1._commit(db)
    await db.refresh(row)
    return {"data": v1._product(row, _costs(p))}


@router.get("/products", summary="List products",
            description="cost_price is included only for keys with the costs:read scope.")
async def list_products(page: int = _page_q(), page_size: int = _size_q(), db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("products")):
    rows, total, page, size = await _paged(db, Part, p.org_id, page, page_size)
    return _page([v1._product(r, _costs(p)) for r in rows], total, page, size)


@router.get("/products/{pid}", summary="Get a product")
async def get_product(pid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("products")):
    return {"data": v1._product(await lsvc.own(db, Part, pid, p.org_id, "Product"), _costs(p))}


@router.post("/products", status_code=201, summary="Create a product")
async def create_product(data: ProductIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("products"),
                         idempotency_key: Optional[str] = _idem_h()):
    _guard_cost_write(p, data)
    async def run():
        row = Part(org_id=p.org_id, **{k: (v.strip() or None) if isinstance(v, str) and k != "uom" else v
                                       for k, v in data.model_dump().items()})
        row.name = data.name.strip()
        db.add(row)
        return await _save_product(db, p, row, "product.created", "part.create")
    return await _idempotent(db, p, idempotency_key, "POST /v2/products", data, run)


@router.patch("/products/{pid}", summary="Update a product")
async def update_product(pid: str, data: ProductPatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("products")):
    row = await lsvc.own(db, Part, pid, p.org_id, "Product")
    _guard_cost_write(p, data)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if k in ("name", "uom", "sell_price", "cost_price", "active") and v is None:
            continue   # required columns can't be cleared
        setattr(row, k, (v.strip() or None) if isinstance(v, str) and k not in ("name", "uom") else v)
    return await _save_product(db, p, row, "product.updated", "part.update", fields=sorted(changes))


# ── webhooks (subscriptions managed with the API) ────────────────────────────
class HookIn(_In):
    url: str = Field(..., min_length=1, max_length=500)
    events: Union[str, list[str]] = Field("*", description="A list or csv of catalogue events, or * for every event.")
    description: Optional[str] = Field(None, max_length=200)
    active: bool = True


def _without_secret(body: dict) -> dict:
    d = dict(body.get("data") or {})
    d.pop("secret", None)
    return {**body, "data": d}


@router.get("/webhooks", summary="List webhook endpoints",
            description="Needs webhooks:read (a broad read key can't). Secrets are never listed.")
async def list_hooks(db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("webhooks")):
    from backend.routers.webhooks import _live_endpoints, _ser
    from backend.services import webhooks as wh
    rows = await _live_endpoints(db, p.org_id)
    return {"data": [_ser(e) for e in rows], "events": wh.EVENTS, "aliases": wh.EVENT_ALIASES,
            "max_endpoints": wh.MAX_ENDPOINTS_PER_ORG}


@router.post("/webhooks", status_code=201, summary="Add a webhook endpoint",
             description="Needs webhooks:write. The same rules as Settings → Webhooks: at most 10 per shop, "
                         "catalogue events only, a public https URL. The signing secret is in this response "
                         "only; an Idempotency-Key replay returns the endpoint without it.")
async def create_hook(data: HookIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("webhooks"),
                      idempotency_key: Optional[str] = _idem_h()):
    from backend.routers.webhooks import _ser, create_endpoint_core

    async def run():
        e, secret = await create_endpoint_core(db, p.org_id, url=data.url, events=data.events,
                                               description=data.description, active=data.active)
        await _audit(db, p, "webhook.create", "webhook_endpoint", e.id, url=e.url, events=e.events)
        await v1._commit(db)
        await db.refresh(e)
        return {"data": {**_ser(e), "secret": secret}}
    return await _idempotent(db, p, idempotency_key, "POST /v2/webhooks", data, run, stored=_without_secret)


@router.delete("/webhooks/{hid}", summary="Delete a webhook endpoint",
               description="Soft delete: nothing more is sent and queued retries stop; the log is kept.")
async def delete_hook(hid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("webhooks")):
    from backend.routers.webhooks import _get, delete_endpoint_core
    e = await _get(db, p.org_id, hid)
    await delete_endpoint_core(db, e)
    await _audit(db, p, "webhook.delete", "webhook_endpoint", e.id, url=e.url)
    await v1._commit(db)
    return {"data": {"id": hid, "deleted": True}}


# ── OpenAPI ──────────────────────────────────────────────────────────────────
_OPENAPI_DESCRIPTION = """\
The Tekly public API v2: inquiries, tags, services, bundles, writable products, contacts with
structured addresses, and webhook subscriptions. v1 (/api/v1/openapi.json) is unchanged.

**Authentication.** `Authorization: Bearer rok_…`, HTTPS only. The same keys work on v1 and v2.

**Scopes.** Broad `read` / `write` cover every v2 resource except webhooks. `<resource>:read` and
`<resource>:write` narrow a key to one resource (write includes read), for example
`inquiries:write`. Webhook management always needs `webhooks:read` / `webhooks:write`.
`costs:read` adds cost fields. A resource-scoped key can't call v1.

**Envelope, paging, errors, idempotency and rate limits** are as in v1: `page` 1–10000,
`page_size` 1–100, `{"detail": ...}` errors, `Idempotency-Key` on every POST, and each key's own
per-minute limit reported in `X-RateLimit-Limit` / `X-RateLimit-Remaining`.
"""
_openapi_cache: Optional[dict] = None


def build_openapi() -> dict:
    global _openapi_cache
    if _openapi_cache is None:
        _openapi_cache = v1.openapi_for(router, version=API_VERSION, description=_OPENAPI_DESCRIPTION,
                                        tag="public-api-v2", tag_description="Tekly public API v2")
    return _openapi_cache


@router.get("/openapi.json", include_in_schema=False)
async def openapi_document():
    """Public, no key needed, served in production too."""
    return JSONResponse(build_openapi(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/docs", include_in_schema=False)
async def docs_redirect():
    return RedirectResponse("/api/v2/openapi.json", status_code=307)
