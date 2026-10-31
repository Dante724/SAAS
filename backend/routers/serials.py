"""Serialized units and their passport (ii144c, council 2026-10-25 item 10).

One unit, everything about it: what it cost and who supplied it, until when the supplier covers
it, where it is now (location, bin, the job it's on, the asset it became) and its history — every
receipt, move, job, sale and write-off (services/serial_log.py). Cost is shown only to staff who
can see purchase prices. Every read and write is scoped to the caller's shop.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Device, Employee, Location, Order, Part, Sale, SerialEvent, SerializedItem, StorageBin, Supplier, Warehouse,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.redact import can_see_costs

router = APIRouter(prefix="/api/serials", tags=["serials"])

STATUS_LABELS = {"in_stock": "In stock", "allocated_to_order": "On a job", "sold": "Sold",
                 "written_off": "Written off", "converted_to_asset": "Became an asset"}


async def _unit(db, org_id: str, unit_id: str) -> SerializedItem:
    u = (await db.execute(select(SerializedItem).where(
        SerializedItem.id == unit_id, SerializedItem.org_id == org_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, "Unit not found")
    return u


def _names(rows) -> dict:
    return {k: v for k, v in rows}


@router.get("")
async def list_units(
    q: Optional[str] = None,
    part_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Units of a product, or units whose serial contains `q` (spaces and case ignored)."""
    stmt = select(SerializedItem, Part.name).join(Part, Part.id == SerializedItem.part_id).where(
        SerializedItem.org_id == current.org_id, Part.org_id == current.org_id)
    if part_id:
        stmt = stmt.where(SerializedItem.part_id == part_id)
    if status:
        stmt = stmt.where(SerializedItem.status == status)
    if q and q.strip():
        key = q.strip().upper().replace(" ", "")
        stmt = stmt.where(func.upper(func.replace(SerializedItem.serial_number, " ", "")).contains(key, autoescape=True))
    elif not part_id:
        raise HTTPException(422, "Search by serial or pick a product")
    rows = (await db.execute(stmt.order_by(SerializedItem.created_at.desc()).limit(max(1, min(limit, 500))))).all()
    bin_ids = {u.bin_id for u, _n in rows if u.bin_id}
    bins = _names((await db.execute(select(StorageBin.id, StorageBin.name).where(
        StorageBin.id.in_(bin_ids), StorageBin.org_id == current.org_id))).all()) if bin_ids else {}
    show_cost = await can_see_costs(db, current)
    return {"items": [{
        "id": u.id, "serial_number": u.serial_number, "part_id": u.part_id, "product_name": name,
        "status": u.status, "status_label": STATUS_LABELS.get(u.status, u.status),
        "bin_id": u.bin_id, "bin": bins.get(u.bin_id), "location_id": u.location_id, "order_id": u.order_id,
        "unit_cost": float(u.unit_cost or 0) if show_cost else None,
        "supplier_warranty_until": u.supplier_warranty_until.isoformat() if u.supplier_warranty_until else None,
        "received_at": u.created_at.isoformat() if u.created_at else None,
    } for u, name in rows]}


@router.get("/{unit_id}")
async def unit_passport(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    u = await _unit(db, current.org_id, unit_id)
    org = current.org_id
    part = (await db.execute(select(Part).where(Part.id == u.part_id, Part.org_id == org))).scalar_one_or_none()
    sup = (await db.execute(select(Supplier).where(
        Supplier.id == u.supplier_id, Supplier.org_id == org))).scalar_one_or_none() if u.supplier_id else None
    events = (await db.execute(select(SerialEvent).where(
        SerialEvent.item_id == u.id, SerialEvent.org_id == org)
        .order_by(SerialEvent.created_at, SerialEvent.id))).scalars().all()

    loc_ids = {u.location_id} | {e.location_id for e in events}
    bin_ids = {u.bin_id} | {e.bin_id for e in events}
    emp_ids = {e.created_by_id for e in events}
    locs = _names((await db.execute(select(Location.id, Location.name).where(
        Location.id.in_(loc_ids - {None}), Location.org_id == org))).all())
    bins = _names((await db.execute(select(StorageBin.id, StorageBin.name).where(
        StorageBin.id.in_(bin_ids - {None}), StorageBin.org_id == org))).all())
    emps = _names((await db.execute(select(Employee.id, Employee.name).where(
        Employee.id.in_(emp_ids - {None}), Employee.org_id == org))).all())
    order_ids = {u.order_id} | {e.ref_id for e in events if e.ref_type == "order"}
    orders = _names((await db.execute(select(Order.id, Order.order_number).where(
        Order.id.in_(order_ids - {None}), Order.org_id == org))).all())
    sale_ids = {e.ref_id for e in events if e.ref_type in ("sale", "sale_void")}
    sales = _names((await db.execute(select(Sale.id, Sale.sale_number).where(
        Sale.id.in_(sale_ids - {None}), Sale.org_id == org))).all())
    wh_name = (await db.execute(select(Warehouse.name).where(
        Warehouse.id == u.warehouse_id, Warehouse.org_id == org))).scalar_one_or_none() if u.warehouse_id else None
    asset = (await db.execute(select(Device).where(
        Device.id == u.converted_asset_id, Device.org_id == org))).scalar_one_or_none() if u.converted_asset_id else None

    history = [{
        "kind": e.kind, "at": e.created_at.isoformat() if e.created_at else None, "label": e.label,
        "ref_type": e.ref_type, "ref_id": e.ref_id,
        "ref_label": orders.get(e.ref_id) if e.ref_type == "order" else sales.get(e.ref_id)
        if e.ref_type in ("sale", "sale_void") else None,
        "location": locs.get(e.location_id), "bin": bins.get(e.bin_id), "by": emps.get(e.created_by_id),
    } for e in events]
    if not any(e.kind == "received" for e in events):
        # units received before the passport existed: their receipt is the unit's own row
        history.insert(0, {"kind": "received", "at": u.created_at.isoformat() if u.created_at else None,
                           "label": None, "ref_type": "po" if u.po_id else None, "ref_id": u.po_id,
                           "ref_label": None, "location": None, "bin": None, "by": None})

    until = u.supplier_warranty_until
    show_cost = await can_see_costs(db, current)
    return {
        "id": u.id, "serial_number": u.serial_number, "status": u.status,
        "status_label": STATUS_LABELS.get(u.status, u.status),
        "product": {"id": part.id, "name": part.name, "sku": part.sku} if part else None,
        "unit_cost": float(u.unit_cost or 0) if show_cost else None,
        "supplier": {"id": sup.id, "name": sup.name} if sup else None,
        "po_id": u.po_id,
        "supplier_warranty_until": until.isoformat() if until else None,
        "in_supplier_warranty": (until >= date.today()) if until else None,
        "received_at": u.created_at.isoformat() if u.created_at else None,
        "where": {"location_id": u.location_id, "location": locs.get(u.location_id),
                  "warehouse": wh_name, "bin_id": u.bin_id, "bin": bins.get(u.bin_id),
                  "order_id": u.order_id, "order_number": orders.get(u.order_id),
                  "asset_id": asset.id if asset else None,
                  "asset": " ".join(x for x in (asset.brand, asset.model) if x) or None if asset else None},
        "history": history,
    }


class UnitPatch(BaseModel):
    supplier_warranty_until: Optional[date] = None


@router.patch("/{unit_id}")
async def update_unit(
    unit_id: str,
    data: UnitPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Correct the supplier-warranty end date (a supplier's own warranty card says otherwise)."""
    u = await _unit(db, current.org_id, unit_id)
    if "supplier_warranty_until" in data.model_fields_set:
        u.supplier_warranty_until = data.supplier_warranty_until
    await db.commit()
    return {"ok": True, "supplier_warranty_until": u.supplier_warranty_until.isoformat()
            if u.supplier_warranty_until else None}


async def units_for_labels(db, org_id: str, ids: list[str]):
    """The shop's units with their products, in the order asked (serial labels)."""
    rows = (await db.execute(select(SerializedItem, Part).join(Part, Part.id == SerializedItem.part_id).where(
        SerializedItem.id.in_(ids), SerializedItem.org_id == org_id, Part.org_id == org_id))).all()
    pos = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: pos.get(r[0].id, 0))
