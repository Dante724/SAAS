"""Bin-level (micro-location) storage — Orderry "bin locations".
Bins live under a location and its product warehouse. ii144a: stock is one row per
(product, location, bin) — see services/stock_rows.py — so a transfer moves quantity between
two exact rows and the location total never changes. Bin names are unique per warehouse and
bin codes per shop (database indexes back both checks)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Annotated, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.models import StorageBin, StockLevel, StockMovement, Location, Organisation, Part
from backend.services import numbering, stock_rows
from backend.services.auth import get_current_employee, require_perm, Employee

router = APIRouter(prefix="/api/bins", tags=["bins"])


def _ser(b: StorageBin, defaults: Optional[set] = None) -> dict:
    return {
        "id": b.id, "location_id": b.location_id, "warehouse_id": b.warehouse_id, "name": b.name,
        "zone": b.zone, "shelf": b.shelf, "bin_number": b.bin_number, "barcode": b.barcode,
        "barcode_type": b.barcode_type or "code128",
        "max_weight_capacity": b.max_weight_capacity, "active": b.active,
        "is_default": b.id in (defaults or set()),
    }


async def _default_bin_ids(db, org_id: str) -> set:
    """ii144c — every warehouse's default bin in this shop."""
    from backend.models import Warehouse
    return set((await db.execute(select(Warehouse.default_bin_id).where(
        Warehouse.org_id == org_id, Warehouse.default_bin_id.is_not(None)))).scalars().all())


async def _set_default(db, org_id: str, b: StorageBin, on: Optional[bool]) -> None:
    """Make `b` its warehouse's default bin (on=True), or stop it being the default (on=False)."""
    from backend.models import Warehouse
    if on is None or not b.warehouse_id:
        return
    w = (await db.execute(select(Warehouse).where(
        Warehouse.id == b.warehouse_id, Warehouse.org_id == org_id))).scalar_one_or_none()
    if w is None:
        return
    if on:
        w.default_bin_id = b.id
    elif w.default_bin_id == b.id:
        w.default_bin_id = None


async def find_by_code(db, org_id: str, code: str, *, names: bool = True) -> Optional[StorageBin]:
    """ii144c — the bin a scanned label (or a typed bin name) means, in this shop: its code exactly
    first, then (names=True) a bin name, case-insensitive, when only one bin has that name."""
    code = (code or "").strip()
    if not code:
        return None
    base = select(StorageBin).where(StorageBin.org_id == org_id, StorageBin.active == True)  # noqa: E712
    b = (await db.execute(base.where(StorageBin.barcode.in_({code, code.upper()})))).scalars().first()
    if b or not names:
        return b
    named = (await db.execute(base.where(func.lower(StorageBin.name) == code.lower()).limit(2))).scalars().all()
    return named[0] if len(named) == 1 else None


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
    defaults = await _default_bin_ids(db, current.org_id)
    # on-hand per bin, one query
    qty = dict((await db.execute(select(StockLevel.bin_id, func.coalesce(func.sum(StockLevel.qty_available), 0))
                                 .join(Part, Part.id == StockLevel.part_id)
                                 .where(StockLevel.bin_id.in_([b.id for b in rows]), Part.org_id == current.org_id)
                                 .group_by(StockLevel.bin_id))).all()) if rows else {}
    return {"items": [{**_ser(b, defaults), "qty_on_hand": qty.get(b.id, 0)} for b in rows]}


@router.get("/{bin_id}/contents")
async def bin_contents(
    bin_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """ii144c — what one bin holds: each product with its quantity (the bin's own stock rows)."""
    b = (await db.execute(select(StorageBin).where(
        StorageBin.id == bin_id, StorageBin.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bin not found")
    rows = (await db.execute(select(StockLevel, Part).join(Part, Part.id == StockLevel.part_id).where(
        StockLevel.bin_id == b.id, Part.org_id == current.org_id,
        (StockLevel.qty_available != 0) | (StockLevel.qty_reserved != 0)).order_by(Part.name))).all()
    return {"bin": _ser(b, await _default_bin_ids(db, current.org_id)),
            "items": [{"part_id": p.id, "name": p.name, "sku": p.sku, "qty": float(sl.qty_available or 0),
                       "reserved": float(sl.qty_reserved or 0)} for sl, p in rows]}


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
    is_default: Optional[bool] = None                       # ii144c: the warehouse's default bin


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
    # ii144c: a bin label scans anywhere, so it can't also be one of the shop's product codes
    from backend.services import product_codes
    prod = (await db.execute(select(Part.name).where(
        Part.org_id == org_id, Part.active == True, product_codes.matches_code(org_id, code)))).scalars().first()  # noqa: E712
    if prod:
        raise HTTPException(409, f"The bin code {code} is already a code of the product '{prod}'")


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
    await db.flush()
    await _set_default(db, current.org_id, b, data.is_default)
    await _commit(db)
    await db.refresh(b)
    return _ser(b, await _default_bin_ids(db, current.org_id))


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
    await _set_default(db, current.org_id, b, data.is_default)
    await _commit(db)
    return _ser(b, await _default_bin_ids(db, current.org_id))


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
    await _set_default(db, current.org_id, b, False)
    await db.commit()
    return {"ok": True}


class TransferIn(BaseModel):
    part_id: str
    source_bin_id: Optional[str] = None      # ii144c: None = the location's unbinned stock (put away)
    destination_bin_id: str
    qty: float
    location_id: Optional[str] = None        # needed only when there's no source bin
    serial_numbers: Optional[list[Annotated[str, Field(max_length=120)]]] = Field(None, max_length=500)  # which units move (default: oldest)


@router.post("/transfer")
async def transfer_stock_between_bins(
    data: TransferIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Move `qty` of a product from one bin to another, atomically — or (no source bin) put
    unbinned stock away into a bin.

    The source is drawn with a guarded conditional UPDATE (only fires while the bin still holds
    >= qty), so the source bin can never go negative (no ghost stock). The destination is the
    exact (product, location, bin) row — created when missing, never a second row.
    """
    from backend.models import SerializedItem
    from backend.services import serial_log
    from backend.services.serial_intake import matches as _sn_matches
    if data.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if data.source_bin_id == data.destination_bin_id:
        raise HTTPException(400, "Source and destination bins are the same")
    if data.serial_numbers and len(set(data.serial_numbers)) != data.qty:
        raise HTTPException(422, "Name one serial for each unit you move")

    # Both bins and the product must exist and belong to this org.
    wanted = [x for x in (data.source_bin_id, data.destination_bin_id) if x]
    bins = (await db.execute(select(StorageBin).where(
        StorageBin.org_id == current.org_id, StorageBin.active == True,  # noqa: E712
        StorageBin.id.in_(wanted)))).scalars().all()
    bmap = {b.id: b for b in bins}
    src = bmap.get(data.source_bin_id) if data.source_bin_id else None
    dst = bmap.get(data.destination_bin_id)
    if not dst or (data.source_bin_id and not src):
        raise HTTPException(404, "Source or destination bin not found")
    loc_id = src.location_id if src else (data.location_id or dst.location_id)
    if dst.location_id != loc_id:
        raise HTTPException(409, "Both bins must be at the same location — use a stock transfer between locations")
    part = (await db.execute(select(Part.id).where(
        Part.id == data.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Product not found")

    if src:
        src_rows = [r for r in [await stock_rows.row_for(db, data.part_id, loc_id, src.id)] if r]
    else:
        src_rows = [r for r in await stock_rows.rows(db, data.part_id, loc_id) if r.bin_id is None]
    src_name = src.name if src else "unbinned stock"
    # A serialized unit sits in a bin too: the named units, else as many as were moved, oldest first.
    uq = select(SerializedItem).where(
        SerializedItem.org_id == current.org_id, SerializedItem.part_id == data.part_id,
        SerializedItem.location_id == loc_id, SerializedItem.status == "in_stock",
        SerializedItem.bin_id == src.id if src else SerializedItem.bin_id.is_(None))
    if data.serial_numbers:
        from sqlalchemy import or_
        uq = uq.where(or_(*[_sn_matches(SerializedItem.serial_number, sn) for sn in data.serial_numbers]))
    units = (await db.execute(uq.order_by(SerializedItem.created_at).limit(int(data.qty))
                              .with_for_update())).scalars().all()
    if data.serial_numbers and len(units) != len(set(data.serial_numbers)):
        raise HTTPException(409, f"Not every serial you named is in stock in {src_name}")
    try:
        await stock_rows.draw(db, data.part_id, loc_id, data.qty, bin_id=src.id if src else None, only=src_rows)
    except stock_rows.NotEnoughStock as e:
        raise HTTPException(409, f"{'Source bin ' + repr(src.name) if src else 'Unbinned stock'} has only "
                                 f"{e.have:g}, cannot move {data.qty:g}")
    await stock_rows.add(db, data.part_id, loc_id, data.qty, bin_id=dst.id)

    # Two ledger rows (out of source, into destination) for a full audit trail.
    db.add(StockMovement(part_id=data.part_id, location_id=loc_id, bin_id=src.id if src else None,
                         movement_type="transfer", qty=-data.qty, ref_type="bin_transfer",
                         ref_id=dst.id, created_by_id=current.id))
    db.add(StockMovement(part_id=data.part_id, location_id=loc_id, bin_id=dst.id,
                         movement_type="transfer", qty=data.qty, ref_type="bin_transfer",
                         ref_id=src.id if src else None, created_by_id=current.id))
    for u in units:
        u.bin_id = dst.id
        serial_log.record(db, u, "moved", ref_type="bin_transfer", label=f"{src_name} → {dst.name}",
                          actor_id=current.id)
    await db.commit()
    return {"ok": True, "moved": data.qty, "from": src_name, "to": dst.name}


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
    from backend.models import _uuid
    from backend.services import serial_log
    for sn in serials:
        unit = SerializedItem(id=_uuid(), org_id=current.org_id, part_id=data.part_id, serial_number=sn,
                              bin_id=data.bin_id, location_id=data.location_id, batch_id=batch.id,
                              unit_cost=_Dec(str(data.unit_cost)), status="in_stock")
        db.add(unit)
        serial_log.record(db, unit, "received", ref_type="batch", ref_id=batch.id, actor_id=current.id)
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
    from backend.models import Order
    if not (await db.execute(select(Order.id).where(
            Order.id == data.order_id, Order.org_id == current.org_id))).scalar_one_or_none():
        raise HTTPException(404, "Order not found")
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
    from backend.services.redact import can_see_costs
    show = await can_see_costs(db, current)    # ii144c: cost only for staff who may see purchase prices
    return {
        "batches": [{"id": b.id, "unit_cost": float(b.unit_cost or 0) if show else None,
                     "qty_received": float(b.qty_received or 0),
                     "qty_remaining": float(b.qty_remaining or 0),
                     "bin": bin_names.get(b.bin_id),
                     "received_at": b.received_at.isoformat() if b.received_at else None}
                    for b in batches],
        "serials": [{"id": s.id, "serial_number": s.serial_number, "status": s.status,
                     "bin": bin_names.get(s.bin_id),
                     "unit_cost": float(s.unit_cost or 0) if show else None} for s in serials],
    }
