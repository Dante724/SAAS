"""Build 76/77 — Product ↔ Asset conversion API + the Conversions document list."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import AssetProductConversion, Device, Employee, Part
from backend.services import conversion as conv_svc
from backend.services.dates import parse_date
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/conversions", tags=["conversions"])


@router.get("")
async def list_conversions(
    direction: Optional[str] = None,
    part_id: Optional[str] = None,
    device_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The Conversions page: every conversion document, newest first, filterable by
    direction, product, asset, employee and date."""
    q = select(AssetProductConversion).where(AssetProductConversion.org_id == current.org_id)
    if direction:
        q = q.where(AssetProductConversion.direction == direction)
    if part_id:
        q = q.where(AssetProductConversion.part_id == part_id)
    if device_id:
        q = q.where(AssetProductConversion.device_id == device_id)
    if employee_id:
        q = q.where(AssetProductConversion.created_by_id == employee_id)
    if date_from:
        q = q.where(AssetProductConversion.created_at >= datetime.combine(
            parse_date(date_from, "date_from"), datetime.min.time()))
    if date_to:
        q = q.where(AssetProductConversion.created_at <= datetime.combine(
            parse_date(date_to, "date_to"), datetime.max.time()))
    rows = (await db.execute(
        q.order_by(AssetProductConversion.created_at.desc()).limit(300))).scalars().all()

    # decorate with display names so the list is clickable without N+1 on the client
    part_ids = {r.part_id for r in rows if r.part_id}
    dev_ids = {r.device_id for r in rows if r.device_id}
    parts = {p.id: p for p in (await db.execute(select(Part).where(
        Part.id.in_(part_ids)))).scalars().all()} if part_ids else {}
    devs = {d.id: d for d in (await db.execute(select(Device).where(
        Device.id.in_(dev_ids)))).scalars().all()} if dev_ids else {}
    emps = {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id))).scalars().all()}

    items = []
    for r in rows:
        d = conv_svc.serialize(r)
        p, dev = parts.get(r.part_id), devs.get(r.device_id)
        d["product_name"] = p.name if p else (r.metadata_json or {}).get("product_name")
        d["asset_name"] = " ".join(x for x in [dev.brand, dev.model] if x) if dev else None
        d["employee_name"] = emps.get(r.created_by_id)
        items.append(d)
    return {"items": items}


@router.get("/prefill/product/{unit_id}")
async def prefill_product(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    return await conv_svc.prefill_product_to_asset(db, current.org_id, unit_id)


@router.get("/prefill/asset/{device_id}")
async def prefill_asset(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    return await conv_svc.prefill_asset_to_product(db, current.org_id, device_id)


class ProductToAssetIn(BaseModel):
    serialized_item_id: str
    owner_type: str = "company"          # company|client|supplier
    client_id: Optional[str] = None
    template_key: Optional[str] = None
    asset_group_id: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    condition: Optional[str] = None
    custom_data: Optional[dict] = None
    notes: Optional[str] = None


@router.post("/product-to-asset", status_code=201)
async def convert_product_to_asset(
    data: ProductToAssetIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_convert_product_to_asset")),
):
    if data.owner_type not in ("company", "client", "supplier"):
        raise HTTPException(400, "owner_type must be company|client|supplier")
    device, conv = await conv_svc.product_to_asset(
        db, current.org_id, current.id,
        unit_id=data.serialized_item_id, owner_type=data.owner_type,
        client_id=data.client_id, template_key=data.template_key,
        asset_group_id=data.asset_group_id, brand=data.brand, model=data.model,
        condition=data.condition, custom_data=data.custom_data, notes=data.notes)
    return {"ok": True, "device_id": device.id, "conversion": conv_svc.serialize(conv)}


class AssetToProductIn(BaseModel):
    device_id: str
    part_id: Optional[str] = None         # link an existing product instead of creating one
    name: Optional[str] = None
    sku: Optional[str] = None             # never auto-filled from the serial
    category: Optional[str] = None
    cost_price: float = 0
    sell_price: float = 0
    create_stock_unit: bool = False
    location_id: Optional[str] = None
    notes: Optional[str] = None


@router.post("/asset-to-product", status_code=201)
async def convert_asset_to_product(
    data: AssetToProductIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_convert_asset_to_product")),
):
    part, conv = await conv_svc.asset_to_product(
        db, current.org_id, current.id,
        device_id=data.device_id, part_id=data.part_id, name=data.name, sku=data.sku,
        category=data.category, cost_price=data.cost_price, sell_price=data.sell_price,
        create_stock_unit=data.create_stock_unit, location_id=data.location_id,
        notes=data.notes)
    return {"ok": True, "part_id": part.id, "conversion": conv_svc.serialize(conv)}


@router.delete("/{conversion_id}")
async def delete_conversion(conversion_id: str):
    """Conversion documents are immutable. Reverse by converting the other way."""
    raise HTTPException(
        405, "Conversion documents can't be deleted. To reverse one, convert the item "
             "back the other way — that writes a second document and links the pair.")
