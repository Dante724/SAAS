"""Brand / model names: one spelling per shop, learned from what staff type.

Free text at the counter turned one phone into "Samsung", "samsung" and "SAMSUNG " — reports
split, search missed. On every asset save the typed brand/model snaps to the existing spelling
(the shop's catalog, then the built-in list) and anything new is remembered in the shop's
catalog so it is suggested next time. No setup screen required.
"""
from typing import Optional

from sqlalchemy import func, select

from backend.services import industry as I


def _key(v: str) -> str:
    return " ".join((v or "").split()).lower()


def _builtin_brands() -> dict[str, str]:
    out: dict[str, str] = {}
    for names in I.STARTER_BRANDS.values():
        for n in names:
            out.setdefault(_key(n), n)
    return out


def _builtin_models(brand: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for per in I.STARTER_MODELS.values():
        for b, models in per.items():
            if _key(b) == _key(brand):
                for m in models:
                    out.setdefault(_key(m), m)
    return out


async def canonical(db, org_id: str, template_key: Optional[str], brand: Optional[str],
                    model: Optional[str], *, learn: bool = True) -> tuple[Optional[str], Optional[str]]:
    """Return (brand, model) in their canonical spelling; remember new ones when ``learn``."""
    from backend.models import AssetBrand, AssetModel
    brand = " ".join((brand or "").split()) or None
    model = " ".join((model or "").split()) or None
    if not brand:
        return None, model
    tk = I.normalize_template_key(template_key)

    row = (await db.execute(select(AssetBrand).where(
        AssetBrand.org_id == org_id, func.lower(AssetBrand.name) == _key(brand))
        .order_by(AssetBrand.template_key != tk))).scalars().first()
    if row:
        brand = row.name
    else:
        brand = _builtin_brands().get(_key(brand), brand)
    if learn:
        here = (await db.execute(select(AssetBrand).where(
            AssetBrand.org_id == org_id, AssetBrand.template_key == tk,
            AssetBrand.asset_group_id.is_(None), func.lower(AssetBrand.name) == _key(brand)))).scalars().first()
        if not here:
            here = AssetBrand(org_id=org_id, template_key=tk, asset_group_id=None, name=brand, is_system=False)
            db.add(here)
            await db.flush()
        row = here

    if model:
        m = None
        if row is not None:
            m = (await db.execute(select(AssetModel).where(
                AssetModel.org_id == org_id, AssetModel.brand_id == row.id,
                func.lower(AssetModel.name) == _key(model)))).scalars().first()
        if m:
            model = m.name
        else:
            model = _builtin_models(brand).get(_key(model), model)
            if learn and row is not None:
                db.add(AssetModel(org_id=org_id, template_key=row.template_key, brand_id=row.id,
                                  name=model, is_system=False))
                await db.flush()
    return brand, model


async def suggest(db, org_id: str, field: str, q: str = "", brand: Optional[str] = None, limit: int = 30) -> list[str]:
    """Names for a brand or model picker: what this shop already uses (most-used first), then
    its catalog, then the built-in list. Case-insensitive de-duplication."""
    from backend.models import AssetBrand, AssetModel, Device
    seen: dict[str, str] = {}

    def add(names):
        for n in names:
            if n and _key(n) not in seen and (not q or _key(q) in _key(n)):
                seen[_key(n)] = n

    if field == "brand":
        used = (await db.execute(select(Device.brand, func.count()).where(
            Device.org_id == org_id, Device.brand.is_not(None)).group_by(Device.brand)
            .order_by(func.count().desc()))).all()
        add([b for b, _ in used])
        add([b for (b,) in (await db.execute(select(AssetBrand.name).where(
            AssetBrand.org_id == org_id, AssetBrand.active == True))).all()])  # noqa: E712
        add(sorted(_builtin_brands().values(), key=str.lower))
    else:
        if not brand:
            return []
        used = (await db.execute(select(Device.model, func.count()).where(
            Device.org_id == org_id, func.lower(Device.brand) == _key(brand), Device.model.is_not(None))
            .group_by(Device.model).order_by(func.count().desc()))).all()
        add([m for m, _ in used])
        add([m for (m,) in (await db.execute(select(AssetModel.name).join(
            AssetBrand, AssetBrand.id == AssetModel.brand_id).where(
            AssetModel.org_id == org_id, func.lower(AssetBrand.name) == _key(brand),
            AssetModel.active == True))).all()])  # noqa: E712
        add(list(_builtin_models(brand).values()))
    return list(seen.values())[:limit]
