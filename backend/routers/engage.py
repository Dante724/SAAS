"""Customer engagement (Roadmap A2): gift cards, coupons, loyalty points.

Coupons and loyalty points land on the order as a negative fee line (order_lines.line_role says
which) plus a ledger row or a coupon use; a gift card spent on a job is a payment (FIN-09). The
ledgers make every balance derivable. Loyalty earning happens automatically in the
payment reconciliation hook.
"""
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    REDEMPTION_ROLES,
    Client,
    Coupon,
    Employee,
    GiftCard,
    GiftCardLedger,
    LoyaltyLedger,
    Order,
    OrderLine,
    Organisation,
    Payment,
)
from backend.services.auth import assert_perm, get_current_employee, record_scope, require_perm
from backend.services.dates import now_utc
from backend.services.money import (
    Points,  # NUMERIC input bounds (N3)
    d2,
    dec,
    q2,
)

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


async def _add_discount_line(db, order: Order, description: str, amount: float, *, role: str, ref_id=None):
    from backend.routers.orders import _recalc_order
    db.add(OrderLine(order_id=order.id, line_type="fee", kind="custom", ref_id=ref_id, line_role=role,
                     description=description, qty=1, unit_price=-q2(amount),
                     total=-q2(amount)))
    await db.flush()
    await _recalc_order(db, order)


# ── Audit FIN-03: redemption lines ──────────────────────────────────────────────────────────────
# A coupon or loyalty redemption (and, before FIN-09, a gift card) is a negative fee line on the job
# plus a ledger row or a coupon use. order_lines.line_role says which (w4s01); only the redeem
# endpoints set it, so a hand-typed negative line named "Coupon X" is just a line. Removing a
# redemption line gives back what it spent; a line can't be edited (its ledger would disagree); when
# a line the customer was paying for is removed, redemptions the job no longer needs go back
# (recap_redemptions). Gift-card lines from before FIN-09 carry ref_id = the card (backfilled).
GIFT_PREFIX, COUPON_PREFIX, LOYALTY_PREFIX = "Gift card ", "Coupon ", "Loyalty points ("


def redemption_kind(line: OrderLine) -> Optional[str]:
    """'giftcard' | 'coupon' | 'loyalty' for a redemption line, else None."""
    role = getattr(line, "line_role", None)
    return role if role in REDEMPTION_ROLES else None


REDEMPTION_LOCKED = ("This line is a {what} redemption. Its amount can't be edited; delete the line to "
                     "give the {back} back, then redeem again.")
_WHAT = {"giftcard": ("gift card", "card balance"), "coupon": ("coupon", "coupon use"),
         "loyalty": ("loyalty points", "points")}


def refuse_redemption_edit(line: OrderLine) -> None:
    kind = redemption_kind(line)
    if kind:
        what, back = _WHAT[kind]
        raise HTTPException(409, REDEMPTION_LOCKED.format(what=what, back=back))


async def _line_card(db, org_id: str, line: OrderLine, *, lock: bool) -> Optional[GiftCard]:
    """The card a gift-card line spent: its ref_id (w4s01 backfilled it from the code in the
    description); a line whose card couldn't be matched then falls back to that code."""
    q = select(GiftCard).where(GiftCard.org_id == org_id)
    q = q.where(GiftCard.id == line.ref_id) if line.ref_id else q.where(
        GiftCard.code == (line.description or "")[len(GIFT_PREFIX):].strip().upper())
    return (await db.execute(q.with_for_update() if lock else q)).scalar_one_or_none()


async def card_redeemed_on(db, org_id: str, card_id: str, order_id: str) -> Decimal:
    """What this job took from this card and still holds (the card's ledger, net of give-backs)."""
    return -dec((await db.execute(select(func.coalesce(func.sum(GiftCardLedger.amount), 0)).where(
        GiftCardLedger.org_id == org_id, GiftCardLedger.gift_card_id == card_id,
        GiftCardLedger.ref_type == "order", GiftCardLedger.ref_id == order_id))).scalar())


def _line_points(line: OrderLine, burn_row) -> float:
    if burn_row is not None:
        return -float(burn_row.points)
    try:   # a line from before ref_id was set: the points its description shows
        return float((line.description or "")[len(LOYALTY_PREFIX):].split(")")[0])
    except ValueError:
        return 0.0


async def _give_back(db, org_id: str, order: Order, line: OrderLine, amount: Optional[Decimal] = None) -> dict:
    """Give back what `line` spent: all of it, or (amount) that much of its value. Row-locked on
    the card, coupon or client, and never more than this job actually redeemed. Returns what went
    back: {"kind", "card_id"/"coupon_id"/"client_id", "value"/"points"/"use"} ({} for nothing)."""
    kind = redemption_kind(line)
    whole = -dec(line.total)
    part = whole if amount is None else min(dec(amount), whole)
    label = f"Removed from {order.order_number}"
    if kind == "giftcard":
        g = await _line_card(db, org_id, line, lock=True)
        if g is None:
            return {}
        back = d2(min(part, await card_redeemed_on(db, org_id, g.id, order.id)))
        if back <= 0:
            return {}
        await db.execute(sa_update(GiftCard).where(GiftCard.id == g.id).values(balance=GiftCard.balance + back)
                         .execution_options(synchronize_session=False))
        db.add(GiftCardLedger(org_id=org_id, gift_card_id=g.id, amount=back, ref_type="order",
                              ref_id=order.id, note=f"Returned: {label}"[:200]))
        return {"kind": kind, "card_id": g.id, "value": float(back)}
    if kind == "coupon":
        if amount is not None:
            return {}     # a coupon is one use: shrinking its discount gives no use back
        code = (line.description or "")[len(COUPON_PREFIX):].strip().upper()
        c = (await db.execute(select(Coupon).where(Coupon.org_id == org_id, Coupon.code == code)
                              .with_for_update())).scalar_one_or_none()
        # Re-review N5: only a line apply-coupon made (ref_id = the coupon) gives a use back
        if c is None or line.ref_id != c.id:
            return {}
        res = await db.execute(sa_update(Coupon).where(Coupon.id == c.id, Coupon.uses > 0)
                               .values(uses=Coupon.uses - 1).execution_options(synchronize_session=False))
        return {"kind": kind, "coupon_id": c.id, "use": 1} if res.rowcount else {}
    if kind != "loyalty":
        return {}
    # the points go back to the client who spent them (re-review N8): the burn row's client
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
    if not spender:
        return {}
    await db.execute(select(Client.id).where(Client.id == spender).with_for_update())
    burned = -float((await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.org_id == org_id, LoyaltyLedger.client_id == spender,
        LoyaltyLedger.ref_type == "order", LoyaltyLedger.ref_id == order.id))).scalar() or 0)
    # what the line still holds: its points, less what an earlier partial give-back returned (the
    # job's burned total says so; a job normally has one points line)
    have = min(_line_points(line, burn_row), burned)
    points = have * float(part / whole) if amount is not None and whole > 0 else have
    back = q2(min(points, burned))
    if back <= 0:
        return {}
    db.add(LoyaltyLedger(org_id=org_id, client_id=spender, points=back, ref_type="order",
                         ref_id=order.id, note=f"Returned: {label}"[:200]))
    return {"kind": kind, "client_id": spender, "points": back, "had": have}


async def _coupon_group(db, order: Order, line: OrderLine) -> list[OrderLine]:
    """A coupon split across tax groups goes as a whole, so it is applied or not."""
    siblings = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.description == line.description,
        OrderLine.line_role == "coupon"))).scalars().all()
    return list(siblings) or [line]


async def give_back_redemption(db, org_id: str, order: Order, line: OrderLine,
                               actor_id: Optional[str] = None) -> list[OrderLine]:
    """Undo what a redemption line spent, under a row lock on the card, coupon or client. Called
    by the job's line delete (which holds the job's lock) before the line goes. Returns every line
    to remove: a coupon split across tax groups goes as a whole. Never gives back more than this job
    actually redeemed from that card / client."""
    if not redemption_kind(line):
        return [line]
    out = await _coupon_group(db, order, line) if redemption_kind(line) == "coupon" else [line]
    await _give_back(db, org_id, order, line)
    return out


async def give_back_kept_line(db, org_id: str, order: Order, line: OrderLine, seen: set) -> list[dict]:
    """#226: a deleted job's redemption line that a sent or paid invoice still bills stays on the
    job, but what it spent goes back all the same (a deleted job holds none of the customer's value).
    Returns what went back, per line, for the archive's audit row; restoring the job spends it again
    (respend_kept_redemptions). `seen`: line ids already handled (a coupon's lines give one use back)."""
    if line.id in seen:
        return []
    group = await _coupon_group(db, order, line) if redemption_kind(line) == "coupon" else [line]
    seen.update(ln.id for ln in group)
    back = await _give_back(db, org_id, order, line)
    return [dict(back, line_id=line.id)] if back else []


async def respend_kept_redemptions(db, org_id: str, order: Order, kept: list[dict]) -> None:
    """Restoring a job whose kept redemption lines gave their value back when it was deleted: spend
    it again, row-locked, or refuse the restore (409) when the card, coupon or points are gone."""
    present = set((await db.execute(select(OrderLine.id).where(
        OrderLine.order_id == order.id, OrderLine.line_role.in_(REDEMPTION_ROLES)))).scalars().all())
    note = f"Spent again: {order.order_number} restored"
    for k in kept:
        if k.get("line_id") not in present:
            continue
        if k.get("kind") == "giftcard":
            amt = d2(k["value"])
            res = await db.execute(sa_update(GiftCard).where(
                GiftCard.id == k["card_id"], GiftCard.org_id == org_id, GiftCard.balance >= amt)
                .values(balance=GiftCard.balance - amt).execution_options(synchronize_session=False))
            if not res.rowcount:
                raise HTTPException(409, f"This job's gift card no longer has {amt:.2f} on it, so the job's "
                                         "gift-card discount can't be restored. Ask the customer to pay that "
                                         "part another way: credit the invoice, then restore.")
            db.add(GiftCardLedger(org_id=org_id, gift_card_id=k["card_id"], amount=-amt, ref_type="order",
                                  ref_id=order.id, note=note[:200]))
        elif k.get("kind") == "coupon":
            res = await db.execute(sa_update(Coupon).where(
                Coupon.id == k["coupon_id"], Coupon.org_id == org_id,
                (Coupon.max_uses.is_(None)) | (Coupon.max_uses == 0) | (Coupon.uses < Coupon.max_uses))
                .values(uses=Coupon.uses + 1).execution_options(synchronize_session=False))
            if not res.rowcount:
                raise HTTPException(409, "This job's coupon has reached its usage limit since the job was deleted, "
                                         "so its discount can't be restored.")
        elif k.get("kind") == "loyalty":
            await db.execute(select(Client.id).where(Client.id == k["client_id"]).with_for_update())
            pts = float(k["points"])
            have = await loyalty_balance(db, org_id, k["client_id"])
            if have + 0.005 < pts:
                raise HTTPException(409, f"The customer has {have:.0f} points now, and this job's discount needs "
                                         f"the {pts:.0f} points given back when it was deleted, so it can't be "
                                         "restored as it was.")
            db.add(LoyaltyLedger(org_id=org_id, client_id=k["client_id"], points=-pts, ref_type="order",
                                 ref_id=order.id, note=note[:200]))
    await db.flush()


async def recap_redemptions(db, org_id: str, order: Order, actor_id: Optional[str] = None) -> bool:
    """FIN-03: after a line the customer was paying for is removed (or made cheaper), redemptions are
    never worth more than what the job still needs beyond the customer's own money, the same cap the
    redeem endpoints apply. The excess goes back: gift-card payments to their cards first (newest
    first), then redemption lines newest first (a coupon only shrinks: it stays one use). Lines a
    sent or paid invoice bills are left as they are. Otherwise a removed line turned gift-card value,
    points or a coupon into a cash overpayment. Returns whether anything changed. Caller holds the
    job's lock, calls this BEFORE _recalc_order (the recalculated total would settle the job's
    invoice as paid, which locks the lines), then recalculates and commits."""
    from backend.services.invoice_money import locking_invoice, release_line
    from backend.services.order_money import credited, net_paid
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    red = [ln for ln in lines if redemption_kind(ln)]
    gc = dec(await gift_card_paid(db, order.id))
    held = sum((-dec(ln.total) for ln in red), Decimal("0")) + gc
    if held <= Decimal("0.005"):
        return False
    gross = (sum((dec(ln.total) for ln in lines if not redemption_kind(ln)), Decimal("0"))
             - dec(getattr(order, "written_off", 0) or 0) - dec(await credited(db, order.id)))
    # BE-4: the job bills its ROUNDED total, so the cap includes the rounding difference too (it
    # used to hand that 0.0x back to the card on every line edit, leaving the job owing it).
    from backend.routers.orders import rounded_total
    every = sum((dec(ln.total) for ln in lines), Decimal("0"))
    gross += dec(await rounded_total(db, org_id, every)) - every
    own = dec(await net_paid(db, order.id)) - gc
    excess = d2(held - max(gross - own, Decimal("0")))
    if excess <= Decimal("0.005"):
        return False
    note = "Not needed after a line was removed"
    for card_id in await _cards_on_job(db, org_id, order.id):
        if excess <= 0:
            break
        excess = d2(excess - await return_to_gift_card(db, org_id, order, card_id, excess, actor_id, note))
    for ln in sorted(red, key=lambda x: (x.created_at or datetime.min, x.id), reverse=True):
        if excess <= 0:
            break
        if await locking_invoice(db, ln.id):
            continue
        whole = -dec(ln.total)
        cut = d2(min(excess, whole))
        if cut <= 0:
            continue
        excess = d2(excess - cut)
        if cut >= whole and ln.line_role != "coupon":
            await _give_back(db, org_id, order, ln)
            await release_line(db, ln.id)
            await db.delete(ln)
            continue
        if ln.line_role != "coupon":
            back = await _give_back(db, org_id, order, ln, cut)
            if back.get("points"):
                ln.description = f"{LOYALTY_PREFIX}{back['had'] - back['points']:.0f})"
        new_total = d2(-(whole - cut))
        ln.unit_price = d2(dec(ln.unit_price) * new_total / dec(ln.total)) if dec(ln.total) else new_total
        ln.total = new_total
    await db.flush()
    return True


# ── Gift cards ───────────────────────────────────────────────────────────────
# BE-8: the most one card may carry. A card is spendable money on any job, so a typo (or worse)
# can't mint an unbounded amount.
GIFT_CARD_MAX = 10_000


class GiftCardIn(BaseModel):
    value: float
    client_id: str                       # gift cards are always linked to a client
    expires_at: Optional[datetime] = None   # optional validity date; null = never expires
    # code is always server-generated & unique — not accepted from the caller.
    # Audit FIN-09: a card sold for money takes that money into a cashbox here. Without an account
    # the card is a gift from the shop (a promotion): no money moves, so BE-8 makes it an
    # owner-level act (can_manage_settings, like coupons) and audits it.
    account_id: Optional[str] = None


def _gc(g: GiftCard) -> dict:
    return {"id": g.id, "code": g.code, "initial_value": g.initial_value, "balance": g.balance,
            "client_id": g.client_id, "active": g.active,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None}


@router.post("/giftcards", status_code=201)
async def issue_giftcard(
    data: GiftCardIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Sell a gift card into a cashbox (account_id) or issue a promotional one (no account).

    BE-8: selling takes a customer's money, so it needs can_take_payments; a promotional card has
    no money behind it, so it needs can_manage_settings and is audited. Value is capped at
    GIFT_CARD_MAX. BE-6: a retry with the same Idempotency-Key gets the first card back instead of
    a second card and a second till leg."""
    if not (0 < data.value <= GIFT_CARD_MAX):      # also refuses NaN
        raise HTTPException(422, f"Gift card value must be more than 0 and at most {GIFT_CARD_MAX:,}")
    await assert_perm(db, current, "can_take_payments" if data.account_id else "can_manage_settings")
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "giftcards.issue", data,
                          lambda: _issue_giftcard(data, db, current))


async def _issue_giftcard(data: GiftCardIn, db: AsyncSession, current: Employee) -> dict:
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
    else:
        from backend.services.audit import record as audit_record
        await audit_record(db, org_id=current.org_id, actor_id=current.id, action="giftcard.promo_issue",
                           entity_type="gift_card", entity_id=g.id,
                           diff={"value": float(d2(data.value)), "code": code, "client_id": data.client_id})
    await db.commit()
    await db.refresh(g)
    return {**_gc(g), "promotional": not data.account_id}


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


# ── Audit FIN-09: a gift card spent on a job is a tender ─────────────────────────────────────────
# As at the counter: a Payment(method="gift_card", no cashbox) on the job's invoice, plus the card's
# ledger row (ref_type "order", ref_id = the job), which says which card paid how much. The job keeps
# its price (revenue, tax and commissions are the work's), and the money isn't new: the card's cash
# was taken when it was sold (revenue.money_in_payment leaves these out). Money paid by a card goes
# back only to that card, never more than the job took from it (return_to_gift_card), and a cash
# refund can't pay it out (invoice_money.refund_payments).
GIFT_CARD = "gift_card"


async def gift_card_paid(db, order_id: str) -> float:
    """What gift cards have paid on this job, net of what went back to them."""
    from sqlalchemy import case
    amt = func.abs(func.coalesce(Payment.amount, 0))
    return q2(float((await db.execute(select(func.coalesce(func.sum(
        case((Payment.kind == "refund", -amt), else_=amt)), 0)).where(
        Payment.order_id == order_id, Payment.method == GIFT_CARD))).scalar() or 0))


async def _cards_on_job(db, org_id: str, order_id: str) -> list[str]:
    """Cards that paid this job (by FIN-09 payment), newest redemption first."""
    rows = (await db.execute(select(GiftCardLedger.gift_card_id, func.max(GiftCardLedger.created_at)).where(
        GiftCardLedger.org_id == org_id, GiftCardLedger.ref_type == "order", GiftCardLedger.ref_id == order_id,
        GiftCardLedger.amount < 0).group_by(GiftCardLedger.gift_card_id))).all()
    return [cid for cid, _at in sorted(rows, key=lambda r: (r[1] is not None, r[1]), reverse=True)]


async def return_to_gift_card(db, org_id: str, order: Order, card_id: str, amount, actor_id: Optional[str],
                              note: str) -> Decimal:
    """Give back up to `amount` of what this card paid on this job: capped by what the job took from
    the card (its ledger) and by what gift cards still have paid on the job (the payments). A refund
    Payment(method gift_card) off the job's invoices, the card's balance and ledger, and the client's
    ledger. Row lock on the card. Returns what went back."""
    from backend.services.invoice_money import refund_payments
    g = (await db.execute(select(GiftCard).where(GiftCard.id == card_id, GiftCard.org_id == org_id)
                          .with_for_update())).scalar_one_or_none()
    if g is None:
        return Decimal("0")
    back = d2(min(dec(amount), await card_redeemed_on(db, org_id, g.id, order.id),
                  dec(await gift_card_paid(db, order.id))))
    if back <= 0:
        return Decimal("0")
    rows = await refund_payments(db, order, float(back), method=GIFT_CARD, received_by_id=actor_id,
                                 reference=f"Gift card {g.code}"[:200])
    await db.execute(sa_update(GiftCard).where(GiftCard.id == g.id).values(balance=GiftCard.balance + back)
                     .execution_options(synchronize_session=False))
    db.add(GiftCardLedger(org_id=org_id, gift_card_id=g.id, amount=back, ref_type="order", ref_id=order.id,
                          note=f"Returned from {order.order_number}: {note}"[:200]))
    if order.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, org_id, order.client_id, "refund", float(back),
                                  note=f"Gift card {g.code} given back from {order.order_number}",
                                  ref_type="payment", ref_id=rows[0].id, created_by_id=actor_id)
    await db.flush()
    return back


async def return_all_gift_cards(db, org_id: str, order: Order, actor_id: Optional[str], note: str) -> float:
    """Everything gift cards paid on this job goes back to them (a deleted job). Returns the total."""
    total = Decimal("0")
    for card_id in await _cards_on_job(db, org_id, order.id):
        total += await return_to_gift_card(db, org_id, order, card_id, Decimal("1e9"), actor_id, note)
    return float(d2(total))


@router.post("/orders/{order_id}/redeem-giftcard")
async def redeem_giftcard(
    order_id: str,
    data: RedeemGiftIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),
):
    """Spend a gift card on a job. BE-6: a retry with the same Idempotency-Key gets the first
    answer back instead of redeeming again."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "giftcards.redeem",
                          {"order_id": order_id, "body": data},
                          lambda: _redeem_giftcard(order_id, data, db, current))


async def _redeem_giftcard(order_id: str, data: RedeemGiftIn, db: AsyncSession, current: Employee) -> dict:
    order = await _editable_order(db, current, order_id)
    from backend.services.order_money import refuse_archived
    refuse_archived(order)
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
        # an uncapped redemption overpaid the job, and refund_overpayment then paid the excess out in
        # cash — turning gift-card balance into money
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
    from backend.services.invoice_money import apply_order_payment
    pays = await apply_order_payment(db, order, float(amount), method=GIFT_CARD, account_id=None,
                                     received_by_id=current.id, reference=f"Gift card {g.code}"[:200])
    if order.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, order.client_id, "income", -float(amount),
                                  note=f"Gift card {g.code} on {order.order_number}", ref_type="payment",
                                  ref_id=pays[0].id, created_by_id=current.id)
    from backend.services.order_money import cancel_open_pay_links
    await cancel_open_pay_links(db, order)     # a link sent earlier must not charge for what the card paid
    await db.commit()
    return {"ok": True, "redeemed": float(amount), "card_balance": new_balance}


class GiftRefundIn(BaseModel):
    code: str
    amount: Optional[float] = None    # default: everything this card paid on the job


@router.post("/orders/{order_id}/giftcard-refund")
async def refund_giftcard(
    order_id: str,
    data: GiftRefundIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_set_discount")),
):
    """FIN-09: give gift-card money paid on a job back to the card (the undo of redeem-giftcard).
    Never more than this job took from that card; the job owes that much again. BE-6: a retry
    with the same Idempotency-Key gets the first answer back."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "giftcards.give_back",
                          {"order_id": order_id, "body": data},
                          lambda: _refund_giftcard(order_id, data, db, current))


async def _refund_giftcard(order_id: str, data: GiftRefundIn, db: AsyncSession, current: Employee) -> dict:
    order = await _editable_order(db, current, order_id)
    from backend.services.invoice_money import lock_order
    await lock_order(db, order.id)
    g = (await db.execute(select(GiftCard).where(
        GiftCard.org_id == current.org_id, GiftCard.code == data.code.strip().upper()))).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Gift card not found")
    room = d2(min(await card_redeemed_on(db, current.org_id, g.id, order.id),
                  dec(await gift_card_paid(db, order.id))))
    if room <= 0:
        raise HTTPException(400, "This card has nothing on this job to give back")
    amount = d2(data.amount) if data.amount is not None else room
    if amount <= 0:
        raise HTTPException(422, "Amount must be positive")
    if amount > room + Decimal("0.005"):
        raise HTTPException(400, f"This job took only {room:.2f} from this card, so at most that goes back to it")
    back = await return_to_gift_card(db, current.org_id, order, g.id, amount, current.id, "given back")
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id, f"Gift card {g.code}: {back:.2f} given back")
    await db.commit()
    return {"ok": True, "returned": float(back), "card_balance": float(d2(dec(g.balance) + back))}


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
        OrderLine.order_id == order.id, OrderLine.line_role == "coupon",
        OrderLine.description == f"Coupon {c.code}"))).scalars().first()
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
                         line_role="coupon",
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
    points: Points


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
    await _add_discount_line(db, order, f"Loyalty points ({used_points:.0f})", amount, role="loyalty",
                             ref_id=burn.id)
    await db.commit()
    return {"ok": True, "discount": amount, "points_left": q2(bal - used_points)}
