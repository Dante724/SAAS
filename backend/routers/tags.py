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


async def _emit(db, org_id: str, event: str, payload: dict) -> None:
    from backend.services.webhooks import emit_event
    await emit_event(db, org_id, event, payload)


async def create_tag_core(db: AsyncSession, org_id: str, name: str, color: Optional[str]) -> Tag:
    """Shared by the staff screen and API v2. Commits and emits tag.created."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if len(name) > 40:
        raise HTTPException(422, "Tag name too long (max 40)")
    # case-insensitive uniqueness per org
    dup = (await db.execute(select(Tag).where(
        Tag.org_id == org_id, func.lower(Tag.name) == name.lower()))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A tag with this name already exists")
    t = Tag(org_id=org_id, name=name, color=(color or "#64748b")[:20])
    db.add(t)
    await db.flush()
    await _emit(db, org_id, "tag.created", _ser(t))
    await db.commit()
    return t


async def get_tag(db: AsyncSession, org_id: str, tag_id: str) -> Tag:
    t = (await db.execute(select(Tag).where(
        Tag.id == tag_id, Tag.org_id == org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tag not found")
    return t


def _not_system(t: Tag) -> None:
    """Membership tags ("Boat Care member") are Tekly's: added and removed with the plan."""
    if getattr(t, "system", False):
        raise HTTPException(409, "This tag is managed automatically by memberships")


async def update_tag_core(db: AsyncSession, t: Tag, name: Optional[str], color: Optional[str]) -> Tag:
    _not_system(t)
    if name and name.strip():
        name = name.strip()[:40]
        dup = (await db.execute(select(Tag).where(Tag.org_id == t.org_id, Tag.id != t.id,
                                                  func.lower(Tag.name) == name.lower()))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "A tag with this name already exists")
        t.name = name
    if color:
        t.color = color[:20]
    await _emit(db, t.org_id, "tag.updated", _ser(t))
    await db.commit()
    return t


async def delete_tag_core(db: AsyncSession, t: Tag) -> None:
    _not_system(t)
    # Remove every attachment FIRST and flush, then the tag. Taggable has no ORM relationship to
    # Tag, so the unit-of-work can't guarantee it deletes the children before the parent — under
    # enforced FKs (SQLite pragma / Postgres) that races into a FOREIGN KEY violation. An explicit
    # bulk delete + flush makes the order deterministic. (labels vanish from their entities)
    org_id, payload = t.org_id, _ser(t)
    await db.execute(sa_delete(Taggable).where(Taggable.tag_id == t.id))
    await db.flush()
    await db.delete(t)
    await _emit(db, org_id, "tag.deleted", payload)
    await db.commit()


@router.post("", status_code=201)
async def create_tag(data: TagIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    return _ser(await create_tag_core(db, current.org_id, data.name, data.color))


@router.patch("/{tag_id}")
async def update_tag(tag_id: str, data: TagIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    t = await get_tag(db, current.org_id, tag_id)
    return _ser(await update_tag_core(db, t, data.name, data.color))


@router.delete("/{tag_id}")
async def delete_tag(tag_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    await delete_tag_core(db, await get_tag(db, current.org_id, tag_id))
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
    _not_system(t)
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
        _not_system(await get_tag(db, current.org_id, tg.tag_id))
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
