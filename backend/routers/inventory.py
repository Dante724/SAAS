import base64 as _base64
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core import paging as _paging
from backend.db.database import get_db
from backend.models import Employee, Part, SerializedItem, StockLevel, StockMovement
from backend.services import product_codes, stock_posting, stock_rows
from backend.services.serial_intake import SerialStr
from backend.services.auth import get_current_employee, require_perm
from backend.services.batching import names_by_id
from backend.services.numbering import next_number
from backend.services.redact import can_see_costs, drop_cost_fields
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

class PartIn(BaseModel):
    uom: str = "pcs"
    name: str
    sku: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    cost_price: float = 0.0
    sell_price: float = 0.0
    repair_price: float = 0.0
    barcode: Optional[str] = None
    code: Optional[str] = None           # internal code (ii144a: may be the shop's unique identifier)
    min_stock: int = 0
    min_order_qty: float = 0
    weight: Optional[float] = None       # kg — used for weight-basis freight allocation (144ar)
    dimensions: Optional[str] = None     # "LxWxH" cm

class StockAdjust(BaseModel):
    location_id: str
    qty: float
    movement_type: str = "receive"  # receive|writeoff|transfer|adjust
    note: Optional[str] = None


# A stock quantity above this is almost certainly a barcode/serial typed into the
# qty field (e.g. a 13-digit EAN), not a real count — reject it at the boundary so
# it never lands in qty_available and blows up the inventory reports.
import math as _math

MAX_STOCK_QTY = 1_000_000


def _guard_qty(*vals) -> None:
    for v in vals:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(422, "Quantity must be a number")
        if not _math.isfinite(f) or abs(f) > MAX_STOCK_QTY:
            raise HTTPException(422, f"Quantity {v} is out of range (max {MAX_STOCK_QTY:,}). "
                                     "This looks like a barcode or serial, not a count.")

def _ser_part(p: Part, stock: list[StockLevel]) -> dict:
    total_qty = sum(s.qty_available for s in stock if s.part_id == p.id)
    total_res = sum(s.qty_reserved for s in stock if s.part_id == p.id)
    margin = round((p.sell_price - p.cost_price) / p.sell_price * 100, 1) if p.sell_price else 0
    return {
        "id": p.id, "sku": p.sku, "uom": p.uom, "name": p.name, "brand": p.brand,
        "category": p.category, "category_id": getattr(p, "category_id", None),
        "cost_price": p.cost_price, "avg_cost": p.avg_cost, "sell_price": p.sell_price, "repair_price": p.repair_price,
        "barcode": p.barcode, "mpn": p.mpn, "min_stock": p.min_stock, "margin_pct": margin,
        "weight": getattr(p, "weight", None), "dimensions": getattr(p, "dimensions", None),
        "image_data": getattr(p, "image_data", None),
        "min_order_qty": getattr(p, "min_order_qty", 0) or 0,
        "qty_available": total_qty, "qty_reserved": total_res,
        "low_stock": total_qty <= p.min_stock,
        "created_at": p.created_at.isoformat(),
    }

@router.get("/parts")
async def list_parts(
    search: Optional[str] = None, category: Optional[str] = None,
    category_id: Optional[str] = None,        # build 144ao: filter by the managed category tree
    location_id: Optional[str] = None,        # build 140: show stock for one branch
    device_id: Optional[str] = None,          # 144jt: flag/limit parts that fit this device
    compatible_only: bool = False,
    low_stock: bool = False, skip: int = 0, limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Part).where(Part.org_id == current.org_id, Part.active == True)
    if search:
        # Build 144an — POS findability: match name, SKU, barcode, or internal code.
        s = search.strip()
        q = q.where(or_(Part.name.icontains(s), Part.sku.icontains(s),
                        Part.barcode.icontains(s), Part.code.icontains(s), Part.mpn.icontains(s),
                        Part.id.in_(product_codes.barcode_part_ids(current.org_id, s))))   # ii144a: extra barcodes
    if category_id:
        q = q.where(Part.category_id == category_id)
    if category:
        q = q.where(Part.category == category)
    result = await db.execute(q.offset(skip).limit(limit))
    parts = result.scalars().all()

    part_ids = [p.id for p in parts]
    _sq = select(StockLevel).where(StockLevel.part_id.in_(part_ids))
    if location_id:
        _sq = _sq.where(StockLevel.location_id == location_id)
    stock_result = await db.execute(_sq)
    stocks = stock_result.scalars().all()

    items = [_ser_part(p, stocks) for p in parts]
    if device_id:
        from backend.models import Device
        from backend.services.compatibility import part_fits
        dev = (await db.execute(select(Device).where(
            Device.id == device_id, Device.org_id == current.org_id))).scalar_one_or_none()
        by_id = {p.id: p for p in parts}
        for it in items:
            it["fits"] = part_fits(by_id[it["id"]].compatibility_json, dev.brand, dev.model) if dev else False
        if compatible_only:
            items = [i for i in items if i.get("fits")]
    if low_stock:
        items = [i for i in items if i["low_stock"]]
    return {"total": len(items), "items": items}


@router.get("/scan")
async def scan_resolve(
    code: str,
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144an — resolve a scanned/typed code to a product for the POS. Matches, in
    order: exact SKU, barcode, internal code, MPN, then a serialized unit's serial number.
    Returns the product (with stock) and, for a serial hit, which unit was matched."""
    from backend.services import scan_codes
    raw = (code or "").strip()
    scan = scan_codes.parse(raw)       # ii144b: AIM prefix stripped, GS1 label read
    code = scan.text
    if not code:
        raise HTTPException(400, "No code provided")
    base = select(Part).where(Part.org_id == current.org_id, Part.active == True)  # noqa: E712
    part = (await db.execute(base.where(product_codes.matches_code(
        current.org_id, raw, include_mpn=True)))).scalars().first()    # MPNs are unique case-insensitively
    matched_serial = None
    matched_on = None
    if part:
        matched_on = ("sku" if (part.sku or "").lower() == code.lower() else "code" if part.code == code
                      else "mpn" if (part.mpn or "").lower() == code.lower() else "barcode")
    else:
        from backend.services.serial_intake import matches as _sn_matches
        si = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == current.org_id,
            or_(*[_sn_matches(SerializedItem.serial_number, t) for t in (scan.serial, scan.clean) if t])
        ))).scalars().first()      # the GS1 (21) serial, or the code exactly as scanned
        if si:
            part = (await db.execute(base.where(Part.id == si.part_id))).scalars().first()
            matched_serial = si.serial_number
            matched_on = "serial"
    if not part:
        raise HTTPException(404, "No product matches that code")
    _sq = select(StockLevel).where(StockLevel.part_id == part.id)
    if location_id:
        _sq = _sq.where(StockLevel.location_id == location_id)
    stocks = (await db.execute(_sq)).scalars().all()
    return {"matched_on": matched_on, "serial": matched_serial or scan.serial, "product": _ser_part(part, stocks),
            "scan": scan.as_dict()}

@router.post("/parts", status_code=201)
async def create_part(
    data: PartIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_products")),
):
    payload = data.model_dump()
    if not await can_see_costs(db, current):
        drop_cost_fields(payload)
    part = Part(org_id=current.org_id, **payload)
    await product_codes.ensure_identity_free(db, current.org_id, name=part.name, sku=part.sku,
                                             barcode=part.barcode, code=part.code)
    await product_codes.fill_new_product(db, part)
    db.add(part)
    await db.commit()
    await db.refresh(part)
    return {"id": part.id, "name": part.name, "sku": part.sku}

@router.get("/parts/{part_id}")
async def get_part(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(select(Part).where(Part.id == part_id, Part.org_id == current.org_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Part not found")
    stock_result = await db.execute(select(StockLevel).where(StockLevel.part_id == part_id))
    stocks = stock_result.scalars().all()
    return _ser_part(part, stocks)

@router.put("/parts/{part_id}")
async def update_part(
    part_id: str, data: PartIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_products")),
):
    result = await db.execute(select(Part).where(Part.id == part_id, Part.org_id == current.org_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Part not found")
    # exclude_unset so a client that omits a field (e.g. an older bundle without weight)
    # leaves it untouched instead of nulling it.
    payload = data.model_dump(exclude_unset=True)
    if not await can_see_costs(db, current):
        drop_cost_fields(payload)
    await product_codes.ensure_identity_free(
        db, current.org_id, exclude_part_id=part.id, fields=set(payload), **{
            k: payload.get(k) for k in ("name", "sku", "barcode", "code")})
    for k, v in payload.items():
        setattr(part, k, v)
    await db.commit()
    return {"ok": True}

@router.post("/parts/{part_id}/adjust")
async def adjust_stock(
    part_id: str, data: StockAdjust,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    _guard_qty(data.qty)
    result = await db.execute(select(Part).where(Part.id == part_id, Part.org_id == current.org_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Part not found")
    # Build 89: the warehouse must permit this action, and this employee must be
    # allowed to perform it here (per-employee access beats the role-level scope).
    from backend.services import warehouses as _wh
    _action = "writeoff" if data.movement_type == "writeoff" else \
              "transfer" if data.movement_type == "transfer" else "post"
    _w = await _wh.guard_action(db, current, _action, location_id=data.location_id)

    if data.movement_type in ("receive", "adjust"):
        # Into the location's main row; a first receipt creates it tagged with the warehouse it came through.
        has_main = any(r.bin_id is None for r in await stock_rows.rows(db, part_id, data.location_id))
        await stock_rows.add(db, part_id, data.location_id, data.qty,
                             warehouse_id=None if has_main or not _w else _w.id)
    elif data.movement_type == "writeoff":
        # Never below zero (the old clamp): take what is there, across bins.
        have, _res = await stock_rows.totals(db, part_id, data.location_id)
        await stock_rows.draw(db, part_id, data.location_id, min(data.qty, max(have, 0)))

    movement = StockMovement(
        part_id=part_id, location_id=data.location_id,
        movement_type=data.movement_type, qty=data.qty,
        created_by_id=current.id,
        warehouse_id=_w.id if _w else None,
    )
    db.add(movement)
    await db.commit()
    return {"ok": True, "qty_available": (await stock_rows.totals(db, part_id, data.location_id))[0]}


class ScanAdjust(BaseModel):
    code: str                         # scanned SKU / barcode / internal code
    location_id: str
    qty: float
    movement_type: str = "receive"    # receive|writeoff|adjust
    note: Optional[str] = None


@router.post("/scan-adjust")
async def scan_adjust(
    data: ScanAdjust,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Phase 2 — hands-free stock posting: scan a part (sku/barcode/code) + qty and
    receive / write-off / count it, without looking up the part id. Resolves the
    part, then delegates to the same adjust logic (permission + warehouse guards)."""
    code = (data.code or "").strip()
    if not code:
        raise HTTPException(422, "code required")
    part = await product_codes.find_by_code(db, current.org_id, code, active_only=False)
    if not part:
        raise HTTPException(404, "No part matches that code")
    out = await adjust_stock(part.id, StockAdjust(
        location_id=data.location_id, qty=data.qty,
        movement_type=data.movement_type, note=data.note), db, current)
    return {**out, "part_id": part.id, "part_name": part.name, "sku": part.sku}


class PostingLine(BaseModel):
    part_id: str
    qty: float
    purchase_price: float = 0.0
    markup_pct: Optional[float] = None   # if set, new sell price = purchase * (1 + markup/100)
    serials: Optional[list[SerialStr]] = Field(None, max_length=500)  # serial-tracked: one serial / IMEI per unit
    bin_id: Optional[str] = None         # ii144a: put away straight into a bin at the posting's location
    lot: Optional[str] = Field(None, max_length=40)   # ii144b: batch / lot (GS1 (10) from the label)


class PostingIn(BaseModel):
    location_id: str
    note: Optional[str] = None           # invoice / reference
    supplier_id: Optional[str] = None    # Build 120: which supplier this stock came from
    lines: list[PostingLine] = Field(max_length=500)


async def _do_posting(db, org_id, created_by_id, location_id, supplier_id, lines):
    """Shared posting engine used by create_posting + bulk commit. `lines` is an iterable of
    objects/dicts with part_id, qty, purchase_price, markup_pct. Assumes parts already exist.
    Returns (lines_posted, total_cost). Does NOT commit — caller owns the transaction.
    Serial-tracked products need one serial per unit (`serials`), checked before anything moves."""
    from backend.services import serial_intake

    def _g(ln, k, d=None):
        return getattr(ln, k, None) if not isinstance(ln, dict) else ln.get(k, d)
    posted = 0
    total_cost = 0.0
    seen_serials: set = set()
    for ln in lines:
        qty = float(_g(ln, "qty") or 0)
        if qty <= 0:
            raise HTTPException(422, "Quantity must be positive")
        pid = _g(ln, "part_id")
        part = (await db.execute(select(Part).where(
            Part.id == pid, Part.org_id == org_id))).scalar_one_or_none()
        if not part:
            raise HTTPException(404, f"Part {pid} not found")
        cost = float(_g(ln, "purchase_price") or 0.0)
        serials = await serial_intake.validate_intake(db, org_id, part, qty, _g(ln, "serials"), seen_serials)
        from backend.services import inventory_alerts as _alerts
        before_oh = await _alerts.global_on_hand(db, part.id)
        await stock_posting.apply_receipt_line(
            db, org_id=org_id, part=part, location_id=location_id,
            qty=qty, unit_cost=cost, created_by_id=created_by_id,
            ref_type="supplier" if supplier_id else "posting", ref_id=supplier_id,
            serials=serials, create_batch=True, set_last_cost=True, bin_id=_g(ln, "bin_id"), lot=_g(ln, "lot"),
            supplier_id=supplier_id,
            expiry_date=_g(ln, "expiry_date") if part.expiry_tracking else None)
        # 144bo: fresh stock arrived — reset low-stock dedupe + ping any job that was waiting.
        after_oh = await _alerts.global_on_hand(db, part.id)
        await _alerts.on_received(db, org_id, part, before_oh, after_oh)
        markup = _g(ln, "markup_pct")
        if markup is not None and cost:
            part.sell_price = q2(cost * (1 + float(markup) / 100.0))
        posted += 1
        total_cost += qty * cost
    if supplier_id and total_cost:
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == supplier_id, Supplier.org_id == org_id))).scalar_one_or_none()
        if sup:
            # 144ax: single write-path keeps Supplier.balance + contact ledger in lockstep.
            from backend.services.ledger import adjust_supplier_ap
            await adjust_supplier_ap(db, org_id, sup, total_cost, entry_type="posting",
                                     note="Stock posting", ref_type="supplier", ref_id=sup.id,
                                     created_by_id=created_by_id)
    return posted, q2(total_cost)


@router.post("/postings", status_code=201)
async def create_posting(
    data: PostingIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Bulk stock inflow (a 'posting'): receive several products at once, each with
    a purchase price and optional markup% that auto-sets the new sell price.
    Updates stock, moving-average cost, and records a movement per line."""
    if not data.location_id:
        raise HTTPException(422, "Warehouse is required")
    if not data.lines:
        raise HTTPException(422, "Add at least one product line")
    posted, total_cost = await _do_posting(
        db, current.org_id, current.id, data.location_id, data.supplier_id, data.lines)
    await db.commit()
    return {"ok": True, "lines_posted": posted, "total_cost": total_cost}


# ── 144bk: bulk-posting front doors (preview + commit) + bulk qty adjust ──────
class PreviewRow(BaseModel):
    code: Optional[str] = None       # sku / barcode / code typed or pasted
    mpn: Optional[str] = None        # opa: manufacturer part number column
    name: Optional[str] = None
    qty: float = 1
    purchase_price: Optional[float] = None
    markup_pct: Optional[float] = None
    product_type: Optional[str] = Field(None, max_length=60)   # 16a: supplier sheet "Type" (serialized / expiry …)
    serials: Optional[list[SerialStr]] = Field(None, max_length=500)  # serials already pasted for this row


class PostingPreviewIn(BaseModel):
    rows: list[PreviewRow] = Field(max_length=500)


@router.post("/postings/preview")
async def preview_posting(
    data: PostingPreviewIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Classify each pasted/typed row against the catalog so the user confirms before stock
    moves: `matched` (exact code/name hit), `unsure` (fuzzy candidates — pick one), or
    `new` (will be created on commit). Never writes anything. Each row also says whether it is
    serial-tracked and how many serials it still needs (`needs_serials`)."""
    import difflib

    from backend.services import serial_intake
    # ponytail: loads the org's full active catalog into memory for fuzzy matching; fine at SMB
    # scale, paginate / move matching to SQL if a tenant exceeds ~10k active SKUs.
    parts = (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.active == True))).scalars().all()  # noqa: E712
    by_code = {}
    by_mpn = {}
    for p in parts:
        for key in (p.sku, p.barcode, p.code):
            if key:
                by_code[str(key).strip().lower()] = p
        if p.mpn:
            by_mpn[p.mpn.strip().lower()] = p
    names = {p.name.strip().lower(): p for p in parts if p.name}
    name_list = list(names.keys())
    # ii144b: every barcode by match key, so any form of a GTIN (or a GS1 label) finds its product
    from backend.models import ProductBarcode
    from backend.services import scan_codes
    by_id = {p.id: p for p in parts}
    by_key = {k: by_id[pid] for k, pid in (await db.execute(select(ProductBarcode.match_key, ProductBarcode.part_id)
              .where(ProductBarcode.org_id == current.org_id, ProductBarcode.active == True))).all()  # noqa: E712
              if pid in by_id}

    out = []
    for i, row in enumerate(data.rows):
        scan = scan_codes.parse(row.code) if row.code else None
        code = (scan.text if scan else "").strip()
        mpn = (row.mpn or "").strip()
        nm = (row.name or "").strip()
        status, part, cands = "new", None, []
        hit = by_mpn.get(mpn.lower()) if mpn else None
        if hit is None and scan:
            hit = next((by_key[k] for k in scan.keys if k in by_key), None)
        if hit is None and code:     # a supplier sheet's "code" is often the MPN too
            hit = by_code.get(code.lower()) or by_mpn.get(code.lower())
        if hit is None and nm:
            hit = names.get(nm.lower())
        if hit is not None:
            status, part = "matched", hit
        elif nm or code:
            # fuzzy by name → candidates for the user to pick
            q = (nm or code).lower()
            close = difflib.get_close_matches(q, name_list, n=4, cutoff=0.5)
            # also substring contains
            for n in name_list:
                if q and q in n and n not in close:
                    close.append(n)
            cands = [{"id": names[n].id, "name": names[n].name, "sku": names[n].sku,
                      "serialized": bool(names[n].serialized),
                      "expiry_tracking": bool(names[n].expiry_tracking)} for n in close[:5]]
            status = "unsure" if cands else "new"
        # 16a: an existing product keeps its own type; the sheet's type only shapes new products.
        ptype = serial_intake.parse_product_type(row.product_type)
        serialized = bool(part.serialized) if part else ptype == "serialized"
        expiry = bool(part.expiry_tracking) if part else ptype == "expiry"
        row_serials = [x for x in (row.serials or []) if x and x.strip()]
        if serialized and scan and scan.serial and scan.serial not in row_serials:
            row_serials.append(scan.serial)      # a GS1 label carries the unit's serial (21)
        have = len(row_serials)
        need = max(0, int(_math.ceil(row.qty or 0)) - have) if serialized else 0
        whole = not serialized or abs((row.qty or 0) - round(row.qty or 0)) < 1e-9
        out.append({
            "index": i, "match_status": status,
            "product_type": "serialized" if serialized else "expiry" if expiry else "standard",
            "serialized": serialized, "expiry_tracking": expiry,
            "serials": row_serials,
            "needs_serials": need,
            # ii144b: what a GS1 label says about this delivery (batch, expiry)
            "lot": scan.lot if scan else None,
            "expiry_date": scan.expiry.isoformat() if scan and scan.expiry else None,
            "qty_not_whole": not whole,          # a serial-tracked row needs a whole quantity
            "part_id": part.id if part else None,
            "part_name": part.name if part else None,
            "sku": (part.sku if part else None) or code or None,
            "candidates": cands,
            "code": code or None, "mpn": mpn or None, "name": nm or None,
            "qty": row.qty, "purchase_price": row.purchase_price, "markup_pct": row.markup_pct,
        })
    return {"rows": out}


class CommitRow(BaseModel):
    part_id: Optional[str] = None        # existing part chosen/confirmed
    new_name: Optional[str] = Field(None, max_length=300)   # create a new part with this name
    new_sku: Optional[str] = Field(None, max_length=100)
    new_mpn: Optional[str] = Field(None, max_length=64)
    new_type: Optional[str] = None       # 16a: standard | serialized | expiry (new products only)
    qty: float = 1
    purchase_price: Optional[float] = None
    markup_pct: Optional[float] = None
    serials: Optional[list[SerialStr]] = Field(None, max_length=500)  # one per unit for serial-tracked products
    expiry_date: Optional[date] = None   # dates this delivery's batch (expiry-tracked products)
    lot: Optional[str] = Field(None, max_length=40)   # ii144b: batch / lot, e.g. GS1 (10) from the label
    bin_id: Optional[str] = None         # ii144c: put this row away into a bin
    @field_validator("qty")
    @classmethod
    def _qty_cap(cls, v):
        if v > 100_000:
            raise ValueError("Quantity looks like a barcode or serial, not a count")
        return v


class PostingCommitIn(BaseModel):
    location_id: str
    supplier_id: Optional[str] = None
    note: Optional[str] = None
    bin_id: Optional[str] = None         # ii144c: the bin every row without its own goes into (a scanned bin)
    rows: list[CommitRow] = Field(max_length=500)
    generate_serials: bool = False       # fill missing serials from the shop's serial template


async def _check_posting_serials(db, current, rows: list, parts: dict, generate: bool) -> None:
    """Serials on a posting: one per unit of a serialized product, none already in stock, none
    twice in the file; missing ones generated on request. Fills each row's `serials` in place."""
    from backend.models import Organisation
    from backend.services import importer
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    ctx = importer.Ctx(db=db, current=current, org=org, options={}, perms={})
    ctx.data["serials_in_stock"] = set((await db.execute(select(SerializedItem.serial_number).where(
        SerializedItem.org_id == current.org_id,
        SerializedItem.status.in_(("in_stock", "allocated_to_order"))))).scalars())
    ctx.data["serial_n"] = (await db.execute(select(func.count()).select_from(SerializedItem).where(
        SerializedItem.org_id == current.org_id))).scalar() or 0
    seen: set = set()
    todo = []            # (line no, row, typed serials, how many to generate)
    for i, ln in enumerate(rows, start=1):
        sns = [s.strip() for s in (ln.get("serials") or []) if s and s.strip()]
        part = parts[ln["part_id"]]
        if not part.serialized:
            if sns:
                raise HTTPException(422, f"Line {i}: {part.name} isn't serialized — remove the serial numbers")
            continue
        if not sns and not generate:
            continue                  # serials are optional on a posting unless asked for
        qty = float(ln["qty"] or 0)
        if not qty.is_integer():
            raise HTTPException(422, f"Line {i}: serialized stock must be a whole number of units")
        for sn in sns:
            if sn in seen:
                raise HTTPException(422, f"Serial {sn} is listed twice")
            if sn in ctx.data["serials_in_stock"]:
                raise HTTPException(409, f"Serial {sn} is already in stock")
            seen.add(sn)
            ctx.seen[("serial", sn)] = i
        if len(sns) > qty:
            raise HTTPException(422, f"Line {i}: more serial numbers ({len(sns)}) than units ({int(qty)})")
        if len(sns) < qty and not generate:
            raise HTTPException(422, f"Line {i}: {int(qty)} units of {part.name} need {int(qty)} serial numbers "
                                     f"({len(sns)} given)")
        todo.append((i, ln, sns, int(qty) - len(sns)))
    # every typed serial is claimed first, so a generated one can never repeat a later line's
    for i, ln, sns, missing in todo:
        if missing > 0:
            sns = sns + importer.generate_serials(ctx, missing, importer.Row(row=i, rec={}))
        ln["serials"] = sns


@router.post("/postings/commit", status_code=201)
async def commit_posting(
    data: PostingCommitIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Commit a reviewed bulk posting: create any `new_name` rows as products, then post
    every row through the same WAC engine as a normal posting — one transaction."""
    if not data.location_id:
        raise HTTPException(422, "Warehouse is required")
    from backend.services import warehouses as _wh
    await _wh.guard_action(db, current, "post", location_id=data.location_id)
    if not data.rows:
        raise HTTPException(422, "Add at least one row")
    if any(not r.part_id for r in data.rows):
        # new products land in the catalog, so this needs the same right as creating one
        from backend.services.auth import assert_perm
        await assert_perm(db, current, "can_create_products")
    created = 0
    lines = []
    for r in data.rows:
        pid = r.part_id
        if not pid:
            if not (r.new_name and r.new_name.strip()):
                raise HTTPException(422, "Each row needs an existing part or a new product name")
            cost = float(r.purchase_price or 0)
            sell = q2(cost * (1 + float(r.markup_pct) / 100.0)) if (r.markup_pct and cost) else 0.0
            from backend.routers.catalog_products import _checked_mpn
            from backend.services.serial_intake import parse_product_type
            ptype = parse_product_type(r.new_type)
            part = Part(org_id=current.org_id, name=r.new_name.strip(),
                        sku=(r.new_sku or "").strip() or None,
                        mpn=await _checked_mpn(db, current.org_id, r.new_mpn),   # one active product per MPN
                        cost_price=cost, sell_price=sell,
                        serialized=ptype == "serialized" or bool(r.serials), expiry_tracking=ptype == "expiry")
            db.add(part)
            await db.flush()
            pid = part.id
            created += 1
        lines.append({"part_id": pid, "qty": r.qty, "serials": r.serials, "expiry_date": r.expiry_date,
                      "lot": r.lot, "bin_id": r.bin_id or data.bin_id,
                      "purchase_price": r.purchase_price, "markup_pct": r.markup_pct})
    ids = {ln["part_id"] for ln in lines}
    parts = {p.id: p for p in (await db.execute(select(Part).where(
        Part.id.in_(ids), Part.org_id == current.org_id))).scalars()}
    if len(parts) != len(ids):
        raise HTTPException(404, "Product not found")
    await _check_posting_serials(db, current, lines, parts, data.generate_serials)
    posted, total_cost = await _do_posting(
        db, current.org_id, current.id, data.location_id, data.supplier_id, lines)
    from backend.routers.catalog_products import commit_mpn_safe
    await commit_mpn_safe(db)
    return {"ok": True, "lines_posted": posted, "created_parts": created, "total_cost": total_cost}


class SerialCheckIn(BaseModel):
    serials: list[SerialStr] = Field(max_length=500)
    part_id: Optional[str] = None        # the product being received (scopes the check to this shop's part)


@router.post("/serials/check")
async def check_serials(
    data: SerialCheckIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """16b — live check behind the serial / IMEI scan box: duplicates in this delivery and
    units already in stock are blocked; a wrong IMEI check digit or a unit this shop sold
    before is a warning. Same rules the posting and PO receive enforce. Never writes."""
    from backend.services import serial_intake
    if data.part_id:
        owned = (await db.execute(select(Part.id).where(
            Part.id == data.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
        if not owned:
            raise HTTPException(404, "Product not found")
    rows = await serial_intake.check(db, current.org_id, data.serials)
    return {"rows": rows, "summary": serial_intake.summary(rows)}


class ResolveRow(BaseModel):
    part_id: Optional[str] = None
    new_name: Optional[str] = Field(None, max_length=300)
    new_sku: Optional[str] = Field(None, max_length=100)
    new_mpn: Optional[str] = Field(None, max_length=64)
    new_barcode: Optional[str] = Field(None, max_length=100)
    product_type: Optional[str] = Field(None, max_length=60)
    cost: Optional[float] = Field(None, ge=0)


class ResolveIn(BaseModel):
    rows: list[ResolveRow] = Field(max_length=2000)


@router.post("/import/resolve-products")
async def resolve_import_products(
    data: ResolveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """13b — turn reviewed import rows (from `/postings/preview`) into product ids for a
    purchase order: confirmed matches are checked to be this shop's, and missing products are
    created (needs "Create new products"). The PO itself is then saved through the usual calls."""
    from backend.routers.catalog_products import _checked_mpn, commit_mpn_safe
    from backend.services.serial_intake import parse_product_type
    if not data.rows:
        raise HTTPException(422, "Nothing to import")
    ids = {r.part_id for r in data.rows if r.part_id}
    names = dict((await db.execute(select(Part.id, Part.name).where(
        Part.org_id == current.org_id, Part.id.in_(ids)))).all()) if ids else {}
    if ids - set(names):
        raise HTTPException(422, "One or more products don't belong to your company")
    if any(not r.part_id for r in data.rows):
        from backend.services.auth import assert_perm
        await assert_perm(db, current, "can_create_products")
    out, created = [], 0
    for r in data.rows:
        if r.part_id:
            out.append({"part_id": r.part_id, "created": False, "name": names[r.part_id]})
            continue
        name = (r.new_name or "").strip() or (r.new_sku or "").strip() or (r.new_mpn or "").strip()
        if not name:
            raise HTTPException(422, "Each row needs an existing product or a name for the new one")
        ptype = parse_product_type(r.product_type)
        sku = (r.new_sku or "").strip() or None
        if sku:
            # the same SKU twice (a retried import, or two rows) would make two products for one item
            dup = (await db.execute(select(Part.id, Part.name).where(
                Part.org_id == current.org_id, Part.active == True,  # noqa: E712
                func.lower(Part.sku) == sku.lower()).limit(1))).first()
            if dup:
                out.append({"part_id": dup[0], "created": False, "name": dup[1]})
                continue
        bc = (r.new_barcode or "").strip() or None
        if bc:      # ii144b: the same product number in any barcode form is the same product
            owner = await product_codes.barcode_owner(db, current.org_id, bc)
            if owner:
                out.append({"part_id": owner.id, "created": False, "name": owner.name})
                continue
        part = Part(org_id=current.org_id, name=name, sku=sku,
                    barcode=bc,
                    mpn=await _checked_mpn(db, current.org_id, r.new_mpn),
                    cost_price=float(r.cost or 0),
                    serialized=ptype == "serialized", expiry_tracking=ptype == "expiry")
        db.add(part)
        await db.flush()
        created += 1
        out.append({"part_id": part.id, "created": True, "name": part.name})
    await commit_mpn_safe(db)
    return {"rows": out, "created": created}


@router.post("/postings/extract")
async def extract_posting(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """AI front door: turn an uploaded supplier invoice / packing-slip photo (or pasted text)
    into draft posting rows. The rows are returned only — the caller runs them through
    /postings/preview + /postings/commit, so nothing is written without human confirmation.
    Degrades to deterministic text parsing when no AI provider is configured."""
    from backend.services import ai as ai_service
    image_b64 = media = None
    if file is not None:
        raw = await file.read()
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(413, "File too large (max 8 MB)")
        media = file.content_type or "image/jpeg"
        if not (media.startswith("image/") or media == "application/pdf"):
            raise HTTPException(422, "Upload an image or PDF of the invoice / packing slip")
        image_b64 = _base64.b64encode(raw).decode()
    if image_b64 is None and not (text and text.strip()):
        raise HTTPException(422, "Provide a file or some pasted text")
    return await ai_service.extract_posting_rows(text=text, image_b64=image_b64, media_type=media)


class BulkAdjustRow(BaseModel):
    part_id: str
    set_qty: Optional[float] = None      # set absolute on-hand at the location
    delta: Optional[float] = None        # or add/subtract this much
    note: Optional[str] = None


class BulkAdjustIn(BaseModel):
    location_id: str
    rows: list[BulkAdjustRow]


@router.post("/parts/bulk-adjust")
async def bulk_adjust(
    data: BulkAdjustIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """ShopMonkey-style bulk quantity adjustment across selected parts. Each row either
    sets an absolute on-hand (`set_qty`) or applies a `delta`; one `adjust` movement each."""
    if not data.location_id:
        raise HTTPException(422, "Warehouse is required")
    if not data.rows:
        raise HTTPException(422, "No rows to adjust")
    adjusted = 0
    for r in data.rows:
        if r.set_qty is None and r.delta is None:
            raise HTTPException(422, "Each row needs set_qty or delta")
        part = (await db.execute(select(Part).where(
            Part.id == r.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
        if not part:
            raise HTTPException(404, f"Part {r.part_id} not found")
        if r.delta is not None:
            delta = float(r.delta)
        else:
            cur = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                    .where(StockLevel.part_id == r.part_id,
                                           StockLevel.location_id == data.location_id))).scalar() or 0
            delta = float(r.set_qty) - float(cur)
        if delta == 0:
            continue
        await adjust_stock(r.part_id, StockAdjust(
            location_id=data.location_id, qty=delta, movement_type="adjust",
            note=r.note or "Bulk adjust"), db, current)
        adjusted += 1
    await db.commit()
    return {"ok": True, "adjusted": adjusted}


@router.get("/to-order")
async def parts_to_order(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """144bm — job-driven procurement: every part needed across OPEN work orders, netted
    against on-hand and already-on-order, grouped by supplier. Read-only — the FE turns a
    supplier group into a PO via the existing /inventory/po endpoint."""
    from collections import defaultdict

    from backend.models import Order, OrderLine, OrderStatus, POLine, Supplier

    closed = {"closed", "done", "delivered", "dropped", "cancelled", "completed", "picked_up"}
    for (k,) in (await db.execute(select(OrderStatus.key).where(
            OrderStatus.org_id == current.org_id,
            OrderStatus.status_group.in_(["closed", "dropped"])))).all():
        if k:
            closed.add(k)

    # ponytail: loads all open orders + their part lines into memory to aggregate; fine at SMB
    # scale, push the aggregation into SQL (group-by) if a tenant carries thousands of open jobs.
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.archived == False,  # noqa: E712
        ~Order.status.in_(closed)))).scalars().all()
    if not orders:
        return {"items": [], "summary": {"parts": 0, "jobs_waiting": 0, "shortfall_units": 0}}
    ometa = {o.id: o for o in orders}
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.order_id.in_(list(ometa.keys())),
        OrderLine.line_type == "part", OrderLine.ref_id.isnot(None)))).scalars().all()
    if not lines:
        return {"items": [], "summary": {"parts": 0, "jobs_waiting": 0, "shortfall_units": 0}}

    need: dict = defaultdict(float)
    jobs: dict = defaultdict(set)
    for l in lines:
        need[l.ref_id] += l.qty or 0
        jobs[l.ref_id].add(l.order_id)
    pids = list(need.keys())

    parts = {p.id: p for p in (await db.execute(select(Part).where(
        Part.id.in_(pids), Part.org_id == current.org_id))).scalars().all()}
    onhand: dict = defaultdict(float)
    for pid, q in (await db.execute(select(StockLevel.part_id, func.coalesce(func.sum(StockLevel.qty_available), 0))
                                    .where(StockLevel.part_id.in_(pids)).group_by(StockLevel.part_id))).all():
        onhand[pid] = float(q or 0)
    onorder: dict = defaultdict(float)
    # part_id is org-unique, so summing PO lines by part_id is already org-scoped.
    for pid, q in (await db.execute(
            select(POLine.part_id, func.coalesce(func.sum(POLine.qty_ordered - POLine.qty_received), 0))
            .where(POLine.part_id.in_(pids)).group_by(POLine.part_id))).all():
        onorder[pid] = max(0.0, float(q or 0))

    sup_ids = {p.default_supplier_id for p in parts.values() if p.default_supplier_id}
    sup_names = {s.id: s.name for s in (await db.execute(select(Supplier).where(
        Supplier.id.in_(sup_ids)))).scalars().all()} if sup_ids else {}

    now = now_utc()
    items = []
    for pid, qty_need in need.items():
        p = parts.get(pid)
        if not p:
            continue
        short = round(qty_need - onhand.get(pid, 0) - onorder.get(pid, 0), 3)
        if short <= 0:
            continue
        job_orders = [ometa[oid] for oid in jobs[pid] if oid in ometa]
        stalled = 0
        for o in job_orders:
            ref = o.status_entered_at or o.created_at
            if ref:
                stalled = max(stalled, (now - ref).days)
        items.append({
            "part_id": pid, "name": p.name, "sku": p.sku,
            "needed": round(qty_need, 3), "on_hand": onhand.get(pid, 0), "on_order": onorder.get(pid, 0),
            "shortfall": short, "unit_cost": float(p.avg_cost or p.cost_price or 0),
            "supplier_id": p.default_supplier_id, "supplier_name": sup_names.get(p.default_supplier_id) or "Unassigned",
            "jobs": sorted(o.order_number for o in job_orders), "stalled_days": stalled,
        })
    items.sort(key=lambda x: x["shortfall"], reverse=True)
    waiting = len({oid for s in jobs.values() for oid in s})
    return {"items": items, "summary": {
        "parts": len(items), "jobs_waiting": waiting,
        "shortfall_units": round(sum(i["shortfall"] for i in items), 3)}}


@router.get("/parts/{part_id}/movements")
async def part_movements(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Full stock-movement history for the product's History tab — every posting, sale,
    transfer, write-off and adjustment with quantity, location and reference."""
    # stock_movements carries no org_id: prove the part is this org's before reading its history
    if not (await db.execute(select(Part.id).where(Part.id == part_id, Part.org_id == current.org_id))).scalar_one_or_none():
        raise HTTPException(404, "Part not found")
    result = await db.execute(
        select(StockMovement)
        .where(StockMovement.part_id == part_id)
        .order_by(StockMovement.created_at.desc())
        .limit(200)
    )
    movements = result.scalars().all()
    # resolve location, bin and people names in one pass each
    from backend.models import Employee as _Emp
    from backend.models import Location, StorageBin
    loc_ids = {m.location_id for m in movements if getattr(m, "location_id", None)}
    bin_ids = {m.bin_id for m in movements if m.bin_id}
    emp_ids = {m.created_by_id for m in movements if m.created_by_id}
    locs = dict((await db.execute(select(Location.id, Location.name).where(
        Location.id.in_(loc_ids), Location.org_id == current.org_id))).all()) if loc_ids else {}
    bins = dict((await db.execute(select(StorageBin.id, StorageBin.name).where(
        StorageBin.id.in_(bin_ids), StorageBin.org_id == current.org_id))).all()) if bin_ids else {}
    emps = dict((await db.execute(select(_Emp.id, _Emp.name).where(
        _Emp.id.in_(emp_ids), _Emp.org_id == current.org_id))).all()) if emp_ids else {}
    show_cost = await can_see_costs(db, current)
    return [{"type": m.movement_type, "qty": m.qty,
             "at": m.created_at.isoformat(), "ref_type": m.ref_type,
             "ref_id": getattr(m, "ref_id", None),
             "location": locs.get(getattr(m, "location_id", None)),
             "bin_id": m.bin_id, "bin": bins.get(m.bin_id),           # ii144c
             "created_by": emps.get(m.created_by_id),
             "unit_cost": float(getattr(m, "unit_cost", 0) or 0) if show_cost else None} for m in movements]


@router.get("/parts/{part_id}/batches")
async def part_batches(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 130 — the product's Batches tab (Orderry-style): each cost/stock layer with
    the document + supplier it came from, price, received/remaining qty, amount and
    expiry. `in_stock` = qty still remaining; used by the All / In stock / Out of stock
    filter on the client."""
    from backend.models import Location, PurchaseOrder, StockItemBatch, Supplier, Warehouse
    rows = (await db.execute(
        select(StockItemBatch).where(
            StockItemBatch.org_id == current.org_id, StockItemBatch.part_id == part_id)
        .order_by(StockItemBatch.received_at.desc()).limit(300))).scalars().all()
    po_ids = {r.po_id for r in rows if r.po_id}
    loc_ids = {r.location_id for r in rows if r.location_id}
    wh_ids = {r.warehouse_id for r in rows if r.warehouse_id}
    pos, sup_names, locs, whs = {}, {}, {}, {}
    if po_ids:
        for p in (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))).scalars().all():
            pos[p.id] = p
        sup_ids = {p.supplier_id for p in pos.values() if p.supplier_id}
        if sup_ids:
            for s in (await db.execute(select(Supplier).where(Supplier.id.in_(sup_ids)))).scalars().all():
                sup_names[s.id] = s.name
    if loc_ids:
        for loc in (await db.execute(select(Location).where(Location.id.in_(loc_ids)))).scalars().all():
            locs[loc.id] = loc.name
    if wh_ids:
        for w in (await db.execute(select(Warehouse).where(Warehouse.id.in_(wh_ids)))).scalars().all():
            whs[w.id] = w.name
    out = []
    for r in rows:
        po = pos.get(r.po_id)
        cost = float(r.unit_cost or 0)
        remaining = float(r.qty_remaining or 0)
        received = float(r.qty_received or 0)
        out.append({
            "id": r.id,
            "document": (po.po_number if po else None),
            "supplier": (sup_names.get(po.supplier_id) if po else None),
            "location": locs.get(r.location_id),
            "warehouse": whs.get(r.warehouse_id),
            "unit_cost": cost,
            "qty_received": received,
            "qty_remaining": remaining,
            "amount": q2(cost * remaining),
            "expiry_date": r.expiry_date.isoformat() if r.expiry_date else None,
            "lot": r.lot_number,
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "in_stock": remaining > 0,
        })
    return {"items": out}


@router.get("/parts/{part_id}/stock")
async def part_stock(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Per-warehouse stock levels for the product's Stock tab. ii144c: one row per bin (and the
    location's unbinned stock), so the card shows what each bin holds."""
    part = (await db.execute(select(Part).where(
        Part.id == part_id, Part.org_id == current.org_id)
        .options(selectinload(Part.stock_levels)))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Product not found")
    from backend.models import Location, StorageBin
    unit_cost = float(part.avg_cost or part.cost_price or 0)   # valuation basis
    loc_ids = {sl.location_id for sl in part.stock_levels}
    bin_ids = {sl.bin_id for sl in part.stock_levels if sl.bin_id}
    locs = dict((await db.execute(select(Location.id, Location.name).where(
        Location.id.in_(loc_ids), Location.org_id == current.org_id))).all()) if loc_ids else {}
    bins = dict((await db.execute(select(StorageBin.id, StorageBin.name).where(
        StorageBin.id.in_(bin_ids), StorageBin.org_id == current.org_id))).all()) if bin_ids else {}
    rows = []
    for sl in sorted(part.stock_levels, key=lambda r: (locs.get(r.location_id) or "", r.bin_id is None,
                                                       bins.get(r.bin_id) or "")):
        avail = sl.qty_available or 0
        rows.append({"location": locs.get(sl.location_id) or "—", "location_id": sl.location_id,
                     "warehouse_id": sl.warehouse_id, "bin_id": sl.bin_id, "bin": bins.get(sl.bin_id),
                     "available": avail, "reserved": sl.qty_reserved or 0,
                     "value": q2(avail * unit_cost)})   # 144jv: Amount column (Orderry parity)
    total = sum(r["available"] for r in rows)
    out = {"total_available": total,
           "total_reserved": sum(r["reserved"] for r in rows),
           "total_value": q2(sum(r["value"] for r in rows)),
           "unit_cost": q2(unit_cost), "items": rows}
    if not await can_see_costs(db, current):
        out["total_value"] = out["unit_cost"] = None
        for r in rows:
            r["value"] = None
    return out


# ════════ Phase B.6 — Reservations & Purchase Orders ════════
from datetime import datetime, timedelta

from backend.models import (
    Organisation,
    POLine,
    PurchaseOrder,
    StockReservation,
    Supplier,
)
from backend.services.money import q2, q4  # noqa: E402  (mid-file import block, Phase-B section)

# Build 144al — default charge types a shop can add to a PO (customizable per org).
DEFAULT_PO_CHARGE_TYPES = ["Shipping", "In-transit"]


async def _release_expired(db: AsyncSession, org_id: str):
    """Release reservations whose hold period has elapsed (Orderry auto-release)."""
    now = now_utc()
    rows = (await db.execute(
        select(StockReservation).where(
            StockReservation.org_id == org_id,
            StockReservation.released == False,  # noqa: E712
            StockReservation.expires_at != None,  # noqa: E711
            StockReservation.expires_at < now,
        )
    )).scalars().all()
    for r in rows:
        await stock_rows.reserve(db, r.part_id, r.location_id, -r.qty)
        r.released = True
    if rows:
        await db.commit()


class ReserveIn(BaseModel):
    part_id: str
    location_id: str
    qty: float
    order_id: Optional[str] = None
    hold_days: int = 7


@router.post("/reserve", status_code=201)
async def reserve_stock(
    data: ReserveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_reserve_stock")),
):
    """Reserve stock for an order. Reserved qty is excluded from available."""
    from backend.models import Location
    part = (await db.execute(select(Part.id).where(
        Part.id == data.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    loc = (await db.execute(select(Location.id).where(
        Location.id == data.location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not part or not loc:      # checked first: another shop's stock levels never show
        raise HTTPException(404, "Product not found")
    avail, held = await stock_rows.totals(db, data.part_id, data.location_id)
    free = avail - held
    if data.qty > free:
        raise HTTPException(409, f"Only {free:g} available to reserve")
    if avail <= 0:
        raise HTTPException(404, "No stock at this location")
    await stock_rows.reserve(db, data.part_id, data.location_id, data.qty)
    r = StockReservation(org_id=current.org_id, part_id=data.part_id, location_id=data.location_id,
                         order_id=data.order_id, qty=data.qty,
                         expires_at=now_utc() + timedelta(days=data.hold_days))
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"ok": True, "reservation_id": r.id, "reserved": data.qty, "expires_at": r.expires_at.isoformat()}


@router.post("/reservations/{res_id}/release")
async def release_reservation(
    res_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_reserve_stock")),
):
    r = (await db.execute(select(StockReservation).where(
        StockReservation.id == res_id, StockReservation.org_id == current.org_id))).scalar_one_or_none()
    if not r or r.released:
        raise HTTPException(404, "Reservation not found or already released")
    await stock_rows.reserve(db, r.part_id, r.location_id, -r.qty)
    r.released = True
    await db.commit()
    return {"ok": True}


@router.get("/reservations")
async def list_reservations(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _release_expired(db, current.org_id)
    rows = (await db.execute(select(StockReservation).where(
        StockReservation.org_id == current.org_id, StockReservation.released == False  # noqa: E712
    ))).scalars().all()
    parts = await names_by_id(db, Part.id, Part.name, [r.part_id for r in rows],
                              org_col=Part.org_id, org_id=current.org_id)
    return {"items": [{"id": r.id, "part_id": r.part_id, "part": parts.get(r.part_id), "qty": r.qty,
                       "order_id": r.order_id, "expires_at": r.expires_at.isoformat() if r.expires_at else None}
                      for r in rows]}


# ── Purchase orders ──────────────────────────────────

class POLineIn(BaseModel):
    part_id: str
    qty_ordered: float
    unit_cost: float = 0.0

class POIn(BaseModel):
    supplier_id: str
    location_id: str
    on_credit: bool = False
    expected_at: Optional[str] = None
    draft: bool = False        # start in Draft (editable, not yet placed) vs Ordered
    lines: list[POLineIn] = []


def _po_charges(po: PurchaseOrder) -> list:
    return [c for c in (po.charges or []) if isinstance(c, dict)]


def _po_charges_total(po: PurchaseOrder) -> float:
    return q2(sum(float(c.get("amount") or 0) for c in _po_charges(po)))


# ── 144ar — landed-cost allocation basis ────────────────────────────────────
# How a lump of charges (freight/customs) is spread across PO lines when capitalised
# into unit cost. "value" (cost-weighted, the default and what everyone expects for
# customs), "qty" (per unit — right for a flat per-piece handling fee), "weight" (per kg,
# using Part.weight — right for freight, which is priced by weight). None of
# Orderry/Jobber/ShopMonkey let you choose; real ERPs do.
_ALLOC_BASES = ("value", "qty", "weight")


def _line_alloc_base(pl, qty: float, basis: str, weights: dict) -> float:
    """The weight this line carries when splitting charges, for a given quantity."""
    if basis == "qty":
        return qty
    if basis == "weight":
        return qty * float(weights.get(pl.part_id) or 0)
    return qty * (pl.unit_cost or 0)  # value


def _freight_per_unit(po_lines, charges_total: float, basis: str, weights: dict,
                      qty_of=lambda pl: (pl.qty_ordered or 0)) -> dict:
    """Freight to add per received unit, keyed by po_line id, spread by `basis`.

    Falls back value → qty when the chosen basis yields nothing (e.g. weight basis but no
    product weights recorded), so a mis-set basis never silently drops freight."""
    def _bases(b):
        return {pl.id: _line_alloc_base(pl, qty_of(pl), b, weights) for pl in po_lines}
    bases = _bases(basis)
    total = sum(bases.values())
    if total <= 0 and basis != "value":
        bases = _bases("value"); total = sum(bases.values())
    if total <= 0:
        bases = _bases("qty"); total = sum(bases.values())
    if total <= 0:
        return {pl.id: 0.0 for pl in po_lines}
    return {pl.id: charges_total * (bases[pl.id] / total) / (qty_of(pl) or 1) for pl in po_lines}


async def _po_part_weights(db, po_lines) -> dict:
    """{part_id: weight_kg} for the parts on these lines (0 when unset)."""
    ids = list({pl.part_id for pl in po_lines})
    if not ids:
        return {}
    rows = (await db.execute(select(Part.id, Part.weight).where(Part.id.in_(ids)))).all()
    return {pid: (w or 0) for pid, w in rows}


async def _resolve_received_workflow(db, org_id: str) -> Optional[str]:
    """The org's 'Received' purchase-order workflow key, if the pipeline has one — used to
    auto-advance the manual stage on a full receipt. Returns None (leave unchanged) when the
    org has renamed/removed that stage, so we never invent a key the pipeline doesn't define."""
    from backend.models import OrderStatus
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, org_id)
    return (await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "purchase_order",
        OrderStatus.active == True, OrderStatus.key == "po_received"))).scalars().first()  # noqa: E712


def _ser_po(po: PurchaseOrder, lines, part_names, supplier=None) -> dict:
    subtotal = q2(sum((l.qty_ordered or 0) * (l.unit_cost or 0) for l in lines))
    charges_total = _po_charges_total(po)
    d = {
        "id": po.id, "po_number": po.po_number, "status": po.status,
        # 144an — manual, configurable workflow stage (Orderry parity). Separate from the
        # auto receipt state (`status`). FE resolves name/colour from the purchase_order pipeline.
        "workflow_status": po.workflow_status,
        "supplier_id": po.supplier_id, "location_id": po.location_id,
        "on_credit": bool(po.on_credit),
        "supplier_invoice_no": po.supplier_invoice_no,
        "supplier_invoice_date": po.supplier_invoice_date.isoformat() if po.supplier_invoice_date else None,
        "notes": po.notes,
        "charges": _po_charges(po), "charges_to_cost": bool(po.charges_to_cost),
        "alloc_basis": po.alloc_basis or "value",           # 144ar
        "freight_capitalised": q2(po.freight_capitalised or 0),
        "shipment_freight": q2(po.shipment_freight or 0),
        "expected_at": po.expected_at.isoformat() if po.expected_at else None,
        "ordered_at": po.ordered_at.isoformat() if po.ordered_at else None,
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "subtotal": subtotal, "charges_total": charges_total,
        "total": q2(subtotal + charges_total),
        "supplier_invoice_total": po.supplier_invoice_total,
        "supplier_invoice_file": ({"id": po.supplier_invoice_blob_id, "url": f"/api/media/{po.supplier_invoice_blob_id}",
                                   "name": po.supplier_invoice_name} if po.supplier_invoice_blob_id else None),
        "match": _po_match(po, lines),
        # a PO can be edited until it is fully received or cancelled
        "editable": po.status in ("draft", "ordered", "partial"),
        "lines": [{"id": l.id, "part_id": l.part_id, "part": part_names.get(l.part_id),
                   "qty_ordered": l.qty_ordered, "qty_received": l.qty_received,
                   "unit_cost": l.unit_cost} for l in lines],
    }
    if supplier is not None:
        d["supplier"] = {"id": supplier.id, "name": supplier.name,
                         "contact_name": supplier.contact_name, "phone": supplier.phone,
                         "email": supplier.email, "balance": supplier.balance}
    return d


@router.get("/po")
async def list_pos(
    location_id: Optional[str] = None,        # build 141: scope POs to a branch
    skip: int = _paging.skip_query(), limit: int = _paging.limit_query(100),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    _pq = (select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
           .where(Supplier.org_id == current.org_id))
    if location_id:
        _pq = _pq.where(PurchaseOrder.location_id == location_id)
    total = (await db.execute(select(func.count()).select_from(_pq.subquery()))).scalar() or 0
    pos = (await db.execute(_pq.order_by(PurchaseOrder.created_at.desc())
                            .offset(skip).limit(_paging.clamp(limit)))).scalars().all()
    # One query for every page's lines and only the parts they name (was a query per PO plus
    # every part in the shop).
    lines_by_po: dict = {}
    if pos:
        for ln in (await db.execute(select(POLine).where(POLine.po_id.in_([p.id for p in pos])))).scalars().all():
            lines_by_po.setdefault(ln.po_id, []).append(ln)
    part_ids = {ln.part_id for lns in lines_by_po.values() for ln in lns if ln.part_id}
    part_names = {p.id: p.name for p in (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.id.in_(part_ids)))).scalars().all()} if part_ids else {}
    return {"items": [_ser_po(po, lines_by_po.get(po.id, []), part_names) for po in pos], "total": total}


@router.post("/po", status_code=201)
async def create_po(
    data: POIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    # Build 144aa (QA hardening): only accept line items for this org's own parts,
    # so a crafted request can't attach another tenant's part_id to a PO.
    req_ids = {l.part_id for l in data.lines}
    owned = set((await db.execute(select(Part.id).where(
        Part.id.in_(req_ids), Part.org_id == current.org_id))).scalars().all()) if req_ids else set()
    bad = req_ids - owned
    if bad:
        raise HTTPException(422, "One or more products don't belong to your company")
    sup = (await db.execute(select(Supplier).where(
        Supplier.id == data.supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not sup:
        raise HTTPException(404, "Supplier not found")
    total = sum(l.qty_ordered * l.unit_cost for l in data.lines)
    po = PurchaseOrder(supplier_id=data.supplier_id, location_id=data.location_id,
                       org_id=current.org_id, po_number=await next_number(db, current.org_id, "po"),
                       status="draft" if data.draft else "ordered", total=total, on_credit=data.on_credit,
                       # 144an — manual workflow stage starts at Draft or Ordered to mirror the receipt state.
                       workflow_status="po_draft" if data.draft else "po_ordered",
                       expected_at=datetime.fromisoformat(data.expected_at) if data.expected_at else None,
                       ordered_at=None if data.draft else now_utc())
    db.add(po)
    await db.flush()
    for l in data.lines:
        db.add(POLine(po_id=po.id, part_id=l.part_id, qty_ordered=l.qty_ordered, unit_cost=l.unit_cost))
    await db.commit()
    await db.refresh(po)
    return {"ok": True, "po_id": po.id, "po_number": po.po_number}


async def _load_po_scoped(db, org_id, po_id):
    """Fetch a PO that belongs to this org (via its supplier), or None."""
    po = (await db.execute(select(PurchaseOrder)
          .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
          .where(PurchaseOrder.id == po_id, Supplier.org_id == org_id))).scalar_one_or_none()
    return po


# ── PO state machine (Build 144aac) ──────────────────────────────────────────
# One place that says which status can follow which. "received"/"cancelled"/"grouped"
# are terminal. Receipt drives ordered/draft → partial → received automatically; the
# only manual transitions are send (→ordered) and cancel.
PO_TERMINAL = {"received", "cancelled", "canceled", "grouped"}
PO_TRANSITIONS = {
    "draft": {"ordered", "partial", "received", "cancelled"},
    "ordered": {"partial", "received", "cancelled"},
    "partial": {"received", "cancelled"},
}


def _guard_po_transition(current_status: str, target: str) -> None:
    cur = (current_status or "draft").lower()
    if cur in PO_TERMINAL:
        raise HTTPException(409, f"This purchase order is {cur} and can't change")
    if target != cur and target not in PO_TRANSITIONS.get(cur, set()):
        raise HTTPException(409, f"Can't move a {cur} purchase order to {target}")


def _po_match(po: PurchaseOrder, lines) -> dict:
    """Three-way match: ordered vs received vs supplier-invoiced. `status` is one of
    pending (nothing received), unbilled (received, no invoice total yet), matched, or
    variance (a qty short/over or a price gap beyond tolerance)."""
    ordered_qty = round(sum(l.qty_ordered or 0 for l in lines), 3)
    received_qty = round(sum(l.qty_received or 0 for l in lines), 3)
    expected_cost = q2(po.received_cost or 0)          # landed cost of what arrived
    invoiced = po.supplier_invoice_total
    qty_variance = round(received_qty - ordered_qty, 3)      # <0 short, >0 over
    price_variance = q2((invoiced or 0) - expected_cost) if invoiced is not None else None
    tol = max(1.0, expected_cost * 0.005)                    # ₹1 or 0.5% of expected
    if received_qty <= 0:
        status = "pending"
    elif invoiced is None:
        status = "unbilled"
    elif qty_variance != 0 or (price_variance is not None and abs(price_variance) > tol):
        status = "variance"
    else:
        status = "matched"
    return {"status": status, "ordered_qty": ordered_qty, "received_qty": received_qty,
            "qty_variance": qty_variance, "expected_cost": expected_cost,
            "invoiced_total": invoiced, "price_variance": price_variance}


@router.get("/po/{po_id}")
async def get_po(
    po_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144al — full, editable PO detail: supplier block, our + supplier document
    numbers/dates, line items, and the optional charges breakdown."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
    supplier = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    part_names = await names_by_id(db, Part.id, Part.name, [l.part_id for l in lines],
                                   org_col=Part.org_id, org_id=current.org_id)
    out = _ser_po(po, lines, part_names, supplier=supplier)
    # 16b: receiving asks for a serial per unit on serial-tracked lines
    serialized = set((await db.execute(select(Part.id).where(
        Part.org_id == current.org_id, Part.id.in_({l.part_id for l in lines}),
        Part.serialized == True))).scalars().all()) if lines else set()  # noqa: E712
    for ln in out["lines"]:
        ln["serialized"] = ln["part_id"] in serialized
    return out


@router.get("/po/{po_id}/receipts")
async def po_receipts(
    po_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Receipt timeline — every 'receive' stock movement booked against this PO, so a
    partial-delivery PO shows each drop (when, what, how many, at what unit cost, by whom)."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    rows = (await db.execute(select(StockMovement).where(
        StockMovement.ref_type == "po", StockMovement.ref_id == po_id)
        .order_by(StockMovement.created_at.desc()))).scalars().all()
    part_names = await names_by_id(db, Part.id, Part.name, [m.part_id for m in rows],
                                   org_col=Part.org_id, org_id=current.org_id)
    emp_names = {e.id: e.name for e in (await db.execute(
        select(Employee).where(Employee.org_id == current.org_id))).scalars().all()}
    return {"items": [{"id": m.id, "part": part_names.get(m.part_id, m.part_id), "qty": m.qty,
                       "unit_cost": m.unit_cost, "by": emp_names.get(m.created_by_id) if m.created_by_id else None,
                       "at": m.created_at.isoformat()} for m in rows]}


class POChargeIn(BaseModel):
    label: str
    amount: float = 0.0


class POEditLineIn(BaseModel):
    id: Optional[str] = None          # existing line to update; omit to add a new one
    part_id: str
    qty_ordered: float
    unit_cost: float = 0.0


class POEditIn(BaseModel):
    supplier_invoice_no: Optional[str] = None
    supplier_invoice_date: Optional[str] = None
    expected_at: Optional[str] = None
    notes: Optional[str] = None
    on_credit: Optional[bool] = None
    charges: Optional[list[POChargeIn]] = None
    charges_to_cost: Optional[bool] = None
    alloc_basis: Optional[str] = None          # 144ar — value | qty | weight
    lines: Optional[list[POEditLineIn]] = None


@router.patch("/po/{po_id}")
async def edit_po(
    po_id: str,
    data: POEditIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Build 144al — edit a purchase order's header, line items and charges while it is
    still open (draft/ordered/partial). Recomputes the total (lines + charges). Lines that
    already have received quantity are never deleted, so the receiving record stays intact."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    if po.status in ("received", "cancelled", "canceled"):
        raise HTTPException(409, "This purchase order is closed and can no longer be edited")

    sent = data.model_fields_set
    from datetime import date as _date
    if "supplier_invoice_no" in sent:
        po.supplier_invoice_no = (data.supplier_invoice_no or None)
    if "supplier_invoice_date" in sent:
        po.supplier_invoice_date = _date.fromisoformat(data.supplier_invoice_date) if data.supplier_invoice_date else None
    if "expected_at" in sent:
        _was = po.expected_at
        po.expected_at = datetime.fromisoformat(data.expected_at) if data.expected_at else None
        if po.expected_at != _was:
            from backend.services.po_links import notify_po_change
            await notify_po_change(db, current.org_id, po,
                                   f"now expected {po.expected_at:%b %-d}" if po.expected_at else "expected date cleared",
                                   actor_id=current.id)
    if "notes" in sent:
        po.notes = data.notes or None
    if data.on_credit is not None:
        po.on_credit = data.on_credit
    if data.charges_to_cost is not None:
        po.charges_to_cost = data.charges_to_cost
    if data.alloc_basis is not None:
        po.alloc_basis = data.alloc_basis if data.alloc_basis in _ALLOC_BASES else "value"
    if "charges" in sent:
        po.charges = [{"label": (c.label or "Charge").strip(), "amount": q2(float(c.amount or 0))}
                      for c in (data.charges or []) if (c.amount or 0) or (c.label or "").strip()]

    # ── line edits ──
    if data.lines is not None:
        existing = {l.id: l for l in (await db.execute(
            select(POLine).where(POLine.po_id == po.id))).scalars().all()}
        # validate every referenced part belongs to this org
        req_parts = {ln.part_id for ln in data.lines}
        owned = set((await db.execute(select(Part.id).where(
            Part.id.in_(req_parts), Part.org_id == current.org_id))).scalars().all()) if req_parts else set()
        if req_parts - owned:
            raise HTTPException(422, "One or more products don't belong to your company")
        keep_ids = set()
        for ln in data.lines:
            row = existing.get(ln.id) if ln.id else None
            if row:
                row.part_id = ln.part_id
                row.qty_ordered = ln.qty_ordered
                row.unit_cost = ln.unit_cost
                keep_ids.add(row.id)
            else:
                new = POLine(po_id=po.id, part_id=ln.part_id,
                             qty_ordered=ln.qty_ordered, unit_cost=ln.unit_cost)
                db.add(new)
        # delete removed lines, but never one that already has received stock
        for lid, row in existing.items():
            if lid not in keep_ids:
                if (row.qty_received or 0) > 0:
                    raise HTTPException(409, "Can't remove a line that has already been received")
                await db.delete(row)
        await db.flush()

    # recompute total = line subtotal + charges
    lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
    subtotal = sum((l.qty_ordered or 0) * (l.unit_cost or 0) for l in lines)
    po.total = q2(subtotal + _po_charges_total(po))
    await db.commit()

    supplier = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
    part_names = await names_by_id(db, Part.id, Part.name, [l.part_id for l in lines],
                                   org_col=Part.org_id, org_id=current.org_id)
    return _ser_po(po, lines, part_names, supplier=supplier)


@router.get("/po-charge-types")
async def get_po_charge_types(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The customizable list of charge labels a buyer can add to a PO."""
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    types = (org.po_charge_types if org and org.po_charge_types else None) or DEFAULT_PO_CHARGE_TYPES
    return {"types": types}


class POChargeTypesIn(BaseModel):
    types: list[str] = []


@router.put("/po-charge-types")
async def set_po_charge_types(
    data: POChargeTypesIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organisation not found")
    cleaned = []
    for t in data.types:
        t = (t or "").strip()
        if t and t not in cleaned:
            cleaned.append(t)
    org.po_charge_types = cleaned or DEFAULT_PO_CHARGE_TYPES
    await db.commit()
    return {"types": org.po_charge_types}


class GroupPOIn(BaseModel):
    po_ids: list[str]


@router.post("/po/group", status_code=201)
async def group_pos(
    data: GroupPOIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Build 132 — merge several open POs for the SAME supplier into one (Orderry group).
    Lines for the same product are summed; the source POs are marked 'grouped'."""
    ids = list(dict.fromkeys(data.po_ids))
    if len(ids) < 2:
        raise HTTPException(400, "Select at least two purchase orders to group")
    pos = (await db.execute(select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
           .where(PurchaseOrder.id.in_(ids), Supplier.org_id == current.org_id))).scalars().all()
    if len(pos) != len(ids):
        raise HTTPException(404, "One or more purchase orders not found")
    if len({p.supplier_id for p in pos}) != 1:
        raise HTTPException(422, "All purchase orders must be from the same supplier")
    if any(p.status in ("received", "partial", "cancelled", "grouped") for p in pos):
        raise HTTPException(409, "Only fully-open purchase orders can be grouped")
    merged: dict = {}
    for p in pos:
        for ln in (await db.execute(select(POLine).where(POLine.po_id == p.id))).scalars().all():
            if ln.part_id in merged:
                merged[ln.part_id][0] += ln.qty_ordered
            else:
                merged[ln.part_id] = [ln.qty_ordered, ln.unit_cost]
    total = sum(q * c for q, c in merged.values())
    newpo = PurchaseOrder(supplier_id=pos[0].supplier_id, location_id=pos[0].location_id,
                          org_id=current.org_id, po_number=await next_number(db, current.org_id, "po"),
                          status="ordered", total=total, ordered_at=now_utc())
    db.add(newpo)
    await db.flush()
    for pid, (q, c) in merged.items():
        db.add(POLine(po_id=newpo.id, part_id=pid, qty_ordered=q, unit_cost=c))
    for p in pos:
        p.status = "grouped"
    await db.commit()
    await db.refresh(newpo)
    return {"ok": True, "po_id": newpo.id, "po_number": newpo.po_number, "grouped": len(pos)}


class SendPOIn(BaseModel):
    channel: str = "email"          # email|sms|whatsapp


@router.post("/po/{po_id}/send")
async def send_po(
    po_id: str,
    data: SendPOIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_purchase_orders")),
):
    """Build 132 — send a PO to the supplier over email / SMS / WhatsApp. Delivery is
    stubbed at the provider boundary (like every other channel); the message is logged to
    the supplier's contact thread so there's a record it went out."""
    if data.channel not in ("email", "sms", "whatsapp"):
        raise HTTPException(422, "channel must be email|sms|whatsapp")
    po = (await db.execute(select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
          .where(PurchaseOrder.id == po_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    # Build 144ah — spend-approval gate: a PO over the org threshold can only be sent by a
    # manager (a role NOT denied can_manage_settings). Deny-wins, consistent with the rest of RBAC.
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    thr = (org.po_approval_threshold if org else None) or 0
    if thr and float(po.total or 0) > thr and current.role_id:
        from backend.models import Role
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        from backend.services.permissions import role_allows as _ra
        if role is None or not _ra(role, "can_manage_settings"):
            raise HTTPException(403, f"PO total {float(po.total or 0):g} is over the approval limit "
                                     f"of {thr:g} — a manager must send it.")
    sup = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    nlines = (await db.execute(select(func.count()).select_from(POLine).where(POLine.po_id == po.id))).scalar() or 0
    body = f"Purchase order {po.po_number}: {nlines} item(s), total {float(po.total or 0):.2f}."
    logged = False
    if sup and sup.client_id:
        from backend.models import InboxMessage, InboxThread
        thread = (await db.execute(select(InboxThread).where(
            InboxThread.org_id == current.org_id, InboxThread.client_id == sup.client_id,
            InboxThread.channel == data.channel)
            .order_by(InboxThread.last_message_at.desc()))).scalars().first()
        if not thread:
            thread = InboxThread(org_id=current.org_id, channel=data.channel, client_id=sup.client_id,
                                 handle=(sup.email if data.channel == "email" else sup.phone),
                                 status="open", unread=False, last_message_at=now_utc())
            db.add(thread)
            await db.flush()
        db.add(InboxMessage(org_id=current.org_id, thread_id=thread.id, direction="outbound",
                            body=body, author_id=current.id, delivered=True))
        thread.last_message_at = now_utc()
        logged = True
    from backend.services.po_links import notify_po_change
    await notify_po_change(db, current.org_id, po, "sent to the supplier", actor_id=current.id)
    await db.commit()
    return {"ok": True, "channel": data.channel, "po_number": po.po_number, "logged": logged}


@router.post("/po/{po_id}/cancel")
async def cancel_po(
    po_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Cancel a PO. The state machine forbids cancelling one that's already received,
    cancelled or grouped — those are terminal (a received PO moved real stock)."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    _guard_po_transition(po.status, "cancelled")
    po.status = "cancelled"
    po.workflow_status = "po_cancelled"   # keep the manual stage in step with a cancel
    from backend.services.po_links import notify_po_change
    await notify_po_change(db, current.org_id, po, "cancelled, so these parts aren't coming", actor_id=current.id)
    await db.commit()
    return {"ok": True, "status": po.status}


class POStatusIn(BaseModel):
    status: str   # draft | ordered  (receipt statuses partial/received are receipt-driven only)


@router.post("/po/{po_id}/status")
async def set_po_status(
    po_id: str,
    data: POStatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Manual status trigger for the pre-receipt lifecycle — Draft ⇄ Ordered — mirroring
    ShopMonkey/ServiceTitan: a Draft is editable and not yet placed; marking it Ordered
    opens receiving. Receipt statuses (partial/received) are never set here — they are
    driven only by actually receiving stock in `receive_po`, so a PO can never read
    "received" without the goods (and their cost layers) having landed. Cancel has its own
    endpoint. Un-ordering back to Draft is allowed only while nothing has been received."""
    target = (data.status or "").lower().strip()
    if target not in ("draft", "ordered"):
        raise HTTPException(422, "Status can only be set to draft or ordered here")
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    cur = (po.status or "draft").lower()
    if cur in PO_TERMINAL:
        raise HTTPException(409, f"This purchase order is {cur} and can't change")
    if target == cur:
        return {"ok": True, "status": po.status}
    lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
    received_any = any((l.qty_received or 0) > 0 for l in lines)
    if target == "draft":
        if cur != "ordered" or received_any:
            raise HTTPException(409, "Only an ordered PO with nothing received can return to draft")
        po.status = "draft"
        po.ordered_at = None
    else:  # ordered
        if cur != "draft":
            raise HTTPException(409, f"Can't move a {cur} purchase order to ordered")
        po.status = "ordered"
        po.ordered_at = po.ordered_at or now_utc()
    from backend.services.po_links import notify_po_change
    await notify_po_change(db, current.org_id, po, "marked ordered" if po.status == "ordered" else f"back to {po.status}",
                           actor_id=current.id)
    await db.commit()
    return {"ok": True, "status": po.status}


class POWorkflowIn(BaseModel):
    workflow_status: str   # an OrderStatus.key in the org's 'purchase_order' pipeline


@router.post("/po/{po_id}/workflow")
async def set_po_workflow(
    po_id: str,
    data: POWorkflowIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """144an — set the PO's manual, configurable WORKFLOW stage (Orderry parity). Any active
    status in the org's 'purchase_order' pipeline is valid; the stage is a human-driven label
    and never moves stock — the receipt state (`status`) is the only thing receiving touches,
    so a workflow label can never fake what was actually received."""
    from backend.models import OrderStatus
    from backend.routers.statuses import ensure_seeded
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    await ensure_seeded(db, current.org_id)
    valid = (await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == current.org_id, OrderStatus.record_type == "purchase_order",
        OrderStatus.active == True, OrderStatus.key == data.workflow_status))).scalars().first()  # noqa: E712
    if not valid:
        raise HTTPException(422, "Unknown purchase-order status for this workspace")
    changed = po.workflow_status != data.workflow_status
    po.workflow_status = data.workflow_status
    if changed:
        label = (await db.execute(select(OrderStatus.name).where(
            OrderStatus.org_id == current.org_id, OrderStatus.record_type == "purchase_order",
            OrderStatus.key == data.workflow_status))).scalars().first() or data.workflow_status
        from backend.services.po_links import notify_po_change
        await notify_po_change(db, current.org_id, po, f"stage is now {label}", actor_id=current.id)
    await db.commit()
    return {"ok": True, "workflow_status": po.workflow_status}


class PORecostIn(BaseModel):
    charges: Optional[list[POChargeIn]] = None    # the (late) freight bill, if adding it now
    charges_to_cost: Optional[bool] = None
    alloc_basis: Optional[str] = None             # 144ar — value | qty | weight


@router.post("/po/{po_id}/recost")
async def recost_po(
    po_id: str,
    data: PORecostIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """144ap — landed-cost recost. Freight/customs bills usually arrive AFTER the goods. Add or
    edit the PO's charges here (even on a fully-received PO) and the freight attributable to
    units already received is applied as a real cost adjustment to on-hand stock (moving-average
    + remaining FIFO layers) — only the *delta* beyond what was already capitalised, so calling
    it twice is safe. None of Orderry/Jobber/ShopMonkey recost received stock; real ERPs do."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    if (po.status or "").lower() in ("cancelled", "canceled"):
        raise HTTPException(409, "This purchase order is cancelled")
    if data.charges is not None:
        po.charges = [{"label": (c.label or "Charge").strip(), "amount": q2(float(c.amount or 0))}
                      for c in (data.charges or []) if (c.amount or 0) or (c.label or "").strip()]
    if data.charges_to_cost is not None:
        po.charges_to_cost = data.charges_to_cost
    if data.alloc_basis is not None:
        po.alloc_basis = data.alloc_basis if data.alloc_basis in _ALLOC_BASES else "value"

    po_lines = (await db.execute(select(POLine).where(POLine.po_id == po_id))).scalars().all()
    charges_total = _po_charges_total(po)
    if not (po.charges_to_cost and charges_total > 0):
        await db.commit()
        return {"ok": True, "applied": 0.0, "note": "No capitalised charges to recost"}

    # 144ar — spread by the PO's alloc_basis (value / qty / weight), matching how receive_po does it.
    basis = po.alloc_basis if po.alloc_basis in _ALLOC_BASES else "value"
    weights = await _po_part_weights(db, po_lines) if basis == "weight" else {}
    per_unit_map = _freight_per_unit(po_lines, charges_total, basis, weights)

    # Freight that *should* now sit in the units already received, at current charges.
    target = 0.0
    per_part_target: dict[str, float] = {}
    for pl in po_lines:
        recvd = pl.qty_received or 0
        if recvd <= 0:
            continue
        tline = per_unit_map.get(pl.id, 0.0) * recvd
        target += tline
        per_part_target[pl.part_id] = per_part_target.get(pl.part_id, 0.0) + tline

    delta_total = q2(target - (po.freight_capitalised or 0))
    if abs(delta_total) < 0.005:
        await db.commit()
        return {"ok": True, "applied": 0.0, "note": "Already up to date"}

    applied = 0.0
    scale = (delta_total / target) if target else 0
    for part_id, tpart in per_part_target.items():
        part = (await db.execute(select(Part).where(Part.id == part_id))).scalar_one_or_none()
        if not part:
            continue
        res = await stock_posting.apply_cost_adjustment(
            db, org_id=current.org_id, part=part, add_cost_total=q4(tpart * scale),
            po_id=po_id, created_by_id=current.id)
        applied += res["applied"]
    po.freight_capitalised = q2((po.freight_capitalised or 0) + applied)
    await db.commit()
    return {"ok": True, "applied": q2(applied), "delta_total": delta_total}


class POInvoiceIn(BaseModel):
    supplier_invoice_total: Optional[float] = None
    supplier_invoice_no: Optional[str] = None
    supplier_invoice_date: Optional[str] = None
    invoice_file: Optional[str] = None      # base64 / data URL of the supplier's PDF/image
    invoice_name: Optional[str] = None      # original filename


@router.post("/po/{po_id}/invoice")
async def set_po_invoice(
    po_id: str,
    data: POInvoiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Record the supplier's bill for three-way match. Allowed on a received PO too — the
    invoice usually lands after the goods — but never on a cancelled one."""
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "Purchase order not found")
    if (po.status or "").lower() in ("cancelled", "canceled", "grouped"):
        raise HTTPException(409, f"This purchase order is {po.status}")
    sent = data.model_fields_set
    if "supplier_invoice_total" in sent:
        po.supplier_invoice_total = data.supplier_invoice_total
    if "supplier_invoice_no" in sent:
        po.supplier_invoice_no = data.supplier_invoice_no or None
    if "supplier_invoice_date" in sent:
        from datetime import date as _date
        po.supplier_invoice_date = _date.fromisoformat(data.supplier_invoice_date) if data.supplier_invoice_date else None
    if data.invoice_file:
        # store the supplier's PDF/image via the shared media service → keep a blob pointer
        from backend.services.media import store as _store
        blob = await _store(db, current.org_id, data.invoice_file, name=data.invoice_name or "supplier-invoice")
        po.supplier_invoice_blob_id = blob.id
        po.supplier_invoice_name = data.invoice_name or "supplier-invoice"
    await db.commit()
    lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
    return {"ok": True, "match": _po_match(po, lines)}


class ReceiveLineIn(BaseModel):
    po_line_id: str
    qty: float
    bin_id: Optional[str] = None        # post-to-bin (Orderry: enter the matching bin per product)
    serials: Optional[list[SerialStr]] = Field(None, max_length=500)  # serialized intake: one serial per unit

class ReceiveIn(BaseModel):
    lines: list[ReceiveLineIn] = Field(default_factory=list, max_length=500)
    bin_id: Optional[str] = None  # default bin for all lines
    # 144ar — per-shipment freight: the freight/handling billed WITH this delivery. Spread across
    # only the units received in THIS call (by the PO's alloc_basis) and, when capitalised,
    # folded into their landed cost. Distinct from PO-level charges, which recost across the
    # whole order. Right for suppliers who invoice freight per drop.
    shipment_freight: Optional[float] = None
    shipment_freight_to_cost: bool = True   # False → treat as a flat expense (supplier balance only)


@router.post("/po/{po_id}/receive")
async def receive_po(
    po_id: str,
    data: ReceiveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Receive goods from a PO → auto-post to stock + StockMovement(receive)."""
    # 144bc security: scope to the caller's org via the supplier (prevents cross-tenant
    # PO receipt / IDOR). PurchaseOrder has no org_id column — ownership is the supplier's.
    po = await _load_po_scoped(db, current.org_id, po_id)
    if not po:
        raise HTTPException(404, "PO not found")
    if (po.status or "").lower() in PO_TERMINAL:
        raise HTTPException(409, f"This purchase order is {po.status} — nothing more to receive")
    if (po.status or "").lower() == "draft":
        # A draft hasn't been placed yet — receiving must follow "mark as ordered", so stock
        # can never land against an order that was never actually sent to the supplier.
        raise HTTPException(409, "Mark this purchase order as ordered before receiving")
    by_id = {r.po_line_id: r for r in data.lines}
    # Lock the PO's lines for this receive: two concurrent "receive the remainder" calls both read
    # the same qty_received and doubled stock and supplier AP. The loser now waits and sees 0 left.
    po_lines = (await db.execute(select(POLine).where(POLine.po_id == po_id)
                                 .with_for_update())).scalars().all()
    basis = po.alloc_basis if po.alloc_basis in _ALLOC_BASES else "value"
    weights = await _po_part_weights(db, po_lines) if basis == "weight" else {}
    # Build 144al — landed cost: when the PO opts in, spread its charges (shipping/transit/custom)
    # across each unit's cost so margins reflect the true acquisition price. 144ar: spread by the
    # PO's alloc_basis (value / qty / weight), not just value.
    po_charges_total = _po_charges_total(po)
    allocate_freight = bool(po.charges_to_cost) and po_charges_total > 0
    po_freight_pu = _freight_per_unit(po_lines, po_charges_total, basis, weights) if allocate_freight else {}

    # ── Pass 1: resolve how much of each line lands in THIS receive ─────────────────
    plan = []   # (pl, recv, part, rline)
    for pl in po_lines:
        rline = by_id.get(pl.id)
        left = (pl.qty_ordered or 0) - (pl.qty_received or 0)
        # No lines sent → receive the whole remainder; lines sent → only those lines move.
        # (An unlisted line used to receive its full remainder too.)
        recv = rline.qty if rline else (0 if data.lines else left)
        if recv <= 0:
            continue
        # B21: more than is still outstanding would post phantom stock and over-bill the
        # supplier balance. Extra units go on a new PO (or a stock posting) instead.
        if recv > left + 1e-9:
            name = (await db.execute(select(Part.name).where(Part.id == pl.part_id))).scalar_one_or_none() or "a line"
            raise HTTPException(409, f"Receiving {recv:g} of {name}, but only {max(left, 0):g} "
                                     f"{'is' if left == 1 else 'are'} still to come on this order")
        part = (await db.execute(select(Part).where(Part.id == pl.part_id))).scalar_one_or_none()
        if not part:
            continue
        plan.append((pl, recv, part, rline))

    # 16b: serial-tracked lines need one valid, unique serial per unit — checked for the whole
    # delivery before anything moves, so a bad scan never leaves half a receipt behind.
    from backend.services import serial_intake
    seen_serials: set = set()
    checked_serials = {}
    for pl, recv, part, rline in plan:
        checked_serials[pl.id] = await serial_intake.validate_intake(
            db, current.org_id, part, recv, rline.serials if rline else None, seen_serials)

    # ── 144ar per-shipment freight: spread this delivery's freight across only the units
    #    received now (by the same alloc_basis), folded into their landed cost when opted in. ──
    ship_total = q2(float(data.shipment_freight or 0))
    ship_capitalise = ship_total > 0 and data.shipment_freight_to_cost and bool(plan)
    ship_pu = (_freight_per_unit([pl for pl, _, _, _ in plan], ship_total, basis, weights,
                                 qty_of=lambda pl: next(r for p, r, _, _ in plan if p.id == pl.id))
               if ship_capitalise else {})

    received_any = False
    received_cost_now = 0.0
    freight_added_now = 0.0   # 144ap — PO-level freight capitalised into units received in this call
    ship_freight_now = 0.0    # 144ar — per-shipment freight capitalised in this call
    for pl, recv, part, rline in plan:
        line_bin = (rline.bin_id if rline and rline.bin_id else data.bin_id)
        pl.qty_received = (pl.qty_received or 0) + recv
        unit_cost = pl.unit_cost or part.cost_price or 0
        if allocate_freight:
            fpu = po_freight_pu.get(pl.id, 0.0)
            unit_cost = q4(unit_cost + fpu)
            freight_added_now += fpu * recv
        if ship_capitalise:
            spu = ship_pu.get(pl.id, 0.0)
            unit_cost = q4(unit_cost + spu)
            ship_freight_now += spu * recv
        # Single source of truth: global WAC + StockMovement + FIFO batch + serials.
        from backend.services import inventory_alerts as _alerts
        before_oh = await _alerts.global_on_hand(db, part.id)
        await stock_posting.apply_receipt_line(
            db, org_id=current.org_id, part=part, location_id=po.location_id,
            qty=recv, unit_cost=unit_cost, created_by_id=current.id,
            ref_type="po", ref_id=po_id, bin_id=line_bin, supplier_id=po.supplier_id,
            serials=checked_serials.get(pl.id) or None, create_batch=True)
        # 144bo: reset low-stock dedupe + notify any job that was waiting on this part.
        after_oh = await _alerts.global_on_hand(db, part.id)
        await _alerts.on_received(db, current.org_id, part, before_oh, after_oh)
        # ── Reserve-on-receive (P1-D): goods a work order was waiting on stay earmarked
        #    for it instead of landing as free stock. Orderry reserves incoming goods too;
        #    we go further by keeping the exact cost layer as well. ──
        from backend.services.po_links import apply_receipt_to_orders
        to_reserve = recv - await apply_receipt_to_orders(db, current.org_id, po, pl, recv)
        if pl.reserved_order_id and to_reserve > 0:
            from backend.models import StockReservation
            await stock_rows.reserve(db, pl.part_id, po.location_id, to_reserve)   # ii144a: on the location
            db.add(StockReservation(org_id=current.org_id, part_id=pl.part_id,
                                    location_id=po.location_id, order_id=pl.reserved_order_id,
                                    qty=to_reserve, released=False))
        received_cost_now += recv * unit_cost
        received_any = True
    if received_any:
        fully = all((pl.qty_received or 0) >= pl.qty_ordered for pl in po_lines)
        po.status = "received" if fully else "partial"
        from backend.services.po_links import notify_po_change, writes_off_automatically
        arrived = "all parts arrived" if fully else "some parts arrived"
        if not await writes_off_automatically(db, current.org_id):
            arrived += ", ready to write off on the job"
        await notify_po_change(db, current.org_id, po, arrived, actor_id=current.id)
        po.received_at = now_utc()
        po.received_cost = (po.received_cost or 0) + received_cost_now
        po.freight_capitalised = q2((po.freight_capitalised or 0) + freight_added_now)
        po.shipment_freight = q2((po.shipment_freight or 0) + ship_freight_now)
        # 144ar — auto-advance the manual workflow stage to Received on a full receipt, unless
        # the buyer has already parked it at a *later or terminal* stage (closed/cancelled). The
        # receipt state (`status`) is the source of truth; this just keeps the human label honest
        # without stomping a deliberate manual choice.
        if fully and (po.workflow_status or "") not in ("po_received", "po_closed", "po_cancelled"):
            po.workflow_status = await _resolve_received_workflow(db, current.org_id)
        # Buy-on-credit: increment the supplier's outstanding balance by what we received
        if po.on_credit and received_cost_now > 0:
            sup = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
            if sup:
                # 144ax: one write-path, one quantization — fixes the old drift where the column
                # took the raw float while the ledger took q2(received_cost_now).
                from backend.services.ledger import adjust_supplier_ap
                await adjust_supplier_ap(db, current.org_id, sup, received_cost_now, entry_type="posting",
                                         note=f"PO {po.po_number} received", ref_type="po",
                                         ref_id=po.id, created_by_id=current.id)
        # Build 144al — charges the supplier bills us for (shipping/transit/custom). When
        # NOT capitalised into item cost, they're still money we owe, so post them to the
        # supplier balance once the PO is fully received (freight already sits in unit cost
        # when charges_to_cost is on, so we skip it there to avoid double-counting).
        if fully and po.on_credit and po_charges_total > 0 and not po.charges_to_cost:
            sup = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
            if sup:
                from backend.services.ledger import adjust_supplier_ap
                await adjust_supplier_ap(db, current.org_id, sup, po_charges_total, entry_type="posting",
                                         note=f"PO {po.po_number} charges (shipping/transit)",
                                         ref_type="po", ref_id=po.id, created_by_id=current.id)
        # 144ar — per-shipment freight billed on credit but NOT capitalised is still money owed:
        # post it now. (When capitalised it's already inside received_cost_now above.)
        if po.on_credit and ship_total > 0 and not data.shipment_freight_to_cost:
            sup = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
            if sup:
                from backend.services.ledger import adjust_supplier_ap
                await adjust_supplier_ap(db, current.org_id, sup, ship_total, entry_type="posting",
                                         note=f"PO {po.po_number} shipment freight",
                                         ref_type="po", ref_id=po.id, created_by_id=current.id)
    await db.commit()
    return {"ok": True, "status": po.status, "received_cost": received_cost_now,
            "workflow_status": po.workflow_status}


@router.get("/parts/{part_id}/purchase-orders")
async def part_pos(
    part_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """POs this part is on (excluding closed) — shown in the product dialog."""
    rows = (await db.execute(
        select(PurchaseOrder, POLine).join(POLine, POLine.po_id == PurchaseOrder.id)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)   # POs are scoped via their supplier
        .where(POLine.part_id == part_id, Supplier.org_id == current.org_id,
               PurchaseOrder.status != "closed")
    )).all()
    return {"items": [{"id": po.id, "po_number": po.po_number, "status": po.status,
                       "qty_ordered": pl.qty_ordered, "qty_received": pl.qty_received,
                       "expected_at": po.expected_at.isoformat() if po.expected_at else None}
                      for po, pl in rows]}


@router.get("/reorder-list")
async def reorder_list(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Parts at or below min stock — the suggested reorder list."""
    parts = (await db.execute(
        select(Part).where(Part.org_id == current.org_id, Part.active == True).options(selectinload(Part.stock_levels))  # noqa: E712
    )).scalars().all()
    out = []
    for p in parts:
        avail = sum(s.qty_available for s in p.stock_levels)
        if avail <= (p.min_stock or 0):
            out.append({"id": p.id, "name": p.name, "sku": p.sku, "available": avail,
                        "min_stock": p.min_stock, "suggested_qty": max((p.min_stock or 0) * 2 - avail, 1)})
    return {"items": out}


@router.get("/purchasing/cockpit")
async def purchasing_cockpit(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """144aq — the Purchasing Cockpit: an action worklist, not a board. Surfaces only what needs
    the buyer right now, each row one click from a fix. Read-only aggregation over data we already
    hold, so it's cheap and always truthful. Beats a Kanban (which only shows *where* a PO sits)
    and beats Orderry/ShopMonkey's filterable list by being exception-driven."""
    from datetime import timedelta
    now = now_utc()
    soon = now + timedelta(days=7)
    OPEN = ("ordered", "partial")

    pos = (await db.execute(select(PurchaseOrder)
           .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
           .where(Supplier.org_id == current.org_id))).scalars().all()
    sup_names = {s.id: s.name for s in (await db.execute(select(Supplier).where(Supplier.org_id == current.org_id))).scalars().all()}
    lines_by_po: dict[str, list] = {}
    for l in (await db.execute(select(POLine).where(POLine.po_id.in_([p.id for p in pos]))) ).scalars().all() if pos else []:
        lines_by_po.setdefault(l.po_id, []).append(l)
    # weights for any weight-basis POs (one query, reused across the loop)
    _wids = {l.part_id for ls in lines_by_po.values() for l in ls}
    all_weights = {pid: (w or 0) for pid, w in (await db.execute(
        select(Part.id, Part.weight).where(Part.id.in_(_wids)))).all()} if _wids else {}

    overdue, arriving, awaiting_invoice, recost_needed = [], [], [], []
    for po in pos:
        st = (po.status or "").lower()
        lines = lines_by_po.get(po.id, [])
        outstanding = sum(max((l.qty_ordered or 0) - (l.qty_received or 0), 0) for l in lines)
        received_any = any((l.qty_received or 0) > 0 for l in lines)
        row = {"po_id": po.id, "po_number": po.po_number, "supplier": sup_names.get(po.supplier_id, "—"),
               "status": po.status, "workflow_status": po.workflow_status,
               "expected_at": po.expected_at.isoformat() if po.expected_at else None,
               "total": po.total, "outstanding": outstanding}
        if st in OPEN and outstanding > 0 and po.expected_at and po.expected_at < now:
            overdue.append({**row, "days_late": (now - po.expected_at).days})
        elif st in OPEN and outstanding > 0 and po.expected_at and now <= po.expected_at <= soon:
            arriving.append(row)
        if received_any and po.status != "cancelled" and (po.supplier_invoice_total is None):
            awaiting_invoice.append(row)
        # recost gap: charges capitalised but a freight delta hasn't hit received stock (144ar: honour alloc_basis)
        if po.charges_to_cost and received_any:
            ctot = _po_charges_total(po)
            if ctot > 0:
                b = po.alloc_basis if po.alloc_basis in _ALLOC_BASES else "value"
                pu = _freight_per_unit(lines, ctot, b, all_weights)
                target = sum(pu.get(l.id, 0.0) * (l.qty_received or 0) for l in lines)
                if q2(target - (po.freight_capitalised or 0)) >= 0.01:
                    recost_needed.append({**row, "freight_gap": q2(target - (po.freight_capitalised or 0))})

    # below minimum, and not already covered by an open PO
    on_order_parts = {l.part_id for po in pos if (po.status or "").lower() in OPEN for l in lines_by_po.get(po.id, [])
                      if (l.qty_ordered or 0) > (l.qty_received or 0)}
    parts = (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.active == True).options(selectinload(Part.stock_levels)))).scalars().all()  # noqa: E712
    below_min = []
    for p in parts:
        avail = sum(s.qty_available for s in p.stock_levels)
        if avail <= (p.min_stock or 0) and p.id not in on_order_parts and (p.min_stock or 0) > 0:
            below_min.append({"id": p.id, "name": p.name, "sku": p.sku, "available": avail,
                              "min_stock": p.min_stock, "suggested_qty": max((p.min_stock or 0) * 2 - avail, 1)})

    balances = [{"id": s.id, "name": s.name, "client_id": s.client_id, "balance": float(s.balance or 0)}
                for s in (await db.execute(select(Supplier).where(
                    Supplier.org_id == current.org_id, Supplier.balance != 0))).scalars().all()]
    balances.sort(key=lambda b: b["balance"], reverse=True)

    buckets = {
        "overdue": sorted(overdue, key=lambda r: -r["days_late"]),
        "arriving": sorted(arriving, key=lambda r: r["expected_at"] or ""),
        "recost_needed": recost_needed,
        "awaiting_invoice": awaiting_invoice,
        "below_min": below_min[:50],
        "supplier_balances": balances[:50],
    }
    return {"buckets": buckets, "counts": {k: len(v) for k, v in buckets.items()}}


async def _forecast_suggestions(db, org_id: str):
    """Velocity-based reorder suggestions as structured rows (with ids), so the
    forecast can turn into real POs. Mirrors the reorder_forecast report's math."""
    WINDOW, COVER = 28, 14
    since = now_utc() - timedelta(days=WINDOW)
    vel = {pid: float(q or 0) for pid, q in (await db.execute(
        select(StockMovement.part_id, func.sum(func.abs(StockMovement.qty)))
        .join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == org_id, StockMovement.movement_type == "use",
               StockMovement.created_at >= since)
        .group_by(StockMovement.part_id))).all()}
    incoming: dict = {}
    open_pos = (await db.execute(select(PurchaseOrder)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .where(Supplier.org_id == org_id,
               PurchaseOrder.status.notin_(["received", "cancelled", "canceled", "grouped"])))).scalars().all()
    for po in open_pos:
        for ln in (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all():
            out = (ln.qty_ordered or 0) - (ln.qty_received or 0)
            if out > 0:
                incoming[ln.part_id] = incoming.get(ln.part_id, 0) + out
    sup_names = {s.id: s.name for s in (await db.execute(
        select(Supplier).where(Supplier.org_id == org_id))).scalars().all()}
    sup_lead = {s.id: (s.lead_days or 0) for s in (await db.execute(
        select(Supplier).where(Supplier.org_id == org_id))).scalars().all()}
    parts = (await db.execute(select(Part).where(
        Part.org_id == org_id, Part.active == True)  # noqa: E712
        .options(selectinload(Part.stock_levels)))).scalars().all()
    out = []
    for p in parts:
        daily = vel.get(p.id, 0) / WINDOW
        if daily <= 0:
            continue
        avail = sum(s.qty_available or 0 for s in p.stock_levels)
        res = sum(s.qty_reserved or 0 for s in p.stock_levels)
        free = avail - res
        lead = sup_lead.get(p.default_supplier_id, 0)
        suggest = daily * (lead + COVER) - free - incoming.get(p.id, 0)
        if suggest <= 0:
            continue
        cost = p.avg_cost or p.cost_price or 0
        out.append({
            "part_id": p.id, "name": p.name, "sku": p.sku,
            "supplier_id": p.default_supplier_id,
            "supplier_name": sup_names.get(p.default_supplier_id) if p.default_supplier_id else None,
            "free": q2(free), "daily_use": q2(daily),
            "days_left": round(free / daily, 1) if daily else None,
            "lead_days": lead, "on_order": q2(incoming.get(p.id, 0)),
            "suggest_qty": round(suggest, 1), "unit_cost": q2(cost),
            "est_cost": q2(suggest * cost),
        })
    out.sort(key=lambda r: (r["days_left"] if r["days_left"] is not None else 1e9))
    return out


@router.get("/reorder-forecast")
async def reorder_forecast(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Structured velocity reorder suggestions (with part/supplier ids) for the
    'create PO from forecast' flow."""
    return {"items": await _forecast_suggestions(db, current.org_id)}


class ForecastPOItem(BaseModel):
    part_id: str
    qty: float


class ForecastPOIn(BaseModel):
    # Optional subset; when omitted, every current suggestion is ordered.
    items: Optional[list[ForecastPOItem]] = None
    location_id: Optional[str] = None


@router.post("/reorder-forecast/create-pos", status_code=201)
async def create_pos_from_forecast(
    data: ForecastPOIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """One click: turn the reorder forecast into draft POs, grouped by each product's
    default supplier. Parts with no default supplier are skipped and reported back.
    POs are created as 'draft' so they're reviewed before sending."""
    from backend.models import Location
    sugg = {s["part_id"]: s for s in await _forecast_suggestions(db, current.org_id)}
    if data.items:
        chosen = [(i.part_id, i.qty) for i in data.items if i.part_id in sugg]
    else:
        chosen = [(s["part_id"], s["suggest_qty"]) for s in sugg.values()]
    loc_id = data.location_id
    if not loc_id:
        loc = (await db.execute(select(Location).where(
            Location.org_id == current.org_id).limit(1))).scalar_one_or_none()
        loc_id = loc.id if loc else None
    if not loc_id:
        raise HTTPException(422, "No location to raise the purchase order against")
    # group by supplier
    by_sup: dict = {}
    skipped = []
    for pid, qty in chosen:
        if qty <= 0:
            continue
        s = sugg[pid]
        if not s["supplier_id"]:
            skipped.append(s["name"])
            continue
        by_sup.setdefault(s["supplier_id"], []).append((pid, qty, s["unit_cost"]))
    created = []
    for sup_id, lines in by_sup.items():
        total = sum(q * c for _, q, c in lines)
        po = PurchaseOrder(supplier_id=sup_id, location_id=loc_id,
                           org_id=current.org_id, po_number=await next_number(db, current.org_id, "po"),
                           status="draft", total=total)
        db.add(po)
        await db.flush()
        for pid, q, c in lines:
            db.add(POLine(po_id=po.id, part_id=pid, qty_ordered=q, unit_cost=c))
        created.append({"po_id": po.id, "po_number": po.po_number,
                        "supplier_id": sup_id, "lines": len(lines), "total": q2(total)})
    await db.commit()
    return {"ok": True, "created": created, "po_count": len(created), "skipped_no_supplier": skipped}


@router.post("/reorder-digest")
async def reorder_digest(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Push a weekly 'what to reorder' digest to inventory managers' notification feed.
    Intended to be called on a schedule (e.g. weekly), like /automations/sweep. No-op
    when nothing is forecast to run out."""
    from backend.models import Role
    from backend.services import push
    items = await _forecast_suggestions(db, current.org_id)
    if not items:
        return {"sent": 0, "items": 0}
    total = q2(sum(i["est_cost"] for i in items))
    top = ", ".join(f"{i['name']} ({i['days_left']}d)" for i in items[:3] if i["days_left"] is not None)
    title = f"{len(items)} product{'' if len(items) == 1 else 's'} to reorder (~{total})"
    body = f"Soonest to run out: {top}." if top else "Review the reorder forecast in Inventory."
    # recipients: owners (no role) + roles that aren't explicitly denied inventory
    from backend.services.permissions import role_allows as _ra
    roles = {r.id: r for r in (await db.execute(
        select(Role).where(Role.org_id == current.org_id))).scalars().all()}
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id, Employee.active == True))).scalars().all()  # noqa: E712
    sent = 0
    for e in emps:
        if e.role_id and (e.role_id not in roles or not _ra(roles[e.role_id], "can_manage_inventory")):
            continue
        n = await push.notify(db, org_id=current.org_id, employee_id=e.id,
                              kind="inventory.reorder", title=title, body=body,
                              deep_link="/inventory", commit=False)
        if n:
            sent += 1
    await db.commit()
    return {"sent": sent, "items": len(items), "est_cost": total}


@router.get("/suppliers")
async def list_suppliers(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Supplier).where(Supplier.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": s.id, "name": s.name, "phone": s.phone, "email": s.email,
                       "balance": s.balance, "currency": s.currency} for s in rows]}


@router.get("/movements")
async def list_movements(
    kind: str = "posting",   # posting (receive/adjust) | writeoff
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Global stock movement log, grouped for Orderry's Postings / Write-offs tabs.
    Postings = stock coming in (receive/adjust); Write-offs = stock going out manually."""
    from backend.models import Location
    types = ["writeoff"] if kind == "writeoff" else ["receive", "adjust"]
    rows = (await db.execute(
        select(StockMovement)
        .join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == current.org_id, StockMovement.movement_type.in_(types))
        .order_by(StockMovement.created_at.desc()).limit(300))).scalars().all()
    parts = await names_by_id(db, Part.id, Part.name, [m.part_id for m in rows],
                              org_col=Part.org_id, org_id=current.org_id)
    locs = {l.id: l.name for l in (await db.execute(
        select(Location).where(Location.org_id == current.org_id))).scalars().all()}
    return {"items": [{
        "id": m.id, "part_name": parts.get(m.part_id, "?"),
        "location": locs.get(m.location_id, "-"), "type": m.movement_type,
        "qty": m.qty, "unit_cost": float(m.unit_cost or 0),
        "ref_type": m.ref_type, "at": m.created_at.isoformat() if m.created_at else None,
    } for m in rows]}


@router.get("/conversions")
async def list_conversions(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Assets converted into sellable products (Orderry's Conversions tab)."""
    from sqlalchemy import and_, or_

    from backend.models import Client, Device, Part
    # Same org scoping as the asset list (144do): the old `owner_type == "company"` arm listed
    # every shop's converted company assets.
    client_ids = select(Client.id).where(Client.org_id == current.org_id)
    rows = (await db.execute(select(Device).where(
        Device.converted_to_part_id.is_not(None),
        or_(Device.org_id == current.org_id,
            and_(Device.org_id.is_(None), Device.client_id.in_(client_ids))))
        .order_by(Device.id))).scalars().all()
    # resolve the converted products so we can show their real sell price
    part_ids = [d.converted_to_part_id for d in rows if d.converted_to_part_id]
    parts = {}
    if part_ids:
        parts = {p.id: p for p in (await db.execute(
            select(Part).where(Part.id.in_(part_ids)))).scalars().all()}
    out = []
    for d in rows:
        p = parts.get(d.converted_to_part_id)
        out.append({"id": d.id, "name": getattr(d, "name", None) or f"{d.brand or ''} {d.model or ''}".strip() or "Device",
                    "asset_type": getattr(d, "asset_type", None), "status": d.asset_status,
                    "product_id": d.converted_to_part_id,
                    "product_name": p.name if p else None,
                    "product_sku": p.sku if p else None,
                    "sale_price": (p.sell_price if p else None) or getattr(d, "sale_price", 0) or 0})
    return {"items": out}


# ── Build 78: merge duplicate products ───────────────────────────────────────
class MergeIn(BaseModel):
    target_id: str            # the product that survives
    source_ids: list[str]     # merged into the target, then deactivated (max 9)


@router.post("/parts/merge")
async def merge_parts(
    data: MergeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Merge duplicate products into one (Orderry: up to 10 at a time).

    Stock levels, movements, serialized units and batches are re-pointed at the
    target; empty fields on the target are filled from the sources (first non-empty
    wins) so nothing is silently lost. Sources are deactivated rather than deleted,
    keeping historical documents intact. A serialized product can only be merged
    with another serialized product — mixing the two would break unit identity.
    """
    from backend.models import SerializedItem, StockItemBatch
    if not data.source_ids:
        raise HTTPException(400, "Pick at least one product to merge in")
    if len(data.source_ids) > 9:
        raise HTTPException(400, "You can merge at most 10 products at once")
    if data.target_id in data.source_ids:
        raise HTTPException(400, "The target can't also be a source")

    target = (await db.execute(select(Part).where(
        Part.id == data.target_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target product not found")
    sources = (await db.execute(select(Part).where(
        Part.id.in_(data.source_ids), Part.org_id == current.org_id))).scalars().all()
    if len(sources) != len(data.source_ids):
        raise HTTPException(404, "One or more source products not found")
    for s in sources:
        if s.serialized != target.serialized:
            raise HTTPException(
                400, "Serialized products can only be merged with serialized products")

    moved = {"stock": 0, "movements": 0, "units": 0, "batches": 0}
    for s in sources:
        # ii144a: archive the source first, so its SKU / barcodes are free before the target takes them
        s.active = False
        s.name = f"{s.name} (merged into {target.name})"
        await db.flush()
        # fill blanks on the target from the source (never overwrite real values)
        for field in ("sku", "barcode", "brand", "category", "code", "image_data",
                      "asset_brand", "asset_model", "template_key"):
            if not getattr(target, field, None) and getattr(s, field, None):
                setattr(target, field, getattr(s, field))
        if not target.cost_price and s.cost_price:
            target.cost_price = s.cost_price
        if not target.sell_price and s.sell_price:
            target.sell_price = s.sell_price

        # fold stock rows into the target's row with the same (location, bin)
        for sl in (await db.execute(select(StockLevel).where(
                StockLevel.part_id == s.id))).scalars().all():
            tgt_sl = await stock_rows.row_for(db, target.id, sl.location_id, sl.bin_id,
                                              warehouse_id=sl.warehouse_id)
            if tgt_sl:
                await _atomic_add(db, tgt_sl, "qty_available", sl.qty_available, round_to=None)   # atomic
                await _atomic_add(db, tgt_sl, "qty_reserved", sl.qty_reserved or 0, round_to=None)
                sl.qty_available = 0
                sl.qty_reserved = 0
            else:
                sl.part_id = target.id
                await db.flush()
            moved["stock"] += 1
        # every barcode of the source now finds the target (old labels keep scanning): its main code
        # becomes the target's main when the target had none (the hook does that), else an extra
        await db.flush()
        from backend.models import ProductBarcode
        from sqlalchemy import update as _upd
        await db.execute(_upd(ProductBarcode).where(
            ProductBarcode.part_id == s.id, ProductBarcode.barcode != (target.barcode or ""))
            .values(part_id=target.id, active=True, is_main=False).execution_options(synchronize_session=False))

        for m in (await db.execute(select(StockMovement).where(
                StockMovement.part_id == s.id))).scalars().all():
            m.part_id = target.id
            moved["movements"] += 1
        for u in (await db.execute(select(SerializedItem).where(
                SerializedItem.part_id == s.id))).scalars().all():
            u.part_id = target.id
            moved["units"] += 1
        for b in (await db.execute(select(StockItemBatch).where(
                StockItemBatch.part_id == s.id))).scalars().all():
            b.part_id = target.id
            moved["batches"] += 1

    await db.commit()
    return {"ok": True, "target_id": target.id, "merged": len(sources), "moved": moved}


# ── Build 79: markup-based auto-pricing ──────────────────────────────────────
class MarkupIn(BaseModel):
    markup_pct: float          # sell = cost * (1 + markup/100)
    part_ids: Optional[list[str]] = None   # default: every active product
    basis: str = "avg_cost"    # avg_cost|cost_price


@router.post("/parts/apply-markup")
async def apply_markup(
    data: MarkupIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Set sell prices from cost + markup (Orderry prices products by markup on
    posting). Uses the moving-average cost by default so pricing tracks what the
    stock actually cost, not the last quoted price."""
    if data.markup_pct < 0:
        raise HTTPException(400, "markup_pct can't be negative")
    if data.basis not in ("avg_cost", "cost_price"):
        raise HTTPException(400, "basis must be avg_cost|cost_price")
    q = select(Part).where(Part.org_id == current.org_id, Part.active == True)  # noqa: E712
    if data.part_ids:
        q = q.where(Part.id.in_(data.part_ids))
    parts = (await db.execute(q)).scalars().all()
    updated = []
    for p in parts:
        base = (p.avg_cost or p.cost_price or 0) if data.basis == "avg_cost" else (p.cost_price or 0)
        if not base:
            continue                      # no cost basis → leave the price alone
        p.sell_price = q2(base * (1 + data.markup_pct / 100))
        updated.append({"id": p.id, "name": p.name, "cost": base, "sell_price": p.sell_price})
    await db.commit()
    return {"ok": True, "updated": len(updated), "items": updated[:50]}


# ── Build 96: bulk product actions ───────────────────────────────────────────
class BulkAction(BaseModel):
    part_ids: list[str]
    action: str          # set_category|set_min_stock|adjust_price_pct|set_active|set_supplier
    value: Optional[str] = None      # category id / supplier id
    number: Optional[float] = None   # min stock / percent / 0|1


@router.post("/parts/bulk")
async def bulk_update_parts(
    data: BulkAction,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Apply one change to many products at once — the "select 40 rows and retag them"
    operation that otherwise means 40 trips through the edit dialog.

    Price changes are expressed as a **percentage**, not an absolute, because that's the
    only thing that makes sense across a mixed selection: +5% means the same intent for a
    £2 cable and a £200 board, where "set price to 5" would be nonsense for both.
    """
    if not data.part_ids:
        raise HTTPException(400, "Select at least one product")
    if len(data.part_ids) > 1000:
        raise HTTPException(422, "Too many products in one batch (limit 1000)")

    parts = (await db.execute(select(Part).where(
        Part.id.in_(data.part_ids), Part.org_id == current.org_id))).scalars().all()
    if not parts:
        raise HTTPException(404, "No matching products")

    changed = 0
    if data.action == "set_category":
        for p in parts:
            p.category_id = data.value
            changed += 1
    elif data.action == "set_min_stock":
        if data.number is None or data.number < 0:
            raise HTTPException(422, "min stock must be zero or more")
        for p in parts:
            p.min_stock = data.number
            changed += 1
    elif data.action == "adjust_price_pct":
        if data.number is None:
            raise HTTPException(422, "Provide a percentage")
        factor = 1 + data.number / 100.0
        if factor < 0:
            raise HTTPException(422, "That would make prices negative")
        for p in parts:
            if p.sell_price:
                p.sell_price = q2(p.sell_price * factor)
                changed += 1
    elif data.action == "set_active":
        if data.number not in (0, 1):
            raise HTTPException(422, "set_active expects 0 or 1")
        for p in parts:
            p.active = bool(data.number)
            changed += 1
    elif data.action == "set_supplier":
        # kept on custom_data so it works whether or not a supplier column exists
        for p in parts:
            if hasattr(p, "supplier_id"):
                p.supplier_id = data.value
                changed += 1
    else:
        raise HTTPException(422, f"Unknown action “{data.action}”")

    await db.commit()
    return {"ok": True, "changed": changed, "action": data.action}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
