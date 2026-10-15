"""Sales (over-the-counter POS) + Stocktake (count → variance → adjust)."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
from backend.services.auth import get_current_employee, require_perm
from backend.services.numbering import next_number
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/sales", tags=["sales"])
st_router = APIRouter(prefix="/api/stocktake", tags=["stocktake"])


async def _can_override_reservation(db, current) -> bool:
    """144at — opt-in (default-deny) override: owner, or a role with can_override_reservation
    explicitly on, may consume stock reserved for a job."""
    if not getattr(current, "role_id", None):
        return True
    from backend.models import Role
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    return bool(role and (role.permissions or {}).get("can_override_reservation") is True)


# ─────────────────────────────── SALES ───────────────────────────────
class SaleLineIn(BaseModel):
    kind: str  # product|service
    ref_id: Optional[str] = None
    description: str = ""
    qty: float = 1
    unit_price: float = 0
    discount: float = 0  # pct


class SaleIn(BaseModel):
    client_id: Optional[str] = None
    account_id: Optional[str] = None  # cashbox to take payment into
    discount: float = 0               # whole-sale flat amount
    lines: list[SaleLineIn] = []
    coupon_code: Optional[str] = None     # 144bb — POS rewards: fold into discount + decrement
    gift_card_code: Optional[str] = None
    redeem_points: float = 0


async def _pos_redemptions(db, org_id, sale, client_id, gross, data):
    """144bb — resolve coupon / gift card / loyalty at POS into a single extra discount, and
    decrement the gift-card and loyalty ledgers (ref_type='sale'). Returns (amount, detail).
    Mirrors the order-based engage.py rules; no-op when no reward fields are passed so plain
    sales are byte-identical to before."""
    from backend.models import Client, GiftCard, GiftCardLedger, Coupon, LoyaltyLedger, Organisation
    from sqlalchemy import func as _f
    extra = 0.0
    detail: dict = {}
    remaining = max(gross - (data.discount or 0), 0)
    if data.coupon_code:
        c = (await db.execute(select(Coupon).where(
            Coupon.org_id == org_id, Coupon.code == data.coupon_code.strip().upper()))).scalar_one_or_none()
        now = datetime.utcnow()
        if not c or not c.active:
            raise HTTPException(404, "Coupon not found or inactive")
        if c.client_id and c.client_id != client_id:
            raise HTTPException(403, "This coupon was issued to a different customer")
        if (c.starts_at and now < c.starts_at) or (c.ends_at and now > c.ends_at):
            raise HTTPException(400, "Coupon is not valid at this time")
        if c.max_uses and c.uses >= c.max_uses:
            raise HTTPException(400, "Coupon usage limit reached")
        if gross < c.min_total:
            raise HTTPException(400, f"Order total below coupon minimum ({c.min_total:.2f})")
        amt = q2(gross * c.value / 100.0) if c.kind == "pct" else q2(c.value)
        amt = min(amt, remaining)
        # guarded atomic increment (like the gift-card path) — refuse if a concurrent
        # redemption hit max_uses between the read above and this write.
        res = await db.execute(sa_update(Coupon)
            .where(Coupon.id == c.id, or_(Coupon.max_uses.is_(None), Coupon.uses < Coupon.max_uses))
            .values(uses=Coupon.uses + 1))
        if res.rowcount == 0:
            raise HTTPException(400, "Coupon usage limit reached")
        extra += amt; remaining -= amt; detail["coupon"] = amt
    if data.gift_card_code:
        g = (await db.execute(select(GiftCard).where(
            GiftCard.org_id == org_id, GiftCard.code == data.gift_card_code.strip().upper()))).scalar_one_or_none()
        if not g or not g.active:
            raise HTTPException(404, "Gift card not found or inactive")
        if g.expires_at and g.expires_at < datetime.utcnow():
            raise HTTPException(400, "Gift card has expired")
        amt = q2(min(g.balance, max(remaining, 0)))
        if amt > 0:
            # 144bc — guarded atomic decrement: refuse to drive the balance negative if a
            # concurrent redemption drained the card between the read above and this write.
            res = await db.execute(sa_update(GiftCard)
                .where(GiftCard.id == g.id, GiftCard.balance >= amt)
                .values(balance=GiftCard.balance - amt))
            if res.rowcount == 0:
                raise HTTPException(409, "Gift card balance changed — please retry")
            db.add(GiftCardLedger(org_id=org_id, gift_card_id=g.id, amount=-amt,
                                  ref_type="sale", ref_id=sale.id, note=f"Redeemed on {sale.sale_number}"))
            extra += amt; remaining -= amt; detail["gift_card"] = amt
    if data.redeem_points and data.redeem_points > 0:
        if not client_id:
            raise HTTPException(400, "Select a customer to redeem points")
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
        if org.loyalty_point_value <= 0:
            raise HTTPException(400, "Loyalty redemption is not configured")
        # Lock the client so two concurrent redemptions can't both read the same point balance and
        # over-spend (no-op on SQLite; real serialization on Postgres).
        await db.execute(select(Client.id).where(
            Client.id == client_id, Client.org_id == org_id).with_for_update())
        bal = float((await db.execute(select(_f.coalesce(_f.sum(LoyaltyLedger.points), 0.0)).where(
            LoyaltyLedger.org_id == org_id, LoyaltyLedger.client_id == client_id))).scalar_one())
        if data.redeem_points > bal:
            raise HTTPException(400, f"Client has only {bal:.0f} points")
        # 144bc — cap redeemed value at what's still owed so points past the bill aren't burned.
        amt = q2(data.redeem_points * org.loyalty_point_value)
        used_points = data.redeem_points
        if amt > remaining:
            amt = q2(max(remaining, 0))
            used_points = q2(amt / org.loyalty_point_value) if org.loyalty_point_value else 0
        if used_points > 0:
            db.add(LoyaltyLedger(org_id=org_id, client_id=client_id, points=-used_points,
                                 ref_type="sale", ref_id=sale.id, note=f"Redeemed on {sale.sale_number}"))
            extra += amt; remaining -= amt; detail["loyalty"] = amt
    return q2(extra), detail


@router.get("")
async def list_sales(
    location_id: Optional[str] = None,        # build 138: scope sales to a branch
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    _q = select(Sale).where(Sale.org_id == current.org_id)
    if location_id:
        _q = _q.where(Sale.location_id == location_id)
    rows = (await db.execute(_q.order_by(Sale.created_at.desc()).limit(200))).scalars().all()
    return {"items": [{
        "id": s.id, "sale_number": s.sale_number, "client_id": s.client_id,
        "total": s.total, "total_cost": s.total_cost, "profit": q2(s.total - s.total_cost),
        "paid": s.paid, "created_at": s.created_at.isoformat() if s.created_at else None,
    } for s in rows]}


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
    # A seller without a home location (e.g. the org owner) still needs a location on the sale —
    # sales.location_id is NOT NULL. Fall back to the org's first location rather than 500.
    loc_id = current.location_id
    if not loc_id:
        from backend.models import Location
        loc_id = (await db.execute(select(Location.id).where(
            Location.org_id == current.org_id).order_by(Location.created_at).limit(1))).scalar_one_or_none()
        if not loc_id:
            raise HTTPException(400, "No location is set up for this sale")
    sale = Sale(org_id=current.org_id, location_id=loc_id,
                sale_number=await next_number(db, current.org_id, "sale"), client_id=data.client_id,
                seller_id=current.id, account_id=data.account_id, discount=data.discount)
    db.add(sale)
    await db.flush()

    total = 0.0
    total_cost = 0.0
    for li in data.lines:
        line_total = q2(li.unit_price * li.qty * (1 - (li.discount or 0) / 100))
        cost = 0.0
        if li.kind == "product" and li.ref_id:
            part = (await db.execute(select(Part).where(Part.id == li.ref_id))).scalar_one_or_none()
            if not part:
                raise HTTPException(404, f"Product not found: {li.description}")
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == li.ref_id,
                StockLevel.location_id == loc_id))).scalar_one_or_none()
            if not sl or (sl.qty_available or 0) < li.qty:
                raise HTTPException(409, f"Only {(sl.qty_available if sl else 0):g} of '{part.name}' in this warehouse")
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
            res = await db.execute(sa_update(StockLevel)
                .where(StockLevel.id == sl.id, StockLevel.qty_available >= li.qty)
                .values(qty_available=StockLevel.qty_available - li.qty))
            if res.rowcount == 0:
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
                except HTTPException:
                    pass
            cost = q4(unit_cost * li.qty)
            db.add(StockMovement(part_id=li.ref_id, location_id=loc_id,
                                 movement_type="use", qty=-li.qty, unit_cost=unit_cost,
                                 ref_type="sale", ref_id=sale.id))
            from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
            await _alerts.maybe_low_stock_by_id(db, current.org_id, li.ref_id)
        db.add(SaleLine(sale_id=sale.id, kind=li.kind, ref_id=li.ref_id,
                        description=li.description, qty=li.qty, unit_price=li.unit_price,
                        cost_price=cost / li.qty if li.qty else 0, discount=li.discount,
                        total=line_total))
        total += line_total
        total_cost += cost

    # 144bb — POS rewards: coupon / gift card / loyalty fold into the sale discount and
    # decrement their ledgers. No-op (and byte-identical) when no reward fields are passed.
    reward_amt, reward_detail = await _pos_redemptions(db, current.org_id, sale, data.client_id, total, data)
    sale.discount = q2((data.discount or 0) + reward_amt)
    sale.total = q2(max(total - sale.discount, 0))
    sale.total_cost = q2(total_cost)

    # Payment straight into a cashbox (signed ledger entry linked to the sale)
    if data.account_id:
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "income", sale.total,
                               comment=f"Sale {sale.sale_number}", ref_type="sale",
                               ref_id=sale.id, employee_id=current.id)
        sale.paid = True

    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "sale", sale.id,
                           f"Sale {sale.sale_number}: {len(data.lines)} line(s), total {sale.total:g}")
    await db.commit()
    return {"id": sale.id, "sale_number": sale.sale_number, "total": sale.total,
            "total_cost": sale.total_cost, "profit": q2(sale.total - sale.total_cost),
            "paid": sale.paid, "redeemed": reward_detail}


# ───────────────────────────── STOCKTAKE ─────────────────────────────
async def _st_stock_row(db, part_id, st):
    """144bh — resolve a part's StockLevel for a stocktake, preferring the warehouse-keyed
    row but falling back to the location-level row that postings/sales/PO actually write
    (warehouse_id NULL). Without this a count reads system_qty 0 and posting duplicates stock."""
    row = None
    if st.warehouse_id:
        row = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part_id,
            StockLevel.warehouse_id == st.warehouse_id))).scalars().first()
    if row is None:
        row = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part_id,
            StockLevel.location_id == st.location_id))).scalars().first()
    return row


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

    st = Stocktake(org_id=current.org_id, location_id=w.location_id, warehouse_id=w.id,
                   bin_id=data.bin_id, note=data.note, kind=data.kind,
                   created_by_id=current.id)
    db.add(st)
    await db.flush()
    seeded = 0
    if data.kind == "full":
        q = select(StockLevel).where(StockLevel.warehouse_id == w.id)
        if data.bin_id:
            q = q.where(StockLevel.bin_id == data.bin_id)
        rows = (await db.execute(q)).scalars().all()
        if not rows and not data.bin_id:
            # 144bh — stock is kept at location level (warehouse_id NULL); seed from there so a
            # full count isn't empty on orgs whose stock was never warehouse-keyed.
            rows = (await db.execute(select(StockLevel).where(
                StockLevel.location_id == w.location_id))).scalars().all()
        for sl in rows:
            db.add(StocktakeLine(stocktake_id=st.id, part_id=sl.part_id,
                                 bin_id=sl.bin_id, system_qty=sl.qty_available or 0))
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
        sl = await _st_stock_row(db, l.part_id, st)
        if not sl:
            # Build 91: a product found on the shelf that the system had no row for.
            # Posting the count is what brings it into stock — that's the whole point of
            # registering unknowns during a count.
            sl = StockLevel(part_id=l.part_id, location_id=st.location_id,
                            warehouse_id=st.warehouse_id, bin_id=l.bin_id or st.bin_id,
                            qty_available=0, qty_reserved=0)
            db.add(sl)
        sl.qty_available = (sl.qty_available or 0) + l.variance
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
    code = data.code.strip()

    # a serial identifies one specific unit
    unit = (await db.execute(select(SerializedItem).where(
        SerializedItem.org_id == current.org_id, SerializedItem.serial_number == code))).scalars().first()
    if unit:
        part = (await db.execute(select(Part).where(Part.id == unit.part_id))).scalar_one_or_none()
    else:
        part = (await db.execute(select(Part).where(
            Part.org_id == current.org_id,
            or_(Part.barcode == code, Part.sku == code)))).scalars().first()

    is_unknown = False
    if not part:
        if not data.register_unknown:
            raise HTTPException(404, f"Nothing matches “{code}”. Tick “register unknown” "
                                     f"to add it as a new product.")
        part = Part(org_id=current.org_id, name=f"Unknown item {code}", sku=code,
                    barcode=code, cost_price=0, sell_price=0)
        db.add(part)
        await db.flush()
        is_unknown = True

    line = (await db.execute(select(StocktakeLine).where(
        StocktakeLine.stocktake_id == st_id,
        StocktakeLine.part_id == part.id))).scalars().first()
    if not line:
        # not seeded → either a partial count, or a product the warehouse had no row for
        sl = await _st_stock_row(db, part.id, st)
        line = StocktakeLine(stocktake_id=st_id, part_id=part.id,
                             bin_id=data.bin_id or st.bin_id,
                             system_qty=(sl.qty_available if sl else 0) or 0,
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
    rows: list[dict]                        # [{code, counted_qty}, ...]
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
            or_(Part.barcode == code, Part.sku == code)))).scalars().first()
        if not part:
            if not data.register_unknown:
                errors.append({"row": i + 1, "error": f"Unknown code “{code}”"})
                continue
            part = Part(org_id=current.org_id, name=f"Unknown item {code}", sku=code,
                        barcode=code, cost_price=0, sell_price=0)
            db.add(part)
            await db.flush()

        line = (await db.execute(select(StocktakeLine).where(
            StocktakeLine.stocktake_id == st_id,
            StocktakeLine.part_id == part.id))).scalars().first()
        if not line:
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == part.id,
                StockLevel.warehouse_id == st.warehouse_id))).scalars().first()
            line = StocktakeLine(stocktake_id=st_id, part_id=part.id,
                                 system_qty=(sl.qty_available if sl else 0) or 0,
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
        w.writerow([sku or "", name, ln.system_qty or 0,
                    "" if ln.counted_qty is None else ln.counted_qty])
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
