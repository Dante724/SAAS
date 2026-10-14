"""Industry templates — Phase 2 API.

Endpoints that let a shop pick its industry template and curate its own
per-template asset directories (groups / brands / models / identifier rules),
plus a template-aware ``scan-resolve`` that picks the right lookup for the
selected template+group instead of trying every kind on every scan.

Isolation is enforced on every write: rows are always scoped to
(org_id, template_key), and lookups never cross a template.
"""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    AssetBrand,
    AssetGroup,
    AssetIdentifierRule,
    AssetModel,
    Client,
    Device,
    Employee,
    Organisation,
)
from backend.services import industry as I
from backend.services.auth import get_current_employee, require_perm
from backend.services.lookups import decode_hin, decode_imei, decode_vin

router = APIRouter(prefix="/api/settings", tags=["settings"])
scan_router = APIRouter(prefix="/api/assets", tags=["assets"])


# ── helpers ──────────────────────────────────────────────────────────────────
async def _org(db: AsyncSession, org_id: str) -> Organisation:
    o = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Company not found")
    return o


async def _current_template(db: AsyncSession, org_id: str) -> str:
    o = await _org(db, org_id)
    return I.normalize_template_key(o.industry_template)


async def _group(db, org_id, group_id) -> Optional[AssetGroup]:
    if not group_id:
        return None
    g = (await db.execute(select(AssetGroup).where(
        AssetGroup.id == group_id, AssetGroup.org_id == org_id))).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Asset group not found")
    return g


def _grp_json(g: AssetGroup) -> dict:
    return {"id": g.id, "template_key": g.template_key, "key": g.key, "label": g.label,
            "sort_order": g.sort_order, "is_system": g.is_system, "active": g.active}


def _brand_json(b: AssetBrand) -> dict:
    return {"id": b.id, "template_key": b.template_key, "asset_group_id": b.asset_group_id,
            "name": b.name, "is_system": b.is_system, "sort_order": b.sort_order, "active": b.active}


def _model_json(m: AssetModel) -> dict:
    return {"id": m.id, "template_key": m.template_key, "brand_id": m.brand_id,
            "asset_group_id": m.asset_group_id, "name": m.name, "year_from": m.year_from,
            "year_to": m.year_to, "is_system": m.is_system, "sort_order": m.sort_order, "active": m.active}


# ── industry template (get/set) ──────────────────────────────────────────────
@router.get("/industry-template")
async def get_industry_template(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Current template + full descriptor (labels/fields/groups/scan rule) for the
    preview, plus the registry list for the picker. Seeds the active template's
    defaults on first read."""
    tk = await _current_template(db, current.org_id)
    await I.ensure_industry_seed(db, current.org_id, only_template=tk)
    return {
        "current": tk,
        "describe": I.describe(tk),
        "templates": I.template_list(),
    }


class TemplateIn(BaseModel):
    template_key: str


@router.get("/industry-template/describe")
async def describe_industry_template(
    template_key: str,
    current: Employee = Depends(get_current_employee),
):
    """Read-only descriptor (labels/fields/groups/scan rule) for ANY template, so
    the Settings picker can preview what a template changes before switching."""
    return I.describe(template_key)


@router.put("/industry-template")
async def set_industry_template(
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Switch the org's active template. Existing assets/orders keep their own
    template_key (stamped at creation); only *new* records use the new one."""
    if data.template_key not in I.TEMPLATES:
        raise HTTPException(422, f"Unknown template. Valid: {I.TEMPLATE_KEYS}")
    o = await _org(db, current.org_id)
    o.industry_template = data.template_key
    await db.commit()
    created = await I.ensure_industry_seed(db, current.org_id, only_template=data.template_key)
    return {"ok": True, "current": data.template_key, "seeded": created,
            "note": "Existing assets and work orders keep their original template."}


# ── asset groups ─────────────────────────────────────────────────────────────
@router.get("/asset-groups")
async def list_groups(
    template_key: Optional[str] = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    tk = I.normalize_template_key(template_key) if template_key else await _current_template(db, current.org_id)
    await I.ensure_industry_seed(db, current.org_id, only_template=tk)
    q = select(AssetGroup).where(AssetGroup.org_id == current.org_id, AssetGroup.template_key == tk)
    if not include_inactive:
        q = q.where(AssetGroup.active == True)  # noqa: E712
    rows = (await db.execute(q.order_by(AssetGroup.sort_order, AssetGroup.label))).scalars().all()
    return {"template_key": tk, "items": [_grp_json(g) for g in rows]}


class GroupIn(BaseModel):
    template_key: str
    key: str
    label: str
    sort_order: int = 0


@router.post("/asset-groups", status_code=201)
async def create_group(
    data: GroupIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    tk = I.normalize_template_key(data.template_key)
    key = data.key.strip().lower().replace(" ", "_")
    dup = (await db.execute(select(AssetGroup).where(
        AssetGroup.org_id == current.org_id, AssetGroup.template_key == tk,
        AssetGroup.key == key))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A group with this key already exists for this template")
    g = AssetGroup(org_id=current.org_id, template_key=tk, key=key,
                   label=data.label.strip(), sort_order=data.sort_order)
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return _grp_json(g)


class GroupPatch(BaseModel):
    label: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@router.put("/asset-groups/{group_id}")
async def update_group(
    group_id: str, data: GroupPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    g = await _group(db, current.org_id, group_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    await db.commit()
    return _grp_json(g)


@router.delete("/asset-groups/{group_id}")
async def delete_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    g = await _group(db, current.org_id, group_id)
    g.active = False  # soft-delete: assets may still reference it
    await db.commit()
    return {"ok": True}


# ── asset brands ─────────────────────────────────────────────────────────────
@router.get("/asset-brands")
async def list_brands(
    template_key: Optional[str] = None,
    asset_group_id: Optional[str] = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    tk = I.normalize_template_key(template_key) if template_key else await _current_template(db, current.org_id)
    await I.ensure_industry_seed(db, current.org_id, only_template=tk)
    q = select(AssetBrand).where(AssetBrand.org_id == current.org_id, AssetBrand.template_key == tk)
    if asset_group_id:
        # brands scoped to this group OR template-wide (null group)
        q = q.where(or_(AssetBrand.asset_group_id == asset_group_id, AssetBrand.asset_group_id.is_(None)))
    if not include_inactive:
        q = q.where(AssetBrand.active == True)  # noqa: E712
    rows = (await db.execute(q.order_by(AssetBrand.sort_order, AssetBrand.name))).scalars().all()
    return {"template_key": tk, "items": [_brand_json(b) for b in rows]}


class BrandIn(BaseModel):
    template_key: str
    name: str
    asset_group_id: Optional[str] = None
    sort_order: int = 0


@router.post("/asset-brands", status_code=201)
async def create_brand(
    data: BrandIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    tk = I.normalize_template_key(data.template_key)
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Brand name required")
    await _group(db, current.org_id, data.asset_group_id)
    # app-level dedupe (nullable group makes the DB UNIQUE NULL-distinct)
    dup = (await db.execute(select(AssetBrand).where(
        AssetBrand.org_id == current.org_id, AssetBrand.template_key == tk,
        AssetBrand.name.ilike(name),
        (AssetBrand.asset_group_id == data.asset_group_id) if data.asset_group_id
        else AssetBrand.asset_group_id.is_(None)))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "That brand already exists for this template/group")
    b = AssetBrand(org_id=current.org_id, template_key=tk, asset_group_id=data.asset_group_id,
                   name=name, sort_order=data.sort_order)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return _brand_json(b)


class BrandPatch(BaseModel):
    name: Optional[str] = None
    asset_group_id: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@router.put("/asset-brands/{brand_id}")
async def update_brand(
    brand_id: str, data: BrandPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    b = (await db.execute(select(AssetBrand).where(
        AssetBrand.id == brand_id, AssetBrand.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Brand not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(b, k, v.strip() if k == "name" and v else v)
    await db.commit()
    return _brand_json(b)


@router.delete("/asset-brands/{brand_id}")
async def delete_brand(
    brand_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    b = (await db.execute(select(AssetBrand).where(
        AssetBrand.id == brand_id, AssetBrand.org_id == current.org_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Brand not found")
    b.active = False
    # also deactivate its models
    for m in (await db.execute(select(AssetModel).where(
            AssetModel.org_id == current.org_id, AssetModel.brand_id == brand_id))).scalars().all():
        m.active = False
    await db.commit()
    return {"ok": True}


# ── asset models ─────────────────────────────────────────────────────────────
@router.get("/asset-models")
async def list_models(
    template_key: Optional[str] = None,
    brand_id: Optional[str] = None,
    asset_group_id: Optional[str] = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    tk = I.normalize_template_key(template_key) if template_key else await _current_template(db, current.org_id)
    q = select(AssetModel).where(AssetModel.org_id == current.org_id, AssetModel.template_key == tk)
    if brand_id:
        q = q.where(AssetModel.brand_id == brand_id)
    if asset_group_id:
        q = q.where(or_(AssetModel.asset_group_id == asset_group_id, AssetModel.asset_group_id.is_(None)))
    if not include_inactive:
        q = q.where(AssetModel.active == True)  # noqa: E712
    rows = (await db.execute(q.order_by(AssetModel.sort_order, AssetModel.name))).scalars().all()
    return {"template_key": tk, "items": [_model_json(m) for m in rows]}


class ModelIn(BaseModel):
    template_key: str
    brand_id: str
    name: str
    asset_group_id: Optional[str] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    sort_order: int = 0


@router.post("/asset-models", status_code=201)
async def create_model(
    data: ModelIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    tk = I.normalize_template_key(data.template_key)
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Model name required")
    brand = (await db.execute(select(AssetBrand).where(
        AssetBrand.id == data.brand_id, AssetBrand.org_id == current.org_id,
        AssetBrand.template_key == tk))).scalar_one_or_none()
    if not brand:
        raise HTTPException(404, "Brand not found in this template")
    await _group(db, current.org_id, data.asset_group_id)
    dup = (await db.execute(select(AssetModel).where(
        AssetModel.org_id == current.org_id, AssetModel.template_key == tk,
        AssetModel.brand_id == data.brand_id, AssetModel.name.ilike(name)))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "That model already exists for this brand")
    m = AssetModel(org_id=current.org_id, template_key=tk, brand_id=data.brand_id,
                   asset_group_id=data.asset_group_id, name=name,
                   year_from=data.year_from, year_to=data.year_to, sort_order=data.sort_order)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _model_json(m)


class ModelPatch(BaseModel):
    name: Optional[str] = None
    asset_group_id: Optional[str] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@router.put("/asset-models/{model_id}")
async def update_model(
    model_id: str, data: ModelPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    m = (await db.execute(select(AssetModel).where(
        AssetModel.id == model_id, AssetModel.org_id == current.org_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Model not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(m, k, v.strip() if k == "name" and v else v)
    await db.commit()
    return _model_json(m)


@router.delete("/asset-models/{model_id}")
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    m = (await db.execute(select(AssetModel).where(
        AssetModel.id == model_id, AssetModel.org_id == current.org_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Model not found")
    m.active = False
    await db.commit()
    return {"ok": True}


# ── identifier rules (read) ──────────────────────────────────────────────────
@router.get("/asset-identifier-rules")
async def list_identifier_rules(
    template_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    tk = I.normalize_template_key(template_key) if template_key else await _current_template(db, current.org_id)
    await I.ensure_industry_seed(db, current.org_id, only_template=tk)
    rows = (await db.execute(select(AssetIdentifierRule).where(
        AssetIdentifierRule.org_id == current.org_id,
        AssetIdentifierRule.template_key == tk))).scalars().all()
    return {"template_key": tk, "items": [
        {"id": r.id, "asset_group_id": r.asset_group_id, "primary_kind": r.primary_kind,
         "allowed_kinds": r.allowed_kinds or []} for r in rows]}


# ── CSV import (brands + models) ─────────────────────────────────────────────
class CsvImportIn(BaseModel):
    template_key: str
    csv: str  # header row required: brand[,model][,group]


@router.post("/asset-directory/import")
async def import_directory_csv(
    data: CsvImportIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Bulk-load brands (and optional models) for one template from CSV text.
    Header must include ``brand``; optional ``model`` and ``group`` (group key or
    label). Everything is scoped to the given template — never another."""
    tk = I.normalize_template_key(data.template_key)
    reader = csv.DictReader(io.StringIO(data.csv.strip()))
    if not reader.fieldnames or "brand" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(422, "CSV must have a 'brand' column")

    # existing group lookup by key OR label (for optional 'group' column)
    groups = (await db.execute(select(AssetGroup).where(
        AssetGroup.org_id == current.org_id, AssetGroup.template_key == tk))).scalars().all()
    gmap = {}
    for g in groups:
        gmap[g.key.lower()] = g.id
        gmap[g.label.lower()] = g.id

    made = {"brands": 0, "models": 0}
    brand_cache: dict[tuple, AssetBrand] = {}
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        bname = row.get("brand")
        if not bname:
            continue
        gid = gmap.get(row.get("group", "").lower()) if row.get("group") else None
        ck = (bname.lower(), gid)
        brand = brand_cache.get(ck)
        if not brand:
            brand = (await db.execute(select(AssetBrand).where(
                AssetBrand.org_id == current.org_id, AssetBrand.template_key == tk,
                AssetBrand.name.ilike(bname),
                (AssetBrand.asset_group_id == gid) if gid else AssetBrand.asset_group_id.is_(None)
            ))).scalar_one_or_none()
            if not brand:
                brand = AssetBrand(org_id=current.org_id, template_key=tk, asset_group_id=gid, name=bname)
                db.add(brand)
                await db.flush()
                made["brands"] += 1
            brand_cache[ck] = brand
        mname = row.get("model")
        if mname:
            exists = (await db.execute(select(AssetModel).where(
                AssetModel.org_id == current.org_id, AssetModel.template_key == tk,
                AssetModel.brand_id == brand.id, AssetModel.name.ilike(mname)))).scalar_one_or_none()
            if not exists:
                db.add(AssetModel(org_id=current.org_id, template_key=tk, brand_id=brand.id,
                                  asset_group_id=gid, name=mname))
                made["models"] += 1
    await db.commit()
    return {"ok": True, "template_key": tk, **made}


# ── scan-resolve (template-aware) ────────────────────────────────────────────
_DECODE_FIELD = {"vin": "vin", "hin": "hin", "imei": "imei"}  # kinds with a Device column + decoder


async def _match_existing(db, org_id, kind, value) -> Optional[Device]:
    """Find an existing asset by identifier, scoped to this org's assets."""
    client_ids = (await db.execute(select(Client.id).where(Client.org_id == org_id))).scalars().all()
    base = or_(Device.client_id.in_(client_ids), Device.owner_type == "company")
    col = {"vin": Device.vin, "hin": Device.hin, "imei": Device.imei}.get(kind, Device.serial)
    return (await db.execute(select(Device).where(base, col == value))).scalars().first()


class ScanIn(BaseModel):
    identifier: str
    template_key: Optional[str] = None
    asset_group_id: Optional[str] = None
    group_key: Optional[str] = None


@scan_router.post("/scan-resolve")
async def scan_resolve(
    data: ScanIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Resolve a scanned identifier using ONLY the lookup(s) valid for the
    selected template + group. Returns an existing asset (autofill) when matched,
    or a structural decode (prefill) when it is a valid-but-new identifier."""
    value = (data.identifier or "").strip()
    if not value:
        raise HTTPException(422, "identifier required")
    tk = I.normalize_template_key(data.template_key) if data.template_key else await _current_template(db, current.org_id)

    group_key = data.group_key
    if data.asset_group_id and not group_key:
        g = await _group(db, current.org_id, data.asset_group_id)
        group_key = g.key if g else None

    rule = I.identifier_rule(tk, group_key)
    kind = rule["primary"]

    # 1) existing asset?
    existing = await _match_existing(db, current.org_id, kind, value)
    if existing:
        owner = None
        if existing.client_id:
            c = (await db.execute(select(Client).where(Client.id == existing.client_id))).scalar_one_or_none()
            owner = {"id": c.id, "name": c.name, "phone": c.phone, "email": c.email} if c else None
        return {
            "kind": kind, "identifier": value, "template_key": tk, "matched": True,
            "asset": {"id": existing.id, "asset_type": existing.asset_type,
                      "brand": existing.brand, "model": existing.model,
                      "serial": existing.serial, "imei": existing.imei,
                      "vin": existing.vin, "hin": existing.hin,
                      "template_key": existing.template_key, "asset_group_id": existing.asset_group_id},
            "client": owner,
        }

    # 2) valid-but-new: only decode for the template's kind (never all kinds)
    decoded, valid, prefill = None, None, {}
    if kind == "vin":
        decoded = await decode_vin(value)
        valid = decoded.get("valid")
        if valid:
            prefill = {k: decoded.get(k) for k in ("make", "model", "model_year") if decoded.get(k)}
            if decoded.get("model_year"):
                prefill["year"] = decoded["model_year"]
    elif kind == "hin":
        decoded = decode_hin(value)
        valid = decoded.get("valid")
        if valid and decoded.get("manufacturer"):
            prefill = {"make": decoded["manufacturer"], "year": decoded.get("model_year")}
    elif kind == "imei":
        decoded = await decode_imei(value)
        valid = decoded.get("valid")
    else:
        # serial / asset_tag / barcode / engine_serial — no external decode, always accept
        valid = True

    return {
        "kind": kind, "identifier": value, "template_key": tk, "matched": False,
        "valid": bool(valid), "decoded": decoded, "prefill": prefill,
        "field_key": {"vin": "vin", "hin": "hin", "imei": "imei"}.get(kind, "serial"),
    }
