"""Public REST API v2 (WS-C).

Same keys, envelope and paging as v1 (docs/public-api-v1.md), plus:
  * inquiries — list/get/create (idempotent with Idempotency-Key)/update/convert to a job;
  * tags — list/create/update/delete;  services — list/get/create/update;
  * bundles — list/get/create/update/delete;  products — list/get/create/update;
  * contacts — with structured addresses;  webhooks — manage subscriptions with the API.
Keys may be narrowed to one resource (``inquiries:read``, ``products:write``…); the legacy
``read``/``write``/``*`` scopes cover everything. Every referenced id is checked against the
key's shop, so a key can never touch, or learn about, another shop's records.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Bundle, Client, Lead, LeadType, Part, Service, Tag, WebhookEndpoint
from backend.routers import v1
from backend.services import addresses
from backend.services import leads as lsvc
from backend.routers.bundles import BundleIn as BundleBody
from backend.services.apikeys import ApiPrincipal, get_api_principal, require_scope

router = APIRouter(prefix="/api/v2", tags=["public-api-v2"])


def R(resource: str):
    return Depends(require_scope("read", resource))


def W(resource: str):
    return Depends(require_scope("write", resource))


def _page(items, total, page, size):
    return {"data": items, "page": page, "page_size": size, "total": total}


async def _paged(db, model, org_id: str, ser, page: int, size: int, *conds, order=None):
    cond = [model.org_id == org_id, *conds]
    total = (await db.execute(select(func.count()).select_from(model).where(*cond))).scalar() or 0
    rows = (await db.execute(select(model).where(*cond).order_by(desc(order if order is not None else model.created_at))
                             .limit(size).offset((page - 1) * size))).scalars().all()
    return rows, total


Page = Query(1, ge=1)
Size = Query(25, ge=1, le=100)


@router.get("")
async def index(p: ApiPrincipal = Depends(get_api_principal)):
    """Discovery: any valid key may call it (it shows only the key's own shop and scopes)."""
    base = "/api/v2"
    names = ("contacts", "inquiries", "inquiry_types", "tags", "services", "bundles", "products", "webhooks")
    return {"data": {"version": "2.0", "org_id": p.org_id, "scopes": sorted(p.scopes),
                     "resources": {n: f"{base}/{n.replace('_', '-')}" for n in names},
                     "v1": "/api/v1", "docs": "/docs"}}


# ── contacts (with structured address) ───────────────────────────────────────
def _contact(c: Client) -> dict:
    return {**v1._contact(c), "address_parts": addresses.ser(c)}


class ContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=200)
    client_type: str = "person"
    address: Optional[str] = None
    addr_street: Optional[str] = None
    addr_city: Optional[str] = None
    addr_region: Optional[str] = None
    addr_postcode: Optional[str] = None
    addr_country: Optional[str] = None


@router.get("/contacts")
async def list_contacts(page: int = Page, page_size: int = Size, db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("contacts")):
    rows, total = await _paged(db, Client, p.org_id, _contact, page, page_size)
    return _page([_contact(c) for c in rows], total, page, page_size)


@router.get("/contacts/{cid}")
async def get_contact(cid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("contacts")):
    return {"data": _contact(await lsvc.own(db, Client, cid, p.org_id, "Contact"))}


@router.post("/contacts", status_code=201)
async def create_contact(data: ContactIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("contacts")):
    if data.client_type not in ("person", "organization"):
        raise HTTPException(422, "client_type must be person or organization")
    c = Client(org_id=p.org_id, name=data.name.strip(), phone=data.phone, email=data.email,
               address=data.address, client_type=data.client_type)
    addresses.apply(c, {k: getattr(data, k) for k in addresses.PARTS if k in data.model_fields_set})
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"data": _contact(c)}


# ── inquiries ────────────────────────────────────────────────────────────────
class InquiryIn(BaseModel):
    name: str = Field(..., max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    source: str = "api"
    inquiry: Optional[str] = Field(None, max_length=4000)
    sla_minutes: int = Field(60, ge=1, le=60 * 24 * 30)
    lead_type_id: Optional[str] = None
    manager_id: Optional[str] = None
    client_id: Optional[str] = None
    device_brand: Optional[str] = Field(None, max_length=100)
    device_model: Optional[str] = Field(None, max_length=100)
    serial: Optional[str] = Field(None, max_length=100)
    custom_data: Optional[dict] = None


class InquiryPatch(BaseModel):
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


@router.get("/inquiries")
async def list_inquiries(status: Optional[str] = None, page: int = Page, page_size: int = Size,
                         db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    if status and status not in lsvc.STATUSES:
        raise HTTPException(422, f"status must be one of {lsvc.STATUSES}")
    rows, total = await _paged(db, Lead, p.org_id, None, page, page_size,
                               *([Lead.status == status] if status else []))
    return _page(await lsvc.ser_many(db, rows), total, page, page_size)


@router.get("/inquiries/{lid}")
async def get_inquiry(lid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    return {"data": await lsvc.ser_one(db, await lsvc.own(db, Lead, lid, p.org_id, "Inquiry"))}


@router.post("/inquiries", status_code=201)
async def create_inquiry(data: InquiryIn, response: Response,
                         idempotency_key: Optional[str] = Header(None, max_length=64),
                         db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("inquiries")):
    lead, created = await lsvc.create_lead(db, p.org_id, data.model_dump(),
                                           idempotency_key=idempotency_key, via="api")
    if not created:
        response.status_code = 200
    return {"data": await lsvc.ser_one(db, lead)}


@router.patch("/inquiries/{lid}")
async def update_inquiry(lid: str, data: InquiryPatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("inquiries")):
    lead = await lsvc.own(db, Lead, lid, p.org_id, "Inquiry")
    lead = await lsvc.update_lead(db, p.org_id, lead, data.model_dump(exclude_unset=True))
    return {"data": await lsvc.ser_one(db, lead)}


@router.post("/inquiries/{lid}/convert")
async def convert_inquiry(lid: str, is_estimate: bool = False, db: AsyncSession = Depends(get_db),
                          p: ApiPrincipal = W("inquiries")):
    lead = await lsvc.own(db, Lead, lid, p.org_id, "Inquiry")
    return {"data": await lsvc.convert_lead(db, p.org_id, lead, is_estimate=is_estimate)}


@router.get("/inquiry-types")
async def list_inquiry_types(db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("inquiries")):
    rows = (await db.execute(select(LeadType).where(LeadType.org_id == p.org_id, LeadType.active == True)  # noqa: E712
                             .order_by(LeadType.sort_order, LeadType.name))).scalars().all()
    return {"data": [{"id": t.id, "name": t.name, "sla_minutes": t.sla_minutes} for t in rows]}


# ── tags ─────────────────────────────────────────────────────────────────────
class TagIn(BaseModel):
    name: str = Field(..., max_length=40)
    color: Optional[str] = Field(None, max_length=20)


class TagPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = Field(None, max_length=20)


def _tag(t: Tag) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color,
            "created_at": t.created_at.isoformat() if t.created_at else None}


@router.get("/tags")
async def list_tags(page: int = Page, page_size: int = Size, db: AsyncSession = Depends(get_db),
                    p: ApiPrincipal = R("tags")):
    rows, total = await _paged(db, Tag, p.org_id, None, page, page_size)
    return _page([_tag(t) for t in rows], total, page, page_size)


@router.post("/tags", status_code=201)
async def create_tag(data: TagIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags")):
    from backend.routers.tags import create_tag_core
    return {"data": _tag(await create_tag_core(db, p.org_id, data.name, data.color))}


@router.patch("/tags/{tid}")
async def update_tag(tid: str, data: TagPatch, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags")):
    from backend.routers.tags import get_tag, update_tag_core
    t = await get_tag(db, p.org_id, tid)
    return {"data": _tag(await update_tag_core(db, t, data.name, data.color))}


@router.delete("/tags/{tid}")
async def delete_tag(tid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("tags")):
    from backend.routers.tags import delete_tag_core, get_tag
    await delete_tag_core(db, await get_tag(db, p.org_id, tid))
    return {"data": {"id": tid, "deleted": True}}


# ── services ─────────────────────────────────────────────────────────────────
def _service(s: Service) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "category": s.category,
            "category_id": s.category_id, "price": s.price, "cost_price": s.cost_price,
            "duration_min": s.duration_min, "warranty_days": s.warranty_days, "barcode": s.barcode,
            "unit": s.unit, "is_labor": s.is_labor, "bookable": s.bookable, "active": s.active}


class ServiceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    description: str = Field("", max_length=2000)
    price: float = Field(0.0, ge=0)
    cost_price: float = Field(0.0, ge=0)
    duration_min: int = Field(60, ge=1, le=60 * 24)
    warranty_days: int = Field(0, ge=0, le=3650)
    barcode: Optional[str] = Field(None, max_length=100)
    unit: str = Field("pc", max_length=24)
    is_labor: bool = False
    bookable: bool = True
    active: bool = True


class ServicePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=300)
    description: Optional[str] = Field(None, max_length=2000)
    price: Optional[float] = Field(None, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    duration_min: Optional[int] = Field(None, ge=1, le=60 * 24)
    warranty_days: Optional[int] = Field(None, ge=0, le=3650)
    barcode: Optional[str] = Field(None, max_length=100)
    unit: Optional[str] = Field(None, max_length=24)
    is_labor: Optional[bool] = None
    bookable: Optional[bool] = None
    active: Optional[bool] = None


@router.get("/services")
async def list_services(page: int = Page, page_size: int = Size, db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("services")):
    rows, total = await _paged(db, Service, p.org_id, None, page, page_size)
    return _page([_service(s) for s in rows], total, page, page_size)


@router.get("/services/{sid}")
async def get_service(sid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("services")):
    return {"data": _service(await lsvc.own(db, Service, sid, p.org_id, "Service"))}


@router.post("/services", status_code=201)
async def create_service(data: ServiceIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("services")):
    s = Service(org_id=p.org_id, **{**data.model_dump(), "name": data.name.strip()})
    db.add(s)
    await db.commit()
    return {"data": _service(s)}


@router.patch("/services/{sid}")
async def update_service(sid: str, data: ServicePatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("services")):
    s = await lsvc.own(db, Service, sid, p.org_id, "Service")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None or k == "barcode":
            setattr(s, k, v)
    await db.commit()
    return {"data": _service(s)}


# ── bundles ──────────────────────────────────────────────────────────────────
@router.get("/bundles")
async def list_bundles(page: int = Page, page_size: int = Size, db: AsyncSession = Depends(get_db),
                       p: ApiPrincipal = R("bundles")):
    from backend.routers.bundles import _ser
    rows, total = await _paged(db, Bundle, p.org_id, None, page, page_size, Bundle.active == True)  # noqa: E712
    return _page([await _ser(db, b) for b in rows], total, page, page_size)


@router.get("/bundles/{bid}")
async def get_bundle(bid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("bundles")):
    from backend.routers.bundles import _ser, get_bundle as _get
    return {"data": await _ser(db, await _get(db, p.org_id, bid))}


@router.post("/bundles", status_code=201)
async def create_bundle(data: BundleBody, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("bundles")):
    from backend.routers.bundles import _ser, create_bundle_core
    return {"data": await _ser(db, await create_bundle_core(db, p.org_id, data))}


@router.put("/bundles/{bid}")
async def update_bundle(bid: str, data: BundleBody, db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = W("bundles")):
    from backend.routers.bundles import _ser, get_bundle as _get, update_bundle_core
    b = await _get(db, p.org_id, bid)
    return {"data": await _ser(db, await update_bundle_core(db, b, data))}


@router.delete("/bundles/{bid}")
async def delete_bundle(bid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("bundles")):
    from backend.routers.bundles import delete_bundle_core, get_bundle as _get
    b = await _get(db, p.org_id, bid)
    if b.active:
        await delete_bundle_core(db, b)
    return {"data": {"id": bid, "deleted": True}}


# ── products (now writable) ──────────────────────────────────────────────────
class ProductIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    sku: Optional[str] = Field(None, max_length=100)
    mpn: Optional[str] = Field(None, max_length=100)
    brand: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    uom: str = Field("pcs", max_length=10)
    sell_price: float = Field(0.0, ge=0)
    cost_price: float = Field(0.0, ge=0)
    active: bool = True


class ProductPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=300)
    sku: Optional[str] = Field(None, max_length=100)
    mpn: Optional[str] = Field(None, max_length=100)
    brand: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    uom: Optional[str] = Field(None, max_length=10)
    sell_price: Optional[float] = Field(None, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    active: Optional[bool] = None


async def _save_product(db, p: Part, event: str) -> dict:
    from backend.services.webhooks import emit_event
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Another product already uses this part number (MPN)")
    await emit_event(db, p.org_id, event, {"id": p.id, "sku": p.sku, "name": p.name})
    await db.commit()
    await db.refresh(p)
    return {"data": v1._product(p)}


@router.get("/products")
async def list_products(page: int = Page, page_size: int = Size, db: AsyncSession = Depends(get_db),
                        p: ApiPrincipal = R("products")):
    rows, total = await _paged(db, Part, p.org_id, None, page, page_size)
    return _page([v1._product(r) for r in rows], total, page, page_size)


@router.get("/products/{pid}")
async def get_product(pid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("products")):
    return {"data": v1._product(await lsvc.own(db, Part, pid, p.org_id, "Product"))}


@router.post("/products", status_code=201)
async def create_product(data: ProductIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("products")):
    row = Part(org_id=p.org_id, **{k: (v.strip() or None) if isinstance(v, str) and k != "uom" else v
                                   for k, v in data.model_dump().items()})
    row.name = data.name.strip()
    db.add(row)
    return await _save_product(db, row, "product.created")


@router.patch("/products/{pid}")
async def update_product(pid: str, data: ProductPatch, db: AsyncSession = Depends(get_db),
                         p: ApiPrincipal = W("products")):
    row = await lsvc.own(db, Part, pid, p.org_id, "Product")
    for k, v in data.model_dump(exclude_unset=True).items():
        if k in ("name", "uom", "sell_price", "cost_price", "active") and v is None:
            continue   # required columns can't be cleared
        setattr(row, k, (v.strip() or None) if isinstance(v, str) and k not in ("name", "uom") else v)
    return await _save_product(db, row, "product.updated")


# ── webhooks (subscriptions managed with the API) ────────────────────────────
class HookIn(BaseModel):
    url: str = Field(..., max_length=500)
    events: str = Field("*", max_length=1000)
    active: bool = True


def _hook(e: WebhookEndpoint, secret: bool = False) -> dict:
    d = {"id": e.id, "url": e.url, "events": e.events, "active": e.active,
         "created_at": e.created_at.isoformat() if e.created_at else None}
    if secret:
        d["secret"] = e.secret
    return d


@router.get("/webhooks")
async def list_hooks(db: AsyncSession = Depends(get_db), p: ApiPrincipal = R("webhooks")):
    from backend.services.webhooks import ALIASES, EVENTS
    rows = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.org_id == p.org_id)
                             .order_by(WebhookEndpoint.created_at))).scalars().all()
    return {"data": [_hook(e) for e in rows], "events": EVENTS, "aliases": ALIASES}


@router.post("/webhooks", status_code=201)
async def create_hook(data: HookIn, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("webhooks")):
    """The signing secret is returned once, here."""
    from backend.routers.webhooks import _events, _require_public_url
    await _require_public_url(data.url)
    e = WebhookEndpoint(org_id=p.org_id, url=data.url.strip(), events=_events(data.events), active=data.active)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return {"data": _hook(e, secret=True)}


@router.delete("/webhooks/{hid}")
async def delete_hook(hid: str, db: AsyncSession = Depends(get_db), p: ApiPrincipal = W("webhooks")):
    e = await lsvc.own(db, WebhookEndpoint, hid, p.org_id, "Webhook")
    await db.delete(e)
    await db.commit()
    return {"data": {"id": hid, "deleted": True}}

