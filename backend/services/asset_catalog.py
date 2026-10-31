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


def _builtin_models(brand: str, prefer: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    tks = list(prefer) + [k for k in I.STARTER_MODELS if k not in prefer]
    for per in (I.STARTER_MODELS.get(k, {}) for k in tks):
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


# Words in a shop's own group names that point at a template's built-in brands, for groups the
# templates don't define ("Drill", "Mower", "Phones"). First hit wins.
_GROUP_HINTS: list[tuple[str, str]] = [
    ("tool", "tools"), ("drill", "tools"), ("saw", "tools"), ("grinder", "tools"), ("compressor", "tools"),
    ("mower", "small_engine"), ("generator", "small_engine"), ("trimmer", "small_engine"), ("blower", "small_engine"),
    ("phone", "mobile_phone"), ("tablet", "mobile_phone"), ("watch", "mobile_phone"),
    ("laptop", "computer"), ("computer", "computer"), ("desktop", "computer"), ("printer", "computer"),
    ("bicycle", "bike"), ("bike", "bike"), ("scooter", "bike"),
    ("boat", "marine"), ("outboard", "marine"), ("vessel", "marine"),
    ("atv", "powersports"), ("utv", "powersports"), ("snowmobile", "powersports"),
    ("car", "auto"), ("suv", "auto"), ("vehicle", "auto"),
]


def fit_templates(group: Optional[str], template_key: Optional[str] = None) -> tuple[str, ...]:
    """Templates whose built-in brands fit an asset group, best first. A group the templates
    define ("Power Tool" / "power_tool") maps to every template that has it, the shop's own
    industry first (Truck is auto and fleet). Unknown groups go by name hints, then fall back
    to the shop's industry. Empty when nothing is known — callers keep the unranked list."""
    tk = I.normalize_template_key(template_key) if template_key else None
    g = _key((group or "").replace("_", " "))
    out: list[str] = []
    if g:
        for k, t in I.TEMPLATES.items():
            if k == "general":
                continue   # Device / Equipment / Other say nothing about the brand
            if any(g in (_key(x["key"].replace("_", " ")), _key(x["label"])) for x in t["groups"]):
                out.append(k)
        if not out:
            words = g.replace("-", " ").split()
            out = next(([k] for hint, k in _GROUP_HINTS
                        if any(w == hint or w == hint + "s" for w in words)), [])
        if tk in out:
            out.remove(tk)
            out.insert(0, tk)
    if not out and tk and tk != "general":
        out = [tk]
    return tuple(out)


async def suggest(db, org_id: str, field: str, q: str = "", brand: Optional[str] = None, limit: int = 30,
                  group: Optional[str] = None, template_key: Optional[str] = None) -> list[str]:
    """Names for a brand or model picker: what this shop already uses (most-used first), then
    its catalog, then the built-in list. Case-insensitive de-duplication.

    With an asset ``group`` (and the shop's industry ``template_key``) the names that fit come
    first — the shop's own names for that group, then its other names that fit, then the
    built-in brands for it (power tools: DeWalt, Makita, Milwaukee…) — and everything else
    after, so a tool shop isn't offered Apple and Samsung for a drill."""
    from backend.models import AssetBrand, AssetModel, Device
    seen: dict[str, str] = {}
    fit = fit_templates(group, template_key)
    g = _key(group or "")

    def add(names):
        for n in names:
            if n and _key(n) not in seen and (not q or _key(q) in _key(n)):
                seen[_key(n)] = n

    if field == "brand":
        used = [b for b, _ in (await db.execute(select(Device.brand, func.count()).where(
            Device.org_id == org_id, Device.brand.is_not(None)).group_by(Device.brand)
            .order_by(func.count().desc()))).all()]
        catalog = (await db.execute(select(AssetBrand.name, AssetBrand.template_key).where(
            AssetBrand.org_id == org_id, AssetBrand.active == True)  # noqa: E712
            .order_by(AssetBrand.sort_order))).all()
        builtin = sorted(_builtin_brands().values(), key=str.lower)
        if fit:
            fit_builtin = [n for k in fit for n in I.STARTER_BRANDS.get(k, [])]
            fit_keys = {_key(n) for n in fit_builtin} | {_key(n) for n, tk in catalog if tk in fit}
            if g:                                                       # this shop, this group
                add([b for b, _ in (await db.execute(select(Device.brand, func.count()).where(
                    Device.org_id == org_id, Device.brand.is_not(None), func.lower(Device.asset_type) == g)
                    .group_by(Device.brand).order_by(func.count().desc()))).all()])
            add([b for b in used if _key(b) in fit_keys])               # this shop, fits the group
            add([n for n, tk in catalog if tk in fit])
            add(fit_builtin)
        add(used)
        add([n for n, _ in catalog])
        add(builtin)
    else:
        if not brand:
            return []
        if g:                                                           # this shop, this group
            add([m for m, _ in (await db.execute(select(Device.model, func.count()).where(
                Device.org_id == org_id, func.lower(Device.brand) == _key(brand), Device.model.is_not(None),
                func.lower(Device.asset_type) == g).group_by(Device.model)
                .order_by(func.count().desc()))).all()])
        used = (await db.execute(select(Device.model, func.count()).where(
            Device.org_id == org_id, func.lower(Device.brand) == _key(brand), Device.model.is_not(None))
            .group_by(Device.model).order_by(func.count().desc()))).all()
        add([m for m, _ in used])
        add([m for (m,) in (await db.execute(select(AssetModel.name).join(
            AssetBrand, AssetBrand.id == AssetModel.brand_id).where(
            AssetModel.org_id == org_id, func.lower(AssetBrand.name) == _key(brand),
            AssetModel.active == True))).all()])  # noqa: E712
        add(list(_builtin_models(brand, fit).values()))
    return list(seen.values())[:limit]
