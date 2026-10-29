"""Bundles — catalog kits that explode into components on the order."""
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import Bundle, BundleComponent, Part, Service, Employee
from backend.services.audit import record as _audit
from backend.services.auth import assert_perm, get_current_employee, require_perm
from backend.services.kit_print import PRINT_MODES, norm_mode
from backend.services.money import q2

router = APIRouter(prefix="/api/bundles", tags=["bundles"])


async def _resolve(db, org_id, ref_type, ref_id):
    """Resolve a component to (name, price, missing) — always org-scoped so a bundle can never show
    or price another tenant's catalog item (144co)."""
    if ref_type == "product":
        item = (await db.execute(select(Part).where(
            Part.id == ref_id, Part.org_id == org_id))).scalar_one_or_none()
        return (item.name, item.sell_price, False) if item else ("(deleted item)", 0, True)
    item = (await db.execute(select(Service).where(
        Service.id == ref_id, Service.org_id == org_id))).scalar_one_or_none()
    return (item.name, item.price, False) if item else ("(deleted item)", 0, True)


async def _ser(db, b: Bundle) -> dict:
    comps = (await db.execute(select(BundleComponent).where(
        BundleComponent.bundle_id == b.id))).scalars().all()
    out = []
    total_std = 0.0
    for c in comps:
        name, price, missing = await _resolve(db, b.org_id, c.ref_type, c.ref_id)
        total_std += (price or 0) * c.qty
        out.append({"id": c.id, "ref_type": c.ref_type, "ref_id": c.ref_id,
                    "name": name, "qty": c.qty, "std_price": price, "missing": missing,
                    "device_variant": bool(getattr(c, "device_variant", False))})
    return {"id": b.id, "name": b.name, "price_mode": b.price_mode, "price": b.price,
            "category_id": getattr(b, "category_id", None),
            "print_mode": norm_mode(getattr(b, "print_mode", None)),
            "components": out, "sum_price": q2(total_std),
            "broken": any(c["missing"] for c in out),
            "effective_price": q2(b.price if b.price_mode == "fixed" else total_std)}


async def _validate_components(db, org_id, comps) -> None:
    """Every component must point at this org's own part/service (144co) — reject cross-org or bogus ids."""
    for c in comps:
        if c.ref_type not in ("product", "service"):
            raise HTTPException(400, "Component ref_type must be product or service")
        _n, _p, missing = await _resolve(db, org_id, c.ref_type, c.ref_id)
        if missing:
            raise HTTPException(400, f"Component {c.ref_id} is not one of your {c.ref_type}s")


@router.get("")
async def list_bundles(
    category_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Bundle).where(Bundle.org_id == current.org_id, Bundle.active == True)  # noqa: E712
    if category_id:
        q = q.where(Bundle.category_id == category_id)
    rows = (await db.execute(q.order_by(Bundle.created_at))).scalars().all()
    from backend.models import Organisation
    from backend.services.kit_print import itemise_warning
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    return {"items": [await _ser(db, b) for b in rows],
            "print_warning": itemise_warning(getattr(org, "country", None), getattr(org, "industry_template", None))}


@router.get("/print-warning")
async def print_warning(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The itemising-law reminder shown before a kit is set to print as one line (or null)."""
    from backend.models import Organisation
    from backend.services.kit_print import itemise_warning
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    return {"warning": itemise_warning(getattr(org, "country", None), getattr(org, "industry_template", None))}


@router.get("/using")
async def where_used(
    ref_type: str, ref_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144co — which bundles / job kits reference this part or service ('where used')."""
    from backend.services.catalog_links import catalog_usage
    return {"items": await catalog_usage(db, current.org_id, ref_type, ref_id)}


class CompIn(BaseModel):
    ref_type: str  # service|product
    ref_id: str
    qty: float = 1
    device_variant: bool = False   # 144db — product slot auto-picks the part that fits the WO's device


class BundleIn(BaseModel):
    name: str
    price_mode: Literal["sum", "fixed"] = "sum"
    price: float = 0.0
    category_id: Optional[str] = None
    print_mode: Optional[str] = None   # itemized (default) | one_line | one_line_includes
    components: list[CompIn] = []


def _check_price(price):
    from backend.services.kit_print import price_ok
    if not price_ok(price):
        raise HTTPException(400, "Enter a kit price of 0 or more")


def _check_mode(mode):
    if mode is not None and mode not in PRINT_MODES:
        raise HTTPException(400, "Choose One line, One line and what's included, or Item by item")


async def _emit(db, org_id: str, event: str, b: Bundle) -> None:
    from backend.services.webhooks import emit_event
    await emit_event(db, org_id, event, {"id": b.id, "name": b.name, "active": b.active})


async def _check_bundle_fields(db, org_id: str, data: "BundleIn") -> None:
    if data.price_mode not in ("sum", "fixed"):
        raise HTTPException(422, "price_mode must be sum or fixed")
    if data.category_id:
        from backend.models import Category
        ok = (await db.execute(select(Category.id).where(
            Category.id == data.category_id, Category.org_id == org_id))).scalar_one_or_none()
        if not ok:
            raise HTTPException(404, "Category not found")


async def create_bundle_core(db: AsyncSession, org_id: str, data: "BundleIn",
                             actor_id: Optional[str] = None) -> Bundle:
    """Shared by the staff screen and API v2. Commits and emits bundle.created."""
    if not data.components:
        raise HTTPException(400, "A bundle needs at least one component")
    await _check_bundle_fields(db, org_id, data)
    await _validate_components(db, org_id, data.components)
    _check_mode(data.print_mode)
    _check_price(data.price)
    b = Bundle(org_id=org_id, name=data.name,
               price_mode=data.price_mode, price=data.price,
               category_id=data.category_id or None, print_mode=norm_mode(data.print_mode))
    db.add(b)
    await db.flush()
    if b.print_mode != "itemized":
        await _audit(db, org_id=org_id, actor_id=actor_id, action="bundle.print_mode",
                     entity_type="bundle", entity_id=b.id, diff={"print_mode": [None, b.print_mode]})
    for c in data.components:
        db.add(BundleComponent(bundle_id=b.id, ref_type=c.ref_type, ref_id=c.ref_id, qty=c.qty, device_variant=bool(getattr(c, "device_variant", False))))
    await _emit(db, org_id, "bundle.created", b)
    await db.commit()
    return b


async def get_bundle(db: AsyncSession, org_id: str, bundle_id: str) -> Bundle:
    b = (await db.execute(select(Bundle).where(
        Bundle.id == bundle_id, Bundle.org_id == org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bundle not found")
    return b


async def update_bundle_core(db: AsyncSession, b: Bundle, data: "BundleIn",
                             actor_id: Optional[str] = None) -> Bundle:
    # A1: write only what was sent — a body without category_id keeps the category, and one
    # without components keeps the kit's items instead of emptying it.
    sent = data.model_fields_set
    await _check_bundle_fields(db, b.org_id, data if "price_mode" in sent else data.model_copy(update={"price_mode": b.price_mode}))
    b.name = data.name
    if "price_mode" in sent:
        b.price_mode = data.price_mode
    if "price" in sent:
        _check_price(data.price)
        b.price = data.price
    if "category_id" in sent:
        b.category_id = data.category_id or None
    if "print_mode" in sent and data.print_mode is not None:
        _check_mode(data.print_mode)
        before = norm_mode(b.print_mode)
        if before != data.print_mode:
            b.print_mode = data.print_mode
            # Only new jobs pick this up; kits already on jobs keep how they print.
            await _audit(db, org_id=b.org_id, actor_id=actor_id, action="bundle.print_mode",
                         entity_type="bundle", entity_id=b.id, diff={"print_mode": [before, data.print_mode]})
    if "components" in sent:
        if not data.components:
            raise HTTPException(400, "A bundle needs at least one component")
        await _validate_components(db, b.org_id, data.components)
        for old in (await db.execute(select(BundleComponent).where(
                BundleComponent.bundle_id == b.id))).scalars().all():
            await db.delete(old)
        for c in data.components:
            db.add(BundleComponent(bundle_id=b.id, ref_type=c.ref_type, ref_id=c.ref_id, qty=c.qty, device_variant=bool(getattr(c, "device_variant", False))))
    await _emit(db, b.org_id, "bundle.updated", b)
    await db.commit()
    return b


async def delete_bundle_core(db: AsyncSession, b: Bundle) -> None:
    b.active = False
    await _emit(db, b.org_id, "bundle.deleted", b)
    await db.commit()


@router.post("", status_code=201)
async def create_bundle(
    data: BundleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    await assert_perm(db, current, "can_manage_bundles")
    return await _ser(db, await create_bundle_core(db, current.org_id, data, actor_id=current.id))


@router.put("/{bundle_id}")
async def update_bundle(
    bundle_id: str, data: BundleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    await assert_perm(db, current, "can_manage_bundles")
    b = await get_bundle(db, current.org_id, bundle_id)
    return await _ser(db, await update_bundle_core(db, b, data, actor_id=current.id))


@router.delete("/{bundle_id}")
async def delete_bundle(
    bundle_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    await assert_perm(db, current, "can_manage_bundles")
    b = (await db.execute(select(Bundle).where(
        Bundle.id == bundle_id, Bundle.org_id == current.org_id))).scalar_one_or_none()
    if b and b.active:
        await delete_bundle_core(db, b)
    return {"ok": True}
