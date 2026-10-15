"""Price types & discounts (Orderry Settings > Prices and discounts).
A price type is a named markup over cost. Computed price = basis x (1+markup/100)
where the basis is a product's avg_cost (fallback cost_price) or a service's
cost_price. Clients can carry a price type or a fixed percent as their personal
discount on services/materials in orders & invoices."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Location, Organisation, Part, PriceType, PriceTypeItem, Service
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


from datetime import datetime


def _in_window(p: PriceType, now: Optional[datetime] = None) -> bool:
    """True when a price type's promo window is open (null bounds = open-ended)."""
    now = now or datetime.utcnow()
    if getattr(p, "starts_at", None) and now < p.starts_at:
        return False
    if getattr(p, "ends_at", None) and now > p.ends_at:
        return False
    return True


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


def _ser(p: PriceType) -> dict:
    return {"id": p.id, "name": p.name, "markup_pct": p.markup_pct,
            "is_default": p.is_default, "active": p.active,
            "location_scope": p.location_scope,
            "location_ids": json.loads(p.location_ids or "[]"),
            "starts_at": p.starts_at.isoformat() if getattr(p, "starts_at", None) else None,
            "ends_at": p.ends_at.isoformat() if getattr(p, "ends_at", None) else None,
            "in_window": _in_window(p)}


def computed_price(basis: float, markup_pct: float) -> float:
    """basis x (1 + markup/100), rounded to 2 dp. A zero basis yields 0 — callers
    fall back to the item's standard price in that case."""
    return q2((basis or 0) * (1 + (markup_pct or 0) / 100))


@router.get("/types")
async def list_types(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(PriceType).where(
        PriceType.org_id == current.org_id, PriceType.active == True)  # noqa: E712
        .order_by(PriceType.created_at))).scalars().all()
    return {"items": [_ser(p) for p in rows]}


class TypeIn(BaseModel):
    name: str
    markup_pct: float = 0.0
    is_default: bool = False
    location_scope: str = "all"
    location_ids: list[str] = []
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


@router.post("/types", status_code=201)
async def create_type(
    data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.is_default:
        for p in (await db.execute(select(PriceType).where(
                PriceType.org_id == current.org_id, PriceType.is_default == True))).scalars().all():  # noqa: E712
            p.is_default = False
    p = PriceType(org_id=current.org_id, name=data.name,
                  markup_pct=data.markup_pct, is_default=data.is_default,
                  location_scope=data.location_scope,
                  location_ids=json.dumps(data.location_ids) if data.location_ids else None,
                  starts_at=_parse_dt(data.starts_at), ends_at=_parse_dt(data.ends_at))
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _ser(p)


@router.put("/types/{type_id}")
async def update_type(
    type_id: str,
    data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    p = (await db.execute(select(PriceType).where(
        PriceType.id == type_id, PriceType.org_id == current.org_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Price type not found")
    if data.is_default and not p.is_default:
        for o in (await db.execute(select(PriceType).where(
                PriceType.org_id == current.org_id, PriceType.is_default == True))).scalars().all():  # noqa: E712
            o.is_default = False
    p.name = data.name
    p.markup_pct = data.markup_pct
    p.is_default = data.is_default
    p.starts_at = _parse_dt(data.starts_at)
    p.ends_at = _parse_dt(data.ends_at)
    await db.commit()
    return _ser(p)


@router.delete("/types/{type_id}")
async def delete_type(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    p = (await db.execute(select(PriceType).where(
        PriceType.id == type_id, PriceType.org_id == current.org_id))).scalar_one_or_none()
    if p:
        p.active = False
        p.is_default = False
        await db.commit()
    return {"ok": True}


@router.get("/parts/{part_id}/prices")
async def part_prices(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Computed price per active price type for a product (basis: avg_cost > cost_price)."""
    part = (await db.execute(select(Part).where(
        Part.id == part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Part not found")
    basis = part.avg_cost or part.cost_price or 0
    types = (await db.execute(select(PriceType).where(
        PriceType.org_id == current.org_id, PriceType.active == True))).scalars().all()  # noqa: E712
    return {"basis": basis, "standard_price": part.sell_price,
            "prices": [{"price_type_id": t.id, "name": t.name,
                        "markup_pct": t.markup_pct,
                        "price": computed_price(basis, t.markup_pct) or part.sell_price}
                       for t in types]}


async def _effective_markup(db: AsyncSession, pt, qty: float) -> float:
    """144he — the base markup, or a quantity-break tier's markup when qty reaches it."""
    from backend.models import PriceTypeTier
    tiers = (await db.execute(select(PriceTypeTier).where(
        PriceTypeTier.price_type_id == pt.id).order_by(PriceTypeTier.min_qty))).scalars().all()
    markup = pt.markup_pct
    for t in tiers:
        if (qty or 0) >= (t.min_qty or 0):
            markup = t.markup_pct
    return markup


async def resolve_price_for(db: AsyncSession, org_id: str, *, kind: str,
                            ref_id: Optional[str], price_type_id: str, qty: float = 1) -> Optional[float]:
    """Computed price for a catalog item under a price type, or None when no cost
    basis exists (caller keeps the standard price)."""
    pt = (await db.execute(select(PriceType).where(
        PriceType.id == price_type_id, PriceType.org_id == org_id,
        PriceType.active == True))).scalar_one_or_none()  # noqa: E712
    if not pt or not ref_id or not _in_window(pt):   # 144hb: expired promo -> fall back
        return None
    item_type = "product" if kind == "product" else "service"
    # explicit per-item override wins over the cost x markup derivation
    override = (await db.execute(select(PriceTypeItem).where(
        PriceTypeItem.price_type_id == pt.id, PriceTypeItem.item_type == item_type,
        PriceTypeItem.item_id == ref_id))).scalar_one_or_none()
    if override:
        return override.price
    if kind == "product":
        item = (await db.execute(select(Part).where(Part.id == ref_id))).scalar_one_or_none()
        basis = (item.avg_cost or item.cost_price or 0) if item else 0
    else:
        item = (await db.execute(select(Service).where(Service.id == ref_id))).scalar_one_or_none()
        basis = (item.cost_price or 0) if item else 0
    price = computed_price(basis, await _effective_markup(db, pt, qty))
    return price if price > 0 else None


# ── Margin-floor guardrail (144gy) ──────────────────────────────────────────
async def line_unit_cost(db: AsyncSession, org_id: str, *, kind: Optional[str],
                         ref_id: Optional[str], cost_price: Optional[float] = None) -> float:
    """Unit cost basis for a line: an explicit cost wins, else the catalog item's
    cost (product avg_cost > cost_price; service cost_price)."""
    explicit = float(cost_price or 0) if cost_price and cost_price > 0 else 0.0
    if not ref_id:
        return explicit
    if kind == "product":
        item = (await db.execute(select(Part).where(Part.id == ref_id, Part.org_id == org_id))).scalar_one_or_none()
        catalog = float((item.avg_cost or item.cost_price or 0) if item else 0)
    else:
        item = (await db.execute(select(Service).where(Service.id == ref_id, Service.org_id == org_id))).scalar_one_or_none()
        catalog = float((item.cost_price or 0) if item else 0)
    # A client-supplied cost may raise the basis (special-order parts) but never undercut the
    # catalog cost: sending cost_price=0.01 used to fake a ~100% margin past the guard.
    return max(explicit, catalog)


def pretax_revenue(unit_price: float, qty: float, discount: float, discount_type: str) -> float:
    """Line revenue after discount, before tax."""
    base = (qty or 0) * (unit_price or 0)
    if discount_type == "fixed":
        return max(base - (discount or 0), 0)
    return base * (1 - (discount or 0) / 100)


def margin_pct(revenue: float, cost_total: float) -> Optional[float]:
    """Gross margin %. None when there's no revenue to measure against."""
    if revenue <= 0:
        return None
    return q2((revenue - cost_total) / revenue * 100)


async def evaluate_line_margin(db: AsyncSession, org_id: str, *, kind, ref_id, unit_price,
                               qty, discount=0.0, discount_type="pct", cost_price=None) -> dict:
    """Cost / revenue / margin for one line, plus the org floor and a pass/block
    verdict. Reused by the WO line-add guard and the FE preview."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    floor = float(getattr(org, "min_margin_pct", 0) or 0)
    guard = getattr(org, "margin_guard", "off") or "off"
    unit_cost = await line_unit_cost(db, org_id, kind=kind, ref_id=ref_id, cost_price=cost_price)
    cost_total = unit_cost * (qty or 0)
    rev = pretax_revenue(unit_price, qty, discount, discount_type)
    m = margin_pct(rev, cost_total)
    # ok unless we can measure a margin and it falls under the floor (and a cost exists)
    # Zero/negative revenue on a line that has a cost is the worst margin there is — it used to
    # return None and pass (100% discounts, fixed discounts >= the price).
    below = unit_cost > 0 and guard != "off" and (m is None or m < floor)
    return {"unit_cost": q2(unit_cost), "cost_total": q2(cost_total),
            "revenue": q2(rev), "margin_pct": m, "floor": floor,
            "guard": guard, "below_floor": below, "blocked": below and guard == "block"}


class MarginCheckIn(BaseModel):
    kind: Optional[str] = None          # product|service
    ref_id: Optional[str] = None
    unit_price: float = 0.0
    qty: float = 1.0
    discount: float = 0.0
    discount_type: str = "pct"          # pct|fixed
    cost_price: Optional[float] = None


@router.post("/margin-check")
async def margin_check(
    data: MarginCheckIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Live margin evaluation for a prospective line — powers the effective-price
    preview and the below-floor warning in the work-order editor."""
    return await evaluate_line_margin(
        db, current.org_id, kind=data.kind, ref_id=data.ref_id, unit_price=data.unit_price,
        qty=data.qty, discount=data.discount, discount_type=data.discount_type, cost_price=data.cost_price)


async def default_price_type_id(db, org_id: str, *, doc_type: str = "tickets",
                                location_id: Optional[str] = None) -> Optional[str]:
    """Resolve which price list applies for a document: location override ->
    org default for that doc type -> the org's default price type -> None."""
    if location_id:
        loc = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
        if loc and getattr(loc, "price_type_id", None):
            return loc.price_type_id
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if org:
        by_doc = {"tickets": org.price_type_tickets_id, "sales": org.price_type_sales_id,
                  "invoices": org.price_type_invoices_id}.get(doc_type)
        if by_doc:
            return by_doc
    dflt = (await db.execute(select(PriceType).where(
        PriceType.org_id == org_id, PriceType.is_default == True))).scalar_one_or_none()  # noqa: E712
    return dflt.id if dflt else None


async def resolve_doc_price(db, org_id: str, *, kind: str, ref_id: Optional[str],
                            doc_type: str = "tickets", location_id: Optional[str] = None) -> Optional[float]:
    """Doc-type-aware convenience: pick the price list for this document, then
    compute the item price. Returns None to fall back to the standard price."""
    ptid = await default_price_type_id(db, org_id, doc_type=doc_type, location_id=location_id)
    if not ptid:
        return None
    return await resolve_price_for(db, org_id, kind=kind, ref_id=ref_id, price_type_id=ptid)

# ── Explicit item price overrides within a price type ───────────────────────
class PriceItemIn(BaseModel):
    item_type: str    # product|service
    item_id: str
    price: float


@router.get("/types/{type_id}/items")
async def list_type_items(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(PriceTypeItem).where(
        PriceTypeItem.price_type_id == type_id,
        PriceTypeItem.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": r.id, "item_type": r.item_type, "item_id": r.item_id,
                       "price": r.price} for r in rows]}


@router.put("/types/{type_id}/items")
async def set_type_item(
    type_id: str, data: PriceItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.item_type not in ("product", "service"):
        raise HTTPException(422, "item_type must be product|service")
    row = (await db.execute(select(PriceTypeItem).where(
        PriceTypeItem.price_type_id == type_id, PriceTypeItem.item_type == data.item_type,
        PriceTypeItem.item_id == data.item_id, PriceTypeItem.org_id == current.org_id))).scalar_one_or_none()
    if row:
        row.price = data.price
    else:
        db.add(PriceTypeItem(org_id=current.org_id, price_type_id=type_id,
                             item_type=data.item_type, item_id=data.item_id, price=data.price))
    await db.commit()
    return {"ok": True}


# ── Quantity break tiers (144he) ────────────────────────────────────────────
class TierIn(BaseModel):
    min_qty: float
    markup_pct: float


@router.get("/types/{type_id}/tiers")
async def list_tiers(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import PriceTypeTier
    rows = (await db.execute(select(PriceTypeTier).where(
        PriceTypeTier.price_type_id == type_id, PriceTypeTier.org_id == current.org_id)
        .order_by(PriceTypeTier.min_qty))).scalars().all()
    return {"items": [{"id": r.id, "min_qty": r.min_qty, "markup_pct": r.markup_pct} for r in rows]}


@router.post("/types/{type_id}/tiers", status_code=201)
async def add_tier(
    type_id: str, data: TierIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import PriceTypeTier
    pt = (await db.execute(select(PriceType).where(
        PriceType.id == type_id, PriceType.org_id == current.org_id))).scalar_one_or_none()
    if not pt:
        raise HTTPException(404, "Price type not found")
    t = PriceTypeTier(org_id=current.org_id, price_type_id=type_id,
                      min_qty=data.min_qty, markup_pct=data.markup_pct)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id, "min_qty": t.min_qty, "markup_pct": t.markup_pct}


@router.delete("/types/{type_id}/tiers/{tier_id}")
async def delete_tier(
    type_id: str, tier_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import PriceTypeTier
    t = (await db.execute(select(PriceTypeTier).where(
        PriceTypeTier.id == tier_id, PriceTypeTier.org_id == current.org_id))).scalar_one_or_none()
    if t:
        await db.delete(t)
        await db.commit()
    return {"ok": True}


# ── Company defaults: which price type per doc type + decimals ──────────────
class PriceDefaultsIn(BaseModel):
    price_type_tickets_id: Optional[str] = None
    price_type_sales_id: Optional[str] = None
    price_type_invoices_id: Optional[str] = None
    price_decimals: Optional[int] = None
    min_margin_pct: Optional[float] = None
    margin_guard: Optional[str] = None       # off | warn | block
    discount_approval_pct: Optional[float] = None


@router.get("/defaults")
async def get_price_defaults(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    return {"price_type_tickets_id": org.price_type_tickets_id,
            "price_type_sales_id": org.price_type_sales_id,
            "price_type_invoices_id": org.price_type_invoices_id,
            "price_decimals": org.price_decimals,
            "min_margin_pct": getattr(org, "min_margin_pct", 0) or 0,
            "margin_guard": getattr(org, "margin_guard", "off") or "off",
            "discount_approval_pct": getattr(org, "discount_approval_pct", 0) or 0}


@router.put("/defaults")
async def set_price_defaults(
    data: PriceDefaultsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(org, k, v)
    await db.commit()
    return {"ok": True}
