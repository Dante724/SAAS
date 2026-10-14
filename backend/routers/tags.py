"""B6 — unified Tags: reusable labels attachable to contacts, orders, chats."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Tag, Taggable
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/tags", tags=["tags"])

ENTITY_TYPES = {"contact", "order", "chat"}


class TagIn(BaseModel):
    name: str
    color: str = "#64748b"


class AttachIn(BaseModel):
    tag_id: str
    entity_type: str
    entity_id: str


def _ser(t: Tag) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color}


@router.get("")
async def list_tags(db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(Tag).where(Tag.org_id == current.org_id)
                             .order_by(Tag.name))).scalars().all()
    return {"items": [_ser(t) for t in rows]}


@router.post("", status_code=201)
async def create_tag(data: TagIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if len(name) > 40:
        raise HTTPException(422, "Tag name too long (max 40)")
    # case-insensitive uniqueness per org
    dup = (await db.execute(select(Tag).where(
        Tag.org_id == current.org_id, func.lower(Tag.name) == name.lower()))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A tag with this name already exists")
    t = Tag(org_id=current.org_id, name=name, color=data.color or "#64748b")
    db.add(t)
    await db.commit()
    return _ser(t)


@router.patch("/{tag_id}")
async def update_tag(tag_id: str, data: TagIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    t = (await db.execute(select(Tag).where(
        Tag.id == tag_id, Tag.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tag not found")
    if data.name and data.name.strip():
        t.name = data.name.strip()[:40]
    if data.color:
        t.color = data.color
    await db.commit()
    return _ser(t)


@router.delete("/{tag_id}")
async def delete_tag(tag_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    t = (await db.execute(select(Tag).where(
        Tag.id == tag_id, Tag.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tag not found")
    # Remove every attachment FIRST and flush, then the tag. Taggable has no ORM relationship to
    # Tag, so the unit-of-work can't guarantee it deletes the children before the parent — under
    # enforced FKs (SQLite pragma / Postgres) that races into a FOREIGN KEY violation. An explicit
    # bulk delete + flush makes the order deterministic. (labels vanish from their entities)
    await db.execute(sa_delete(Taggable).where(Taggable.tag_id == tag_id))
    await db.flush()
    await db.delete(t)
    await db.commit()
    return {"ok": True}


@router.post("/attach", status_code=201)
async def attach_tag(data: AttachIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    if data.entity_type not in ENTITY_TYPES:
        raise HTTPException(422, f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    t = (await db.execute(select(Tag).where(
        Tag.id == data.tag_id, Tag.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tag not found")
    existing = (await db.execute(select(Taggable).where(
        Taggable.org_id == current.org_id, Taggable.tag_id == data.tag_id,
        Taggable.entity_type == data.entity_type,
        Taggable.entity_id == data.entity_id))).scalar_one_or_none()
    if existing:
        return {"ok": True, "already": True}
    db.add(Taggable(org_id=current.org_id, tag_id=data.tag_id,
                    entity_type=data.entity_type, entity_id=data.entity_id))
    await db.commit()
    return {"ok": True}


@router.post("/detach")
async def detach_tag(data: AttachIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    tg = (await db.execute(select(Taggable).where(
        Taggable.org_id == current.org_id, Taggable.tag_id == data.tag_id,
        Taggable.entity_type == data.entity_type,
        Taggable.entity_id == data.entity_id))).scalar_one_or_none()
    if tg:
        await db.delete(tg)
        await db.commit()
    return {"ok": True}


@router.get("/for")
async def tags_for_entity(entity_type: str, entity_id: str,
                          db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    """Tags currently on one entity."""
    rows = (await db.execute(
        select(Tag).join(Taggable, Taggable.tag_id == Tag.id).where(
            Taggable.org_id == current.org_id,
            Taggable.entity_type == entity_type,
            Taggable.entity_id == entity_id).order_by(Tag.name))).scalars().all()
    return {"items": [_ser(t) for t in rows]}


@router.get("/entities")
async def entities_for_tag(tag_id: str, entity_type: Optional[str] = None,
                           db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(get_current_employee)):
    """Entity ids carrying a tag (for filtering lists by tag)."""
    q = select(Taggable).where(Taggable.org_id == current.org_id, Taggable.tag_id == tag_id)
    if entity_type:
        q = q.where(Taggable.entity_type == entity_type)
    rows = (await db.execute(q)).scalars().all()
    return {"items": [{"entity_type": r.entity_type, "entity_id": r.entity_id} for r in rows]}
