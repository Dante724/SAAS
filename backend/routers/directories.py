"""Directories — org-customizable reference lists that feed dropdowns app-wide
(asset types, manufacturers, cancel reasons, lead sources, ...). Orderry parity.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Client, CustomField, Directory, DirectoryItem, Employee, Order
from backend.services.auth import get_current_employee, require_perm
from backend.core.spreadsheet import MAX_IMPORT_ROWS, safe_row, too_many_rows, unguard_cell

router = APIRouter(prefix="/api/directories", tags=["settings"])

# System directories seeded on first access so the UI always has the core lists. Each one feeds
# a real field: a list that drives nothing just teaches owners that settings don't work.
SEED = [
    ("cancel_reason", "Cancellation reasons"),
    ("lead_source", "Lead sources"),
]
# Starter values (US/CA launch markets), added once to an empty list — never re-added after the
# shop archives them.
DEFAULT_ITEMS = {
    "lead_source": ["Walk-in", "Google search", "Google Maps", "Yelp", "Facebook", "Instagram",
                    "Referral", "Repeat customer", "Website", "Other"],
    "cancel_reason": ["Customer declined estimate", "Too expensive", "Parts unavailable",
                      "Not repairable", "Customer no-show", "Fixed elsewhere", "Duplicate order", "Other"],
}
# Where each built-in list shows up, so the page can say what editing it changes.
BUILT_IN_USES = {
    "lead_source": ["New client → Lead source", "Client profile → Lead source", "Report: Revenue by lead source"],
    "cancel_reason": ["Cancelling a work order", "Report: Cancellations by reason"],
}
# Earlier seeds that duplicated a real home elsewhere. Their rows are kept (a custom field may be
# bound to them) but the page only lists them when something still uses them.
MANAGED_ELSEWHERE = {
    "asset_type": ("Asset types", "Settings → Industry & assets"),
    "manufacturer": ("Manufacturers", "Settings → Industry & assets (brands & models)"),
    "payment_method": ("Payment methods", "Finance → Cashboxes (each account is a payment method)"),
}
# Records that store a directory value as text, so a rename or merge carries them along.
VALUE_COLUMNS = {
    "lead_source": (Client, Client.source),
    "cancel_reason": (Order, Order.cancel_reason),
}


async def _ensure_seed(db, org_id):
    existing = {d.key: d for d in (await db.execute(select(Directory).where(
        Directory.org_id == org_id))).scalars().all()}
    made = False
    for key, name in SEED:
        d = existing.get(key)
        if not d:
            d = Directory(org_id=org_id, key=key, name=name, is_system=True)
            db.add(d)
            await db.flush()
            made = True
        has_any = (await db.execute(select(func.count()).select_from(DirectoryItem).where(
            DirectoryItem.directory_id == d.id))).scalar() or 0
        if not has_any:
            values = await _adopt_existing(db, org_id, key, list(DEFAULT_ITEMS.get(key, [])))
            for i, v in enumerate(values):
                db.add(DirectoryItem(org_id=org_id, directory_id=d.id, value=v, sort_order=i))
                made = True
    if made:
        await db.commit()


def _norm(v: str) -> str:
    return "".join(ch for ch in (v or "").lower() if ch.isalnum())


async def _adopt_existing(db, org_id: str, key: str, defaults: list[str]) -> list[str]:
    """First-time setup of a list that replaces a free-text field: keep what shops already typed.
    Values that are a default in another spelling ("walk_in" for "Walk-in") are rewritten to the
    default; anything else becomes its own option (codes like google_ads read as "Google Ads")."""
    if key not in VALUE_COLUMNS:
        return defaults
    model, col = VALUE_COLUMNS[key]
    existing = [v for (v,) in (await db.execute(select(col).where(
        model.org_id == org_id, col.is_not(None), col != "").distinct())).all()]
    by_norm = {_norm(d): d for d in defaults}
    out = list(defaults)
    for v in existing:
        label = by_norm.get(_norm(v))
        if not label:
            label = v.replace("_", " ").title() if ("_" in v or v.islower()) else v
            if _norm(label) not in by_norm:
                by_norm[_norm(label)] = label
                out.append(label)
            label = by_norm[_norm(label)]
        if label != v:
            await db.execute(update(model).where(model.org_id == org_id, col == v).values({col.key: label}))
    return out


async def _bound_fields(db, org_id) -> dict:
    """directory key -> labels of custom fields whose options come from it."""
    out: dict = {}
    for k, label in (await db.execute(select(CustomField.directory_key, CustomField.field_label).where(
            CustomField.org_id == org_id, CustomField.directory_key.is_not(None)))).all():
        out.setdefault(k, []).append(f"Custom field: {label}")
    return out


async def _remap(db, org_id: str, key: str, old: str, new: str) -> int:
    """Rewrite stored copies of a directory value (rename / merge)."""
    if key not in VALUE_COLUMNS or old == new:
        return 0
    model, col = VALUE_COLUMNS[key]
    res = await db.execute(update(model).where(model.org_id == org_id, col == old).values({col.key: new}))
    return res.rowcount or 0


@router.get("")
async def list_directories(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _ensure_seed(db, current.org_id)
    rows = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id).order_by(Directory.name))).scalars().all()
    counts = dict((await db.execute(select(DirectoryItem.directory_id, func.count()).where(
        DirectoryItem.org_id == current.org_id, DirectoryItem.active == True)  # noqa: E712
        .group_by(DirectoryItem.directory_id))).all())
    bound = await _bound_fields(db, current.org_id)
    items, elsewhere = [], []
    for d in rows:
        used_in = BUILT_IN_USES.get(d.key, []) + bound.get(d.key, [])
        if d.key in MANAGED_ELSEWHERE and not bound.get(d.key):
            continue
        items.append({"id": d.id, "key": d.key, "name": d.name, "is_system": d.is_system,
                      "item_count": counts.get(d.id, 0), "used_in": used_in})
    for key, (name, where) in MANAGED_ELSEWHERE.items():
        if not bound.get(key):
            elsewhere.append({"name": name, "where": where})
    return {"items": items, "managed_elsewhere": elsewhere}


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
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    d = (await db.execute(select(Directory).where(
        Directory.id == directory_id, Directory.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Directory not found")
    q = select(DirectoryItem).where(
        DirectoryItem.directory_id == directory_id, DirectoryItem.org_id == current.org_id)
    if not include_archived:
        q = q.where(DirectoryItem.active == True)  # noqa: E712
    rows = (await db.execute(_loc_filter(q, location_id)
        .order_by(DirectoryItem.active.desc(), DirectoryItem.sort_order, DirectoryItem.value))).scalars().all()
    kids: dict = {}
    for i in rows:
        if i.parent_id and i.active:
            kids[i.parent_id] = kids.get(i.parent_id, 0) + 1
    # How many records carry each value — shown so owners see what a rename or merge touches.
    uses: dict = {}
    if d.key in VALUE_COLUMNS:
        model, col = VALUE_COLUMNS[d.key]
        uses = dict((await db.execute(select(col, func.count()).where(
            model.org_id == current.org_id, col.is_not(None)).group_by(col))).all())
    return {"items": [{"id": i.id, "value": i.value, "parent_id": i.parent_id,
                       "location_id": i.location_id, "sort_order": i.sort_order, "active": i.active,
                       "child_count": kids.get(i.id, 0), "uses": uses.get(i.value, 0)} for i in rows],
            "counts_usage": d.key in VALUE_COLUMNS}


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
    val = data.value.strip()
    if not val:
        raise HTTPException(422, "Value required")
    if await _active_dup(db, directory_id, val):
        raise HTTPException(409, f"“{val}” is already in this list")
    top = (await db.execute(select(func.max(DirectoryItem.sort_order)).where(
        DirectoryItem.directory_id == directory_id))).scalar() or 0
    it = DirectoryItem(org_id=current.org_id, directory_id=directory_id,
                       value=val, parent_id=data.parent_id,
                       location_id=data.location_id or None, sort_order=data.sort_order or top + 1)
    db.add(it)
    await db.commit()
    await db.refresh(it)
    return {"id": it.id, "value": it.value}


async def _active_dup(db, directory_id: str, value: str, exclude_id: Optional[str] = None):
    q = select(DirectoryItem).where(DirectoryItem.directory_id == directory_id,
                                    func.lower(DirectoryItem.value) == value.lower(),
                                    DirectoryItem.active == True)  # noqa: E712
    if exclude_id:
        q = q.where(DirectoryItem.id != exclude_id)
    return (await db.execute(q)).scalars().first()


async def _item_and_key(db, org_id: str, item_id: str):
    it = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.id == item_id, DirectoryItem.org_id == org_id))).scalar_one_or_none()
    if not it:
        raise HTTPException(404, "Item not found")
    key = (await db.execute(select(Directory.key).where(Directory.id == it.directory_id))).scalar_one()
    return it, key


class ItemPatch(BaseModel):
    value: Optional[str] = None
    parent_id: Optional[str] = None
    location_id: Optional[str] = None


@router.patch("/items/{item_id}")
async def update_item(
    item_id: str, data: ItemPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Rename (records that store the old value follow it) or move to another parent/location."""
    it, key = await _item_and_key(db, current.org_id, item_id)
    renamed = 0
    fields = data.model_fields_set
    if "value" in fields and data.value is not None:
        val = data.value.strip()
        if not val:
            raise HTTPException(422, "Value required")
        if await _active_dup(db, it.directory_id, val, exclude_id=it.id):
            raise HTTPException(409, f"“{val}” is already in this list — merge instead")
        renamed = await _remap(db, current.org_id, key, it.value, val)
        it.value = val
    if "parent_id" in fields:
        if data.parent_id == it.id:
            raise HTTPException(422, "An element can't be its own parent")
        it.parent_id = data.parent_id or None
    if "location_id" in fields:
        it.location_id = data.location_id or None
    await db.commit()
    return {"id": it.id, "value": it.value, "records_updated": renamed}


class ReorderIn(BaseModel):
    ids: list[str]


@router.post("/{directory_id}/reorder")
async def reorder_items(
    directory_id: str, data: ReorderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    rows = {i.id: i for i in (await db.execute(select(DirectoryItem).where(
        DirectoryItem.directory_id == directory_id, DirectoryItem.org_id == current.org_id,
        DirectoryItem.id.in_(data.ids)))).scalars().all()}
    for n, iid in enumerate(data.ids):
        if iid in rows:
            rows[iid].sort_order = n
    await db.commit()
    return {"ok": True}


@router.post("/items/{item_id}/restore")
async def restore_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    it, _key = await _item_and_key(db, current.org_id, item_id)
    if await _active_dup(db, it.directory_id, it.value, exclude_id=it.id):
        raise HTTPException(409, f"“{it.value}” is already in this list")
    it.active = True
    await db.commit()
    return {"ok": True}


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
    current: Employee = Depends(get_current_employee),
):
    """Inline add from a form dialog. Any staff member can extend a directory this way
    (Orderry's design: adding a value here bypasses the directory-edit permission)."""
    await _ensure_seed(db, current.org_id)
    d = (await db.execute(select(Directory).where(
        Directory.org_id == current.org_id, Directory.key == key))).scalar_one_or_none()
    if not d and key in (await _bound_fields(db, current.org_id)):
        # A custom field is bound to a list this shop never had (e.g. a retired seed key):
        # create it on first use rather than failing the form.
        d = Directory(org_id=current.org_id, key=key, name=MANAGED_ELSEWHERE.get(key, (key.replace("_", " ").title(),))[0])
        db.add(d)
        await db.flush()
    if not d:
        raise HTTPException(404, "Directory not found")
    val = data.value.strip()
    if not val:
        raise HTTPException(400, "Value required")
    dup = await _active_dup(db, d.id, val)
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
    if src.directory_id != tgt.directory_id:
        raise HTTPException(422, "Both elements must be in the same list")
    kids = (await db.execute(select(DirectoryItem).where(
        DirectoryItem.parent_id == src.id, DirectoryItem.org_id == current.org_id))).scalars().all()
    for k in kids:
        k.parent_id = tgt.id
    key = (await db.execute(select(Directory.key).where(Directory.id == src.directory_id))).scalar_one()
    moved = await _remap(db, current.org_id, key, src.value, tgt.value)
    src.active = False
    await db.commit()
    return {"ok": True, "kept": tgt.value, "reparented": len(kids), "records_updated": moved}


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
        w.writerow(safe_row([r.value, by_id.get(r.parent_id, "")]))
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
    rows = [[unguard_cell(c) for c in r] for r in reader]
    # skip a header row that literally says "value"
    if rows and rows[0] and rows[0][0].strip().lower() == "value":
        rows = rows[1:]
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(413, too_many_rows(len(rows)))
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
