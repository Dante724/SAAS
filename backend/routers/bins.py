"""Bin-level (micro-location) storage — Orderry "bin locations".
Bins live under a location and its product warehouse. ii144a: stock is one row per
(product, location, bin) — see services/stock_rows.py — so a transfer moves quantity between
two exact rows and the location total never changes. Bin names are unique per warehouse and
bin codes per shop (database indexes back both checks)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import StorageBin, StockLevel, StockMovement, Location, Organisation, Part
from backend.services import numbering, stock_rows
from backend.services.auth import get_current_employee, require_perm, Employee

router = APIRouter(prefix="/api/bins", tags=["bins"])


def _ser(b: StorageBin) -> dict:
    return {
        "id": b.id, "location_id": b.location_id, "warehouse_id": b.warehouse_id, "name": b.name,
        "zone": b.zone, "shelf": b.shelf, "bin_number": b.bin_number, "barcode": b.barcode,
        "barcode_type": b.barcode_type or "code128",
        "max_weight_capacity": b.max_weight_capacity, "active": b.active,
    }


@router.get("")
async def list_bins(
    location_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(StorageBin).where(StorageBin.org_id == current.org_id, StorageBin.active == True)  # noqa: E712
    if location_id:
        q = q.where(StorageBin.location_id == location_id)
    if warehouse_id:
        q = q.where(StorageBin.warehouse_id == warehouse_id)
    rows = (await db.execute(q.order_by(StorageBin.name))).scalars().all()
    # on-hand per bin, one query
    qty = dict((await db.execute(select(StockLevel.bin_id, func.coalesce(func.sum(StockLevel.qty_available), 0))
                                 .join(Part, Part.id == StockLevel.part_id)
                                 .where(StockLevel.bin_id.in_([b.id for b in rows]), Part.org_id == current.org_id)
                                 .group_by(StockLevel.bin_id))).all()) if rows else {}
    return {"items": [{**_ser(b), "qty_on_hand": qty.get(b.id, 0)} for b in rows]}


class BinIn(BaseModel):
    location_id: str
    name: Optional[str] = Field(None, max_length=60)        # empty = named after its code
    barcode: Optional[str] = Field(None, max_length=60)     # empty = from the bin template
    barcode_type: Optional[str] = None                      # ii144b: how the label prints it (default Code 128)
    warehouse_id: Optional[str] = None                      # default: the location's product warehouse
    zone: Optional[str] = Field(None, max_length=40)
    shelf: Optional[str] = Field(None, max_length=40)
    bin_number: Optional[str] = Field(None, max_length=40)
    max_weight_capacity: float = 0.0


async def _warehouse_for(db, org_id: str, location_id: str, warehouse_id: Optional[str]) -> str:
    from backend.models import Warehouse
    from backend.services import warehouses as _wh
    if warehouse_id:
        w = (await db.execute(select(Warehouse).where(
            Warehouse.id == warehouse_id, Warehouse.org_id == org_id, Warehouse.location_id == location_id,
            Warehouse.kind == "product", Warehouse.deleted_at.is_(None)))).scalar_one_or_none()
        if not w:
            raise HTTPException(404, "Warehouse not found at this location")
        return w.id
    return (await _wh.default_for_location(db, org_id, location_id)).id


async def _check_name(db, org_id: str, location_id: str, warehouse_id: str, name: str,
                      exclude_id: Optional[str] = None) -> None:
    q = select(StorageBin).where(
        StorageBin.org_id == org_id, StorageBin.location_id == location_id,
        StorageBin.warehouse_id == warehouse_id, func.lower(StorageBin.name) == name.lower(),
        StorageBin.active == True)  # noqa: E712
    if exclude_id:
        q = q.where(StorageBin.id != exclude_id)
    if (await db.execute(q)).scalars().first():
        raise HTTPException(409, f"A bin called '{name}' already exists in this warehouse")


async def _check_code(db, org_id: str, code: str, exclude_id: Optional[str] = None) -> None:
    q = select(StorageBin).where(StorageBin.org_id == org_id, StorageBin.barcode == code,
                                 StorageBin.active == True)  # noqa: E712
    if exclude_id:
        q = q.where(StorageBin.id != exclude_id)
    other = (await db.execute(q)).scalars().first()
    if other:
        raise HTTPException(409, f"The bin code {code} is already used by bin '{other.name}'")


async def next_bin_code(db, org_id: str) -> str:
    tpl = (await db.execute(select(Organisation.inv_bin_template).where(
        Organisation.id == org_id))).scalar_one_or_none()

    async def taken(codes: set) -> set:
        return set((await db.execute(select(StorageBin.barcode).where(
            StorageBin.org_id == org_id, StorageBin.barcode.in_(codes)))).scalars().all())
    return await numbering.next_code(db, org_id, "bin", tpl, taken=taken)


async def _commit(db) -> None:
    from sqlalchemy.exc import IntegrityError
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Another bin with that name or code was just saved — refresh and try again")


def _label_code(kind: Optional[str], code: str) -> str:
    """The bin code as its label type stores it (Code 39 upper-cases, EAN adds the check digit) —
    stored that way so a scan of the printed label finds the bin. 422 with the reason if it can't."""
    if not kind:
        return code
    from backend.services import barcode_types
    if kind not in barcode_types.TYPES:
        raise HTTPException(422, "Unknown barcode type")
    try:
        return barcode_types.normalize(kind, code)
    except ValueError as e:
        raise HTTPException(422, f"The bin code {code} can't be printed as {barcode_types.label(kind)}: {e}")


@router.post("", status_code=201)
async def create_bin(
    data: BinIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    loc = (await db.execute(select(Location).where(
        Location.id == data.location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not loc:
        raise HTTPException(404, "Warehouse not found")
    wid = await _warehouse_for(db, current.org_id, data.location_id, data.warehouse_id)
    code = (data.barcode or "").strip()
    if code:
        code = _label_code(data.barcode_type, code)
        await _check_code(db, current.org_id, code)
    else:
        code = _label_code(data.barcode_type, await next_bin_code(db, current.org_id))
    name = (data.name or "").strip() or code
    await _check_name(db, current.org_id, data.location_id, wid, name)
    b = StorageBin(org_id=current.org_id, location_id=data.location_id, warehouse_id=wid, name=name,
                   barcode=code, barcode_type=data.barcode_type or None, zone=data.zone, shelf=data.shelf, bin_number=data.bin_number,
                   max_weight_capacity=data.max_weight_capacity)
    db.add(b)
    await _commit(db)
    await db.refresh(b)
    return _ser(b)


@router.put("/{bin_id}")
async def update_bin(
    bin_id: str,
    data: BinIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    b = (await db.execute(select(StorageBin).where(
        StorageBin.id == bin_id, StorageBin.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bin not found")
    if not b.warehouse_id:
        b.warehouse_id = await _warehouse_for(db, current.org_id, b.location_id, None)
    name = (data.name or "").strip() or b.name
    if name.lower() != (b.name or "").lower():
        await _check_name(db, current.org_id, b.location_id, b.warehouse_id, name, exclude_id=b.id)
    kind = data.barcode_type if data.barcode_type is not None else b.barcode_type
    code = (data.barcode or "").strip()
    if (code and code != b.barcode) or kind != b.barcode_type:
        # a new code or a new label type: the code must still fit the type (re-checked either way)
        code = _label_code(kind, code or b.barcode or "")
        if code != b.barcode:
            await _check_code(db, current.org_id, code, exclude_id=b.id)
        b.barcode = code
        b.barcode_type = kind or None
    b.name = name
    b.zone = data.zone
    b.shelf = data.shelf
    b.bin_number = data.bin_number
    b.max_weight_capacity = data.max_weight_capacity
    await _commit(db)
    return _ser(b)


@router.delete("/{bin_id}")
async def delete_bin(
    bin_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    b = (await db.execute(select(StorageBin).where(
        StorageBin.id == bin_id, StorageBin.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bin not found")
    qty = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
        .join(Part, Part.id == StockLevel.part_id)
        .where(StockLevel.bin_id == b.id, Part.org_id == current.org_id))).scalar() or 0
    if qty > 0:
        raise HTTPException(409, f"Bin still holds {qty:g} units — move them out first")
    b.active = False
    await db.commit()
    return {"ok": True}


class TransferIn(BaseModel):
    part_id: str
    source_bin_id: str
    destination_bin_id: str
    qty: float


@router.post("/transfer")
async def transfer_stock_between_bins(
    data: TransferIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Move `qty` of a product from one bin to another, atomically.

    The source is drawn with a guarded conditional UPDATE (only fires while the bin still holds
    >= qty), so the source bin can never go negative (no ghost stock). The destination is the
    exact (product, location, bin) row — created when missing, never a second row.
    """
    if data.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if data.source_bin_id == data.destination_bin_id:
        raise HTTPException(400, "Source and destination bins are the same")

    # Both bins and the product must exist and belong to this org.
    bins = (await db.execute(select(StorageBin).where(
        StorageBin.org_id == current.org_id, StorageBin.active == True,  # noqa: E712
        StorageBin.id.in_([data.source_bin_id, data.destination_bin_id])))).scalars().all()
    bmap = {b.id: b for b in bins}
    src = bmap.get(data.source_bin_id)
    dst = bmap.get(data.destination_bin_id)
    if not src or not dst:
        raise HTTPException(404, "Source or destination bin not found")
    if src.location_id != dst.location_id:
        raise HTTPException(409, "Both bins must be at the same location — use a stock transfer between locations")
    part = (await db.execute(select(Part.id).where(
        Part.id == data.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Product not found")

    src_row = await stock_rows.row_for(db, data.part_id, src.location_id, src.id)
    try:
        await stock_rows.draw(db, data.part_id, src.location_id, data.qty, bin_id=src.id,
                              only=[src_row] if src_row else [])
    except stock_rows.NotEnoughStock as e:
        raise HTTPException(409, f"Source bin '{src.name}' has only {e.have:g}, cannot move {data.qty:g}")
    await stock_rows.add(db, data.part_id, dst.location_id, data.qty, bin_id=dst.id)

    # Two ledger rows (out of source, into destination) for a full audit trail.
    db.add(StockMovement(part_id=data.part_id, location_id=src.location_id, bin_id=src.id,
                         movement_type="transfer", qty=-data.qty, ref_type="bin_transfer",
                         ref_id=dst.id, created_by_id=current.id))
    db.add(StockMovement(part_id=data.part_id, location_id=dst.location_id, bin_id=dst.id,
                         movement_type="transfer", qty=data.qty, ref_type="bin_transfer",
                         ref_id=src.id, created_by_id=current.id))
    # A serialized unit sits in a bin too: move as many units as were moved, oldest first.
    from backend.models import SerializedItem
    units = (await db.execute(select(SerializedItem).where(
        SerializedItem.org_id == current.org_id, SerializedItem.part_id == data.part_id,
        SerializedItem.bin_id == src.id, SerializedItem.status == "in_stock")
        .order_by(SerializedItem.created_at).limit(int(data.qty)))).scalars().all()
    for u in units:
        u.bin_id = dst.id
    await db.commit()
    return {"ok": True, "moved": data.qty, "from": src.name, "to": dst.name}


# ── Serialized inventory & cost layers (Mandate 2) ──
from backend.models import StockItemBatch, SerializedItem
from backend.services.serialized import consume_fifo_lifo, allocate_serialized_item
from decimal import Decimal as _Dec


class BatchIn(BaseModel):
    part_id: str
    location_id: str
    bin_id: Optional[str] = None
    unit_cost: float
    qty: float
    serials: Optional[list[str]] = Field(None, max_length=500)  # if provided, also create SerializedItems


@router.post("/batches", status_code=201)
async def create_batch(
    data: BatchIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Record an intake cost layer (and optional serialized units)."""
    from backend.models import Part
    from backend.services import serial_intake
    part = (await db.execute(select(Part).where(
        Part.id == data.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Product not found")
    # Same serial rules as postings and PO receiving (one per unit, no repeats, none already in stock).
    serials = await serial_intake.validate_intake(db, current.org_id, part, data.qty, data.serials)
    batch = StockItemBatch(
        org_id=current.org_id, part_id=data.part_id, location_id=data.location_id,
        bin_id=data.bin_id, unit_cost=_Dec(str(data.unit_cost)),
        qty_received=_Dec(str(data.qty)), qty_remaining=_Dec(str(data.qty)))
    db.add(batch)
    await db.flush()
    created_serials = 0
    for sn in serials:
        db.add(SerializedItem(org_id=current.org_id, part_id=data.part_id, serial_number=sn,
                              bin_id=data.bin_id, location_id=data.location_id, batch_id=batch.id,
                              unit_cost=_Dec(str(data.unit_cost)), status="in_stock"))
        created_serials += 1
    await db.commit()
    return {"batch_id": batch.id, "serials_created": created_serials}


class ConsumeIn(BaseModel):
    part_id: str
    qty: float
    method: str = "fifo"  # fifo|lifo


@router.post("/consume")
async def consume(
    data: ConsumeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    res = await consume_fifo_lifo(db, org_id=current.org_id, part_id=data.part_id,
                                  qty=_Dec(str(data.qty)), method=data.method)
    await db.commit()
    return res


class AllocateIn(BaseModel):
    part_id: str
    order_id: str
    bin_id: Optional[str] = None
    serial_number: Optional[str] = None


@router.post("/allocate-serial")
async def allocate_serial(
    data: AllocateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    item = await allocate_serialized_item(
        db, org_id=current.org_id, part_id=data.part_id, order_id=data.order_id,
        bin_id=data.bin_id, serial_number=data.serial_number)
    await db.commit()
    return {"ok": True, "serial_number": item.serial_number, "unit_cost": float(item.unit_cost),
            "status": item.status, "order_id": item.order_id}


@router.get("/part/{part_id}/detail")
async def part_stock_detail(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Cost layers + serialized units for one part — the serialized-inventory view."""
    from backend.models import StockItemBatch, SerializedItem, StorageBin
    batches = (await db.execute(select(StockItemBatch).where(
        StockItemBatch.org_id == current.org_id, StockItemBatch.part_id == part_id)
        .order_by(StockItemBatch.received_at))).scalars().all()
    serials = (await db.execute(select(SerializedItem).where(
        SerializedItem.org_id == current.org_id, SerializedItem.part_id == part_id)
        .order_by(SerializedItem.created_at))).scalars().all()
    bin_names = {}
    for b in (await db.execute(select(StorageBin).where(StorageBin.org_id == current.org_id))).scalars().all():
        bin_names[b.id] = b.name
    return {
        "batches": [{"id": b.id, "unit_cost": float(b.unit_cost or 0),
                     "qty_received": float(b.qty_received or 0),
                     "qty_remaining": float(b.qty_remaining or 0),
                     "bin": bin_names.get(b.bin_id),
                     "received_at": b.received_at.isoformat() if b.received_at else None}
                    for b in batches],
        "serials": [{"id": s.id, "serial_number": s.serial_number, "status": s.status,
                     "bin": bin_names.get(s.bin_id),
                     "unit_cost": float(s.unit_cost or 0)} for s in serials],
    }
