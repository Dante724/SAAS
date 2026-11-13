"""Client Returns (goods back to stock) and Refunds (money back) — separate
documents, per Orderry's Feb-2026 split. A refund claws back the refunded share of
the order's commissions via the payroll engine."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.db.database import get_db
from backend.models import (
    ClientReturn, ClientReturnLine, Refund, Order,
    Account, Employee, Part, Payment,
)
from backend.services.auth import assert_perm, get_current_employee, has_perm, require_perm
from backend.services.dates import now_utc
from backend.services.money import Qty  # NUMERIC input bounds (N3)

router = APIRouter(prefix="/api/returns", tags=["returns"])


class ReturnLineIn(BaseModel):
    part_id: str
    qty: Qty = 1
    restock: bool = True
    serials: Optional[list[str]] = None   # QA F2: a serialized product — which units came back


class ReturnIn(BaseModel):
    order_id: Optional[str] = None
    sale_id: Optional[str] = None     # INV-06: the counter sale the goods came from (bounds the return)
    client_id: Optional[str] = None
    reason: Optional[str] = None
    lines: list[ReturnLineIn] = []


@router.post("", status_code=201)
async def create_return(
    data: ReturnIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_returns")),
):
    """Goods back from a customer. QA F2: a double click with the same Idempotency-Key restocks once
    and gets the first answer back (as a sale does). W3-C: gated like refunds (can_create_returns),
    so the front desk that takes returns doesn't need to manage inventory."""
    from backend.services.idempotency import idem_begin
    claim = await idem_begin(db, current.org_id, request, "returns.create")
    if claim.replay is not None:
        return claim.replay
    try:
        result = await _create_return(data, db, current)
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


async def _create_return(data: ReturnIn, db: AsyncSession, current: Employee):
    if not data.lines:
        raise HTTPException(400, "A return needs at least one line")
    if data.order_id and data.sale_id:
        raise HTTPException(422, "A return comes back against a job or a sale, not both")
    loc_id = current.location_id
    if not loc_id:      # an owner without a home location: the shop's first location (as the till does)
        from backend.models import Location
        loc_id = (await db.execute(select(Location.id).where(
            Location.org_id == current.org_id).order_by(Location.created_at).limit(1))).scalar_one_or_none()
    order = None
    if data.order_id:
        order = (await db.execute(select(Order).where(
            Order.id == data.order_id, Order.org_id == current.org_id))).scalar_one_or_none()
        if order is None:
            raise HTTPException(404, "Order not found")
        # W3-C: two returns for one job queue up here, and each sees what the other took back
        from backend.services.invoice_money import lock_order
        await lock_order(db, order.id)
    sale, sale_lines = None, []
    if data.sale_id:
        sale, sale_lines = await _lock_sale(db, current, data.sale_id)
        loc_id = sale.location_id          # back on the shelf the sale took it from
    ret = ClientReturn(org_id=current.org_id, order_id=data.order_id, sale_id=sale.id if sale else None,
                       client_id=data.client_id or (order.client_id if order else None)
                       or (sale.client_id if sale else None), location_id=loc_id,
                       reason=data.reason, created_by_id=current.id)
    db.add(ret)
    await db.flush()
    restocked = 0
    for li in data.lines:
        part = (await db.execute(select(Part).where(
            Part.id == li.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
        if not part:
            raise HTTPException(404, "Product not found")
        if li.qty <= 0:
            raise HTTPException(422, "Return a positive quantity")
        if order is not None:
            await _check_job_holds(db, order, part, li.qty)
        elif sale is not None:
            _check_sale_left(sale, sale_lines, part, li.qty)
        elif li.restock:
            await _check_free_standing(db, current, part, li.qty)
        sold_ids = [u for ln in sale_lines if ln.ref_id == part.id
                    for u in ((ln.stock_draws or {}).get("serials") or [])] if sale is not None else None
        units = (await _returned_units(db, current, part, li, order, sold_ids=sold_ids)
                 if li.restock and li.qty > 0 else [])
        db.add(ClientReturnLine(return_id=ret.id, part_id=li.part_id,
                                qty=li.qty, restock=li.restock))
        await db.flush()
        if order is not None:
            if li.restock:
                # back where the job took it from, onto the cost layers the job drew (not at cost 0)
                from backend.routers.orders import _reverse_fulfillment
                await _reverse_fulfillment(db, order, part.id, li.qty, ref_type="client_return", ref_id=ret.id,
                                           units=units, capped=False)
                from backend.services import serial_log
                for u in units:
                    serial_log.record(db, u, "returned", ref_type="client_return", ref_id=ret.id,
                                      label=data.reason or "Customer return", actor_id=current.id)
                restocked += 1
            continue
        if sale is not None:
            await _return_against_sale(db, current, ret, sale, sale_lines, part, li, units, data.reason)
            restocked += 1 if li.restock else 0
            continue
        if li.restock and li.qty > 0:
            await _restock_free_standing(db, current, ret, part, loc_id, li.qty, units, data.reason)
            restocked += 1
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "return", ret.id,
                           f"Client return: {len(data.lines)} line(s), {restocked} restocked")
    await db.commit()
    return {"id": ret.id, "restocked": restocked}


MAX_RETURN_QTY = 10_000     # one line of goods back over the counter; more is a typo or a scan


# ── INV-06: a walk-in return that names the sale the goods came from ──────────────────────────────
# Bounded by that sale, like a return against a job is bounded by the job: only products it sold,
# never more than it sold less what already came back — through the till (POST /sales/{id}/returns)
# or an earlier walk-in return, which share one counter (sale_lines.stock_draws["returned"]) — and
# a serialized unit only if this sale sold it. The goods go back on the sale's own cost layers at
# its location, so the front desk needs no inventory rights for it. Money is a separate Refund.

async def _lock_sale(db, current, sale_id: str):
    """The sale, locked (a write on its row: FOR UPDATE is a no-op on SQLite) and read fresh, with
    its lines — so two returns of one sale queue up and the second sees what the first took back."""
    from sqlalchemy import update as sa_update

    from backend.models import Sale, SaleLine
    sale = (await db.execute(select(Sale).where(Sale.id == sale_id, Sale.org_id == current.org_id)
                             .with_for_update())).scalar_one_or_none()
    if sale is None:
        raise HTTPException(404, "Sale not found")
    await db.execute(sa_update(Sale).where(Sale.id == sale.id).values(paid=Sale.paid)
                     .execution_options(synchronize_session=False))
    await db.refresh(sale)
    if sale.voided_at:
        raise HTTPException(409, "This sale was deleted, so nothing can come back on it")
    lines = list((await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id)
                                   .execution_options(populate_existing=True))).scalars().all())
    return sale, lines


def _back_already(ln) -> float:
    return float((ln.stock_draws or {}).get("returned") or 0)


def _sold_left(ln) -> float:
    return float(ln.qty or 0) - _back_already(ln)


def _check_sale_left(sale, lines, part, qty: float) -> None:
    from backend.services import stock_rows
    if qty > MAX_RETURN_QTY:
        raise HTTPException(422, f"At most {MAX_RETURN_QTY:,} units of “{part.name}” per return line")
    stock_rows.check_whole(part, qty)
    mine = [ln for ln in lines if ln.kind == "product" and ln.ref_id == part.id]
    num = sale.sale_number or "this sale"
    if not mine:
        raise HTTPException(409, f"“{part.name}” wasn't sold on sale {num}, so it can't come back on it")
    sold = sum(float(ln.qty or 0) for ln in mine)
    left = max(sum(_sold_left(ln) for ln in mine), 0.0)
    if qty > left + 1e-9:
        raise HTTPException(409, f"Only {left:g} of “{part.name}” from sale {num} can still come back "
                                 f"(it sold {sold:g}; the rest is already back)")


async def _return_against_sale(db, current, ret, sale, lines, part, li, units: list, reason) -> None:
    """Spread the qty over the sale's lines for this product (each unit on the line that sold it,
    the rest oldest line first): count it as returned there and, when restocked, put it back on
    the shelf with a `return` movement and onto the cost layers that line drew."""
    from backend.models import StockMovement
    from backend.routers.sales import _restore_layers, _unsell_serials
    from backend.services import stock_rows
    from backend.services.money import q4
    mine = [ln for ln in lines if ln.kind == "product" and ln.ref_id == part.id]
    by_line: dict = {}
    for u in units:
        ln = next(x for x in mine if u.id in ((x.stock_draws or {}).get("serials") or []))
        by_line.setdefault(ln.id, []).append(u)
    plan = {ln_id: float(len(us)) for ln_id, us in by_line.items()}
    left = float(li.qty) - sum(plan.values())
    for ln in mine:
        if left <= 1e-9:
            break
        take = min(max(_sold_left(ln) - plan.get(ln.id, 0.0), 0.0), left)
        if take > 1e-9:
            plan[ln.id] = plan.get(ln.id, 0.0) + take
            left -= take
    label = f"Returned from sale {sale.sale_number or ''}".strip()
    for ln in mine:
        q = plan.get(ln.id, 0.0)
        if q <= 1e-9:
            continue
        back = by_line.get(ln.id, [])
        draws = dict(ln.stock_draws or {})
        draws["returned"] = q4(_back_already(ln) + q)
        if li.restock:
            back_ids = {u.id for u in back}
            draws["serials"] = [u for u in (draws.get("serials") or []) if u not in back_ids]
            await _unsell_serials(db, current.org_id, list(back_ids), ref_type="client_return", ref_id=ret.id,
                                  label=reason or label, actor_id=current.id)
            for u in back:          # back on the unbinned row at the sale's shop, like a till return
                u.bin_id, u.location_id, u.warehouse_id, u.order_id = None, sale.location_id, None, None
            draws["layers"] = await _restore_layers(db, current.org_id, draws.get("layers") or [], q,
                                                    first=[u.batch_id for u in back if u.batch_id])
            await stock_rows.add(db, part.id, sale.location_id, q)
            db.add(StockMovement(part_id=part.id, location_id=sale.location_id, movement_type="return",
                                 qty=q, unit_cost=ln.cost_price or 0, ref_type="client_return",
                                 ref_id=ret.id, created_by_id=current.id))
        ln.stock_draws = draws


async def _check_free_standing(db, current, part, qty: float) -> None:
    """Audit INV-06 — a return with no job behind it puts stock on the shelf that no document took
    off it. A serialized product is bounded by its units (each must be one this shop sold); any other
    product needs `can_manage_inventory` (the front desk returns goods against a job), a sane
    quantity, and a whole one for products counted in whole units."""
    from backend.services import stock_rows
    if qty > MAX_RETURN_QTY:
        raise HTTPException(422, f"At most {MAX_RETURN_QTY:,} units of “{part.name}” per return line")
    stock_rows.check_whole(part, qty)
    if not part.serialized:
        if not await has_perm(db, current, "can_manage_inventory"):
            raise HTTPException(403, "Taking goods back without a job needs inventory rights — return them "
                                     "against the job, or ask a manager")


async def _restock_free_standing(db, current, ret, part, loc_id: str, qty: float, units: list, reason) -> None:
    """INV-06 — the units come in as a receipt of the return document: on a cost layer (a returned
    unit at its own cost, else the product's valuation cost), with a `return` movement naming the
    return, through the one stock-inflow engine. The returned serial units are re-attached to it."""
    from backend.services import serial_log, stock_posting, valuation
    out: dict = {}
    if units:
        cost = sum(float(u.unit_cost or 0) for u in units) / len(units) or valuation.unit_cost(part)
    else:
        cost = valuation.unit_cost(part)
    await stock_posting.apply_receipt_line(
        db, org_id=current.org_id, part=part, location_id=loc_id, qty=qty, unit_cost=float(cost),
        created_by_id=current.id, ref_type="client_return", ref_id=ret.id, movement_type="return",
        create_batch=True, out=out)
    batch = out.get("batch")
    for u in units:
        u.status, u.location_id, u.warehouse_id, u.order_id = "in_stock", loc_id, None, None
        u.bin_id = batch.bin_id if batch is not None else None
        if batch is not None:
            u.batch_id = batch.id
        serial_log.record(db, u, "returned", ref_type="client_return", ref_id=ret.id,
                          label=reason or "Customer return", actor_id=current.id)


async def _check_job_holds(db, order, part, qty: float) -> None:
    """W3-C: a return against a job takes back only a part the job used, up to what it used less
    what already came back (read under the job's lock)."""
    from backend.services.job_returns import returned_qty, still_held, used_by_job
    used = await used_by_job(db, order.id, part.id)
    if used <= 1e-9 and not await returned_qty(db, order.id, part.id):
        raise HTTPException(409, f"“{part.name}” wasn't used on job {order.order_number}, so it can't come back on it")
    left = await still_held(db, order.id, part.id)
    if qty > left + 1e-9:
        raise HTTPException(409, f"Only {left:g} of “{part.name}” from job {order.order_number} can still come back "
                                 f"(it used {used:g}; the rest is already returned)")


async def _returned_units(db, current, part, li, order=None, *, sold_ids=None) -> list:
    """QA F2 — the units of a serialized product that came back: one serial per unit, each one this
    shop sold or used on a job (a unit still in stock can't come back). Against a job, a unit of
    that job; against a sale (`sold_ids`, INV-06), a unit that sale sold and that isn't back yet."""
    from backend.models import SerializedItem
    from backend.services.serial_intake import matches as _sn_matches
    named = list(dict.fromkeys(x.strip() for x in (li.serials or []) if x and x.strip()))
    if not part.serialized:
        return []
    if abs(li.qty - round(li.qty)) > 1e-9 or len(named) != int(round(li.qty)):
        raise HTTPException(422, f"Scan the IMEI / serial of each “{part.name}” coming back "
                                 f"({len(named)} of {li.qty:g} entered)")
    # Re-review N1: with no job, only a unit this shop SOLD can come back. A unit allocated to a job
    # is still held by that job (its stock already left the shelf on the job, and comes back when the
    # line is removed), so taking it back without the job put a phantom unit on the shelf.
    statuses = ("sold", "allocated_to_order") if order is not None else ("sold",)
    units = []
    for sn in named:
        u = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == current.org_id, SerializedItem.part_id == part.id,
            SerializedItem.status.in_(statuses),
            *([SerializedItem.order_id == order.id] if order is not None else []),
            *([SerializedItem.id.in_(list(sold_ids) or [""])] if sold_ids is not None else []),
            _sn_matches(SerializedItem.serial_number, sn)).with_for_update())).scalars().first()
        if u is None:
            if sold_ids is not None:
                raise HTTPException(409, f"{sn} wasn't sold on this sale, or is already back")
            if order is None:
                await _refuse_unit_on_a_job(db, current, part, sn)
            raise HTTPException(409, f"{sn} isn't a “{part.name}” this shop sold")
        units.append(u)
    return units


async def _refuse_unit_on_a_job(db, current, part, sn: str) -> None:
    """N1: say why a unit still on a job can't come back without it, and which job to use."""
    from backend.models import Order, SerializedItem
    from backend.services.serial_intake import matches as _sn_matches
    num = (await db.execute(select(Order.order_number).join(SerializedItem, SerializedItem.order_id == Order.id).where(
        SerializedItem.org_id == current.org_id, SerializedItem.part_id == part.id,
        SerializedItem.status == "allocated_to_order",
        _sn_matches(SerializedItem.serial_number, sn)))).scalars().first()
    if num is not None:
        raise HTTPException(409, f"{sn} is still on job {num}. Return it against that job, or "
                                 f"remove it from the job first.")


class RefundIn(BaseModel):
    account_id: str
    amount: float
    order_id: Optional[str] = None
    return_id: Optional[str] = None
    reason: Optional[str] = None


_DOUBLE_CLICK_S = 10   # as cash._refuse_double_click: the same refund again this soon is a double click


def _keyed(request) -> bool:
    return bool(request is not None and (request.headers.get("Idempotency-Key") or "").strip())


async def refuse_double_refund(db, org_id: str, order_id: str, account_id: str, amount: float) -> None:
    """Audit FIN-01: a refund sent without an Idempotency-Key that repeats one just booked (same
    job, cashbox and amount) is a double click or a network retry, not a second refund. Called
    under the job's lock, so the second request sees the first one's Refund."""
    from datetime import timedelta

    from backend.services.money import q2
    since = now_utc() - timedelta(seconds=_DOUBLE_CLICK_S)
    hit = (await db.execute(select(Refund.id).where(
        Refund.org_id == org_id, Refund.order_id == order_id, Refund.account_id == account_id,
        Refund.voided_at.is_(None), Refund.amount == q2(abs(amount)), Refund.created_at >= since)
        .limit(1))).scalar_one_or_none()
    if hit:
        raise HTTPException(409, f"{q2(abs(amount)):.2f} was just refunded on this job from this cashbox. "
                                 "If the customer is owed more, refund again in a moment.")


@router.post("/refunds", status_code=201)
async def create_refund(
    data: RefundIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_returns")),
):
    """Money back on a job. Audit FIN-01: a retry with the same Idempotency-Key gets the first
    answer back instead of paying out again; without a key, the same refund again within a few
    seconds is refused as a double click."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "returns.refund", data,
                          lambda: _create_refund(data, db, current, keyed=_keyed(request)))


async def _create_refund(data: RefundIn, db: AsyncSession, current: Employee, *, keyed: bool):
    # Giving money back is a payments action (as closing a job with money owed is, #74): the front
    # desk that takes payments can refund without seeing the shop's finances (QA A-5).
    if not (await has_perm(db, current, "can_make_client_payments") or await has_perm(db, current, "can_view_finance")):
        raise HTTPException(403, "Your role can't give money back to customers. Ask someone who takes payments.")
    if data.amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    acct = (await db.execute(select(Account).where(
        Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "Cashbox not found")
    if (acct.payment_option or "") == "card" and not await has_perm(db, current, "can_refund_card_payments"):
        raise HTTPException(403, "Your role can't refund card payments. Refund in cash, or ask a manager.")
    # Every refund belongs to an order (directly or via its goods return) and is capped at what that
    # order actually received net of ALL prior refunds — refunds from this endpoint, archive refunds
    # and overpayment refunds all record a refund Payment, which order_money counts. Previously the
    # cap ignored this endpoint's own refunds (repeatable) and was skipped without an order_id.
    order_id = data.order_id
    if not order_id and data.return_id:
        ret = (await db.execute(select(ClientReturn).where(
            ClientReturn.id == data.return_id, ClientReturn.org_id == current.org_id))).scalar_one_or_none()
        order_id = ret.order_id if ret else None
    if not order_id:
        raise HTTPException(400, "A refund must reference the order (or a return for an order) it repays")
    locked = (await db.execute(select(Order).where(     # NO KEY UPDATE: see invoice_money.lock_order
        Order.id == order_id, Order.org_id == current.org_id).with_for_update(key_share=True))).scalar_one_or_none()
    if not locked:
        raise HTTPException(404, "Order not found")
    from backend.services.invoice_money import lock_order
    await lock_order(db, order_id)          # SQLite ignores FOR UPDATE: two refunds queue up here
    await db.refresh(locked, ["total"])
    from backend.services.order_money import net_paid as _net_paid
    net_paid = await _net_paid(db, order_id)
    if data.amount > net_paid + 1e-6:
        raise HTTPException(400, f"Cannot refund more than was paid ({net_paid:g})")
    if not keyed:
        await refuse_double_refund(db, current.org_id, order_id, acct.id, data.amount)
    # Money handed back beyond an overpayment is money the customer is no longer charged: the
    # job's total goes down with it, so a goodwill refund or a returned item never shows the
    # customer as owing (QA F1). Handing back an overpayment, or (W3-C) a deposit on a job that
    # isn't closed yet, leaves the total alone: it only lowers what was paid.
    keep = await refundable_without_discount(db, locked, deposits=not data.return_id)
    take_off = round(data.amount - min(data.amount, keep), 2)
    claw = take_off
    # A dropped job keeps its bill: money handed back there is more of the bill not collected
    # (written off, order_money.resync_write_off), not a smaller bill. As in Orderry.
    from backend.services.revenue import dropped_keys
    if locked.status in await dropped_keys(db, current.org_id):
        take_off, claw = 0.0, data.amount
    data.order_id = order_id
    refund = await book_refund(db, current, locked, acct, data.amount, reason=data.reason,
                               return_id=data.return_id, reference=f"Refund: {data.reason or ''}")
    if take_off > 0:
        await _take_off_job(db, current, locked, refund, take_off)
    # Refund on an order claws back the refunded share of its commissions
    if data.order_id:
        order = (await db.execute(select(Order).where(
            Order.id == data.order_id, Order.org_id == current.org_id)
            .options(selectinload(Order.lines)))).scalar_one_or_none()
        if order:
            if claw > 0:      # a deposit or an overpayment given back doesn't change what was earned
                from backend.services.payroll import CommissionEngine
                await CommissionEngine(db, current.org_id).clawback_for_refund(order, refund.id, claw)
            from backend.services.order_money import resync_write_off   # dropped job: no debt comes back
            await resync_write_off(db, order, current.id)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "refund", refund.id,
                           f"Refund {data.amount:g} from {acct.name}")
    await db.commit()
    return {"id": refund.id, "amount": data.amount}


async def refundable_without_discount(db, order, *, deposits: bool = True) -> float:
    """How much can go back without taking anything off the job's price: an overpayment, or (W3-C)
    on a job that isn't closed, the deposits still held on it (prepayments less what was already
    refunded). Payments taken on the job itself aren't a deposit: refunding them is a discount."""
    from backend.services.order_money import net_paid as _net_paid
    from backend.services.money import q2
    net = await _net_paid(db, order.id)
    keep = max(0.0, net - float(order.total or 0))
    if deposits:
        from backend.routers.orders import _is_closed
        if not order.is_estimate and not await _is_closed(db, order.org_id, order):
            from sqlalchemy import func
            prepaid = float((await db.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.order_id == order.id, Payment.kind == "prepayment"))).scalar() or 0)
            refunded = float((await db.execute(select(func.coalesce(func.sum(func.abs(Payment.amount)), 0)).where(
                Payment.order_id == order.id, Payment.kind == "refund"))).scalar() or 0)
            keep = max(keep, min(net, prepaid - refunded))
    return q2(max(keep, 0.0))


async def book_refund(db, current, order, acct, amount: float, *, reason=None, return_id=None,
                      reference=None) -> Refund:
    """Money back to the customer on a job, from one cashbox — the one way every refund path books
    it (/returns/refunds, /cash/orders/{id}/refund, deleting a paid job): the Refund document, its
    refund Payment(s) off held money and then the job's invoices (invoice_money.refund_payments),
    cash out of the drawer's shift, the cashbox ledger and the customer's ledger. Caller commits."""
    refund = Refund(org_id=current.org_id, order_id=order.id, return_id=return_id,
                    account_id=acct.id, amount=amount, reason=reason, created_by_id=current.id)
    db.add(refund)
    await db.flush()
    from backend.routers.cash import open_drawer_shift, post_transaction
    from backend.services.invoice_money import refund_payments
    _shift = await open_drawer_shift(db, current.org_id, acct)
    pays = await refund_payments(db, order, amount, method="cash" if _shift else "refund",
                                 account_id=acct.id, received_by_id=current.id,
                                 shift_id=_shift,   # cash handed back from a drawer reduces that drawer's expected cash
                                 refund_id=refund.id,   # a void finds exactly these rows (DB-11)
                                 reference=(reference or f"Refund: {reason or ''}")[:200])
    refund.payment_id = pays[0].id
    await post_transaction(db, current.org_id, acct.id, "refund", -amount,
                           comment=f"Refund for order {order.order_number}: {reason or ''}".strip(),
                           ref_type="refund", ref_id=refund.id, employee_id=current.id)
    if order.client_id:
        # cash went back to them, so the Balance tab / Client.balance stays in sync
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, order.client_id, "refund", abs(amount),
                                  note=f"Refund for {order.order_number}", ref_type="refund",
                                  ref_id=refund.id, created_by_id=current.id)
    return refund


@router.get("")
async def list_returns(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Order, Client, Part
    from backend.services.batching import names_by_id
    rows = (await db.execute(select(ClientReturn).where(ClientReturn.org_id == current.org_id)
        .order_by(ClientReturn.created_at.desc()).limit(200))).scalars().all()
    by_return: dict = {}
    if rows:
        for ln, name in (await db.execute(select(ClientReturnLine, Part.name)
                .join(Part, ClientReturnLine.part_id == Part.id)
                .where(ClientReturnLine.return_id.in_([r.id for r in rows])))).all():
            by_return.setdefault(ln.return_id, []).append((ln, name))
    order_no = await names_by_id(db, Order.id, Order.order_number, [r.order_id for r in rows],
                                 org_col=Order.org_id, org_id=current.org_id)
    client_name = await names_by_id(db, Client.id, Client.name, [r.client_id for r in rows],
                                    org_col=Client.org_id, org_id=current.org_id)
    out = []
    for r in rows:
        out.append({"id": r.id, "order_id": r.order_id, "order_number": order_no.get(r.order_id),
                    "client_name": client_name.get(r.client_id), "reason": r.reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "lines": [{"part_name": name, "qty": l.qty, "restock": l.restock}
                              for l, name in by_return.get(r.id, [])]})
    return {"items": out}


@router.get("/refunds")
async def list_refunds(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    from backend.models import Order, Account as Acct
    rows = (await db.execute(select(Refund).where(Refund.org_id == current.org_id)
        .order_by(Refund.created_at.desc()).limit(200))).scalars().all()
    from backend.services.batching import names_by_id
    order_no = await names_by_id(db, Order.id, Order.order_number, [r.order_id for r in rows],
                                 org_col=Order.org_id, org_id=current.org_id)
    acct_name = await names_by_id(db, Acct.id, Acct.name, [r.account_id for r in rows],
                                  org_col=Acct.org_id, org_id=current.org_id)
    out = []
    for r in rows:
        out.append({"id": r.id, "amount": r.amount, "reason": r.reason,
                    "order_id": r.order_id, "order_number": order_no.get(r.order_id),
                    "account_name": acct_name.get(r.account_id),
                    "return_id": r.return_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "voided_at": r.voided_at.isoformat() if r.voided_at else None,
                    "void_reason": r.void_reason})
    return {"items": out}


async def _refund_payment(db, refund):
    """The Payment row a refund created: linked since refunds record it, matched for older ones."""
    if refund.payment_id:
        return (await db.execute(select(Payment).where(Payment.id == refund.payment_id))).scalar_one_or_none()
    cands = (await db.execute(select(Payment).where(
        Payment.order_id == refund.order_id, Payment.kind == "refund", Payment.amount == refund.amount,
        Payment.account_id == refund.account_id, Payment.refund_id.is_(None)))).scalars().all()
    if not cands:
        return None
    at = refund.created_at or now_utc()
    return min(cands, key=lambda p: abs(((p.paid_at or at) - at).total_seconds()))


@router.delete("/refunds/{refund_id}")
async def delete_refund(
    refund_id: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Void a refund that shouldn't have happened. The refund stays on record (struck through)
    and is undone: its payout leaves the cashbox ledger, its refund Payments are voided (kept on
    record, out of every total, so the order counts as paid again), the client's ledger entry is reversed and the commission it clawed
    back is given back in the same accrual group."""
    await assert_perm(db, current, "can_delete_refunds")
    from backend.models import CashShift, ClientLedgerEntry
    refund = (await db.execute(select(Refund).where(
        Refund.id == refund_id, Refund.org_id == current.org_id).with_for_update())).scalar_one_or_none()
    if not refund:
        raise HTTPException(404, "Refund not found")
    if refund.voided_at:
        raise HTTPException(409, "This refund was already deleted")
    if refund.order_id:
        from backend.services.invoice_money import lock_order
        await lock_order(db, refund.order_id)    # the job's lines and total change below
    order = (await db.execute(select(Order).where(
        Order.id == refund.order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines)))).scalar_one_or_none() if refund.order_id else None
    if order is not None and order.archived:
        raise HTTPException(409, "This refund was made when the order was archived. Restore the order instead.")
    # DB-11: the refund's own Payment rows (by refund id), which are voided below, not deleted
    from backend.services.invoice_money import refund_rows, void_refund_payment
    pays = await refund_rows(db, refund, await _refund_payment(db, refund))
    pay = pays[0] if pays else None
    shifts = {p.shift_id for p in pays if p.shift_id}
    if shifts and (await db.execute(select(CashShift.id).where(
            CashShift.id.in_(shifts), CashShift.closed_at.isnot(None)).limit(1))).first():
        raise HTTPException(409, "The cash for this refund came out of a drawer shift that is already closed.")
    reason = (reason or "Deleted").strip()[:300]

    # they stay on record, every total leaves them out and their invoices count the money again
    for p in pays:
        await void_refund_payment(db, p, current.id)
    from backend.routers.cash import void_document_legs
    await void_document_legs(db, current.org_id, "refund", refund.id, reason, current.id)
    entries = (await db.execute(select(ClientLedgerEntry).where(
        ClientLedgerEntry.org_id == current.org_id, ClientLedgerEntry.ref_type == "refund",
        ClientLedgerEntry.ref_id == refund.id))).scalars().all()
    from backend.services.ledger import post_contact_ledger
    for e in entries:
        await post_contact_ledger(db, current.org_id, e.client_id, "refund_void", -float(e.amount or 0),
                                  note=f"Refund deleted: {reason}", ref_type="refund_void", ref_id=refund.id,
                                  created_by_id=current.id)
    if order is not None:
        await _put_back_on_job(db, current, order, refund)
    refund.voided_at = now_utc()
    refund.voided_by_id = current.id
    refund.void_reason = reason
    await db.flush()
    if order is not None:
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).restore_refund_clawback(order, refund.id)
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="refund.delete",
                       entity_type="refund", entity_id=refund.id,
                       diff={"amount": float(refund.amount or 0), "order_id": refund.order_id, "reason": reason,
                             "payment_id": pay.id if pay is not None else None})
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "refund", refund.id, f"Refund {float(refund.amount or 0):g} deleted: {reason}")
    out = {"ok": True, "id": refund.id, "voided_at": refund.voided_at.isoformat()}
    voided_ids = [p.id for p in pays]
    await db.commit()
    # a refund Payment already pushed to QuickBooks is voided there too, off the request (logged;
    # never undoes this void; void_payments never raises, so no retries)
    if voided_ids:
        from backend.core.jobs import job_runner
        from backend.services import qbo
        job_runner.enqueue(qbo.void_payments_job, current.org_id, voided_ids, retries=0, name="qbo-void")
    return out


# A job line a refund put there: it belongs to the refund (delete the refund to undo it), so the
# line routes refuse to edit or remove it and a copied job leaves it behind.
REFUND_LINE_KIND = "refund"


async def _refund_line_label(db, refund) -> str:
    """What the job line says: the returned items, else the refund's reason."""
    if refund.return_id:
        from backend.models import Part
        names = (await db.execute(select(Part.name).join(ClientReturnLine, ClientReturnLine.part_id == Part.id)
                                  .where(ClientReturnLine.return_id == refund.return_id))).scalars().all()
        if names:
            return f"Returned: {', '.join(names)}"[:255]
    return f"Refund: {refund.reason}"[:255] if (refund.reason or "").strip() else "Refund"


def _job_tax(lines) -> tuple[float, str]:
    """The tax a refund line carries inside its amount: the job's one rate when all its lines share
    it (the refund is money incl. tax), else none."""
    rates = {float(ln.tax_rate or 0) for ln in lines if (ln.total or 0) > 0}
    return (rates.pop(), "included") if len(rates) == 1 else (0.0, "included")


async def _sync_charge(db, current, order) -> None:
    """A billed (closed) job's charge on the customer's ledger follows its new total; an open one's
    stays the client credit spent on it (H6), which a smaller total can lower."""
    if not order.client_id:
        return
    from backend.routers.orders import _is_closed
    from backend.services.ledger import credit_spent_target, order_charge_net, post_contact_ledger
    from backend.services.money import q2
    net = await order_charge_net(db, order.client_id, order.id)
    if await _is_closed(db, current.org_id, order):
        if not net:
            return
        target = q2(float(order.total or 0))
    else:
        target = await credit_spent_target(db, order)
    if q2(target - net):
        await post_contact_ledger(db, current.org_id, order.client_id, "charge", q2(target - net),
                                  note=f"Order {order.order_number} refund", ref_type="order", ref_id=order.id,
                                  created_by_id=current.id)


async def _take_off_job(db, current, order, refund, amount: float) -> None:
    """A negative line for the refunded amount (ref_id = the refund), then the job's totals."""
    from backend.models import OrderLine
    from backend.routers.orders import _recalc_order
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    rate, mode = _job_tax(lines)
    db.add(OrderLine(order_id=order.id, line_type="fee", kind=REFUND_LINE_KIND, ref_id=refund.id,
                     description=await _refund_line_label(db, refund), qty=1, unit_price=-amount,
                     tax_rate=rate, tax_mode=mode, total=-amount))
    await db.flush()
    await _recalc_order(db, order)
    await _sync_charge(db, current, order)


async def _put_back_on_job(db, current, order, refund) -> None:
    """Deleting the refund puts back what it took off the job."""
    from sqlalchemy import delete

    from backend.models import OrderLine
    from backend.routers.orders import _recalc_order
    gone = await db.execute(delete(OrderLine).where(OrderLine.order_id == order.id, OrderLine.ref_id == refund.id,
                                                    OrderLine.kind == REFUND_LINE_KIND))
    if gone.rowcount:
        await db.flush()
        await _recalc_order(db, order)
        await _sync_charge(db, current, order)


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
