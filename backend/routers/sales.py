"""Sales (over-the-counter POS) + Stocktake (count → variance → adjust)."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    Part,
    Sale,
    SaleLine,
    StockItemBatch,
    StockLevel,
    StockMovement,
    Stocktake,
    StocktakeLine,
)
from backend.services.auth import assert_perm, get_current_employee, require_perm
from backend.services import product_codes, stock_rows
from backend.services.numbering import next_number
from backend.services.money import q2, q4
from backend.core.spreadsheet import MAX_IMPORT_ROWS, safe_row

router = APIRouter(prefix="/api/sales", tags=["sales"])
st_router = APIRouter(prefix="/api/stocktake", tags=["stocktake"])


async def _can_override_reservation(db, current) -> bool:
    """144at — opt-in (default-deny) override: owner, or a role with can_override_reservation
    explicitly on, may consume stock reserved for a job."""
    if not getattr(current, "role_id", None):
        return True
    from backend.models import Role
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    from backend.services.permissions import role_allows
    return bool(role and role_allows(role, "can_override_reservation"))


# ─────────────────────────────── SALES ───────────────────────────────
class SaleLineIn(BaseModel):
    kind: str  # product|service
    ref_id: Optional[str] = None
    description: str = ""
    qty: float = 1
    unit_price: float = 0
    discount: float = 0  # pct

    # Security phase 3: negative qty passed both stock guards and ADDED stock; negative prices
    # zeroed sales; >100% discounts made totals negative.
    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_price")
    @classmethod
    def _price_non_negative(cls, v):
        if v < 0:
            raise ValueError("Price can't be negative")
        return v

    @field_validator("discount")
    @classmethod
    def _pct_range(cls, v):
        if not 0 <= v <= 100:
            raise ValueError("Discount must be between 0 and 100%")
        return v


class SaleIn(BaseModel):
    client_id: Optional[str] = None
    account_id: Optional[str] = None  # cashbox to take payment into
    discount: float = 0               # whole-sale flat amount
    lines: list[SaleLineIn] = []
    coupon_code: Optional[str] = None     # 144bb — POS rewards
    gift_card_code: Optional[str] = None  # B5: a tender, not a discount
    redeem_points: float = 0
    cash_tendered: Optional[float] = None   # B5: cash the customer handed over → change due
    expected_due: Optional[float] = None    # B5: what the Charge button showed; a different charge is refused

    @field_validator("discount", "redeem_points")
    @classmethod
    def _not_negative(cls, v):
        if v < 0:
            raise ValueError("Can't be negative")
        return v


async def _check_coupon(db, org_id, code, client_id, gross):
    """The coupon, or (status, message) saying why it can't be used. Reads only."""
    from backend.models import Coupon
    c = (await db.execute(select(Coupon).where(
        Coupon.org_id == org_id, Coupon.code == code.strip().upper()))).scalar_one_or_none()
    now = datetime.utcnow()
    if not c or not c.active:
        return None, (404, "Coupon not found or inactive")
    if c.client_id and c.client_id != client_id:
        return None, (403, "This coupon was issued to a different customer")
    if (c.starts_at and now < c.starts_at) or (c.ends_at and now > c.ends_at):
        return None, (400, "Coupon is not valid at this time")
    if c.max_uses and c.uses >= c.max_uses:
        return None, (400, "Coupon usage limit reached")
    if gross < c.min_total:
        return None, (400, f"Order total below coupon minimum ({c.min_total:.2f})")
    return c, None


async def _check_gift_card(db, org_id, code):
    from backend.models import GiftCard
    g = (await db.execute(select(GiftCard).where(
        GiftCard.org_id == org_id, GiftCard.code == code.strip().upper()))).scalar_one_or_none()
    if not g or not g.active:
        return None, (404, "Gift card not found or inactive")
    if g.expires_at and g.expires_at < datetime.utcnow():
        return None, (400, "Gift card has expired")
    return g, None


async def _price_sale(db, org_id, data: SaleIn, *, location_id, sale: Optional[Sale] = None) -> dict:
    """B5 — the one way a counter sale is priced, shared by the quote and the charge so the Charge
    button always shows what is saved. Order: line discounts → whole-sale discount → coupon →
    loyalty points → line tax on what is left (the tax engine, doc family tickets_sales) → the
    gift card pays part of the total, like cash would.

    sale=None is the quote: a dry run that writes nothing and reports a bad code in `problems`
    (priced without it). With a sale it is the charge: a bad code is refused, and the coupon use,
    gift-card spend and loyalty points are taken (guarded, ref_type='sale')."""
    from backend.models import Client, Coupon, GiftCard, GiftCardLedger, LoyaltyLedger, Organisation, Service
    from backend.services.tax import resolve_taxes
    problems: dict = {}
    status: dict = {}

    def refuse(field, err):
        if sale is not None:
            raise HTTPException(*err)
        problems[field], status[field] = err[1], err[0]

    nets = [q2(li.unit_price * li.qty * (1 - (li.discount or 0) / 100)) for li in data.lines]
    gross = q2(sum(nets))
    discount = q2(min(data.discount or 0, gross))
    remaining = gross - discount

    coupon = 0.0
    if data.coupon_code and data.coupon_code.strip():
        c, err = await _check_coupon(db, org_id, data.coupon_code, data.client_id, gross)
        if err:
            refuse("coupon", err)
        else:
            coupon = min(q2(gross * c.value / 100.0) if c.kind == "pct" else q2(c.value), q2(remaining))
            if sale is not None:
                # guarded atomic increment: refuse if a concurrent redemption hit max_uses meanwhile
                res = await db.execute(sa_update(Coupon)
                    .where(Coupon.id == c.id, or_(Coupon.max_uses.is_(None), Coupon.max_uses == 0,   # 0 = unlimited
                                               Coupon.uses < Coupon.max_uses))
                    .values(uses=Coupon.uses + 1))
                if res.rowcount == 0:
                    raise HTTPException(400, "Coupon usage limit reached")
                sale.coupon_id = c.id
            remaining -= coupon

    loyalty = 0.0
    if data.redeem_points and data.redeem_points > 0:
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
        if not data.client_id:
            refuse("loyalty", (400, "Select a customer to redeem points"))
        elif org.loyalty_point_value <= 0:
            refuse("loyalty", (400, "Loyalty redemption is not configured"))
        else:
            if sale is not None:
                # Lock the client so two redemptions can't both spend the same points (Postgres).
                await db.execute(select(Client.id).where(
                    Client.id == data.client_id, Client.org_id == org_id).with_for_update())
            bal = float((await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
                LoyaltyLedger.org_id == org_id, LoyaltyLedger.client_id == data.client_id))).scalar_one())
            if data.redeem_points > bal:
                refuse("loyalty", (400, f"Client has only {bal:.0f} points"))
            else:
                # 144bc — cap the value at what's still owed so points past the bill aren't burned.
                loyalty = q2(data.redeem_points * org.loyalty_point_value)
                used_points = data.redeem_points
                if loyalty > remaining:
                    loyalty = q2(max(remaining, 0))
                    used_points = q2(loyalty / org.loyalty_point_value)
                if sale is not None and used_points > 0:
                    db.add(LoyaltyLedger(org_id=org_id, client_id=data.client_id, points=-used_points,
                                         ref_type="sale", ref_id=sale.id, note=f"Redeemed on {sale.sale_number}"))
                remaining -= loyalty

    # Line tax on each line's share of what is left after the discounts.
    after = q2(max(remaining, 0))
    share = after / gross if gross else 0.0
    cats: dict = {}
    pids = [li.ref_id for li in data.lines if li.kind == "product" and li.ref_id]
    sids = [li.ref_id for li in data.lines if li.kind == "service" and li.ref_id]
    if pids:
        cats.update(dict((await db.execute(select(Part.id, Part.category_id).where(
            Part.org_id == org_id, Part.id.in_(pids)))).all()))
    if sids:
        cats.update(dict((await db.execute(select(Service.id, Service.category_id).where(
            Service.org_id == org_id, Service.id.in_(sids)))).all()))
    taxes: dict = {}
    added = included = 0.0
    for li, net in zip(data.lines, nets):
        res = await resolve_taxes(db, org_id, doc_family="tickets_sales", net_amount=q2(net * share),
                                  item_type=li.kind, item_id=li.ref_id, category_id=cats.get(li.ref_id),
                                  location_id=location_id)
        for t in res["taxes"]:
            row = taxes.setdefault((t["name"], t["rate"], t["mode"]),
                                   {"name": t["name"], "rate": t["rate"], "mode": t["mode"], "amount": 0.0})
            row["amount"] = q2(row["amount"] + t["amount"])
            if t["mode"] == "included":
                included += t["amount"]
            else:
                added += t["amount"]
    total = q2(after + added)

    gift = 0.0
    if data.gift_card_code and data.gift_card_code.strip():
        g, err = await _check_gift_card(db, org_id, data.gift_card_code)
        if err:
            refuse("gift_card", err)
        else:
            gift = q2(min(g.balance or 0, total))
            if sale is not None and gift > 0:
                # 144bc — guarded atomic decrement: never drive the balance negative.
                res = await db.execute(sa_update(GiftCard)
                    .where(GiftCard.id == g.id, GiftCard.balance >= gift)
                    .values(balance=GiftCard.balance - gift))
                if res.rowcount == 0:
                    raise HTTPException(409, "Gift card balance changed — please retry")
                db.add(GiftCardLedger(org_id=org_id, gift_card_id=g.id, amount=-gift,
                                      ref_type="sale", ref_id=sale.id, note=f"Paid on {sale.sale_number}"))
    due = q2(total - gift)
    tendered = data.cash_tendered
    return {
        "subtotal": gross, "discount": discount, "coupon": coupon, "loyalty": loyalty,
        "discount_total": q2(discount + coupon + loyalty),
        "taxes": list(taxes.values()), "tax_total": q2(added), "tax_included": q2(included),
        "total": total, "gift_card": gift, "due": due,
        "cash_tendered": tendered, "change_due": q2(max(tendered - due, 0)) if tendered is not None else None,
        "problems": problems, "_status": status,
    }


async def sale_gift_card_paid(db, sale_id: str) -> float:
    """What gift cards paid towards a sale (their 'sale' ledger legs)."""
    from backend.models import GiftCardLedger
    return q2(-float((await db.execute(select(func.coalesce(func.sum(GiftCardLedger.amount), 0.0)).where(
        GiftCardLedger.ref_type == "sale", GiftCardLedger.ref_id == sale_id))).scalar_one()))


async def sale_amount_due(db, sale: Sale) -> float:
    """What is still to collect on a sale: nothing once paid, else the total less the gift card."""
    if sale.paid or sale.voided_at:
        return 0.0
    return q2(max(float(sale.total or 0) - await sale_gift_card_paid(db, sale.id), 0))


async def _sale_location(db, current) -> str:
    # A seller without a home location (e.g. the org owner) still needs a location on the sale —
    # sales.location_id is NOT NULL. Fall back to the org's first location rather than 500.
    if current.location_id:
        return current.location_id
    from backend.models import Location
    loc_id = (await db.execute(select(Location.id).where(
        Location.org_id == current.org_id).order_by(Location.created_at).limit(1))).scalar_one_or_none()
    if not loc_id:
        raise HTTPException(400, "No location is set up for this sale")
    return loc_id


@router.post("/quote")
async def quote_sale(
    data: SaleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """B5 — price the cart exactly as a charge would save it, without saving anything. The POS
    calls this on every cart or tender change and shows its `due` on the Charge button."""
    out = await _price_sale(db, current.org_id, data, location_id=await _sale_location(db, current))
    out.pop("_status")
    await db.rollback()
    return out


@router.get("/lookup")
async def lookup_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """F1 scan-to-cart: every active product whose SKU, barcode, internal code or MPN equals the
    scanned code (case-insensitive), else the product a serial number belongs to. One match goes
    straight into the cart; several let the cashier pick."""
    from backend.services import scan_codes
    raw = (code or "").strip()
    scan = scan_codes.parse(raw)        # ii144b: AIM prefix stripped, GS1 label read
    code = scan.text
    if not code:
        raise HTTPException(422, "Scan or type a code")
    low = code.lower()
    fields = {"sku": Part.sku, "barcode": Part.barcode, "code": Part.code}
    mpn_col = getattr(Part, "mpn", None)          # A3 adds Part.mpn; match it once it exists
    if mpn_col is not None:
        fields["mpn"] = mpn_col
    base = select(Part).where(Part.org_id == current.org_id, Part.active == True)  # noqa: E712
    extra = product_codes.barcode_part_ids(current.org_id, raw)      # extra barcodes, any GTIN form
    parts = (await db.execute(base.where(or_(*[func.lower(c) == low for c in fields.values()], Part.id.in_(extra)))
                              .order_by(Part.name).limit(20))).scalars().all()
    matched = {p.id: next((k for k in fields if (getattr(p, k, None) or "").lower() == low), "barcode") for p in parts}
    if not parts:
        from backend.models import SerializedItem
        from backend.services.serial_intake import matches as _sn_matches
        si = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == current.org_id,
            or_(*[_sn_matches(SerializedItem.serial_number, t) for t in (scan.serial, scan.clean) if t])
        ))).scalars().first()      # the GS1 (21) serial, or the code exactly as scanned
        if si:
            parts = (await db.execute(base.where(Part.id == si.part_id))).scalars().all()
            matched = {p.id: "serial" for p in parts}
    loc_id = current.location_id
    stock = {}
    if parts:
        q = select(StockLevel.part_id, func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
            StockLevel.part_id.in_([p.id for p in parts]))
        if loc_id:
            q = q.where(StockLevel.location_id == loc_id)
        stock = {pid: float(qty or 0) for pid, qty in (await db.execute(q.group_by(StockLevel.part_id))).all()}
    return {"code": code, "items": [{
        "id": p.id, "name": p.name, "sku": p.sku, "barcode": p.barcode, "code": p.code,
        "mpn": getattr(p, "mpn", None), "sell_price": p.sell_price or 0,
        "qty_available": stock.get(p.id, 0.0), "image_data": p.image_data, "matched_on": matched.get(p.id),
    } for p in parts]}


@router.get("")
async def list_sales(
    location_id: Optional[str] = None,        # build 138: scope sales to a branch
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    _q = select(Sale).where(Sale.org_id == current.org_id)
    if location_id:
        _q = _q.where(Sale.location_id == location_id)
    rows = (await db.execute(_q.order_by(Sale.created_at.desc()).limit(200))).scalars().all()
    from backend.models import GiftCardLedger
    unpaid = [s.id for s in rows if not s.paid and not s.voided_at]
    gift = dict((await db.execute(select(GiftCardLedger.ref_id, func.sum(GiftCardLedger.amount)).where(
        GiftCardLedger.ref_type == "sale", GiftCardLedger.ref_id.in_(unpaid))
        .group_by(GiftCardLedger.ref_id))).all()) if unpaid else {}
    return {"items": [{
        "id": s.id, "sale_number": s.sale_number, "client_id": s.client_id,
        "total": s.total, "total_cost": s.total_cost, "profit": q2(s.total - s.total_cost),
        "due": q2(max(float(s.total or 0) + float(gift.get(s.id) or 0), 0)) if s.id in unpaid else 0.0,
        "paid": s.paid, "created_at": s.created_at.isoformat() if s.created_at else None,
        "voided_at": s.voided_at.isoformat() if s.voided_at else None, "void_reason": s.void_reason,
    } for s in rows]}


@router.delete("/{sale_id}")
async def delete_sale(
    sale_id: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_sales")),
):
    """Void a counter sale. The sale stays on record (struck through) and everything it did is
    put back: products return to the shelf and to the FIFO cost layers they came from, holds
    taken from other jobs are restored, the coupon use / gift-card spend / loyalty points come
    back, and the takings leave the cashbox."""
    from backend.models import Coupon, GiftCard, GiftCardLedger, LoyaltyLedger, StockReservation
    sale = (await db.execute(select(Sale).where(
        Sale.id == sale_id, Sale.org_id == current.org_id).with_for_update())).scalar_one_or_none()
    if not sale:
        raise HTTPException(404, "Sale not found")
    if sale.voided_at:
        raise HTTPException(409, "This sale was already deleted")
    # F2: voiding only reverses the books; money taken on a card reader must go back to the card first.
    from backend.services.terminal import open_sale_charge, unrefunded_sale_card_payment
    if await open_sale_charge(db, sale.id):   # review M6
        raise HTTPException(409, "A card reader is still waiting for this sale's payment. "
                                 "Cancel that charge first, then delete the sale.")
    if await unrefunded_sale_card_payment(db, sale.id) > 0:
        raise HTTPException(409, "This sale was paid on a card reader. Refund the card payment first "
                                 "(Recent sales › Card reader payments), then delete the sale.")
    reason = (reason or "Deleted").strip()[:300]

    lines = (await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id))).scalars().all()
    if any(_returned(ln) > 0 for ln in lines):   # B5: the return already put stock and money back
        raise HTTPException(409, "Items from this sale were returned, so it can't be deleted.")
    for ln in lines:
        if ln.kind != "product" or not ln.ref_id:
            continue
        await stock_rows.add(db, ln.ref_id, sale.location_id, ln.qty)
        db.add(StockMovement(part_id=ln.ref_id, location_id=sale.location_id, movement_type="return",
                             qty=ln.qty, unit_cost=ln.cost_price or 0, ref_type="sale_void", ref_id=sale.id,
                             created_by_id=current.id))
        draws = ln.stock_draws or {}
        for d in draws.get("layers") or []:
            batch = (await db.execute(select(StockItemBatch).where(
                StockItemBatch.id == d["batch_id"], StockItemBatch.org_id == current.org_id))).scalar_one_or_none()
            if batch:
                back = Decimal(str(d["qty"]))
                cap = Decimal(str(batch.qty_received or 0))
                batch.qty_remaining = min(Decimal(str(batch.qty_remaining or 0)) + back, cap)
        for d in draws.get("holds") or []:
            hold = (await db.execute(select(StockReservation).where(
                StockReservation.id == d["hold_id"], StockReservation.org_id == current.org_id))).scalar_one_or_none()
            if hold:
                hold.qty = (hold.qty or 0) + d["qty"]
                hold.released = False
                await stock_rows.reserve(db, ln.ref_id, sale.location_id, d["qty"])

    if sale.coupon_id:
        await db.execute(sa_update(Coupon).where(Coupon.id == sale.coupon_id, Coupon.uses > 0)
                         .values(uses=Coupon.uses - 1))
    for g in (await db.execute(select(GiftCardLedger).where(
            GiftCardLedger.org_id == current.org_id, GiftCardLedger.ref_type == "sale",
            GiftCardLedger.ref_id == sale.id))).scalars().all():
        back = q2(-(g.amount or 0))
        if back > 0:
            await db.execute(sa_update(GiftCard).where(GiftCard.id == g.gift_card_id)
                             .values(balance=GiftCard.balance + back))
            db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=g.gift_card_id, amount=back,
                                  ref_type="sale_void", ref_id=sale.id, note=f"Sale {sale.sale_number} deleted"))
    for pts in (await db.execute(select(LoyaltyLedger).where(
            LoyaltyLedger.org_id == current.org_id, LoyaltyLedger.ref_type == "sale",
            LoyaltyLedger.ref_id == sale.id))).scalars().all():
        if (pts.points or 0) != 0:
            db.add(LoyaltyLedger(org_id=current.org_id, client_id=pts.client_id, points=-pts.points,
                                 ref_type="sale_void", ref_id=sale.id, note=f"Sale {sale.sale_number} deleted"))

    from backend.routers.cash import void_document_legs
    await void_document_legs(db, current.org_id, "sale", sale.id, reason, current.id)
    sale.voided_at = datetime.utcnow()
    sale.voided_by_id = current.id
    sale.void_reason = reason
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="sale.delete",
                       entity_type="sale", entity_id=sale.id,
                       diff={"sale_number": sale.sale_number, "total": float(sale.total or 0), "reason": reason})
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "sale", sale.id, f"Sale {sale.sale_number} deleted: {reason}")
    await db.commit()
    return {"ok": True, "id": sale.id, "voided_at": sale.voided_at.isoformat()}


@router.post("", status_code=201)
async def create_sale(
    data: SaleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """One warehouse per sale (the seller's location). Products deduct stock via
    the guarded UPDATE + FIFO cost layers, so COGS — and therefore the estimated
    profit (total − cost) — is exact."""
    if not data.lines:
        raise HTTPException(400, "A sale needs at least one line")
    if any(ln.kind == "service" for ln in data.lines):
        await assert_perm(db, current, "can_sell_services")
    loc_id = await _sale_location(db, current)
    # B5: price it first, before anything is written: a bad code, a total that isn't the one on the
    # cashier's screen, or too little cash is refused with nothing saved.
    pre = await _price_sale(db, current.org_id, data, location_id=loc_id)
    if pre["problems"]:
        field = next(iter(pre["problems"]))
        raise HTTPException(pre["_status"][field], pre["problems"][field])
    if data.expected_due is not None and abs(pre["due"] - data.expected_due) > 0.005:
        raise HTTPException(409, f"The total changed to {pre['due']:.2f}. Check the sale and charge again.")
    if data.account_id and data.cash_tendered is not None and data.cash_tendered + 0.005 < pre["due"]:
        raise HTTPException(400, f"Cash received ({data.cash_tendered:.2f}) is less than the "
                                 f"{pre['due']:.2f} due.")
    sale = Sale(org_id=current.org_id, location_id=loc_id,
                sale_number=await next_number(db, current.org_id, "sale"), client_id=data.client_id,
                seller_id=current.id, account_id=data.account_id, discount=data.discount)
    db.add(sale)
    await db.flush()

    total = 0.0
    total_cost = 0.0
    from backend.services.tax import pos_line_tax
    sale_lines = []
    for li in data.lines:
        line_total = q2(li.unit_price * li.qty * (1 - (li.discount or 0) / 100))
        cost = 0.0
        draws = None
        if li.kind == "product" and li.ref_id:
            part = (await db.execute(select(Part).where(
                Part.id == li.ref_id, Part.org_id == current.org_id))).scalar_one_or_none()
            if not part:
                raise HTTPException(404, f"Product not found: {li.description}")
            have, _held = await stock_rows.totals(db, li.ref_id, loc_id)
            if have < li.qty:
                raise HTTPException(409, f"Only {have:g} of '{part.name}' in this warehouse")
            # 144at — reservation lock: a counter sale can't take stock reserved for a job
            # unless the seller can override. order_id=None → no own holds; only free stock is sellable.
            from backend.services import reservations as _rsv
            _allow_ov = await _can_override_reservation(db, current)
            _g = await _rsv.guard_and_draw(
                db, org_id=current.org_id, part_id=li.ref_id, location_id=loc_id,
                order_id=None, qty=li.qty, actor_id=current.id, allow_override=_allow_ov,
                taken_label=f"sale {sale.sale_number or ''}".strip())
            if _g["blocked"] > 0:
                raise HTTPException(409, f"{_g['blocked']:g} of '{part.name}' are reserved for a job. "
                                         f"A manager with override permission must approve selling them.")
            draws = {"layers": [], "holds": [{"hold_id": o["hold_id"], "qty": o["qty"]}
                                             for o in _g["overrides"] if o.get("hold_id")]}
            try:      # guarded per-bin decrements: no bin goes negative under concurrency
                await stock_rows.draw(db, li.ref_id, loc_id, li.qty)
            except stock_rows.NotEnoughStock:
                raise HTTPException(409, "Stock changed during the sale — please retry")
            unit_cost = part.avg_cost or part.cost_price or 0
            # FIFO cost layers → exact COGS when layers exist
            has_batch = (await db.execute(select(StockItemBatch.id).where(
                StockItemBatch.org_id == current.org_id, StockItemBatch.part_id == li.ref_id,
                StockItemBatch.qty_remaining > 0).limit(1))).scalar_one_or_none()
            if has_batch:
                from backend.services.serialized import consume_fifo_lifo
                try:
                    rc = await consume_fifo_lifo(db, org_id=current.org_id, part_id=li.ref_id,
                                                 qty=Decimal(str(li.qty)), method="fifo")
                    unit_cost = rc["avg_unit_cost"]
                    draws["layers"] = [{"batch_id": x["batch_id"], "qty": x["qty"]} for x in rc["layers"]]
                except HTTPException:
                    pass
            cost = q4(unit_cost * li.qty)
            db.add(StockMovement(part_id=li.ref_id, location_id=loc_id,
                                 movement_type="use", qty=-li.qty, unit_cost=unit_cost,
                                 ref_type="sale", ref_id=sale.id))
            from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
            await _alerts.maybe_low_stock_by_id(db, current.org_id, li.ref_id)
        ln = SaleLine(sale_id=sale.id, kind=li.kind, ref_id=li.ref_id,
                      description=li.description, qty=li.qty, unit_price=li.unit_price,
                      cost_price=cost / li.qty if li.qty else 0, discount=li.discount,
                      total=line_total, stock_draws=draws)
        db.add(ln)
        sale_lines.append(ln)
        total += line_total
        total_cost += cost

    # B5 — the same pricing as the quote, now taking the coupon, points and gift card. The gift
    # card is a tender: it pays part of the total; the rest is due in the cashbox.
    priced = await _price_sale(db, current.org_id, data, location_id=loc_id, sale=sale)
    sale.discount = priced["discount_total"]
    sale.total = priced["total"]
    # Launch B4: store the tax charged — on the sale (tax added on top + VAT inside the price) and on
    # each line (its rate, and its tax on its share of what is left after the discounts).
    sale.tax = q2(priced["tax_total"] + priced["tax_included"])
    gross = priced["subtotal"]
    share = (gross - priced["discount_total"]) / gross if gross else 0.0
    for ln in sale_lines:
        tx = await pos_line_tax(db, current.org_id, kind=ln.kind, ref_id=ln.ref_id,
                                net_amount=q2(float(ln.total or 0) * share), location_id=loc_id)
        ln.tax_rate, ln.tax_mode, ln.tax = tx["rate"], tx["mode"], tx["amount"]
    sale.total_cost = q2(total_cost)

    # Payment straight into a cashbox (signed ledger entry linked to the sale)
    if data.account_id and priced["due"] > 0:
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "income", priced["due"],
                               comment=f"Sale {sale.sale_number}", ref_type="sale",
                               ref_id=sale.id, employee_id=current.id)
    sale.paid = bool(data.account_id) or priced["due"] <= 0

    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "sale", sale.id,
                           f"Sale {sale.sale_number}: {len(data.lines)} line(s), total {sale.total:g}")
    await db.commit()
    redeemed = {k: priced[k] for k in ("coupon", "gift_card", "loyalty") if priced[k]}
    return {"id": sale.id, "sale_number": sale.sale_number, "total": sale.total, "tax": sale.tax,
            "total_cost": sale.total_cost, "profit": q2(sale.total - sale.total_cost),
            "paid": sale.paid, "redeemed": redeemed, "due": priced["due"], "gift_card": priced["gift_card"],
            "tax_total": priced["tax_total"], "change_due": priced["change_due"] if sale.paid else None}


@router.get("/{sale_id}")
async def get_sale(
    sale_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """One sale with its lines and how many of each can still be returned."""
    sale = await _load_sale(db, current, sale_id)
    lines = (await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id))).scalars().all()
    return {
        "id": sale.id, "sale_number": sale.sale_number, "client_id": sale.client_id, "total": sale.total,
        "discount": sale.discount, "paid": sale.paid, "account_id": sale.account_id,
        "voided_at": sale.voided_at.isoformat() if sale.voided_at else None,
        "created_at": sale.created_at.isoformat() if sale.created_at else None,
        "gift_card": await sale_gift_card_paid(db, sale.id), "due": await sale_amount_due(db, sale),
        "lines": [{"id": ln.id, "kind": ln.kind, "ref_id": ln.ref_id, "description": ln.description,
                   "qty": ln.qty, "unit_price": ln.unit_price, "total": ln.total, "returned": _returned(ln),
                   "returnable": q4(max((ln.qty or 0) - _returned(ln), 0))} for ln in lines],
    }


async def _load_sale(db, current, sale_id, lock=False) -> Sale:
    q = select(Sale).where(Sale.id == sale_id, Sale.org_id == current.org_id)
    sale = (await db.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if not sale:
        raise HTTPException(404, "Sale not found")
    return sale


def _returned(ln: SaleLine) -> float:
    """How much of a line has come back (kept beside its stock draws)."""
    return float((ln.stock_draws or {}).get("returned") or 0)


class SalePayIn(BaseModel):
    account_id: str
    cash_tendered: Optional[float] = None


@router.post("/{sale_id}/pay")
async def pay_sale(
    sale_id: str, data: SalePayIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """B5 — take payment for a parked sale, or one whose card was declined: what is still due
    (the total less any gift card) goes into the chosen cashbox."""
    from backend.models import Account
    sale = await _load_sale(db, current, sale_id, lock=True)
    if sale.voided_at:
        raise HTTPException(409, "This sale was deleted")
    if sale.paid:
        raise HTTPException(409, "This sale is already paid")
    from backend.services.terminal import open_sale_charge
    if await open_sale_charge(db, sale.id):
        raise HTTPException(409, "A card reader is still waiting for this sale's payment. Cancel that charge first.")
    acc = (await db.execute(select(Account).where(
        Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Cashbox not found")
    due = await sale_amount_due(db, sale)
    if data.cash_tendered is not None and data.cash_tendered + 0.005 < due:
        raise HTTPException(400, f"Cash received ({data.cash_tendered:.2f}) is less than the {due:.2f} due.")
    if due > 0:
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, acc.id, "income", due, comment=f"Sale {sale.sale_number}",
                               ref_type="sale", ref_id=sale.id, employee_id=current.id)
    sale.paid = True
    sale.account_id = acc.id
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "sale", sale.id, f"Sale {sale.sale_number} paid {due:g} into {acc.name}")
    await db.commit()
    change = q2(max(data.cash_tendered - due, 0)) if data.cash_tendered is not None else None
    return {"id": sale.id, "paid": True, "due": due, "change_due": change}


class SaleReturnLineIn(BaseModel):
    line_id: str
    qty: float = Field(gt=0)


class SaleReturnIn(BaseModel):
    lines: list[SaleReturnLineIn]
    account_id: Optional[str] = None   # the cashbox the money goes back out of
    reason: Optional[str] = Field(default=None, max_length=300)


@router.post("/{sale_id}/returns", status_code=201)
async def return_sale_items(
    sale_id: str, data: SaleReturnIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """B5 — the customer brings items back. Products go back on the shelf (a client return), and
    the customer gets the items' share of what they paid: discounts and tax included, the cash part
    as a refund out of the chosen cashbox and any gift-card part back onto the card."""
    from backend.models import Account, ClientReturn, ClientReturnLine, GiftCard, GiftCardLedger, Refund
    await assert_perm(db, current, "can_create_returns")
    if not data.lines:
        raise HTTPException(400, "Pick at least one item to return")
    sale = await _load_sale(db, current, sale_id, lock=True)
    if sale.voided_at:
        raise HTTPException(409, "This sale was deleted")
    if not sale.paid:
        raise HTTPException(409, "This sale isn't paid. Take payment or delete it instead.")
    lines = {ln.id: ln for ln in (await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id))).scalars()}
    base = q2(sum(float(ln.total or 0) for ln in lines.values()))
    value = 0.0
    for r in data.lines:
        ln = lines.get(r.line_id)
        if not ln:
            raise HTTPException(404, "That item isn't on this sale")
        left = (ln.qty or 0) - _returned(ln)
        if r.qty > left + 1e-9:
            raise HTTPException(400, f"Only {left:g} of '{ln.description}' can still be returned")
        value += float(ln.total or 0) * r.qty / ln.qty
    value = q2(value * float(sale.total or 0) / base) if base else 0.0
    gift_paid = await sale_gift_card_paid(db, sale.id)
    gift_back = q2(value * gift_paid / float(sale.total)) if sale.total else 0.0
    money_back = q2(value - gift_back)
    acc = None
    if money_back > 0:
        if not data.account_id:
            raise HTTPException(422, "Choose the cashbox the refund comes out of")
        acc = (await db.execute(select(Account).where(
            Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
        if not acc:
            raise HTTPException(404, "Cashbox not found")
    reason = (data.reason or "").strip()

    ret = ClientReturn(org_id=current.org_id, client_id=sale.client_id, location_id=sale.location_id,
                       reason=f"Sale {sale.sale_number}" + (f": {reason}" if reason else ""), created_by_id=current.id)
    db.add(ret)
    await db.flush()
    for r in data.lines:
        ln = lines[r.line_id]
        ln.stock_draws = {**(ln.stock_draws or {}), "returned": q4(_returned(ln) + r.qty)}
        if ln.kind == "product" and ln.ref_id:
            db.add(ClientReturnLine(return_id=ret.id, part_id=ln.ref_id, qty=r.qty, restock=True))
            await stock_rows.add(db, ln.ref_id, sale.location_id, r.qty)
            db.add(StockMovement(part_id=ln.ref_id, location_id=sale.location_id, movement_type="return",
                                 qty=r.qty, unit_cost=ln.cost_price or 0, ref_type="client_return",
                                 ref_id=ret.id, created_by_id=current.id))
    refund = None
    if money_back > 0:
        refund = Refund(org_id=current.org_id, return_id=ret.id, account_id=acc.id, amount=money_back,
                        reason=ret.reason, created_by_id=current.id)
        db.add(refund)
        await db.flush()
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, acc.id, "refund", -money_back,
                               comment=f"Refund for sale {sale.sale_number}", ref_type="refund",
                               ref_id=refund.id, employee_id=current.id)
    if gift_back > 0:
        leg = (await db.execute(select(GiftCardLedger).where(
            GiftCardLedger.ref_type == "sale", GiftCardLedger.ref_id == sale.id))).scalars().first()
        await db.execute(sa_update(GiftCard).where(GiftCard.id == leg.gift_card_id)
                         .values(balance=GiftCard.balance + gift_back))
        db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=leg.gift_card_id, amount=gift_back,
                              ref_type="sale_return", ref_id=sale.id, note=f"Items returned from {sale.sale_number}"))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "sale", sale.id,
                           f"Items returned from sale {sale.sale_number}: refunded {money_back:g}"
                           + (f", {gift_back:g} back on the gift card" if gift_back else ""))
    await db.commit()
    return {"return_id": ret.id, "refund_id": refund.id if refund else None,
            "refunded": money_back, "gift_card": gift_back}


# ───────────────────────────── RECEIPT ─────────────────────────────
class ReceiptEmailIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=254)   # blank → the sale's client's email


def sale_tax_added(sale: Sale, lines: list) -> float:
    """Tax added on top of the lines: the total less the lines after the discount (B5)."""
    tax = q2(float(sale.total or 0) - (sum(float(ln.total or 0) for ln in lines) - float(sale.discount or 0)))
    return tax if tax > 0.005 else 0.0


def receipt_text(org_name: str, sale: Sale, lines: list, sym: str, tz: Optional[str] = None,
                 gift_card: float = 0) -> str:
    """The customer's receipt in plain words — what was bought and what was paid. Never cost
    or profit (that's the shop's business, not the customer's)."""
    def m(v):
        v = float(v or 0)
        return f"{sym}{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{sym}{v:,.2f}"
    when = ""
    if sale.created_at:   # stored as naive UTC; the customer reads it in the shop's time
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo
        at = sale.created_at.replace(tzinfo=_tz.utc)
        try:
            at = at.astimezone(ZoneInfo(tz)) if tz else at
        except Exception:
            pass
        when = at.strftime("%d %b %Y, %H:%M")
    out = [org_name, f"Receipt {sale.sale_number}" + (f" · {when}" if when else ""), ""]
    for ln in lines:
        qty = ln.qty or 1
        qty_s = f"{qty:g} × " if qty != 1 else ""
        out.append(f"{qty_s}{ln.description or ln.kind}  {m(ln.total)}")
    if float(sale.discount or 0):
        out.append(f"Discount  −{m(sale.discount)}")
    tax = sale_tax_added(sale, lines)
    if tax:
        out.append(f"Tax  {m(tax)}")
    out += ["", f"Total  {m(sale.total)}"]
    if gift_card:
        out.append(f"Gift card  −{m(gift_card)}")
    out += ["Paid" if sale.paid else "Not paid yet", "", "Thank you!"]
    return "\n".join(out)


@router.get("/{sale_id}/receipt")
async def sale_receipt(
    sale_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """The receipt for the after-sale Print / Email / WhatsApp choices."""
    from backend.models import Client, Organisation
    sale = (await db.execute(select(Sale).where(Sale.id == sale_id, Sale.org_id == current.org_id))).scalar_one_or_none()
    if not sale:
        raise HTTPException(404, "Sale not found")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    lines = (await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id))).scalars().all()
    client = (await db.execute(select(Client).where(
        Client.id == sale.client_id, Client.org_id == current.org_id))).scalar_one_or_none() if sale.client_id else None
    from backend.services.currency import _base
    from backend.services.dates import utc_iso
    sym = _base(org)["symbol"]
    gift = await sale_gift_card_paid(db, sale.id)
    return {
        "sale_number": sale.sale_number, "shop_name": org.name if org else "",
        "total": sale.total, "discount": sale.discount, "paid": sale.paid,
        "tax": sale_tax_added(sale, lines), "gift_card": gift,
        "created_at": utc_iso(sale.created_at),   # launch B6: "Z" so the browser reads UTC
        "lines": [{"description": ln.description, "qty": ln.qty, "total": ln.total, "tax": ln.tax} for ln in lines],
        "client_email": client.email if client else None, "client_phone": client.phone if client else None,
        "text": receipt_text(org.name if org else "", sale, lines, sym, getattr(org, "timezone", None), gift),
    }


@router.post("/{sale_id}/receipt/email")
async def email_sale_receipt(
    sale_id: str, data: ReceiptEmailIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_sales")),
):
    """Email the receipt (connected Gmail/Outlook first, then SMTP, else the simulated adapter)."""
    rc = await sale_receipt(sale_id, db, current)
    to = (data.email or rc["client_email"] or "").strip()
    if not to or "@" not in to:
        raise HTTPException(422, "Add an email address for the receipt")
    from backend.services.notifications import _adapter_for, notif_creds, try_oauth_email
    subject = f"Your receipt {rc['sale_number']}" + (f" from {rc['shop_name']}" if rc["shop_name"] else "")
    result = await try_oauth_email(db, current.org_id, recipient=to, subject=subject, body=rc["text"])
    if result is None:
        try:
            await _adapter_for("email", await notif_creds(db, current.org_id, "email")).send(
                recipient=to, subject=subject, body=rc["text"])
        except Exception:
            raise HTTPException(502, "Couldn't send the email. Check the shop's email settings and try again.")
    return {"ok": True, "recipient": to}


# ───────────────────────────── STOCKTAKE ─────────────────────────────
async def _st_stock_row(db, part_id, st, bin_id=None):
    """144bh — the system quantity a count is compared with, or None when the place holds no row
    for the product. ii144a: stock is one row per bin — a bin count reads that bin, any other count
    the sum of the warehouse's rows, falling back to the location's rows that postings/sales/PO
    actually write (warehouse_id NULL). Without the fallback a count reads 0 and posting duplicates stock."""
    if bin_id:
        row = await stock_rows.row_for(db, part_id, st.location_id, bin_id)
        return None if row is None else float(row.qty_available or 0)
    found = []
    if st.warehouse_id:
        found = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part_id, StockLevel.warehouse_id == st.warehouse_id))).scalars().all()
    if not found:
        found = await stock_rows.rows(db, part_id, st.location_id)
    return float(sum(r.qty_available or 0 for r in found)) if found else None


class StocktakeIn(BaseModel):
    location_id: Optional[str] = None
    warehouse_id: Optional[str] = None      # Build 91: warehouses are the real address
    bin_id: Optional[str] = None
    note: Optional[str] = None
    kind: str = "full"                      # full|partial


@st_router.post("", status_code=201)
async def start_stocktake(
    data: StocktakeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Snapshot system quantities for the warehouse (or one bin) into count lines.

    Build 91: a *partial* count only seeds the lines you actually intend to count, so an
    uncounted line in a partial stocktake is not treated as "zero on the shelf" when it
    posts. A *full* count seeds everything the warehouse holds.
    """
    from backend.services import warehouses as _wh
    w = await _wh.guard_action(db, current, "stocktake",
                               warehouse_id=data.warehouse_id, location_id=data.location_id)
    if not w:
        raise HTTPException(400, "No warehouse resolved — pass warehouse_id")
    if data.kind not in ("full", "partial"):
        raise HTTPException(422, "kind must be full|partial")
    if data.bin_id:      # ii144a: this shop's bin, at this warehouse's location
        await stock_rows.check_bin(db, current.org_id, w.location_id, data.bin_id)

    st = Stocktake(org_id=current.org_id, location_id=w.location_id, warehouse_id=w.id,
                   bin_id=data.bin_id, note=data.note, kind=data.kind,
                   created_by_id=current.id)
    db.add(st)
    await db.flush()
    seeded = 0
    if data.kind == "full":
        if data.bin_id:
            # ii144a: one bin — its own rows (bin rows aren't warehouse-tagged)
            rows = (await db.execute(select(StockLevel).where(StockLevel.bin_id == data.bin_id))).scalars().all()
        else:
            rows = (await db.execute(select(StockLevel).where(StockLevel.warehouse_id == w.id))).scalars().all()
            if not rows:
                # 144bh — stock is kept at location level (warehouse_id NULL); seed from there so a
                # full count isn't empty on orgs whose stock was never warehouse-keyed.
                rows = (await db.execute(select(StockLevel).where(
                    StockLevel.location_id == w.location_id))).scalars().all()
        # ii144a: a warehouse count has one line per product (all its bins together); a bin count
        # one line per product in that bin — so a scan always lands on the one line it means.
        per_part: dict = {}
        for sl in rows:
            per_part[sl.part_id] = per_part.get(sl.part_id, 0) + (sl.qty_available or 0)
        for pid, qty in per_part.items():
            db.add(StocktakeLine(stocktake_id=st.id, part_id=pid, bin_id=data.bin_id, system_qty=qty))
            seeded += 1
    await db.commit()
    return {"id": st.id, "kind": st.kind, "warehouse_id": w.id, "lines": seeded}


@st_router.get("/{st_id}")
async def get_stocktake(
    st_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    lines = (await db.execute(select(StocktakeLine, Part.name)
        .join(Part, StocktakeLine.part_id == Part.id)
        .where(StocktakeLine.stocktake_id == st_id))).all()
    return {"id": st.id, "status": st.status, "location_id": st.location_id, "bin_id": st.bin_id,
            "lines": [{"id": l.id, "part_id": l.part_id, "part_name": name,
                       "system_qty": l.system_qty, "counted_qty": l.counted_qty,
                       "variance": l.variance} for l, name in lines]}


class CountIn(BaseModel):
    counted_qty: float


@st_router.patch("/{st_id}/lines/{line_id}")
async def set_count(
    st_id: str, line_id: str, data: CountIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    # verify the parent stocktake belongs to the caller's org (StocktakeLine has no org_id)
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    line = (await db.execute(select(StocktakeLine).where(
        StocktakeLine.id == line_id, StocktakeLine.stocktake_id == st_id))).scalar_one_or_none()
    if not line:
        raise HTTPException(404, "Count line not found")
    line.counted_qty = data.counted_qty
    line.variance = round(data.counted_qty - (line.system_qty or 0), 3)
    await db.commit()
    return {"ok": True, "variance": line.variance}


@st_router.post("/{st_id}/post")
async def post_stocktake(
    st_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Post every counted variance as one signed adjustment per part."""
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    if st.status == "posted":
        raise HTTPException(409, "Stocktake is already posted")
    lines = (await db.execute(select(StocktakeLine).where(
        StocktakeLine.stocktake_id == st_id,
        StocktakeLine.counted_qty.is_not(None)))).scalars().all()
    adjusted = 0
    for l in lines:
        if not l.variance:
            continue
        # Build 91: a product found on the shelf that the system had no row for gets one — posting
        # the count is what brings registered unknowns into stock. ii144a: a bin's line adjusts that
        # bin's row; a shortage on a location count is drawn across its bins (never below zero).
        line_bin = l.bin_id or st.bin_id
        if line_bin:
            await stock_rows.check_bin(db, current.org_id, st.location_id, line_bin)
        if line_bin or l.variance > 0:
            await stock_rows.add(db, l.part_id, st.location_id, l.variance, bin_id=line_bin)
        else:
            await stock_rows.draw(db, l.part_id, st.location_id, -l.variance, allow_short=True)
        part = (await db.execute(select(Part).where(Part.id == l.part_id))).scalar_one_or_none()
        db.add(StockMovement(part_id=l.part_id, location_id=st.location_id,
                             warehouse_id=st.warehouse_id, bin_id=l.bin_id or st.bin_id,
                             movement_type="adjust", qty=l.variance,
                             unit_cost=(part.avg_cost or part.cost_price or 0) if part else 0,
                             ref_type="stocktake", ref_id=st.id))
        adjusted += 1
    st.status = "posted"
    st.posted_at = datetime.utcnow()
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "stocktake", st.id,
                           f"Stocktake posted: {adjusted} adjustment(s)")
    await db.commit()
    return {"ok": True, "adjustments": adjusted}


@st_router.get("")
async def list_stocktakes(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Location, StorageBin
    rows = (await db.execute(select(Stocktake).where(Stocktake.org_id == current.org_id)
        .order_by(Stocktake.created_at.desc()).limit(100))).scalars().all()
    # Batch the lookups instead of 4 queries per stocktake (was up to 400 round-trips): resolve all
    # location + bin names in one query each, and both line counts in one grouped query.
    loc_ids = {s.location_id for s in rows if s.location_id}
    bin_ids = {s.bin_id for s in rows if s.bin_id}
    st_ids = [s.id for s in rows]
    loc_names = dict((await db.execute(select(Location.id, Location.name)
        .where(Location.id.in_(loc_ids)))).all()) if loc_ids else {}
    bin_names = dict((await db.execute(select(StorageBin.id, StorageBin.name)
        .where(StorageBin.id.in_(bin_ids)))).all()) if bin_ids else {}
    counts: dict[str, tuple[int, int]] = {}
    if st_ids:
        for sid, n, counted in (await db.execute(
            select(StocktakeLine.stocktake_id, func.count(),
                   func.count(StocktakeLine.counted_qty))
            .where(StocktakeLine.stocktake_id.in_(st_ids))
            .group_by(StocktakeLine.stocktake_id))).all():
            counts[sid] = (n, counted)
    out = []
    for s in rows:
        n, counted = counts.get(s.id, (0, 0))
        out.append({"id": s.id, "status": s.status, "location_name": loc_names.get(s.location_id),
                    "bin_code": bin_names.get(s.bin_id) if s.bin_id else None,
                    "note": s.note, "lines": n, "counted": counted,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "posted_at": s.posted_at.isoformat() if s.posted_at else None})
    return {"items": out}


# ── Build 91: stocktake depth ────────────────────────────────────────────────
class ScanIn(BaseModel):
    code: str                       # barcode, SKU, or a unit serial
    qty: float = 1                  # each scan adds this much
    bin_id: Optional[str] = None
    register_unknown: bool = False  # create a product for a code we don't recognise


@st_router.post("/{st_id}/scan")
async def scan_count(
    st_id: str, data: ScanIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Count by scanning. Each scan *increments* the counted quantity rather than
    setting it, because that's how counting with a scanner actually works — you point
    it at each item as you pick it up.

    A code the system doesn't know is either rejected (so a typo can't silently invent
    stock) or, with `register_unknown`, registered as a new product and flagged on the
    count sheet so someone reviews it before posting.
    """
    from backend.models import SerializedItem
    st = await _load_open_stocktake(db, current, st_id)

    part = None
    unit = None
    from backend.services import scan_codes
    _scan = scan_codes.parse(data.code)    # ii144b: AIM prefix stripped; a GS1 label's (21) is the unit
    code = data.code.strip()
    unit_codes = [t for t in (_scan.serial, _scan.clean) if t]

    # a serial identifies one specific unit
    from backend.services.serial_intake import matches as _sn_matches
    unit = (await db.execute(select(SerializedItem).where(
        SerializedItem.org_id == current.org_id, or_(*[_sn_matches(SerializedItem.serial_number, t) for t in unit_codes])))).scalars().first()
    if unit:
        part = (await db.execute(select(Part).where(Part.id == unit.part_id))).scalar_one_or_none()
    else:
        part = (await db.execute(select(Part).where(
            Part.org_id == current.org_id,
            product_codes.matches_code(current.org_id, code)))).scalars().first()   # ii144a: extra barcodes

    is_unknown = False
    if not part:
        if not data.register_unknown:
            raise HTTPException(404, f"Nothing matches “{code}”. Tick “register unknown” "
                                     f"to add it as a new product.")
        # a GS1 label registers its product number, not the unit's whole label (serial, batch…)
        code = ((_scan.gtin[1:] if _scan.gtin[0] == "0" else _scan.gtin) if _scan.gtin else _scan.clean) or code
        part = Part(org_id=current.org_id, name=f"Unknown item {code}", sku=code,
                    barcode=code, cost_price=0, sell_price=0)
        db.add(part)
        await db.flush()
        is_unknown = True

    scan_bin = data.bin_id or st.bin_id
    line = (await db.execute(select(StocktakeLine).where(
        StocktakeLine.stocktake_id == st_id, StocktakeLine.part_id == part.id,
        StocktakeLine.bin_id == scan_bin if scan_bin else StocktakeLine.bin_id.is_(None)))).scalars().first()
    if not line:
        # not seeded → either a partial count, or a product the warehouse had no row for
        if data.bin_id:
            await stock_rows.check_bin(db, current.org_id, st.location_id, data.bin_id)
        sl = await _st_stock_row(db, part.id, st, data.bin_id or st.bin_id)
        line = StocktakeLine(stocktake_id=st_id, part_id=part.id,
                             bin_id=data.bin_id or st.bin_id,
                             system_qty=sl or 0,
                             counted_qty=0, is_unknown=is_unknown or sl is None)
        db.add(line)
        await db.flush()

    line.counted_qty = (line.counted_qty or 0) + data.qty
    line.variance = round(line.counted_qty - (line.system_qty or 0), 3)

    # a serialized unit that the system thinks lives somewhere else is worth flagging
    if unit:
        line.serialized_item_id = unit.id
        if st.warehouse_id and unit.warehouse_id and unit.warehouse_id != st.warehouse_id:
            line.serial_mismatch = True
            line.note = "System has this serial in a different warehouse"

    await db.commit()
    return {"ok": True, "part_id": part.id, "part_name": part.name,
            "counted_qty": line.counted_qty, "variance": line.variance,
            "is_unknown": line.is_unknown, "serial_mismatch": line.serial_mismatch}


@st_router.get("/{st_id}/lines")
async def stocktake_lines(
    st_id: str,
    diff: Optional[str] = None,     # surplus|shortage|match|uncounted
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Count lines, filterable by *what kind of difference* they show — which is the
    question you're actually asking when reviewing a count."""
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    rows = (await db.execute(select(StocktakeLine, Part.name, Part.sku)
        .join(Part, StocktakeLine.part_id == Part.id)
        .where(StocktakeLine.stocktake_id == st_id))).all()

    def keep(ln):
        if diff == "uncounted":
            return ln.counted_qty is None
        if ln.counted_qty is None:
            return diff is None
        v = ln.variance or 0
        if diff == "surplus":
            return v > 0
        if diff == "shortage":
            return v < 0
        if diff == "match":
            return v == 0
        return True

    items = [{
        "id": ln.id, "part_id": ln.part_id, "part_name": name, "sku": sku,
        "system_qty": ln.system_qty, "counted_qty": ln.counted_qty,
        "variance": ln.variance, "bin_id": ln.bin_id,
        "is_unknown": ln.is_unknown, "serial_mismatch": ln.serial_mismatch,
        "note": ln.note,
    } for ln, name, sku in rows if keep(ln)]

    counted = [ln for ln, _, _ in rows if ln.counted_qty is not None]
    return {
        "id": st.id, "status": st.status, "kind": st.kind,
        "warehouse_id": st.warehouse_id, "bin_id": st.bin_id,
        "items": items,
        "summary": {
            "total": len(rows),
            "counted": len(counted),
            "uncounted": len(rows) - len(counted),
            "surplus": sum(1 for ln in counted if (ln.variance or 0) > 0),
            "shortage": sum(1 for ln in counted if (ln.variance or 0) < 0),
            "unknown": sum(1 for ln, _, _ in rows if ln.is_unknown),
            "serial_mismatch": sum(1 for ln, _, _ in rows if ln.serial_mismatch),
        },
    }


class CountImportIn(BaseModel):
    rows: list[dict] = Field(max_length=MAX_IMPORT_ROWS)  # [{code, counted_qty}, ...]
    code_field: str = "code"
    qty_field: str = "counted_qty"
    register_unknown: bool = False


@st_router.post("/{st_id}/import")
async def import_counts(
    st_id: str, data: CountImportIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Import counts from a file (the count sheet came back from the shelf on paper or
    from a handheld). Unlike scanning, an imported quantity is the **total** count —
    it's what the counter wrote down, not one more of something."""
    st = await _load_open_stocktake(db, current, st_id)
    applied = 0
    errors: list[dict] = []

    for i, raw in enumerate(data.rows):
        code = str(raw.get(data.code_field, "") or "").strip()
        if not code:
            errors.append({"row": i + 1, "error": "Missing code"})
            continue
        try:
            qty = float(raw.get(data.qty_field))
        except (TypeError, ValueError):
            errors.append({"row": i + 1, "error": "Quantity isn't a number"})
            continue

        part = (await db.execute(select(Part).where(
            Part.org_id == current.org_id,
            product_codes.matches_code(current.org_id, code)))).scalars().first()   # ii144a: extra barcodes
        if not part:
            if not data.register_unknown:
                errors.append({"row": i + 1, "error": f"Unknown code “{code}”"})
                continue
            part = Part(org_id=current.org_id, name=f"Unknown item {code}", sku=code,
                        barcode=code, cost_price=0, sell_price=0)
            db.add(part)
            await db.flush()

        line = (await db.execute(select(StocktakeLine).where(
            StocktakeLine.stocktake_id == st_id, StocktakeLine.part_id == part.id,
            StocktakeLine.bin_id == st.bin_id if st.bin_id else StocktakeLine.bin_id.is_(None)))).scalars().first()
        if not line:
            sl = await _st_stock_row(db, part.id, st, st.bin_id)
            line = StocktakeLine(stocktake_id=st_id, part_id=part.id, bin_id=st.bin_id,
                                 system_qty=sl or 0,
                                 is_unknown=sl is None)
            db.add(line)
            await db.flush()
        line.counted_qty = qty                       # the sheet is authoritative
        line.variance = round(qty - (line.system_qty or 0), 3)
        applied += 1

    await db.commit()
    return {"ok": True, "applied": applied, "errors": errors}


@st_router.get("/{st_id}/export/csv")
async def export_count_sheet(
    st_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The blank count sheet: what the system thinks is there, with an empty column to
    write the real number into. Feed it back through /import."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    rows = (await db.execute(select(StocktakeLine, Part.name, Part.sku)
        .join(Part, StocktakeLine.part_id == Part.id)
        .where(StocktakeLine.stocktake_id == st_id))).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "name", "system_qty", "counted_qty"])
    for ln, name, sku in rows:
        w.writerow(safe_row([sku or "", name, ln.system_qty or 0,
                             "" if ln.counted_qty is None else ln.counted_qty]))
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=count-{st_id[:8]}.csv"})


@st_router.get("/{st_id}/settlement")
async def settlement_act(
    st_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The settlement act — the document you sign off on: every discrepancy, valued at
    cost, with the totals that say what the count actually cost the business."""
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    rows = (await db.execute(select(StocktakeLine, Part)
        .join(Part, StocktakeLine.part_id == Part.id)
        .where(StocktakeLine.stocktake_id == st_id,
               StocktakeLine.counted_qty.is_not(None)))).all()

    lines, surplus_val, shortage_val = [], 0.0, 0.0
    for ln, part in rows:
        v = ln.variance or 0
        if not v:
            continue
        cost = float(part.avg_cost or part.cost_price or 0)
        value = q2(v * cost)
        if v > 0:
            surplus_val += value
        else:
            shortage_val += value
        lines.append({
            "part_name": part.name, "sku": part.sku,
            "system_qty": ln.system_qty, "counted_qty": ln.counted_qty,
            "variance": v, "unit_cost": cost, "value": value,
            "is_unknown": ln.is_unknown, "serial_mismatch": ln.serial_mismatch,
        })

    return {
        "stocktake_id": st.id, "status": st.status, "kind": st.kind,
        "warehouse_id": st.warehouse_id,
        "created_at": st.created_at.isoformat() if st.created_at else None,
        "posted_at": st.posted_at.isoformat() if st.posted_at else None,
        "lines": lines,
        "surplus_value": q2(surplus_val),
        "shortage_value": q2(shortage_val),
        "net_value": q2(surplus_val + shortage_val),
    }


async def _load_open_stocktake(db: AsyncSession, current: Employee, st_id: str) -> Stocktake:
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    if st.status == "posted":
        raise HTTPException(409, "This stocktake is posted — counts can't change now")
    return st


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
