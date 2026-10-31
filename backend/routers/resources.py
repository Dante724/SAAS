"""Phase G — Location resources (bays, rooms, equipment).
Schedulable alongside employees (Orderry 'By resource' scheduler mode).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import Resource, Employee
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/resources", tags=["resources"])


def _ser(r: Resource) -> dict:
    return {"id": r.id, "name": r.name, "kind": r.kind, "color": r.color,
            "location_id": r.location_id, "sort_order": r.sort_order, "active": r.active}


@router.get("")
async def list_resources(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Resource).where(
        Resource.org_id == current.org_id, Resource.active == True)  # noqa: E712
        .order_by(Resource.sort_order))).scalars().all()
    return {"items": [_ser(r) for r in rows]}


class ResourceIn(BaseModel):
    name: str
    kind: str = "bay"
    color: str = "#2cd4c0"
    location_id: Optional[str] = None


@router.post("", status_code=201)
async def create_resource(
    data: ResourceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    count = (await db.execute(select(func.count()).select_from(Resource)
        .where(Resource.org_id == current.org_id))).scalar() or 0
    r = Resource(org_id=current.org_id, name=data.name, kind=data.kind,
                 color=data.color, location_id=data.location_id, sort_order=count)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id}


@router.put("/{rid}")
async def update_resource(
    rid: str,
    data: ResourceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    r = (await db.execute(select(Resource).where(
        Resource.id == rid, Resource.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Resource not found")
    r.name = data.name
    r.kind = data.kind
    r.color = data.color
    r.location_id = data.location_id
    await db.commit()
    return {"ok": True}


@router.delete("/{rid}")
async def delete_resource(
    rid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    r = (await db.execute(select(Resource).where(
        Resource.id == rid, Resource.org_id == current.org_id))).scalar_one_or_none()
    if r:
        r.active = False
        await db.commit()
    return {"ok": True}
