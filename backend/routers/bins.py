"""Bin-level (micro-location) storage — Orderry "bin locations".
Bins live under a warehouse (Location). Stock is tracked per (part, location, bin).
Transfers between bins use a guarded conditional UPDATE so the source can never
go negative under concurrency (no ghost stock)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import select, func, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import StorageBin, StockLevel, StockMovement, Location
from backend.services.auth import get_current_employee, require_perm, Employee

router = APIRouter(prefix="/api/bins", tags=["bins"])


def _ser(b: StorageBin) -> dict:
    return {
        "id": b.id, "location_id": b.location_id, "name": b.name, "zone": b.zone,
        "shelf": b.shelf, "bin_number": b.bin_number, "barcode": b.barcode,
        "max_weight_capacity": b.max_weight_capacity, "active": b.active,
    }


@router.get("")
async def list_bins(
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(StorageBin).where(StorageBin.org_id == current.org_id, StorageBin.active == True)  # noqa: E712
    if location_id:
        q = q.where(StorageBin.location_id == location_id)
    rows = (await db.execute(q.order_by(StorageBin.name))).scalars().all()
    # on-hand per bin
    out = []
    for b in rows:
        qty = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
            .where(StockLevel.bin_id == b.id))).scalar() or 0
        d = _ser(b)
        d["qty_on_hand"] = qty
        out.append(d)
    return {"items": out}


class BinIn(BaseModel):
    location_id: str
    name: str
    zone: Optional[str] = None
    shelf: Optional[str] = None
    bin_number: Optional[str] = None
    max_weight_capacity: float = 0.0


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
    # bin name unique within a warehouse
    dupe = (await db.execute(select(StorageBin).where(
        StorageBin.org_id == current.org_id, StorageBin.location_id == data.location_id,
        StorageBin.name == data.name, StorageBin.active == True))).scalar_one_or_none()  # noqa: E712
    if dupe:
        raise HTTPException(409, f"Bin '{data.name}' already exists in this warehouse")
    b = StorageBin(org_id=current.org_id, location_id=data.location_id, name=data.name,
                   zone=data.zone, shelf=data.shelf, bin_number=data.bin_number,
                   max_weight_capacity=data.max_weight_capacity)
    db.add(b)
    await db.commit()
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
    b.name = data.name
    b.zone = data.zone
    b.shelf = data.shelf
    b.bin_number = data.bin_number
    b.max_weight_capacity = data.max_weight_capacity
    await db.commit()
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
        .where(StockLevel.bin_id == b.id))).scalar() or 0
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

    The decrement is a guarded conditional UPDATE (only fires when the source row
    still has >= qty available). If rowcount is 0 we abort — the source lacked
    stock or lost a race — so the source bin can never go negative (no ghost stock).
    """
    if data.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if data.source_bin_id == data.destination_bin_id:
        raise HTTPException(400, "Source and destination bins are the same")

    # Both bins must exist and belong to this org.
    bins = (await db.execute(select(StorageBin).where(
        StorageBin.org_id == current.org_id,
        StorageBin.id.in_([data.source_bin_id, data.destination_bin_id])))).scalars().all()
    bmap = {b.id: b for b in bins}
    src = bmap.get(data.source_bin_id)
    dst = bmap.get(data.destination_bin_id)
    if not src or not dst:
        raise HTTPException(404, "Source or destination bin not found")

    # Source StockLevel for this part in this bin.
    src_level = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == data.part_id, StockLevel.bin_id == data.source_bin_id))).scalar_one_or_none()
    if not src_level or (src_level.qty_available or 0) < data.qty:
        have = src_level.qty_available if src_level else 0
        raise HTTPException(409, f"Source bin '{src.name}' has only {have:g}, cannot move {data.qty:g}")

    # ── Atomic guarded decrement on the source ──
    res = await db.execute(
        sa_update(StockLevel)
        .where(StockLevel.id == src_level.id, StockLevel.qty_available >= data.qty)
        .values(qty_available=StockLevel.qty_available - data.qty)
    )
    if res.rowcount == 0:
        raise HTTPException(409, "Stock changed during transfer — please retry")

    # Increment (or create) the destination level in the same warehouse as the dest bin.
    dst_level = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == data.part_id, StockLevel.bin_id == data.destination_bin_id))).scalar_one_or_none()
    if dst_level:
        await db.execute(
            sa_update(StockLevel).where(StockLevel.id == dst_level.id)
            .values(qty_available=StockLevel.qty_available + data.qty))
    else:
        db.add(StockLevel(part_id=data.part_id, location_id=dst.location_id,
                          bin_id=data.destination_bin_id, qty_available=data.qty, qty_reserved=0))

    # Two ledger rows (out of source, into destination) for a full audit trail.
    db.add(StockMovement(part_id=data.part_id, location_id=src.location_id, bin_id=src.id,
                         movement_type="transfer", qty=-data.qty, ref_type="bin_transfer",
                         ref_id=dst.id, created_by_id=current.id))
    db.add(StockMovement(part_id=data.part_id, location_id=dst.location_id, bin_id=dst.id,
                         movement_type="transfer", qty=data.qty, ref_type="bin_transfer",
                         ref_id=src.id, created_by_id=current.id))
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
