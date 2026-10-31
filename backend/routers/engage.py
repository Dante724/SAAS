"""Customer engagement (Roadmap A2): gift cards, coupons, loyalty points.

Redemptions land on the order as a negative fee line (so invoices/totals and
payroll's discount handling stay consistent) plus a ledger row; the ledgers
make every balance derivable. Loyalty earning happens automatically in the
payment reconciliation hook.
"""
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Client,
    Coupon,
    Employee,
    GiftCard,
    GiftCardLedger,
    LoyaltyLedger,
    Order,
    OrderLine,
    Organisation,
)
from backend.services.auth import get_current_employee, require_perm, record_scope
from backend.services.money import d2, dec, q2
from backend.services.dates import now_utc

router = APIRouter(prefix="/api", tags=["engage"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

TIERS = [(2000, "gold"), (500, "silver"), (0, "standard")]


def _tier(points: float) -> str:
    for threshold, name in TIERS:
        if points >= threshold:
            return name
    return "standard"


async def _order_or_404(db, org_id, order_id) -> Order:
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


async def _add_discount_line(db, order: Order, description: str, amount: float):
    from backend.routers.orders import _recalc_order
    db.add(OrderLine(order_id=order.id, line_type="fee", kind="custom",
                     description=description, qty=1, unit_price=-q2(amount),
                     total=-q2(amount)))
    await db.flush()
    await _recalc_order(db, order)


# ── Gift cards ───────────────────────────────────────────────────────────────
class GiftCardIn(BaseModel):
    value: float
    client_id: str                       # gift cards are always linked to a client
    expires_at: Optional[datetime] = None   # optional validity date; null = never expires
    # code is always server-generated & unique — not accepted from the caller.


def _gc(g: GiftCard) -> dict:
    return {"id": g.id, "code": g.code, "initial_value": g.initial_value, "balance": g.balance,
            "client_id": g.client_id, "active": g.active,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None}


@router.post("/giftcards", status_code=201)
async def issue_giftcard(
    data: GiftCardIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    if data.value <= 0:
        raise HTTPException(422, "Gift card value must be positive")
    # Gift card must belong to a real client in this org.
    client = (await db.execute(select(Client).where(
        Client.id == data.client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    # Always generate a fresh, guaranteed-unique code (retry on the rare collision).
    code = ""
    for _ in range(8):
        cand = f"GC-{secrets.token_hex(3).upper()}"
        exists = (await db.execute(select(GiftCard).where(
            GiftCard.org_id == current.org_id, GiftCard.code == cand))).scalar_one_or_none()
        if not exists:
            code = cand
            break
    if not code:
        raise HTTPException(500, "Could not allocate a unique gift-card code")
    g = GiftCard(org_id=current.org_id, code=code, initial_value=q2(data.value),
                 balance=q2(data.value), client_id=data.client_id, expires_at=data.expires_at)
    db.add(g)
    await db.flush()
    db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=g.id, amount=q2(data.value),
                          ref_type="manual", note="Issued"))
    await db.commit()
    await db.refresh(g)
    return _gc(g)


@router.get("/giftcards")
async def list_giftcards(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(GiftCard).where(GiftCard.org_id == current.org_id)
                             .order_by(GiftCard.created_at.desc()).limit(200))).scalars().all()
    return {"items": [_gc(g) for g in rows]}


@router.get("/giftcards/{code}")
async def giftcard_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    g = (await db.execute(select(GiftCard).where(
        GiftCard.org_id == current.org_id, GiftCard.code == code.strip().upper()))).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Gift card not found")
    return _gc(g)


class RedeemGiftIn(BaseModel):
    code: str
    amount: Optional[float] = None    # default: min(balance, order balance)


@router.post("/orders/{order_id}/redeem-giftcard")
async def redeem_giftcard(
    order_id: str,
    data: RedeemGiftIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),
):
    order = await _order_or_404(db, current.org_id, order_id)
    g = (await db.execute(select(GiftCard).where(
        GiftCard.org_id == current.org_id, GiftCard.code == data.code.strip().upper()))).scalar_one_or_none()
    if not g or not g.active:
        raise HTTPException(404, "Gift card not found or inactive")
    if g.expires_at and g.expires_at < now_utc():
        raise HTTPException(400, "Gift card has expired")
    from backend.services.order_money import balance_due
    order_balance = await balance_due(db, order)
    amount = q2(data.amount if data.amount is not None else min(g.balance, max(order_balance, 0)))
    if amount <= 0:
        raise HTTPException(400, "Nothing to redeem")
    if amount > order_balance + 0.005:
        # an uncapped redemption drove the order total negative, which refund_overpayment then
        # paid out in cash — turning gift-card balance into money
        raise HTTPException(400, f"Amount exceeds the order balance ({order_balance:.2f})")
    if amount > g.balance:
        raise HTTPException(400, f"Amount exceeds card balance ({g.balance:.2f})")
    # 144bc — guarded atomic decrement: a concurrent redemption can't drive the balance negative.
    new_balance = q2(dec(g.balance) - dec(amount))   # card balance reads back as Decimal
    res = await db.execute(sa_update(GiftCard)
        .where(GiftCard.id == g.id, GiftCard.balance >= amount)
        .values(balance=GiftCard.balance - d2(amount)))   # Decimal: the session evaluates this on g
    if res.rowcount == 0:
        raise HTTPException(409, "Gift card balance changed — please retry")
    db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=g.id, amount=-amount,
                          ref_type="order", ref_id=order.id, note=f"Redeemed on {order.order_number}"))
    await _add_discount_line(db, order, f"Gift card {g.code}", amount)
    await db.commit()
    return {"ok": True, "redeemed": amount, "card_balance": new_balance}


# ── Coupons / promotions ─────────────────────────────────────────────────────
class CouponIn(BaseModel):
    code: str
    name: str = ""
    kind: str = "pct"          # pct|fixed
    value: float = 0.0
    min_total: float = 0.0
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    max_uses: int = 0


@router.post("/coupons", status_code=201)
async def create_coupon(
    data: CouponIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.kind not in ("pct", "fixed") or data.value <= 0:
        raise HTTPException(422, "kind must be pct|fixed with a positive value")
    code = data.code.strip().upper()
    dup = (await db.execute(select(Coupon).where(
        Coupon.org_id == current.org_id, Coupon.code == code))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "Coupon code already exists")
    c = Coupon(org_id=current.org_id, code=code, name=data.name, kind=data.kind, value=data.value,
               min_total=data.min_total, starts_at=data.starts_at, ends_at=data.ends_at,
               max_uses=data.max_uses)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "code": c.code}


@router.get("/coupons")
async def list_coupons(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Coupon).where(Coupon.org_id == current.org_id)
                             .order_by(Coupon.created_at.desc()))).scalars().all()
    return {"items": [{"id": c.id, "code": c.code, "name": c.name, "kind": c.kind, "value": c.value,
                       "min_total": c.min_total, "uses": c.uses, "max_uses": c.max_uses,
                       "active": c.active} for c in rows]}


class ClientCouponIn(BaseModel):
    name: str = ""
    kind: str = "pct"          # pct|fixed
    value: float = 10.0
    min_total: float = 0.0
    max_uses: int = 1          # returning-customer coupons default to single-use


@router.post("/clients/{client_id}/coupons", status_code=201)
async def generate_client_coupon(
    client_id: str,
    data: ClientCouponIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),   # same gate as the other coupon routes
):
    """144bi — mint a coupon issued to a specific returning client. Auto-codes so the
    front desk can generate one in a click and offer it now; it's redeemed on the
    client's next work order."""
    from backend.models import Client as _Client
    cl = (await db.execute(select(_Client).where(
        _Client.id == client_id, _Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Client not found")
    if data.kind not in ("pct", "fixed") or data.value <= 0:
        raise HTTPException(422, "kind must be pct|fixed with a positive value")
    if data.kind == "pct" and data.value > 100:
        raise HTTPException(422, "A percentage coupon can't be more than 100%")
    if not 1 <= data.max_uses <= 100:
        raise HTTPException(422, "A returning-customer coupon can be used 1 to 100 times")
    import random
    import string
    # unique short code for this org
    for _ in range(8):
        code = "C-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        dup = (await db.execute(select(Coupon).where(
            Coupon.org_id == current.org_id, Coupon.code == code))).scalar_one_or_none()
        if not dup:
            break
    c = Coupon(org_id=current.org_id, client_id=client_id, code=code,
               name=data.name or "Returning customer", kind=data.kind, value=data.value,
               min_total=data.min_total, max_uses=max(1, data.max_uses))
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "code": c.code, "value": c.value, "kind": c.kind}


@router.get("/clients/{client_id}/coupons")
async def list_client_coupons(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Coupon).where(
        Coupon.org_id == current.org_id, Coupon.client_id == client_id)
        .order_by(Coupon.created_at.desc()))).scalars().all()
    return {"items": [{
        "id": c.id, "code": c.code, "name": c.name, "kind": c.kind, "value": c.value,
        "min_total": c.min_total, "uses": c.uses, "max_uses": c.max_uses,
        "spent": bool(c.max_uses and c.uses >= c.max_uses),
        "active": c.active} for c in rows]}


class ApplyCouponIn(BaseModel):
    code: str


@router.post("/orders/{order_id}/apply-coupon")
async def apply_coupon(
    order_id: str,
    data: ApplyCouponIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),
):
    order = await _order_or_404(db, current.org_id, order_id)
    c = (await db.execute(select(Coupon).where(
        Coupon.org_id == current.org_id, Coupon.code == data.code.strip().upper()))).scalar_one_or_none()
    now = now_utc()
    if not c or not c.active:
        raise HTTPException(404, "Coupon not found or inactive")
    # 144bi: a client-issued coupon can only be redeemed on that client's order.
    if c.client_id and c.client_id != order.client_id:
        raise HTTPException(403, "This coupon was issued to a different customer")
    if (c.starts_at and now < c.starts_at) or (c.ends_at and now > c.ends_at):
        raise HTTPException(400, "Coupon is not valid at this time")
    if c.max_uses and c.uses >= c.max_uses:
        raise HTTPException(400, "Coupon usage limit reached")
    if (order.total or 0) < c.min_total:
        raise HTTPException(400, f"Order total below coupon minimum ({c.min_total:.2f})")
    already = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.description == f"Coupon {c.code}"))).scalar_one_or_none()
    if already:
        raise HTTPException(400, "Coupon already applied to this order")
    discount = q2((order.total or 0) * c.value / 100.0) if c.kind == "pct" else q2(c.value)
    # Cap at what's still owed, not the order total: a coupon on an order that's already
    # (partly) paid would otherwise leave it "overpaid", and that overpayment can be refunded
    # from the till — a coupon turned into cash (B12).
    from backend.services.order_money import balance_due
    owed = await balance_due(db, order)
    if owed <= 0:
        raise HTTPException(400, "Nothing is owed on this order, so a coupon can't be applied")
    discount = min(discount, owed)
    # Guarded atomic increment: `c.uses += 1` let concurrent redemptions both pass the
    # max_uses check above and overshoot the limit.
    res = await db.execute(sa_update(type(c)).where(
        type(c).id == c.id,
        (type(c).max_uses.is_(None)) | (type(c).max_uses == 0) | (type(c).uses < type(c).max_uses))
        .values(uses=type(c).uses + 1).execution_options(synchronize_session=False))
    if res.rowcount == 0:
        raise HTTPException(409, "Coupon usage limit reached")
    await _add_discount_line(db, order, f"Coupon {c.code}", discount)
    await db.commit()
    return {"ok": True, "discount": discount, "new_total": order.total}


# ── Loyalty ──────────────────────────────────────────────────────────────────
async def loyalty_balance(db: AsyncSession, org_id: str, client_id: str) -> float:
    total = (await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.org_id == org_id, LoyaltyLedger.client_id == client_id))).scalar_one()
    return q2(float(total))


@router.get("/clients/{client_id}/loyalty")
async def client_loyalty(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Client not found")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    bal = await loyalty_balance(db, current.org_id, client_id)
    earned = (await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.org_id == current.org_id, LoyaltyLedger.client_id == client_id,
        LoyaltyLedger.points > 0))).scalar_one()
    return {"client_id": client_id, "points": bal, "lifetime_points": q2(float(earned)),
            "tier": cl.loyalty_tier, "point_value": org.loyalty_point_value,
            "earn_rate": org.loyalty_earn_rate, "enabled": org.loyalty_earn_rate > 0}


class RedeemPointsIn(BaseModel):
    points: float


@router.post("/orders/{order_id}/redeem-loyalty")
async def redeem_loyalty(
    order_id: str,
    data: RedeemPointsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),
):
    order = await _order_or_404(db, current.org_id, order_id)
    if not order.client_id:
        raise HTTPException(400, "Order has no client to redeem points for")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    if org.loyalty_point_value <= 0:
        raise HTTPException(400, "Loyalty redemption is not configured (loyalty_point_value)")
    if data.points <= 0:
        raise HTTPException(422, "points must be positive")
    # Serialise redemptions per client (the POS path already does): two concurrent requests
    # used to both read the same points balance and spend it twice.
    from backend.models import Client as _Client
    await db.execute(select(_Client.id).where(_Client.id == order.client_id).with_for_update())
    bal = await loyalty_balance(db, current.org_id, order.client_id)
    if data.points > bal:
        raise HTTPException(400, f"Client has only {bal:.0f} points")
    # 144bc — cap redeemed value at the order balance so points past what's owed aren't burned.
    from backend.services.order_money import balance_due
    order_balance = await balance_due(db, order)
    amount = q2(data.points * org.loyalty_point_value)
    used_points = data.points
    if amount > order_balance:
        amount = q2(max(order_balance, 0))
        used_points = q2(amount / org.loyalty_point_value) if org.loyalty_point_value else 0
    if used_points <= 0:
        raise HTTPException(400, "Nothing to redeem — order is already covered")
    db.add(LoyaltyLedger(org_id=current.org_id, client_id=order.client_id, points=-used_points,
                         ref_type="order", ref_id=order.id, note=f"Redeemed on {order.order_number}"))
    await _add_discount_line(db, order, f"Loyalty points ({used_points:.0f})", amount)
    await db.commit()
    return {"ok": True, "discount": amount, "points_left": q2(bal - used_points)}
