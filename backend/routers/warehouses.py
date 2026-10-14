"""Builds 84/89/90 — Settings → Warehouses.

Product warehouses hold shop inventory; asset warehouses hold the items being
serviced. Which asset label the UI shows ("Devices", "Vehicles", "Tools"…) comes
from the org's industry template, so the same entity reads correctly per business.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Organisation, Warehouse, WarehouseAccess
from backend.services import warehouses as wh
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/settings/warehouses", tags=["settings"])


async def _asset_label(db: AsyncSession, org_id: str) -> str:
    """The template's word for an asset — Device / Vehicle / Tool / Equipment…

    Must normalize first: the stored industry_template can be a legacy alias
    (e.g. "device_repair", "auto_repair") or the default "generic", none of
    which are direct TEMPLATES keys — a raw lookup silently fell back to
    "Assets", so the warehouse label never followed the chosen template.
    """
    tpl = (await db.execute(select(Organisation.industry_template).where(
        Organisation.id == org_id))).scalar_one_or_none()
    try:
        from backend.services.industry import TEMPLATES, normalize_template_key
        return TEMPLATES[normalize_template_key(tpl)].get("asset_label_plural", "Assets")
    except Exception:
        return "Assets"


@router.get("")
async def list_warehouses(
    kind: Optional[str] = None,          # product|asset
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Warehouse).where(Warehouse.org_id == current.org_id)
    if kind:
        q = q.where(Warehouse.kind == kind)
    if not include_deleted:
        q = q.where(Warehouse.deleted_at.is_(None))
    rows = (await db.execute(q.order_by(Warehouse.kind, Warehouse.name))).scalars().all()
    items = []
    for w in rows:
        items.append(wh.serialize(w, bal=await wh.balance(db, w)))
    return {"items": items, "actions": wh.ACTIONS,
            "asset_label": await _asset_label(db, current.org_id)}


class WarehouseIn(BaseModel):
    name: str
    kind: str = "product"                # product|asset
    scope: str = "location"              # location|company
    location_id: Optional[str] = None
    allowed_actions: Optional[list[str]] = None
    active: bool = True


@router.post("", status_code=201)
async def create_warehouse(
    data: WarehouseIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.kind not in ("product", "asset"):
        raise HTTPException(422, "kind must be product|asset")
    if data.scope not in ("location", "company"):
        raise HTTPException(422, "scope must be location|company")
    if data.scope == "location" and not data.location_id:
        raise HTTPException(422, "A location warehouse needs a location_id")
    bad = set(data.allowed_actions or []) - set(wh.ACTIONS)
    if bad:
        raise HTTPException(422, f"Unknown actions: {sorted(bad)}")

    w = Warehouse(
        org_id=current.org_id, name=data.name, kind=data.kind, scope=data.scope,
        location_id=data.location_id if data.scope == "location" else None,
        allowed_actions=(data.allowed_actions if data.allowed_actions is not None
                         else (wh.ACTIONS if data.kind == "product" else [])),
        active=data.active)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return wh.serialize(w, bal=0.0)


@router.put("/{warehouse_id}")
async def update_warehouse(
    warehouse_id: str, data: WarehouseIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    w = (await db.execute(select(Warehouse).where(
        Warehouse.id == warehouse_id, Warehouse.org_id == current.org_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Warehouse not found")
    bad = set(data.allowed_actions or []) - set(wh.ACTIONS)
    if bad:
        raise HTTPException(422, f"Unknown actions: {sorted(bad)}")
    w.name = data.name
    w.active = data.active
    if data.allowed_actions is not None:
        w.allowed_actions = data.allowed_actions
    # a location's default warehouse can't be re-scoped out from under its stock
    if not w.is_default:
        w.scope = data.scope
        w.location_id = data.location_id if data.scope == "location" else None
    await db.commit()
    return wh.serialize(w, bal=await wh.balance(db, w))


@router.delete("/{warehouse_id}")
async def delete_warehouse(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Soft-delete. Only a warehouse holding nothing can go — otherwise its stock
    would vanish silently. Deleted warehouses stay restorable."""
    w = (await db.execute(select(Warehouse).where(
        Warehouse.id == warehouse_id, Warehouse.org_id == current.org_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Warehouse not found")
    if w.is_default:
        raise HTTPException(409, "A location's default warehouse can't be deleted")
    bal = await wh.balance(db, w)
    if bal:
        raise HTTPException(
            409, f"This warehouse still holds {bal:g} item(s). Transfer or write them "
                 f"off first — deleting would lose the stock.")
    w.deleted_at = datetime.utcnow()
    w.active = False
    await db.commit()
    return {"ok": True, "restorable": True}


@router.post("/{warehouse_id}/restore")
async def restore_warehouse(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    w = (await db.execute(select(Warehouse).where(
        Warehouse.id == warehouse_id, Warehouse.org_id == current.org_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Warehouse not found")
    w.deleted_at = None
    w.active = True
    await db.commit()
    return wh.serialize(w, bal=await wh.balance(db, w))


# ── Build 89: the Access tab ─────────────────────────────────────────────────
@router.get("/{warehouse_id}/access")
async def get_access(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Every active employee, with the actions they're explicitly granted here.
    An employee with no row inherits the role-level scope instead."""
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id, Employee.active == True))).scalars().all()  # noqa: E712
    rows = {a.employee_id: a for a in (await db.execute(select(WarehouseAccess).where(
        WarehouseAccess.warehouse_id == warehouse_id))).scalars().all()}
    return {"items": [{
        "employee_id": e.id, "name": e.name,
        "explicit": e.id in rows,
        "actions": (rows[e.id].actions if e.id in rows and isinstance(rows[e.id].actions, list)
                    else []),
    } for e in emps]}


class AccessIn(BaseModel):
    employee_id: str
    actions: list[str] = []
    explicit: bool = True     # False removes the row → back to role-level scope


@router.post("/{warehouse_id}/access")
async def set_access(
    warehouse_id: str, data: AccessIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    w = (await db.execute(select(Warehouse).where(
        Warehouse.id == warehouse_id, Warehouse.org_id == current.org_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Warehouse not found")
    bad = set(data.actions) - set(wh.ACTIONS)
    if bad:
        raise HTTPException(422, f"Unknown actions: {sorted(bad)}")
    row = (await db.execute(select(WarehouseAccess).where(
        WarehouseAccess.warehouse_id == warehouse_id,
        WarehouseAccess.employee_id == data.employee_id))).scalar_one_or_none()

    if not data.explicit:
        if row:
            await db.delete(row)
            await db.commit()
        return {"ok": True, "explicit": False}

    if row:
        row.actions = data.actions
    else:
        db.add(WarehouseAccess(org_id=current.org_id, warehouse_id=warehouse_id,
                               employee_id=data.employee_id, actions=data.actions))
    await db.commit()
    return {"ok": True, "explicit": True, "actions": data.actions}
