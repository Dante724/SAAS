"""
Assets / Asset Warehouse.

Assets are client devices (or company-owned tools/equipment). Each carries a
mandatory IMEI/Serial — which can be auto-generated when no real number exists.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Client, Device, Employee, Order
from backend.services import asset_catalog as _cat
from backend.services import identifiers as _ids
from backend.services import industry as _ind
from backend.core import paging as _paging
from backend.services.auth import get_current_employee, has_perm, require_perm

router = APIRouter(prefix="/api/assets", tags=["assets"])

# Where a vehicle's plate lives in custom_data, depending on the template / form it was made with.
PLATE_KEYS = ("plate", "number_plate", "plate_registration", "registration")
ASSET_STATES = ("active", "written_off", "all")


def _like(term: str) -> str:
    """A contains-pattern with LIKE wildcards in the user's text taken literally."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _compact(value: str) -> str:
    """How plates and identifiers are compared: no spaces or dashes, upper case."""
    return re.sub(r"[\s\-]+", "", value or "").upper()


def _plate_col(key: str):
    return func.upper(func.replace(func.replace(Device.custom_data[key].as_string(), " ", ""), "-", ""))


def _search_conds(org_id: str, search: str) -> list:
    """21 — one box finds an asset by brand / model / owner or any identifier: serial, IMEI,
    VIN, HIN or licence plate, typed with or without spaces."""
    t = search.strip()
    pat, comp = _like(t), _compact(t)
    conds = [col.ilike(pat, escape="\\") for col in
             (Device.serial, Device.imei, Device.vin, Device.hin, Device.brand, Device.model, Device.asset_type)]
    conds.append(Device.client_id.in_(select(Client.id).where(
        Client.org_id == org_id, Client.name.ilike(pat, escape="\\"))))
    if comp:
        cpat = _like(comp)
        if comp != t:
            conds += [col.ilike(cpat, escape="\\") for col in (Device.serial, Device.imei, Device.vin, Device.hin)]
        conds += [_plate_col(k).like(cpat, escape="\\") for k in PLATE_KEYS]
    return conds


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


async def _hide_cost(db, current, *rows) -> None:
    """Without "See an asset cost" an asset's purchase price stays out of the response."""
    if not await has_perm(db, current, "can_see_asset_cost"):
        for r in rows:
            r.pop("purchase_price", None)


async def _gen_serial(db, org_id: str, asset_type: str = "Device", *, peek: bool = False) -> str:
    """ii144a: the next asset serial from Settings › Inventory's pattern (default {T}-{C:6}, {T} = the
    first three letters of the asset type), skipping any serial already on one of the shop's assets.
    `peek` shows the next one without using it up (the form's suggestion)."""
    from backend.models import NumberSequence, Organisation
    from backend.services import numbering
    tpl = (await db.execute(select(Organisation.inv_asset_serial_template).where(
        Organisation.id == org_id))).scalar_one_or_none()
    tpl = (tpl or "").strip() or numbering.CODE_DEFAULTS["asset_serial"]
    tokens = {"T": numbering.code_word(asset_type, "AST")}

    async def taken(codes: set) -> set:
        return set((await db.execute(select(Device.serial).where(
            Device.org_id == org_id, Device.serial.in_(codes)))).scalars().all())
    if peek:
        nxt = (await db.execute(select(NumberSequence.next_value).where(
            NumberSequence.org_id == org_id, NumberSequence.doc_type == "asset_serial",
            NumberSequence.scope_key == "org"))).scalar_one_or_none() or 1
        cands = [numbering.render(tpl, c, tokens=tokens) for c in range(nxt, nxt + 50)]
        used = await taken(set(cands))       # one query
        free = next((c for c in cands if c not in used), None)
        if free:
            return free
        return cands[-1]                     # only a preview; creating the asset still skips taken serials
    return await numbering.next_code(db, org_id, "asset_serial", tpl, tokens=tokens, taken=taken)


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
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The serial a new asset would get (from the shop's pattern). Nothing is used up."""
    return {"serial": await _gen_serial(db, current.org_id, asset_type, peek=True)}


@router.get("")
async def list_assets(
    owner_type: Optional[str] = None,
    asset_type: Optional[str] = None,
    search: Optional[str] = Query(None, max_length=200),
    state: Optional[str] = None,          # 19: active | written_off | all (none = all, as before)
    skip: int = _paging.skip_query(),
    limit: int = _paging.limit_query(200),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_asset_info")),
):
    # Build 144do (security): scope strictly to this org. Previously company-owned
    # assets from ALL orgs leaked into every org's list via `owner_type == "company"`.
    # Batch 2: one page at a time with the real total; owner names and order counts are
    # batched for the page (it used to load every client of the shop and count per row),
    # and the photo is a URL, not inline base64.
    from sqlalchemy.orm import defer
    limit = _paging.clamp(limit)
    if state and state not in ASSET_STATES:
        raise HTTPException(422, "state must be active, written_off or all")
    conds = [Device.org_id == current.org_id]
    if owner_type:
        conds.append(Device.owner_type == owner_type)
    if asset_type:
        conds.append(Device.asset_type == asset_type)
    if search and search.strip():
        conds.append(or_(*_search_conds(current.org_id, search)))
    # 19: counts for the Active / Written off tabs follow every filter except the tab itself.
    written_off = Device.asset_status == "written_off"
    n_all = (await db.execute(select(func.count(Device.id)).where(*conds))).scalar() or 0
    n_wo = (await db.execute(select(func.count(Device.id)).where(*conds, written_off))).scalar() or 0
    state_counts = {"active": n_all - n_wo, "written_off": n_wo, "all": n_all}
    q = select(Device).where(*conds)
    if state == "active":
        q = q.where(or_(Device.asset_status.is_(None), Device.asset_status != "written_off"))
    elif state == "written_off":
        q = q.where(written_off)
    total = {"active": state_counts["active"], "written_off": n_wo}.get(state, n_all)
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
    await _hide_cost(db, current, *out)
    return {"total": total, "items": out, "skip": skip, "limit": limit, "counts": state_counts}


@router.post("", status_code=201)
async def create_asset(
    data: AssetIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.owner_type == "client" and not data.client_id:
        raise HTTPException(400, "Client is required for a client-owned asset")
    if not await has_perm(db, current, "can_see_asset_cost"):
        data.purchase_price = 0.0
    from backend.routers.forms import check_entity_required
    await check_entity_required(db, current.org_id, "device", data)   # built-ins the device form requires
    serial = data.serial
    generated = False
    if not serial and not data.imei and not data.vin and not data.hin:
        serial = await _gen_serial(db, current.org_id, data.asset_type)
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
    res = _ser(d, 0)
    await _hide_cost(db, current, res)
    return res


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
    value: str = Field(max_length=200)
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
    # 21: a vehicle can be found by its plate too (kept in custom_data)
    plate = _compact(data.value)
    if plate:
        conds += [_plate_col(k) == plate for k in PLATE_KEYS]
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
        matched_on = next((k for k in _ids.KINDS if candidates.get(k) and getattr(d, k) == candidates[k]), "plate")
        matches.append({
            "id": d.id, "matched_on": matched_on,
            "brand": d.brand, "model": d.model, "serial": d.serial, "imei": d.imei,
            "vin": d.vin, "hin": d.hin, "color": d.color, "watch_out": d.watch_out,
            "client": owner, "owner_type": d.owner_type,
            "same_client": bool(data.client_id and d.client_id == data.client_id),
            "jobs": jobs, "last_order": last_out})
    return {"kind": kind, "normalized": norm, "problem": _ids.problem(kind, norm), "matches": matches}


@router.get("/{asset_id}")
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_asset_info")),
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
    await _hide_cost(db, current, res)
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
    if not await has_perm(db, current, "can_see_asset_cost"):
        payload.pop("purchase_price", None)          # a cost they never saw stays as it was
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
    res = _ser(d, order_count, owner_name)
    await _hide_cost(db, current, res)
    return res


@router.get("/{asset_id}/movements")
async def asset_movements(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_asset_info")),
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
    from backend.models import Location, Part, StockMovement
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
    from backend.services import product_codes, stock_rows
    await product_codes.ensure_sku_free(db, current.org_id, part.sku)
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
        await stock_rows.add(db, part.id, loc_id, 1)
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
