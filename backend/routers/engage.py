"""Customer engagement (Roadmap A2): gift cards, coupons, loyalty points.

Redemptions land on the order as a negative fee line (so invoices/totals and
payroll's discount handling stay consistent) plus a ledger row; the ledgers
make every balance derivable. Loyalty earning happens automatically in the
payment reconciliation hook.
"""
import secrets
from decimal import Decimal
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


async def _editable_order(db, current, order_id) -> Order:
    """The job a discount goes on, locked, and only while it can still be edited: a closed job
    takes no new discount lines without "Edit the closed ticket", a locked status none at all
    (audit Q20 — these three paths skipped the guard every line edit has)."""
    from backend.routers.orders import _guard_editable, _guard_order_access
    order = await _order_or_404(db, current.org_id, order_id, lock=True)
    await _guard_order_access(db, current, order)
    await _guard_editable(db, order, current)
    return order


async def _order_or_404(db, org_id, order_id, *, lock: bool = False) -> Order:
    q = select(Order).where(Order.id == order_id, Order.org_id == org_id)
    if lock:
        # Discounts are capped at what's still owed; two redemptions at once both read the same
        # balance and over-discounted the job (audit 2026-10-29, H5). Serialise them per order.
        q = q.with_for_update()
    o = (await db.execute(q)).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


async def _add_discount_line(db, order: Order, description: str, amount: float, *, ref_id=None):
    from backend.routers.orders import _recalc_order
    db.add(OrderLine(order_id=order.id, line_type="fee", kind="custom", ref_id=ref_id,
                     description=description, qty=1, unit_price=-q2(amount),
                     total=-q2(amount)))
    await db.flush()
    await _recalc_order(db, order)


# ── Audit FIN-03: removing a redemption line gives back what it spent ───────────────────────────
# A gift card, coupon or loyalty redemption is a negative fee line on the job plus a ledger row
# (or a coupon use). Deleting the line put the job's price back but the card stayed spent, the
# coupon stayed used and the points stayed burned; editing its price made the discount and the
# ledger disagree. The lines are recognised by their description (all existing rows carry it; no
# migration in this wave) and, for points, by ref_id = the ledger row on lines made from now on.
GIFT_PREFIX, COUPON_PREFIX, LOYALTY_PREFIX = "Gift card ", "Coupon ", "Loyalty points ("


def redemption_kind(line: OrderLine) -> Optional[str]:
    """'giftcard' | 'coupon' | 'loyalty' for a redemption line, else None."""
    if (line.line_type or "") != "fee" or (line.kind or "custom") != "custom":
        return None
    if float(line.total or 0) >= 0 and float(line.unit_price or 0) >= 0:
        return None
    d = line.description or ""
    if d.startswith(GIFT_PREFIX):
        return "giftcard"
    if d.startswith(COUPON_PREFIX):
        return "coupon"
    if d.startswith(LOYALTY_PREFIX):
        return "loyalty"
    return None


REDEMPTION_LOCKED = ("This line is a {what} redemption. Its amount can't be edited; delete the line to "
                     "give the {back} back, then redeem again.")
_WHAT = {"giftcard": ("gift card", "card balance"), "coupon": ("coupon", "coupon use"),
         "loyalty": ("loyalty points", "points")}


def refuse_redemption_edit(line: OrderLine) -> None:
    kind = redemption_kind(line)
    if kind:
        what, back = _WHAT[kind]
        raise HTTPException(409, REDEMPTION_LOCKED.format(what=what, back=back))


async def give_back_redemption(db, org_id: str, order: Order, line: OrderLine,
                               actor_id: Optional[str] = None) -> list[OrderLine]:
    """Undo what a redemption line spent, under a row lock on the card, coupon or client. Called
    by the job's line delete (which holds the job's lock) before the line goes. Returns every line
    to remove: a coupon split across tax groups goes as a whole, so it is applied or not. Never
    gives back more than this job actually redeemed from that card / client."""
    kind = redemption_kind(line)
    if not kind:
        return [line]
    desc = line.description or ""
    label = f"Removed from {order.order_number}"
    if kind == "giftcard":
        code = desc[len(GIFT_PREFIX):].strip().upper()
        g = (await db.execute(select(GiftCard).where(GiftCard.org_id == org_id, GiftCard.code == code)
                              .with_for_update())).scalar_one_or_none()
        if g is not None:
            redeemed = -dec((await db.execute(select(func.coalesce(func.sum(GiftCardLedger.amount), 0)).where(
                GiftCardLedger.org_id == org_id, GiftCardLedger.gift_card_id == g.id,
                GiftCardLedger.ref_type == "order", GiftCardLedger.ref_id == order.id))).scalar())
            back = d2(min(-dec(line.total), redeemed))
            if back > 0:
                await db.execute(sa_update(GiftCard).where(GiftCard.id == g.id)
                                 .values(balance=GiftCard.balance + back)
                                 .execution_options(synchronize_session=False))
                db.add(GiftCardLedger(org_id=org_id, gift_card_id=g.id, amount=back, ref_type="order",
                                      ref_id=order.id, note=f"Returned: {label}"[:200]))
        return [line]
    if kind == "coupon":
        code = desc[len(COUPON_PREFIX):].strip().upper()
        siblings = (await db.execute(select(OrderLine).where(
            OrderLine.order_id == order.id, OrderLine.description == desc,
            OrderLine.line_type == "fee"))).scalars().all()
        siblings = [s for s in siblings if redemption_kind(s) == "coupon"] or [line]
        c = (await db.execute(select(Coupon).where(Coupon.org_id == org_id, Coupon.code == code)
                              .with_for_update())).scalar_one_or_none()
        # Re-review N5: only a line apply-coupon made (ref_id = the coupon) gives a use back; a line
        # that merely says "Coupon CODE" never took one. Lines from before the link give none back.
        if c is not None and any(s.ref_id == c.id for s in siblings):
            await db.execute(sa_update(Coupon).where(Coupon.id == c.id, Coupon.uses > 0)
                             .values(uses=Coupon.uses - 1).execution_options(synchronize_session=False))
        return siblings
    # loyalty: the points go back to the client who spent them (re-review N8), which is the burn
    # row's client, not the job's current one if the job changed hands since.
    burn_row = None
    if line.ref_id:
        burn_row = (await db.execute(select(LoyaltyLedger).where(
            LoyaltyLedger.id == line.ref_id, LoyaltyLedger.org_id == org_id))).scalar_one_or_none()
    spender = burn_row.client_id if burn_row is not None else order.client_id
    if burn_row is None:   # a line from before ref_id was set: whoever burned points on this job
        net = (await db.execute(select(LoyaltyLedger.client_id, func.sum(LoyaltyLedger.points)).where(
            LoyaltyLedger.org_id == org_id, LoyaltyLedger.ref_type == "order",
            LoyaltyLedger.ref_id == order.id).group_by(LoyaltyLedger.client_id))).all()
        spent = sorted(((float(p or 0), cid) for cid, p in net if float(p or 0) < 0))
        if spent and not any(cid == order.client_id for _p, cid in spent):
            spender = spent[0][1]
    if spender:
        await db.execute(select(Client.id).where(Client.id == spender).with_for_update())
        burned = -float((await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
            LoyaltyLedger.org_id == org_id, LoyaltyLedger.client_id == spender,
            LoyaltyLedger.ref_type == "order", LoyaltyLedger.ref_id == order.id))).scalar() or 0)
        points = -float(burn_row.points) if burn_row is not None else None
        if points is None:   # a line from before ref_id was set: the points its description shows
            try:
                points = float(desc[len(LOYALTY_PREFIX):].split(")")[0])
            except ValueError:
                points = 0.0
        back = q2(min(points, burned))
        if back > 0:
            db.add(LoyaltyLedger(org_id=org_id, client_id=spender, points=back, ref_type="order",
                                 ref_id=order.id, note=f"Returned: {label}"[:200]))
    return [line]


# ── Gift cards ───────────────────────────────────────────────────────────────
class GiftCardIn(BaseModel):
    value: float
    client_id: str                       # gift cards are always linked to a client
    expires_at: Optional[datetime] = None   # optional validity date; null = never expires
    # code is always server-generated & unique — not accepted from the caller.
    # Audit FIN-09: a card sold for money takes that money into a cashbox here. Without an account
    # the card is a gift from the shop (a promotion), as before: no money moves.
    account_id: Optional[str] = None


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
    g = GiftCard(org_id=current.org_id, code=code, initial_value=d2(data.value),
                 balance=d2(data.value), client_id=data.client_id, expires_at=data.expires_at)
    db.add(g)
    await db.flush()
    db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=g.id, amount=d2(data.value),
                          ref_type="manual", note="Issued" if not data.account_id else "Issued (sold)"))
    if data.account_id:
        # Money in for a liability, not a sale: revenue is earned when the card is spent. The leg
        # is an "income" movement of the till (so the drawer counts the cash), tagged
        # ref_type="gift_card"; the P&L takes revenue from documents, so it isn't counted twice.
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "income", q2(data.value),
                               comment=f"Gift card {code} sold", ref_type="gift_card", ref_id=g.id,
                               employee_id=current.id)
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


@router.get("/giftcards/liability")
async def giftcard_liability(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Audit FIN-09: what the shop still owes on gift cards (unspent, live balances), the deferred
    revenue a balance sheet carries. Expired or deactivated balances are listed apart."""
    now = now_utc()
    live = (GiftCard.active == True) & ((GiftCard.expires_at.is_(None)) | (GiftCard.expires_at >= now))  # noqa: E712
    rows = (await db.execute(select(live, func.count(), func.coalesce(func.sum(GiftCard.balance), 0))
                             .where(GiftCard.org_id == current.org_id, GiftCard.balance > 0)
                             .group_by(live))).all()
    out = {"outstanding": 0.0, "cards": 0, "lapsed": 0.0, "lapsed_cards": 0}
    for is_live, n, total in rows:
        if is_live:
            out["outstanding"], out["cards"] = q2(total), n
        else:
            out["lapsed"], out["lapsed_cards"] = q2(total), n
    return out


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
    order = await _editable_order(db, current, order_id)
    g = (await db.execute(select(GiftCard).where(
        GiftCard.org_id == current.org_id, GiftCard.code == data.code.strip().upper()))).scalar_one_or_none()
    if not g or not g.active:
        raise HTTPException(404, "Gift card not found or inactive")
    if g.expires_at and g.expires_at < now_utc():
        raise HTTPException(400, "Gift card has expired")
    from backend.services.order_money import balance_due
    order_balance = await balance_due(db, order)
    # Decimal from here on: a float compared with the Decimal balance is exact binary vs exact
    # decimal, so 30.01 > Decimal("30.01") is True and paying off the card would be refused.
    amount = d2(data.amount if data.amount is not None else min(g.balance, max(dec(order_balance), 0)))
    if amount <= 0:
        raise HTTPException(400, "Nothing to redeem")
    if amount > dec(order_balance) + Decimal("0.005"):
        # an uncapped redemption drove the order total negative, which refund_overpayment then
        # paid out in cash — turning gift-card balance into money
        raise HTTPException(400, f"Amount exceeds the order balance ({order_balance:.2f})")
    if amount > g.balance:
        raise HTTPException(400, f"Amount exceeds card balance ({g.balance:.2f})")
    # 144bc — guarded atomic decrement: a concurrent redemption can't drive the balance negative.
    new_balance = q2(g.balance - amount)
    res = await db.execute(sa_update(GiftCard)
        .where(GiftCard.id == g.id, GiftCard.balance >= amount)
        .values(balance=GiftCard.balance - amount)
        .execution_options(synchronize_session=False))   # don't re-evaluate on g (may hold a float)
    if res.rowcount == 0:
        raise HTTPException(409, "Gift card balance changed — please retry")
    db.add(GiftCardLedger(org_id=current.org_id, gift_card_id=g.id, amount=-amount,
                          ref_type="order", ref_id=order.id, note=f"Redeemed on {order.order_number}"))
    await _add_discount_line(db, order, f"Gift card {g.code}", amount)
    await db.commit()
    return {"ok": True, "redeemed": float(amount), "card_balance": new_balance}


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
    from backend.services.coupons import check_coupon_value
    check_coupon_value(data.kind, data.value)
    if data.min_total < 0 or data.max_uses < 0:
        raise HTTPException(422, "Minimum order and usage limit can't be negative")
    code = data.code.strip().upper()
    dup = (await db.execute(select(Coupon).where(
        Coupon.org_id == current.org_id, Coupon.code == code))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "Coupon code already exists")
    c = Coupon(org_id=current.org_id, code=code, name=data.name, kind=data.kind,
               value=data.value if data.kind == "pct" else q2(data.value),     # QA 2026-10-31 B
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
    from backend.services.coupons import check_coupon_value
    check_coupon_value(data.kind, data.value)
    if not 1 <= data.max_uses <= 100:
        raise HTTPException(422, "A returning-customer coupon can be used 1 to 100 times")
    if data.min_total < 0:     # audit DB-07: the coupons CHECK refuses it (a 500) — say why instead
        raise HTTPException(422, "Minimum order can't be negative")
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
    from backend.models import Client as _Client
    from backend.services.owned import get_owned_or_404
    await get_owned_or_404(db, _Client, client_id, current.org_id, "client")
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
    order = await _editable_order(db, current, order_id)
    # The coupon reads the job's lines and balance, then adds lines and recomputes the total:
    # hold the job's write lock (SQLite ignores FOR UPDATE) so concurrent edits wait for it.
    from backend.services.invoice_money import lock_order
    await lock_order(db, order.id)
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
        OrderLine.order_id == order.id, OrderLine.description == f"Coupon {c.code}"))).scalars().first()
    if already:
        raise HTTPException(400, "Coupon already applied to this order")
    # Cap at what's still owed, not the order total: a coupon on an order that's already
    # (partly) paid would otherwise leave it "overpaid", and that overpayment can be refunded
    # from the till — a coupon turned into cash (B12).
    from backend.services.order_money import balance_due
    owed = await balance_due(db, order)
    if owed <= 0:
        raise HTTPException(400, "Nothing is owed on this order, so a coupon can't be applied")
    # QA 2026-10-31 B: the coupon reduces the pre-tax price and carries the lines' tax, so the
    # tax on the invoice goes down with it (was a 0%-tax line off the tax-inclusive total).
    from backend.services.coupons import order_coupon_lines
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    parts = order_coupon_lines(lines, c.kind, c.value, owed)
    if not parts:
        raise HTTPException(400, "Nothing on this order for the coupon to reduce")
    discount = q2(-sum(p["total"] for p in parts))
    # Guarded atomic increment: `c.uses += 1` let concurrent redemptions both pass the
    # max_uses check above and overshoot the limit.
    res = await db.execute(sa_update(type(c)).where(
        type(c).id == c.id,
        (type(c).max_uses.is_(None)) | (type(c).max_uses == 0) | (type(c).uses < type(c).max_uses))
        .values(uses=type(c).uses + 1).execution_options(synchronize_session=False))
    if res.rowcount == 0:
        raise HTTPException(409, "Coupon usage limit reached")
    from backend.routers.orders import _recalc_order
    for p in parts:
        db.add(OrderLine(order_id=order.id, line_type="fee", kind="custom", ref_id=c.id,   # N5: the link
                         description=f"Coupon {c.code}", qty=1, unit_price=p["unit_price"],
                         tax_rate=p["tax_rate"], tax_mode=p["tax_mode"], total=p["total"]))
    await db.flush()
    await _recalc_order(db, order)
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
    order = await _editable_order(db, current, order_id)
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
    burn = LoyaltyLedger(org_id=current.org_id, client_id=order.client_id, points=-used_points,
                         ref_type="order", ref_id=order.id, note=f"Redeemed on {order.order_number}")
    db.add(burn)
    await db.flush()
    # ref_id: the ledger row, so removing the line gives back exactly these points (FIN-03)
    await _add_discount_line(db, order, f"Loyalty points ({used_points:.0f})", amount, ref_id=burn.id)
    await db.commit()
    return {"ok": True, "discount": amount, "points_left": q2(bal - used_points)}
