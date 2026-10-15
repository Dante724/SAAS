"""Directories — org-customizable reference lists that feed dropdowns app-wide
(asset types, manufacturers, cancel reasons, lead sources, ...). Orderry parity.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Directory, DirectoryItem, Employee
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/directories", tags=["settings"])

# System directories seeded on first access so the UI always has the core lists.
SEED = [
    ("asset_type", "Asset types"),
    ("manufacturer", "Manufacturers"),
    ("cancel_reason", "Cancellation reasons"),
    ("lead_source", "Lead sources"),
    ("payment_method", "Payment methods"),
]


async def _ensure_seed(db, org_id):
    existing = {d.key for d in (await db.execute(select(Directory).where(
        Directory.org_id == org_id))).scalars().all()}
    made = False
    for key, name in SEED:
        if key not in existing:
            db.add(Directory(org_id=org_id, key=key, name=name, is_system=True))
            made = True
    if made:
        await db.commit()


@router.get("")
async def list_directories(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _ensure_seed(db, current.org_id)
    rows = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id).order_by(Directory.name))).scalars().all()
    return {"items": [{"id": d.id, "key": d.key, "name": d.name, "is_system": d.is_system}
                      for d in rows]}


class DirectoryIn(BaseModel):
    key: str
    name: str


@router.post("", status_code=201)
async def create_directory(
    data: DirectoryIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    dup = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id, Directory.key == data.key))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A directory with this key already exists")
    d = Directory(org_id=current.org_id, key=data.key.strip(), name=data.name.strip())
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return {"id": d.id, "key": d.key, "name": d.name, "is_system": False}


@router.delete("/{directory_id}")
async def delete_directory(
    directory_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    d = (await db.execute(select(Directory).where(
        Directory.id == directory_id, Directory.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Directory not found")
    if d.is_system:
        raise HTTPException(400, "System directories cannot be deleted")
    await db.execute(DirectoryItem.__table__.delete().where(DirectoryItem.directory_id == d.id))
    await db.delete(d)
    await db.commit()
    return {"ok": True}


def _loc_filter(q, location_id: Optional[str]):
    """Items with no location are global; a location sees global + its own."""
    if location_id:
        return q.where(or_(DirectoryItem.location_id.is_(None),
                           DirectoryItem.location_id == location_id))
    return q


@router.get("/{directory_id}/items")
async def list_items(
    directory_id: str,
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(DirectoryItem).where(
        DirectoryItem.directory_id == directory_id, DirectoryItem.org_id == current.org_id,
        DirectoryItem.active == True)  # noqa: E712
    rows = (await db.execute(_loc_filter(q, location_id)
        .order_by(DirectoryItem.sort_order, DirectoryItem.value))).scalars().all()
    kids: dict = {}
    for i in rows:
        if i.parent_id:
            kids[i.parent_id] = kids.get(i.parent_id, 0) + 1
    return {"items": [{"id": i.id, "value": i.value, "parent_id": i.parent_id,
                       "location_id": i.location_id, "sort_order": i.sort_order,
                       "child_count": kids.get(i.id, 0)} for i in rows]}


# Resolve items by directory KEY (what dropdowns call at runtime).
@router.get("/by-key/{key}/items")
async def items_by_key(
    key: str,
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _ensure_seed(db, current.org_id)
    d = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id, Directory.key == key))).scalar_one_or_none()
    if not d:
        return {"items": []}
    q = select(DirectoryItem).where(
        DirectoryItem.directory_id == d.id, DirectoryItem.active == True)  # noqa: E712
    rows = (await db.execute(_loc_filter(q, location_id)
        .order_by(DirectoryItem.sort_order, DirectoryItem.value))).scalars().all()
    return {"directory_id": d.id, "name": d.name,
            "items": [{"id": i.id, "value": i.value, "parent_id": i.parent_id} for i in rows]}


class ItemIn(BaseModel):
    value: str
    parent_id: Optional[str] = None
    location_id: Optional[str] = None
    sort_order: int = 0


@router.post("/{directory_id}/items", status_code=201)
async def add_item(
    directory_id: str, data: ItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    d = (await db.execute(select(Directory).where(
        Directory.id == directory_id, Directory.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Directory not found")
    it = DirectoryItem(org_id=current.org_id, directory_id=directory_id,
                       value=data.value.strip(), parent_id=data.parent_id,
                       location_id=data.location_id or None, sort_order=data.sort_order)
    db.add(it)
    await db.commit()
    await db.refresh(it)
    return {"id": it.id, "value": it.value}


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    it = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.id == item_id, DirectoryItem.org_id == current.org_id))).scalar_one_or_none()
    if not it:
        raise HTTPException(404, "Item not found")
    kids = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.parent_id == it.id, DirectoryItem.org_id == current.org_id,
        DirectoryItem.active == True))).scalars().all()  # noqa: E712
    # Safe delete: refuse to orphan children silently — caller must confirm with force.
    if kids and not force:
        raise HTTPException(409, f"This element has {len(kids)} child element(s). "
                                 f"Deleting it will unparent them — pass force to confirm.")
    for k in kids:
        k.parent_id = None
    it.active = False
    await db.commit()
    return {"ok": True, "unparented": len(kids)}


@router.post("/by-key/{key}/items", status_code=201)
async def add_item_by_key(
    key: str, data: ItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Inline add from a form dialog. Any staff member can extend a directory this way
    (Orderry's design: adding a value here bypasses the directory-edit permission)."""
    await _ensure_seed(db, current.org_id)
    d = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id, Directory.key == key))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Directory not found")
    val = data.value.strip()
    if not val:
        raise HTTPException(400, "Value required")
    dup = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.directory_id == d.id, DirectoryItem.value == val,
        DirectoryItem.active == True))).scalar_one_or_none()  # noqa: E712
    if dup:
        return {"id": dup.id, "value": dup.value, "existing": True}
    it = DirectoryItem(org_id=current.org_id, directory_id=d.id, value=val,
                       parent_id=data.parent_id or None, location_id=data.location_id or None)
    db.add(it)
    await db.commit()
    await db.refresh(it)
    return {"id": it.id, "value": it.value}


class MergeIn(BaseModel):
    target_id: str          # the element to keep


@router.post("/items/{item_id}/merge")
async def merge_item(
    item_id: str, data: MergeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Merge `item_id` (source) into `target_id` (kept): re-parent the source's
    children onto the target, then retire the source."""
    if item_id == data.target_id:
        raise HTTPException(400, "Cannot merge an element into itself")
    src = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.id == item_id, DirectoryItem.org_id == current.org_id))).scalar_one_or_none()
    tgt = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.id == data.target_id, DirectoryItem.org_id == current.org_id))).scalar_one_or_none()
    if not src or not tgt:
        raise HTTPException(404, "Element not found")
    kids = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.parent_id == src.id, DirectoryItem.org_id == current.org_id))).scalars().all()
    for k in kids:
        k.parent_id = tgt.id
    src.active = False
    await db.commit()
    return {"ok": True, "kept": tgt.value, "reparented": len(kids)}


@router.get("/{directory_id}/export")
async def export_items(
    directory_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Excel-openable CSV of a directory's items (value, parent value)."""
    import csv
    import io
    from fastapi.responses import Response
    rows = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.directory_id == directory_id, DirectoryItem.org_id == current.org_id,
        DirectoryItem.active == True).order_by(DirectoryItem.sort_order, DirectoryItem.value))).scalars().all()  # noqa: E712
    by_id = {r.id: r.value for r in rows}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["value", "parent"])
    for r in rows:
        w.writerow([r.value, by_id.get(r.parent_id, "")])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=directory.csv"})


class ImportIn(BaseModel):
    csv: str                # raw CSV text; col A = value, optional col B = parent value


@router.post("/{directory_id}/import")
async def import_items(
    directory_id: str, data: ImportIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Bulk-add items from CSV text. First column is the value; an optional second
    column names a parent (matched among values in this directory). Location scoping
    is set by hand afterward (Orderry does the same)."""
    import csv
    import io
    d = (await db.execute(select(Directory).where(
        Directory.id == directory_id, Directory.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Directory not found")
    existing = {i.value: i for i in (await db.execute(select(DirectoryItem).where(
        DirectoryItem.directory_id == directory_id, DirectoryItem.org_id == current.org_id,
        DirectoryItem.active == True))).scalars().all()}  # noqa: E712
    reader = csv.reader(io.StringIO(data.csv))
    added = 0
    rows = list(reader)
    # skip a header row that literally says "value"
    if rows and rows[0] and rows[0][0].strip().lower() == "value":
        rows = rows[1:]
    # first pass: create all values so parents can resolve
    for r in rows:
        if not r or not r[0].strip():
            continue
        val = r[0].strip()
        if val in existing:
            continue
        it = DirectoryItem(org_id=current.org_id, directory_id=directory_id, value=val)
        db.add(it)
        existing[val] = it
        added += 1
    await db.flush()
    # second pass: link parents by value
    for r in rows:
        if not r or not r[0].strip() or len(r) < 2 or not r[1].strip():
            continue
        child, parent = existing.get(r[0].strip()), existing.get(r[1].strip())
        if child and parent and child is not parent:
            child.parent_id = parent.id
    await db.commit()
    return {"ok": True, "added": added}
