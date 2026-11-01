"""Product identity: barcodes, SKU and the shop's chosen unique identifier (ii144a).

- `product_barcodes` holds every barcode a product answers to. `parts.barcode` stays the *main*
  barcode (labels, exports, the API) and is mirrored into the table by a before-flush hook, so every
  path that writes a product — catalog, quick add, imports, sales "register unknown", AI intake —
  stays in sync without each one remembering to.
- `ensure_*` give a plain 409 before the database has to; the unique indexes are the real guarantee
  and main.py maps a clash that slips past (two saves at once) to the same kind of 409.
- `matches_code` is the one SQL condition every scan path uses: SKU, main or extra barcode, code.
  ii144b: a scan is first read by services/scan_codes.py (AIM prefix stripped, GS1 parsed) and barcodes
  are found by match key, so UPC-A / EAN-13 / GTIN-14 / UPC-E forms of one GTIN find the same product.
Everything is scoped to the shop: another shop's codes are never blocked or revealed.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, event, func, inspect, or_, select, update
from sqlalchemy.orm import Session

from backend.models import Organisation, Part, ProductBarcode
from backend.services import scan_codes

IDENTIFIERS = ("name", "sku", "barcode", "code")


def clean(value) -> Optional[str]:
    v = str(value).strip() if value is not None else ""
    return v or None


def barcode_part_ids(org_id: str, code: str):
    """Subquery: ids of the shop's products that answer to `code` as a main or extra barcode —
    in any of its forms (scan_codes.parse: GTIN equivalents, the GTIN inside a GS1 string)."""
    scan = scan_codes.parse(code)
    keys = scan.keys or [code]
    live = (ProductBarcode.org_id == org_id, ProductBarcode.active == True)  # noqa: E712
    cond = ProductBarcode.match_key.in_(keys)
    if scan.fallback_keys:      # an 8-digit code: the UPC-E reading only when nothing has the EAN-8 one
        from sqlalchemy import and_, exists
        pb = ProductBarcode.__table__.alias("pb_primary")
        cond = or_(cond, and_(ProductBarcode.match_key.in_(scan.fallback_keys), ~exists().where(
            pb.c.org_id == org_id, pb.c.active == True, pb.c.match_key.in_(keys))))  # noqa: E712
    return select(ProductBarcode.part_id).where(*live, cond)


def matches_code(org_id: str, code: str, *, include_mpn: bool = False):
    """SQL condition on Part: the scanned / typed `code` is this product's SKU, a barcode, or its code."""
    texts = scan_codes.parse(code).all_texts() or [code]
    conds = [Part.id.in_(barcode_part_ids(org_id, code))]
    # Barcodes (main ones too — they're mirrored into product_barcodes) are matched by key above, which
    # already honours the scanner's AIM id; SKU and code are compared as parsed and as sent.
    for text in texts:
        conds += [func.lower(Part.sku) == text.lower(), Part.code == text]   # SKUs: case-insensitive
        if include_mpn:
            conds.append(func.lower(Part.mpn) == text.lower())
    return or_(*conds)


async def find_by_code(db, org_id: str, code: str, *, active_only: bool = True) -> Optional[Part]:
    code = clean(code)
    if not code:
        return None
    q = select(Part).where(Part.org_id == org_id, matches_code(org_id, code))
    if active_only:
        q = q.where(Part.active == True)  # noqa: E712
    return (await db.execute(q)).scalars().first()


async def barcode_owner(db, org_id: str, code: str, *, exclude_part_id: Optional[str] = None,
                        kind: Optional[str] = None) -> Optional[Part]:
    key = scan_codes.match_key(code, kind)
    q = select(Part).join(ProductBarcode, ProductBarcode.part_id == Part.id).where(
        ProductBarcode.org_id == org_id, ProductBarcode.match_key == key, ProductBarcode.active == True)  # noqa: E712
    if exclude_part_id:
        q = q.where(Part.id != exclude_part_id)
    return (await db.execute(q)).scalars().first()


async def ensure_barcode_free(db, org_id: str, code: Optional[str], *, exclude_part_id: Optional[str] = None,
                              kind: Optional[str] = None) -> None:
    code = clean(code)
    if not code:
        return
    other = await barcode_owner(db, org_id, code, exclude_part_id=exclude_part_id, kind=kind)
    if other is not None:
        same = "" if other.barcode == code else " (the same product number in another barcode format)"
        raise HTTPException(409, f"The barcode {code} is already used by “{other.name}”{same}.")


async def ensure_sku_free(db, org_id: str, sku: Optional[str], *, exclude_part_id: Optional[str] = None) -> None:
    sku = clean(sku)
    if not sku:
        return
    q = select(Part).where(Part.org_id == org_id, Part.active == True,  # noqa: E712
                           func.lower(Part.sku) == sku.lower())
    if exclude_part_id:
        q = q.where(Part.id != exclude_part_id)
    other = (await db.execute(q)).scalars().first()
    if other is not None:
        raise HTTPException(409, f"The SKU {sku} is already used by “{other.name}”.")


async def unique_identifier(db, org_id: str) -> str:
    v = (await db.execute(select(Organisation.inv_unique_identifier).where(
        Organisation.id == org_id))).scalar_one_or_none()
    return v if v in IDENTIFIERS else "name"


_WORDS = {"name": "name", "code": "code", "sku": "SKU", "barcode": "barcode"}


async def ensure_identity_free(db, org_id: str, *, name=None, sku=None, barcode=None, code=None,
                               exclude_part_id: Optional[str] = None, fields: Optional[set] = None,
                               barcode_type: Optional[str] = None) -> None:
    """Every check a product save needs. SKU and barcode are always unique; the shop's chosen
    identifier adds name or code. `fields` limits the checks to what the caller is changing."""
    want = fields if fields is not None else {"name", "sku", "barcode", "code"}
    if "barcode" in want:
        await ensure_barcode_free(db, org_id, barcode, exclude_part_id=exclude_part_id, kind=barcode_type)
    if "sku" in want:
        await ensure_sku_free(db, org_id, sku, exclude_part_id=exclude_part_id)
    ident = await unique_identifier(db, org_id)
    value = clean({"name": name, "code": code}.get(ident))
    if ident in ("name", "code") and ident in want and value:
        col = Part.name if ident == "name" else Part.code
        q = select(Part).where(Part.org_id == org_id, Part.active == True,  # noqa: E712
                               func.lower(col) == value.lower())
        if exclude_part_id:
            q = q.where(Part.id != exclude_part_id)
        other = (await db.execute(q)).scalars().first()
        if other is not None:
            raise HTTPException(409, f"A product with the {_WORDS[ident]} “{value}” already exists "
                                     f"(products are unique by {_WORDS[ident]} — Settings › Inventory).")


async def match_for_import(db, org_id: str, *, name=None, sku=None, barcode=None, code=None) -> Optional[Part]:
    """The existing product an import row updates: by the shop's chosen identifier, or by name when
    the row leaves that column empty."""
    ident = await unique_identifier(db, org_id)
    value = clean({"name": name, "sku": sku, "barcode": barcode, "code": code}[ident])
    base = select(Part).where(Part.org_id == org_id, Part.active == True)  # noqa: E712
    if value:
        if ident == "barcode":
            return (await db.execute(base.where(Part.id.in_(barcode_part_ids(org_id, value))))).scalars().first()
        col = {"name": Part.name, "sku": Part.sku, "code": Part.code}[ident]
        return (await db.execute(base.where(func.lower(col) == value.lower()))).scalars().first()
    name = clean(name)
    if not name:
        return None
    return (await db.execute(base.where(Part.name == name))).scalars().first()


# ── keep product_barcodes in step with parts.barcode / parts.active ──────────────────────────────
@event.listens_for(Session, "before_flush")
def _sync_main_barcodes(session: Session, flush_context, instances) -> None:
    from backend.models import _uuid
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Part) or not obj.org_id:
            continue
        state = inspect(obj)
        is_new = obj in session.new
        code = clean(obj.barcode)
        active = obj.active is not False
        if is_new:
            if obj.id is None:
                obj.id = _uuid()
            if code:
                session.add(ProductBarcode(org_id=obj.org_id, part=obj, barcode=code, barcode_type=obj.barcode_type,
                                           match_key=scan_codes.match_key(code, obj.barcode_type),
                                           is_main=True, active=active))
            continue
        bc_changed = state.attrs.barcode.history.has_changes() or state.attrs.barcode_type.history.has_changes()
        act_changed = state.attrs.active.history.has_changes()
        if bc_changed:
            # Drop the old main row, and any extra row holding the new value (it becomes the main one).
            conds = [ProductBarcode.is_main == True]  # noqa: E712
            if code:
                conds.append(ProductBarcode.match_key == scan_codes.match_key(code, obj.barcode_type))
            session.execute(delete(ProductBarcode).where(ProductBarcode.part_id == obj.id, or_(*conds))
                            .execution_options(synchronize_session=False))
            if code:
                session.add(ProductBarcode(org_id=obj.org_id, part_id=obj.id, barcode=code,
                                           barcode_type=obj.barcode_type, is_main=True, active=active,
                                           match_key=scan_codes.match_key(code, obj.barcode_type)))
        if act_changed:
            session.execute(update(ProductBarcode).where(ProductBarcode.part_id == obj.id)
                            .values(active=active).execution_options(synchronize_session=False))


# ── generated codes (ii144a) ─────────────────────────────────────────────────────────────────────
async def _taken_barcodes(db, org_id: str, codes: set) -> set:
    if not codes:
        return set()
    keys = {scan_codes.match_key(c): c for c in codes}
    a = (await db.execute(select(ProductBarcode.match_key).where(
        ProductBarcode.org_id == org_id, ProductBarcode.match_key.in_(set(keys))))).scalars().all()
    b = (await db.execute(select(Part.barcode).where(Part.org_id == org_id, Part.barcode.in_(codes)))).scalars().all()
    return {keys[k] for k in a if k in keys} | set(b)


async def next_barcodes(db, org_id: str, n: int, kind: str = "ean13") -> list[str]:
    """`n` fresh in-store barcodes for the shop (EAN-13 20–29 by default), skipping any in use —
    including on an archived product, so a code printed on an old label never points somewhere new."""
    from backend.services import barcode_types, numbering
    if kind == "upce":
        raise HTTPException(409, "UPC-E codes come from the manufacturer. Scan or type the one on the box, "
                                 "or generate an EAN-13 instead.")
    gtin = kind if kind in ("ean13", "upca", "ean8") else "ean13"
    prefix = (await db.execute(select(Organisation.inv_barcode_prefix).where(
        Organisation.id == org_id))).scalar_one_or_none() or "20"
    if prefix not in numbering.IN_STORE_PREFIXES:
        prefix = "20"
    try:
        codes = await numbering.next_codes(
            db, org_id, f"barcode_{gtin}", n, render_fn=lambda c: numbering.in_store_gtin(gtin, prefix, c),
            taken=lambda codes: _taken_barcodes(db, org_id, codes))
        # ITF-14 / GS1-128 / 2D / Code 39 / Code 128 carry the in-store EAN-13 (same GTIN, same match key)
        return codes if gtin == kind else [barcode_types.generated_value(kind, c) for c in codes]
    except ValueError:      # the counter ran past what the format can hold (EAN-8: a million codes)
        raise HTTPException(409, "No more barcodes of this kind are free. Generate EAN-13 barcodes instead")


async def next_sku(db, org_id: str, template: str, category_name: Optional[str]) -> str:
    from backend.services import numbering

    async def taken(codes: set) -> set:
        rows = (await db.execute(select(func.lower(Part.sku)).where(
            Part.org_id == org_id, func.lower(Part.sku).in_({c.lower() for c in codes})))).scalars().all()
        return {c for c in codes if c.lower() in set(rows)}
    return await numbering.next_code(db, org_id, "sku", template,
                                     tokens={"CAT": numbering.code_word(category_name, "GEN")}, taken=taken)


async def fill_new_product(db, part: Part) -> None:
    """Before a new product is saved: give it a SKU from the shop's template when it has none.
    A typed SKU is never replaced; no template = SKUs stay as typed."""
    if clean(part.sku):
        return
    tpl = (await db.execute(select(Organisation.inv_sku_template).where(
        Organisation.id == part.org_id))).scalar_one_or_none()
    if not (tpl or "").strip():
        return
    cat = part.category
    if part.category_id:
        from backend.models import Category
        cat = (await db.execute(select(Category.name).where(
            Category.id == part.category_id, Category.org_id == part.org_id))).scalar_one_or_none() or cat
    part.sku = await next_sku(db, part.org_id, tpl, cat)


async def next_serials(db, org_id: str, n: int) -> list[str]:
    """`n` serials from the shop's product serial template, none already in stock in the shop."""
    from backend.models import SerializedItem
    from backend.services import numbering
    from backend.services.serial_intake import IN_STOCK, _canon, normalize
    tpl = (await db.execute(select(Organisation.inv_product_serial_template).where(
        Organisation.id == org_id))).scalar_one_or_none()
    tpl = (tpl or "").strip() or numbering.CODE_DEFAULTS["product_serial"]

    async def taken(codes: set) -> set:
        canon = {c.upper().replace(" ", ""): c for c in codes}
        rows = (await db.execute(select(_canon(SerializedItem.serial_number)).where(
            SerializedItem.org_id == org_id, SerializedItem.status.in_(IN_STOCK),
            _canon(SerializedItem.serial_number).in_(set(canon))))).scalars().all()
        return {canon[r] for r in rows if r in canon}
    return await numbering.next_codes(db, org_id, "product_serial", n,
                                      render_fn=lambda c: normalize(numbering.render(tpl, c))[0], taken=taken)


# ── shared product-data rules (create / update / import / any other save path) ──────────────────
def clean_codes(obj: dict, keys=("name", "sku", "code", "barcode", "mpn")) -> dict:
    """Trim the identity fields of a payload in place (blank → None), so " abc-1 " and "abc-1"
    are one SKU. Name stays a string (blank → "") — the caller refuses an empty one."""
    for k in keys:
        if k in obj:
            v = clean(obj[k])
            obj[k] = (v or "") if k == "name" else v
    return obj


_GTIN_KIND = {8: "ean8", 12: "upca", 13: "ean13", 14: "itf14"}


def sheet_barcode(raw, kind: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """A barcode cell from a spreadsheet → (barcode, barcode_type). Raises ValueError with the reason.

    Excel stores 012345678905 as the number 12345678905, so a numeric cell (or an all-digit text
    that isn't a valid GTIN as typed) is zero-padded to the first GTIN length whose check digit
    validates. An all-digit value of a GTIN length must carry a valid check digit. Anything else
    is a Code 128 code (or the given `kind`, checked by its own rules)."""
    from backend.services import barcode_types
    if raw is None:
        return None, None
    numeric = isinstance(raw, (int, float)) and not isinstance(raw, bool)
    if isinstance(raw, float):
        if raw != int(raw):
            raise ValueError(f"the barcode {raw} isn't a whole number")
        raw = int(raw)
    text = clean(raw)
    if not text:
        return None, None
    if kind and kind in barcode_types.TYPES:
        return barcode_types.normalize(kind, text), kind
    if text.isdigit() and len(text) <= 14:
        from backend.services.codes import gtin_check_digit
        if len(text) in _GTIN_KIND and gtin_check_digit(text[:-1]) == text[-1]:
            return text, _GTIN_KIND[len(text)]
        if numeric and len(text) not in _GTIN_KIND:   # a number cell lost its leading zeros (≤ 3)
            for n in (8, 12, 13, 14):
                if 0 < n - len(text) <= 3:
                    padded = text.zfill(n)
                    if gtin_check_digit(padded[:-1]) == padded[-1]:
                        return padded, _GTIN_KIND[n]
        if len(text) in (12, 13, 14):
            raise ValueError(f"the barcode {text} has a wrong check digit")
    return text, "code128"


OPEN_PO_STATUSES = ("draft", "ordered", "partial")


async def archive_blocker(db, org_id: str, part: Part) -> Optional[str]:
    """Why this product can't be archived yet, or None. A product still in stock, on an open
    work order / estimate, or on an open purchase order would vanish from those screens
    (and its stock from the books) — sell, write off or remove it there first.
    inventory.py / sales.py archive paths should call this too."""
    from backend.models import Order, OrderLine, POLine, PurchaseOrder, StockLevel
    from backend.services.po_links import _closed_keys
    on_hand = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == part.id))).scalar() or 0
    if on_hand > 0:
        return f"{on_hand:g} still in stock. Sell or write them off first"
    closed = await _closed_keys(db, org_id)
    jobs = (await db.execute(select(Order.order_number, Order.is_estimate).join(
        OrderLine, OrderLine.order_id == Order.id).where(
        Order.org_id == org_id, OrderLine.ref_id == part.id,
        or_(OrderLine.kind == "product", OrderLine.line_type == "part"),
        Order.archived == False, Order.status.not_in(closed))  # noqa: E712
        .distinct().limit(3))).all()
    if jobs:
        nums = ", ".join(n for n, _e in jobs)
        what = "an open estimate" if all(e for _n, e in jobs) else "an open work order"
        return f"on {what} ({nums})"
    pos = (await db.execute(select(PurchaseOrder.po_number).join(POLine, POLine.po_id == PurchaseOrder.id).where(
        PurchaseOrder.org_id == org_id, POLine.part_id == part.id,
        PurchaseOrder.status.in_(OPEN_PO_STATUSES)).distinct().limit(3))).scalars().all()
    if pos:
        return f"on an open purchase order ({', '.join(pos)})"
    return None
