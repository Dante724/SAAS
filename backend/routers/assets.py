"""
Assets / Asset Warehouse.

Assets are client devices (or company-owned tools/equipment). Each carries a
mandatory IMEI/Serial — which can be auto-generated when no real number exists.
"""
import random
import string
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Client, Device, Employee, Order
from backend.services import asset_catalog as _cat
from backend.services import identifiers as _ids
from backend.services import industry as _ind
from backend.core import paging as _paging
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetIn(BaseModel):
    owner_type: str = "client"          # client | company
    client_id: Optional[str] = None
    asset_type: Optional[str] = "Device"
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    imei: Optional[str] = None
    vin: Optional[str] = None
    hin: Optional[str] = None
    color: Optional[str] = None
    condition: Optional[str] = None
    location_id: Optional[str] = None
    asset_status: str = "in_use"        # in_use|in_stock|for_sale|sold|written_off
    purchase_price: float = 0.0
    sale_price: float = 0.0
    template_key: Optional[str] = None   # industry template; defaults to the org's active one
    asset_group_id: Optional[str] = None
    image_data: Optional[str] = None     # 144da: asset photo (data URL) at create time
    custom_data: Optional[dict] = None   # 144da: system extras (number_plate, year, engine…) + custom fields


def _gen_serial(asset_type: str = "Device") -> str:
    prefix = (asset_type or "AST")[:3].upper()
    body = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"{prefix}-{body}"


def _ser(d: Device, order_count: int = 0, owner_name: str = None, inline_image: bool = True) -> dict:
    return {
        "id": d.id, "owner_type": d.owner_type, "client_id": d.client_id,
        "owner_name": owner_name,
        "asset_type": d.asset_type or "Device",
        "brand": d.brand, "model": d.model,
        "serial": d.serial, "imei": d.imei, "vin": d.vin, "hin": d.hin,
        "template_key": getattr(d, "template_key", None),
        # noun this asset was created under (Vessel / Vehicle / Unit / Device…),
        # so history keeps its own wording even after the org switches template
        "asset_label": _ind.asset_label(getattr(d, "template_key", None)),
        "asset_group_id": getattr(d, "asset_group_id", None),
        "is_generated_serial": d.is_generated_serial,
        "color": d.color, "condition": d.condition,
        **({"image_data": getattr(d, "image_data", None)} if inline_image else {}),
        "watch_out": getattr(d, "watch_out", None),
        "location_id": d.location_id,
        "asset_status": getattr(d, "asset_status", "in_use"),
        "purchase_price": getattr(d, "purchase_price", 0.0),
        "sale_price": getattr(d, "sale_price", 0.0),
        "converted_to_part_id": getattr(d, "converted_to_part_id", None),
        "order_count": order_count,
        "custom_data": getattr(d, "custom_data", None) or {},   # 144bv: custom-field columns
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("/generate-serial")
async def generate_serial(
    asset_type: str = "Device",
    current: Employee = Depends(get_current_employee),
):
    """Return a fresh auto-generated unique serial for an asset."""
    return {"serial": _gen_serial(asset_type)}


@router.get("")
async def list_assets(
    owner_type: Optional[str] = None,
    asset_type: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = _paging.skip_query(),
    limit: int = _paging.limit_query(200),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # Build 144do (security): scope strictly to this org. Previously company-owned
    # assets from ALL orgs leaked into every org's list via `owner_type == "company"`.
    # Batch 2: one page at a time with the real total; owner names and order counts are
    # batched for the page (it used to load every client of the shop and count per row),
    # and the photo is a URL, not inline base64.
    from sqlalchemy.orm import defer
    limit = _paging.clamp(limit)
    q = select(Device).where(Device.org_id == current.org_id)
    if owner_type:
        q = q.where(Device.owner_type == owner_type)
    if asset_type:
        q = q.where(Device.asset_type == asset_type)
    if search:
        q = q.where(or_(
            Device.serial.ilike(f"%{search}%"),
            Device.imei.ilike(f"%{search}%"),
            Device.brand.ilike(f"%{search}%"),
            Device.model.ilike(f"%{search}%"),
            Device.asset_type.ilike(f"%{search}%"),
            Device.client_id.in_(select(Client.id).where(Client.org_id == current.org_id,
                                                         Client.name.ilike(f"%{search}%"))),
        ))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    devices = (await db.execute(q.options(defer(Device.image_data))
                                .order_by(desc(Device.created_at), desc(Device.id))
                                .offset(skip).limit(limit))).scalars().all()
    ids = [d.id for d in devices]
    cids = {d.client_id for d in devices if d.client_id and d.owner_type == "client"}
    client_map = dict((await db.execute(select(Client.id, Client.name).where(
        Client.org_id == current.org_id, Client.id.in_(cids)))).all()) if cids else {}
    counts = dict((await db.execute(select(Order.device_id, func.count(Order.id)).where(
        Order.org_id == current.org_id, Order.device_id.in_(ids)).group_by(Order.device_id))).all()) if ids else {}
    with_img = set((await db.execute(select(Device.id).where(
        Device.id.in_(ids), Device.image_data.is_not(None), Device.image_data != ""))).scalars().all()) if ids else set()

    out = []
    for d in devices:
        owner = client_map.get(d.client_id) if d.owner_type == "client" else "Company"
        row = _ser(d, counts.get(d.id, 0), owner, inline_image=False)
        row["image_url"] = f"/api/assets/{d.id}/image" if d.id in with_img else None
        out.append(row)
    return {"total": total, "items": out, "skip": skip, "limit": limit}


@router.post("", status_code=201)
async def create_asset(
    data: AssetIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.owner_type == "client" and not data.client_id:
        raise HTTPException(400, "Client is required for a client-owned asset")
    from backend.routers.forms import check_entity_required
    await check_entity_required(db, current.org_id, "device", data)   # built-ins the device form requires
    serial = data.serial
    generated = False
    if not serial and not data.imei and not data.vin and not data.hin:
        serial = _gen_serial(data.asset_type)
        generated = True
    # Stamp the industry template this asset was created under (defaults to the
    # org's active template). Historical assets without one render via a fallback.
    from backend.models import Organisation
    from backend.services import industry as _ind
    _org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    template_key = _ind.normalize_template_key(data.template_key or (_org.industry_template if _org else None))
    d = Device(
        org_id=current.org_id,
        client_id=data.client_id if data.owner_type == "client" else None,
        owner_type=data.owner_type, asset_type=data.asset_type or "Device",
        brand=data.brand, model=data.model, serial=serial, imei=data.imei,
        vin=data.vin, hin=data.hin,
        template_key=template_key, asset_group_id=data.asset_group_id,
        is_generated_serial=generated, color=data.color, condition=data.condition,
        location_id=data.location_id,
        image_data=data.image_data,
        custom_data=data.custom_data or {},
        asset_status=data.asset_status or ("in_use" if data.owner_type == "client" else "in_stock"),
        purchase_price=data.purchase_price or 0.0, sale_price=data.sale_price or 0.0,
    )
    _ids.tidy_device(d)
    d.brand, d.model = await _cat.canonical(db, current.org_id, template_key, d.brand, d.model)
    db.add(d)
    await db.flush()
    await _log_asset(db, current.org_id, d.id, "create",
                     to_zone=("Company" if d.owner_type == "company" else "Client"),
                     note="Asset created", by=current.id)
    await db.commit()
    await db.refresh(d)
    return _ser(d, 0)


@router.get("/suggest")
async def suggest_names(
    field: str = "brand", q: str = "", brand: Optional[str] = None,
    group: Optional[str] = None, template_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Brand / model picker options: the shop's own usage first, then its catalog, then the
    built-in US/CA list. ``group`` (the asset group, e.g. "Power Tool") and the shop's industry
    (``template_key``, defaulting to the org's) put the names that fit that group first."""
    if field not in ("brand", "model"):
        raise HTTPException(422, "field must be brand or model")
    if not template_key:
        from backend.models import Organisation
        template_key = (await db.execute(select(Organisation.industry_template).where(
            Organisation.id == current.org_id))).scalar_one_or_none()
    return {"items": await _cat.suggest(db, current.org_id, field, q, brand,
                                        group=group, template_key=template_key)}


class IdentifyIn(BaseModel):
    value: str
    kind: Optional[str] = None          # imei|vin|hin|serial — guessed when omitted
    client_id: Optional[str] = None     # the customer at the counter, to tell "theirs" from "someone else's"


@router.post("/identify")
async def identify(
    data: IdentifyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Intake helper for a typed or scanned IMEI / VIN / HIN / serial: tidy it, say if it looks
    mistyped, and find it if this shop has seen it before (owner, last job, warranty) so staff
    reuse the device instead of creating a duplicate. Never blocks anything."""
    kind = data.kind if data.kind in _ids.KINDS else _ids.guess_kind(data.value)
    norm = _ids.normalize(kind, data.value)
    if not norm:
        return {"kind": kind, "normalized": None, "problem": None, "matches": []}
    candidates = {k: _ids.normalize(k, data.value) for k in _ids.KINDS}
    cols = {"imei": Device.imei, "vin": Device.vin, "hin": Device.hin, "serial": Device.serial}
    conds = [cols[k] == v for k, v in candidates.items() if v]
    rows = (await db.execute(select(Device).where(
        Device.org_id == current.org_id, or_(*conds)).limit(5))).scalars().all()
    from datetime import date as _date
    today = _date.today()
    matches = []
    for d in rows:
        owner = None
        if d.client_id:
            c = (await db.execute(select(Client).where(
                Client.id == d.client_id, Client.org_id == current.org_id))).scalar_one_or_none()
            owner = {"id": c.id, "name": c.name, "phone": c.phone} if c else None
        last = (await db.execute(select(Order).where(
            Order.org_id == current.org_id, Order.device_id == d.id)
            .order_by(desc(Order.created_at)).limit(1))).scalars().first()
        jobs = (await db.execute(select(func.count()).select_from(Order).where(
            Order.org_id == current.org_id, Order.device_id == d.id))).scalar() or 0
        last_out = None
        if last:
            wu = last.warranty_until
            last_out = {"id": last.id, "order_number": last.order_number, "status": last.status,
                        "created_at": last.created_at.isoformat() if last.created_at else None,
                        "warranty_until": wu.isoformat() if wu else None,
                        "under_warranty": bool(wu and wu >= today)}
        matches.append({
            "id": d.id, "brand": d.brand, "model": d.model, "serial": d.serial, "imei": d.imei,
            "vin": d.vin, "hin": d.hin, "color": d.color, "watch_out": d.watch_out,
            "client": owner, "owner_type": d.owner_type,
            "same_client": bool(data.client_id and d.client_id == data.client_id),
            "jobs": jobs, "last_order": last_out})
    return {"kind": kind, "normalized": norm, "problem": _ids.problem(kind, norm), "matches": matches}


@router.get("/{asset_id}")
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    d = (await db.execute(select(Device).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Asset not found")
    # full work-order history for this asset
    orders = (await db.execute(
        select(Order).where(Order.device_id == asset_id)
        .order_by(desc(Order.created_at))
    )).scalars().all()
    owner = None
    client_balance = None
    if d.owner_type == "client" and d.client_id:
        c = (await db.execute(select(Client).where(Client.id == d.client_id))).scalar_one_or_none()
        owner = c.name if c else None
        client_balance = float(c.balance or 0) if c else None
    elif d.owner_type == "company":
        owner = "Company"
    res = _ser(d, len(orders), owner)
    res["client_balance"] = client_balance      # build 126: show the owner's balance inline
    res["history"] = [{
        "id": o.id, "order_number": o.order_number, "status": o.status,
        "total": o.total, "created_at": o.created_at.isoformat() if o.created_at else None,
    } for o in orders]
    return res


class AssetUpdate(BaseModel):
    asset_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    imei: Optional[str] = None
    vin: Optional[str] = None
    hin: Optional[str] = None
    color: Optional[str] = None
    condition: Optional[str] = None
    client_id: Optional[str] = None
    owner_type: Optional[str] = None
    image_data: Optional[str] = None   # Build 144ai: asset photo (data URL)
    watch_out: Optional[str] = None    # Build 144ag: prominent per-asset warning


@router.get("/{asset_id}/image")
async def get_asset_image(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The asset photo as an image, so lists and the board can reference it by URL instead
    of inlining base64 on every row."""
    import base64
    import binascii

    from fastapi import Response

    from backend.routers.media import safe_media_response
    data = (await db.execute(select(Device.image_data).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not data or not data.startswith("data:") or "," not in data:
        raise HTTPException(404, "No photo")
    head, b64 = data[5:].split(",", 1)
    mime = head.split(";", 1)[0] or "application/octet-stream"
    try:
        raw = base64.b64decode(b64, validate=False) if ";base64" in head else b64.encode()
    except (binascii.Error, ValueError):
        raise HTTPException(404, "No photo")
    kw = safe_media_response(raw, mime, "asset")
    kw["headers"]["Cache-Control"] = "private, max-age=300"
    return Response(content=raw, **kw)


@router.put("/{asset_id}")
async def update_asset(
    asset_id: str,
    data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Build 126 — edit an asset's identity fields. Gated by inventory-manage rights so
    only staff with access can change them. Client-owned assets are tenant-checked via the
    owning client; a re-owner (We ↔ Client) is allowed with a valid client."""
    d = (await db.execute(select(Device).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Asset not found")
    # tenancy: a client-owned asset must belong to a client in this org
    if d.client_id:
        owner = (await db.execute(select(Client).where(
            Client.id == d.client_id, Client.org_id == current.org_id))).scalar_one_or_none()
        if not owner:
            raise HTTPException(404, "Asset not found")
    payload = data.model_dump(exclude_unset=True)
    if payload.get("owner_type") == "client" and not (payload.get("client_id") or d.client_id):
        raise HTTPException(400, "Client is required for a client-owned asset")
    if "client_id" in payload and payload["client_id"]:
        c = (await db.execute(select(Client).where(
            Client.id == payload["client_id"], Client.org_id == current.org_id))).scalar_one_or_none()
        if not c:
            raise HTTPException(404, "Client not found")
    if payload.get("owner_type") == "company":
        payload["client_id"] = None
    for k, v in payload.items():
        setattr(d, k, v)
    _ids.tidy_device(d)
    if "brand" in payload or "model" in payload:
        d.brand, d.model = await _cat.canonical(db, current.org_id, d.template_key, d.brand, d.model)
    await db.commit()
    await db.refresh(d)
    order_count = (await db.execute(select(func.count()).select_from(Order)
                   .where(Order.device_id == asset_id))).scalar() or 0
    owner_name = None
    if d.owner_type == "client" and d.client_id:
        oc = (await db.execute(select(Client).where(Client.id == d.client_id))).scalar_one_or_none()
        owner_name = oc.name if oc else None
    elif d.owner_type == "company":
        owner_name = "Company"
    return _ser(d, order_count, owner_name)


@router.get("/{asset_id}/movements")
async def asset_movements(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Full lifecycle log for the asset's History tab: creation, transfers, write-offs,
    ownership changes and zone moves — each with who, when and detail."""
    from backend.models import AssetMovement
    from backend.models import Employee as Emp
    if not (await db.execute(select(Device.id).where(
            Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none():
        raise HTTPException(404, "Asset not found")
    rows = (await db.execute(
        select(AssetMovement).where(AssetMovement.asset_id == asset_id)
        .order_by(desc(AssetMovement.moved_at)))).scalars().all()
    who = {}
    ids = {r.created_by_id for r in rows if r.created_by_id}
    if ids:
        for e in (await db.execute(select(Emp).where(Emp.id.in_(ids)))).scalars().all():
            who[e.id] = e.name
    return {"items": [{
        "event_type": r.event_type or "zone",
        "from_zone": r.from_zone, "to_zone": r.to_zone,
        "status_key": r.status_key, "note": r.note,
        "by": who.get(r.created_by_id),
        "at": r.moved_at.isoformat() if r.moved_at else None,
    } for r in rows]}


async def _log_asset(db, org_id, asset_id, event_type, *, from_zone=None, to_zone=None,
                     note=None, by=None):
    from backend.models import AssetMovement
    db.add(AssetMovement(org_id=org_id, asset_id=asset_id, event_type=event_type,
                         from_zone=from_zone, to_zone=to_zone, note=note, created_by_id=by))


class TransferIn(BaseModel):
    to_client_id: Optional[str] = None     # transfer to a client (or None → company)
    to_location_id: Optional[str] = None
    note: Optional[str] = None


@router.post("/{asset_id}/transfer", status_code=201)
async def transfer_asset(
    asset_id: str, data: TransferIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Move an asset between owners/locations, logging the transfer."""
    d = (await db.execute(select(Device).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Asset not found")
    before = "Company" if d.owner_type == "company" else "Client"
    if data.to_client_id:
        d.owner_type = "client"
        d.client_id = data.to_client_id
        after = "Client"
    else:
        d.owner_type = "company"
        d.client_id = None
        after = "Company"
    if data.to_location_id:
        d.location_id = data.to_location_id
    await _log_asset(db, current.org_id, asset_id, "transfer",
                     from_zone=before, to_zone=after, note=data.note, by=current.id)
    await db.commit()
    return {"ok": True, "owner_type": d.owner_type}


class WriteOffIn(BaseModel):
    note: Optional[str] = None


@router.post("/{asset_id}/write-off", status_code=201)
async def write_off_asset(
    asset_id: str, data: WriteOffIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Mark an asset written off (damaged/lost/scrapped), logging the event."""
    d = (await db.execute(select(Device).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Asset not found")
    if d.asset_status == "written_off":
        raise HTTPException(409, "Already written off")
    prev = d.asset_status
    d.asset_status = "written_off"
    await _log_asset(db, current.org_id, asset_id, "writeoff",
                     from_zone=prev, to_zone="written_off", note=data.note, by=current.id)
    await db.commit()
    return {"ok": True}


class ConvertIn(BaseModel):
    sku: Optional[str] = None
    sale_price: float = 0.0
    location_id: Optional[str] = None


@router.post("/{asset_id}/convert-to-product", status_code=201)
async def convert_to_product(
    asset_id: str,
    data: ConvertIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_convert_asset_to_product")),
):
    """Orderry: turn a non-sellable company asset into a sellable product (stock)."""
    from backend.models import Location, Part, StockLevel, StockMovement
    d = (await db.execute(select(Device).where(
        Device.id == asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Asset not found")
    if d.owner_type != "company":
        raise HTTPException(400, "Only company-owned assets can be converted to products")
    if d.converted_to_part_id:
        raise HTTPException(409, "This asset was already converted")
    part = Part(
        org_id=current.org_id,
        name=f"{d.brand or ''} {d.model or ''}".strip() or (d.asset_type or "Product"),
        sku=data.sku or (d.serial or None),
        sell_price=data.sale_price or d.sale_price or 0.0,
        cost_price=d.purchase_price or 0.0,
    )
    db.add(part)
    await db.flush()
    loc_id = data.location_id or d.location_id
    if not loc_id:
        # Fall back to the org's default location so the converted product always lands
        # in stock (qty 1). Without this, an asset with no location produced 0 available.
        default_loc = (await db.execute(select(Location).where(
            Location.org_id == current.org_id).order_by(Location.id))).scalars().first()
        loc_id = default_loc.id if default_loc else None
    if loc_id:
        sl = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part.id, StockLevel.location_id == loc_id))).scalar_one_or_none()
        if sl:
            await _atomic_add(db, sl, "qty_available", 1, round_to=None)   # atomic
        else:
            db.add(StockLevel(part_id=part.id, location_id=loc_id, qty_available=1, qty_reserved=0))
        # record the stock movement so the product's History shows where the unit came from
        db.add(StockMovement(part_id=part.id, location_id=loc_id, movement_type="receive",
                             qty=1, unit_cost=float(d.purchase_price or 0),
                             ref_type="conversion", ref_id=asset_id, created_by_id=current.id))
    d.converted_to_part_id = part.id
    d.asset_status = "sold"
    # build 144aa: record the conversion document so it appears on the Conversions page
    # (previously this endpoint converted the asset but never wrote a conversion row,
    # so the history was always empty).
    from backend.models import AssetProductConversion
    db.add(AssetProductConversion(
        org_id=current.org_id, direction="asset_to_product",
        part_id=part.id, device_id=d.id,
        owner_before=d.owner_type or "company", owner_after="company",
        template_key=getattr(d, "template_key", None),
        serial_identifier=d.serial, status="completed",
        created_by_id=current.id,
        metadata_json={"product_name": part.name}))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "asset", d.id, f"Converted asset to product: {part.name}")
    await db.commit()
    return {"ok": True, "part_id": part.id, "name": part.name}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
