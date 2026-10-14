"""
Catalog — the Services & Labors directory, plus a unified search that the
work-order line editor uses to pick either a service or a product (part).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.database import get_db
from backend.models import Employee, Part, Service
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/code.svg")
async def render_code_svg(
    data: str, type: str = "code128", height: int = 48,
    current: Employee = Depends(get_current_employee),
):
    """Build 144ef — the single app-wide barcode/QR image source. Renders any
    value as a real Code128 or QR SVG (reusing the same generator the label/print
    engine uses), so Products, Services, Work orders, etc. all show identical codes."""
    from backend.services.codes import code128_svg, qr_svg
    data = (data or "").strip()
    if not data:
        raise HTTPException(400, "data is required")
    svg = qr_svg(data) if type == "qr" else code128_svg(data, height=max(16, min(height, 160)))
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


class ServiceIn(BaseModel):
    name: str
    description: str = ""                # 144ee
    category: Optional[str] = None
    category_id: Optional[str] = None
    is_labor: bool = False              # 144ee: Labor vs Service
    unit: str = "pc"                    # 144ee: unit of measurement
    price: float = 0.0
    cost_price: float = 0.0
    duration_min: int = 60             # 144ee: surfaced (drives appointment slot length)
    flat_rate: bool = False            # 144ax G3: bill booked hours × rate
    warranty_days: int = 0
    barcode: Optional[str] = None
    bookable: bool = True              # 144ee: show on public appointment page
    allowed_employee_ids: list = []   # 144ek: who can perform (empty = all)
    required_resource_types: list = []# 144ek: resource types this service needs


def _ser(s: Service, cat_names: Optional[dict] = None) -> dict:
    margin = 0
    if s.price > 0:
        margin = round((s.price - s.cost_price) / s.price * 100)
    return {
        "id": s.id, "name": s.name, "description": getattr(s, "description", "") or "",
        "category": s.category,
        "category_id": s.category_id,
        "category_name": (cat_names or {}).get(s.category_id) if s.category_id else None,
        "is_labor": getattr(s, "is_labor", False), "unit": getattr(s, "unit", "pc") or "pc",
        "price": s.price, "cost_price": s.cost_price,
        "duration_min": getattr(s, "duration_min", 60),
        "flat_rate": getattr(s, "flat_rate", False),
        "warranty_days": s.warranty_days, "barcode": s.barcode,
        "bookable": getattr(s, "bookable", True),
        "allowed_employee_ids": getattr(s, "allowed_employee_ids", None) or [],
        "required_resource_types": getattr(s, "required_resource_types", None) or [],
        "margin_pct": margin, "active": s.active,
    }


async def _service_cat_names(db, org_id) -> dict:
    from backend.models import Category
    rows = (await db.execute(select(Category).where(
        Category.org_id == org_id, Category.entity_type == "service"))).scalars().all()
    return {c.id: c.name for c in rows}


# ── Services directory CRUD ──────────────────────────

@router.get("/services")
async def list_services(
    search: Optional[str] = None,
    category: Optional[str] = None,
    category_id: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Service).where(Service.org_id == current.org_id, Service.active == True)  # noqa: E712
    if search:
        q = q.where(Service.name.ilike(f"%{search}%"))
    if category:
        q = q.where(Service.category == category)
    if category_id:
        q = q.where(Service.category_id == category_id)
    q = q.order_by(Service.name).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    total = (await db.execute(
        select(func.count()).select_from(Service).where(Service.org_id == current.org_id, Service.active == True)  # noqa: E712
    )).scalar() or 0
    names = await _service_cat_names(db, current.org_id)
    return {"total": total, "items": [_ser(s, names) for s in rows]}


@router.post("/services", status_code=201)
async def create_service(
    data: ServiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_labors")),
):
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    s = Service(
        org_id=current.org_id, name=data.name, description=(data.description or "")[:2000],
        category=data.category, category_id=data.category_id,
        is_labor=data.is_labor, unit=(data.unit or "pc")[:24],
        price=data.price, cost_price=data.cost_price,
        duration_min=max(0, data.duration_min or 0), flat_rate=data.flat_rate,
        warranty_days=data.warranty_days, barcode=data.barcode,
        bookable=data.bookable,
        allowed_employee_ids=list(data.allowed_employee_ids or []),
        required_resource_types=list(data.required_resource_types or []),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _ser(s, await _service_cat_names(db, current.org_id))


@router.put("/services/{service_id}")
async def update_service(
    service_id: str, data: ServiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_labors")),
):
    s = (await db.execute(select(Service).where(Service.id == service_id, Service.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Service not found")
    s.name = data.name
    s.description = (data.description or "")[:2000]
    s.category = data.category
    s.category_id = data.category_id
    s.is_labor = data.is_labor
    s.unit = (data.unit or "pc")[:24]
    s.price = data.price
    s.cost_price = data.cost_price
    s.duration_min = max(0, data.duration_min or 0)
    s.flat_rate = data.flat_rate
    s.warranty_days = data.warranty_days
    s.barcode = data.barcode
    s.bookable = data.bookable
    s.allowed_employee_ids = list(data.allowed_employee_ids or [])
    s.required_resource_types = list(data.required_resource_types or [])
    await db.commit()
    return _ser(s, await _service_cat_names(db, current.org_id))


@router.delete("/services/{service_id}")
async def delete_service(
    service_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_labors")),
):
    s = (await db.execute(select(Service).where(Service.id == service_id, Service.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Service not found")
    # 144co: don't let a delete silently orphan a job kit that uses this service.
    from backend.services.catalog_links import catalog_usage
    used = await catalog_usage(db, current.org_id, "service", service_id)
    if used:
        names = ", ".join(u["name"] for u in used[:5])
        raise HTTPException(409, f"This service is used in {len(used)} job kit(s): {names}. Remove it there first.")
    s.active = False
    await db.commit()
    return {"ok": True}


# ── Service exceptional commissions (Orderry parity) ──
# Per-employee (or all-staff) commission rules scoped to one service. Persisted as
# service-level CommissionRule rows; the payroll engine prefers them for that
# service's labor lines over category/generic rules.

class ServiceCommissionIn(BaseModel):
    employee_id: Optional[str] = None      # None = applies to all staff
    calc_type: str = "percent"             # percent|flat
    amount: float = 0                       # % (0-100) or flat money
    percent_basis: str = "total"           # total|margin


def _ser_svc_comm(r) -> dict:
    return {"id": r.id, "employee_id": r.employee_id, "calc_type": r.calc_type,
            "amount": float(r.amount or 0), "percent_basis": r.percent_basis}


@router.get("/services/{service_id}/commissions")
async def list_service_commissions(
    service_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import CommissionRule
    rows = (await db.execute(select(CommissionRule).where(
        CommissionRule.org_id == current.org_id, CommissionRule.rule_level == "service",
        CommissionRule.service_id == service_id, CommissionRule.active == True))).scalars().all()  # noqa: E712
    return {"items": [_ser_svc_comm(r) for r in rows]}


@router.post("/services/{service_id}/commissions", status_code=201)
async def add_service_commission(
    service_id: str, data: ServiceCommissionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_config_exceptional_commissions")),
):
    from decimal import Decimal
    from backend.models import CommissionRule
    s = (await db.execute(select(Service).where(
        Service.id == service_id, Service.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Service not found")
    r = CommissionRule(
        org_id=current.org_id, name=f"{s.name} commission", event="labor_performed",
        rule_level="service", service_id=service_id, employee_id=data.employee_id,
        calc_type=("flat" if data.calc_type == "flat" else "percent"),
        percent_basis=("margin" if data.percent_basis == "margin" else "total"),
        amount=Decimal(str(data.amount or 0)), calc_timing="on_close")
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _ser_svc_comm(r)


@router.delete("/services/{service_id}/commissions/{rule_id}")
async def delete_service_commission(
    service_id: str, rule_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_config_exceptional_commissions")),
):
    from backend.models import CommissionRule
    r = (await db.execute(select(CommissionRule).where(
        CommissionRule.id == rule_id, CommissionRule.org_id == current.org_id,
        CommissionRule.service_id == service_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Commission rule not found")
    await db.delete(r)
    await db.commit()
    return {"ok": True}


# ── Unified search for the line editor ───────────────

@router.get("/search")
async def catalog_search(
    q: str = "",
    type: str = "all",   # all | service | product
    device_id: str = "",   # 144jt: when set, flag products that fit this device
    limit: int = 8,        # 144da: browse mode (job-kit picker) asks for more
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Return services and/or products matching the query, for the
    'Service, labor, or product' field in a work order."""
    out = []

    dev_brand = dev_model = None
    if device_id:
        from backend.models import Device
        dev = (await db.execute(select(Device).where(
            Device.id == device_id, Device.org_id == current.org_id))).scalar_one_or_none()
        if dev:
            dev_brand, dev_model = dev.brand, dev.model

    if type in ("all", "service"):
        sq = select(Service).where(Service.org_id == current.org_id, Service.active == True)  # noqa: E712
        if q:
            sq = sq.where(or_(Service.name.ilike(f"%{q}%"), Service.barcode == q))
        services = (await db.execute(sq.order_by(Service.name).limit(max(1, min(limit, 200))))).scalars().all()
        for s in services:
            out.append({
                "kind": "service", "ref_id": s.id, "name": s.name,
                "category": s.category, "price": s.price, "cost_price": s.cost_price,
                "available": None,
            })

    if type in ("all", "product"):
        pq = select(Part).where(Part.org_id == current.org_id, Part.active == True).options(selectinload(Part.stock_levels))  # noqa: E712
        if q:
            pq = pq.where(or_(Part.name.ilike(f"%{q}%"), Part.sku.ilike(f"%{q}%"), Part.barcode == q))
        parts = (await db.execute(pq.order_by(Part.name).limit(max(1, min(limit, 200))))).scalars().all()
        for p in parts:
            avail = sum(sl.qty_available for sl in p.stock_levels)
            item = {
                "kind": "product", "ref_id": p.id, "name": p.name,
                "category": p.category, "price": p.sell_price, "cost_price": p.cost_price,
                "repair_price": (p.repair_price or 0) or p.sell_price,  # 144bk: WO price
                "available": avail, "sku": p.sku,
            }
            if dev_brand:
                from backend.services.compatibility import part_fits
                item["fits"] = part_fits(p.compatibility_json, dev_brand, dev_model)
            out.append(item)

    # Bundles ride the same picker: one pick explodes into components on the order.
    if type in ("all", "bundle"):
        from backend.models import Bundle, BundleComponent
        bq = select(Bundle).where(Bundle.org_id == current.org_id, Bundle.active == True)  # noqa: E712
        if q:
            bq = bq.where(Bundle.name.ilike(f"%{q}%"))
        for b in (await db.execute(bq.order_by(Bundle.name).limit(5))).scalars().all():
            comps = (await db.execute(select(BundleComponent).where(
                BundleComponent.bundle_id == b.id))).scalars().all()
            price = b.price
            if b.price_mode == "sum":
                total = 0.0
                for c in comps:
                    if c.ref_type == "product":
                        it = (await db.execute(select(Part).where(Part.id == c.ref_id))).scalar_one_or_none()
                        total += (it.sell_price if it else 0) * c.qty
                    else:
                        it = (await db.execute(select(Service).where(Service.id == c.ref_id))).scalar_one_or_none()
                        total += (it.price if it else 0) * c.qty
                price = q2(total)
            out.append({"kind": "bundle", "ref_id": b.id, "name": b.name,
                        "category": "Job Kit", "price": price, "cost_price": 0,
                        "available": None, "components": len(comps)})

    return {"items": out}


# ── Taxes (named, reusable rates) ────────────────────
from backend.models import Tax
from backend.services.money import q2, q4


class TaxIn(BaseModel):
    name: str
    rate: float = 0.0
    mode: str = "imposed"   # included|imposed
    is_default: bool = False
    code: Optional[str] = None
    name_for_document: Optional[str] = None
    location_scope: str = "all"
    location_ids: list[str] = []

def _ser_tax(t: Tax) -> dict:
    import json as _j
    return {"id": t.id, "name": t.name, "rate": t.rate, "mode": t.mode,
            "code": t.code, "name_for_document": t.name_for_document,
            "location_scope": t.location_scope,
            "location_ids": _j.loads(t.location_ids or "[]"),
            "is_default": t.is_default, "active": t.active}

@router.get("/taxes")
async def list_taxes(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(
        select(Tax).where(Tax.org_id == current.org_id, Tax.active == True)  # noqa: E712
        .order_by(Tax.name)
    )).scalars().all()
    return {"items": [_ser_tax(t) for t in rows]}

@router.post("/taxes", status_code=201)
async def create_tax(
    data: TaxIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    if data.is_default:
        # clear other defaults
        existing = (await db.execute(select(Tax).where(Tax.org_id == current.org_id, Tax.is_default == True))).scalars().all()  # noqa: E712
        for e in existing:
            e.is_default = False
    import json as _j2
    t = Tax(org_id=current.org_id, name=data.name, rate=data.rate, mode=data.mode,
            code=data.code, name_for_document=data.name_for_document,
            location_scope=data.location_scope,
            location_ids=_j2.dumps(data.location_ids) if data.location_ids else None,
            is_default=data.is_default)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _ser_tax(t)

@router.put("/taxes/{tax_id}")
async def update_tax(
    tax_id: str, data: TaxIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(Tax).where(Tax.id == tax_id, Tax.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tax not found")
    if data.is_default and not t.is_default:
        existing = (await db.execute(select(Tax).where(Tax.org_id == current.org_id, Tax.is_default == True))).scalars().all()  # noqa: E712
        for e in existing:
            e.is_default = False
    import json as _j3
    t.name, t.rate, t.mode, t.is_default = data.name, data.rate, data.mode, data.is_default
    t.code, t.name_for_document, t.location_scope = data.code, data.name_for_document, data.location_scope
    t.location_ids = _j3.dumps(data.location_ids) if data.location_ids else None
    await db.commit()
    return _ser_tax(t)

@router.delete("/taxes/{tax_id}")
async def delete_tax(
    tax_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(Tax).where(Tax.id == tax_id, Tax.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tax not found")
    t.active = False
    await db.commit()
    return {"ok": True}
