"""What a work order has actually been paid — one definition for every money path.

Before this, "balance due" was computed four different ways (total − prepayment_total in pay
links / tech / engage; Payment sums in pay_order; Payment sums ignoring Refund rows in the refund
cap), so an in-store payment didn't reduce the pay-link balance (customers charged twice) and
refunds made through /returns/refunds were invisible to the cap (repeatable refunds).

Conventions every writer follows:
- Money in for an order is a Payment with order_id set and kind "payment" or "prepayment".
- Money back is a Payment with kind "refund" and a positive amount (the Refund row is the
  document; the Payment is what the balance math counts).
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Order, Payment, PaymentIntent
from backend.services.money import q2


async def paid_and_refunded(db: AsyncSession, order_id: str) -> tuple[float, float]:
    rows = (await db.execute(
        select(Payment.kind, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.order_id == order_id).group_by(Payment.kind))).all()
    by_kind = {k: float(v or 0) for k, v in rows}
    paid = by_kind.get("payment", 0.0) + by_kind.get("prepayment", 0.0)
    refunded = abs(by_kind.get("refund", 0.0))
    return q2(paid), q2(refunded)


async def net_paid(db: AsyncSession, order_id: str) -> float:
    paid, refunded = await paid_and_refunded(db, order_id)
    return q2(paid - refunded)


async def balance_due(db: AsyncSession, order: Order) -> float:
    """Amount still owed on the order (never negative)."""
    return q2(max(0.0, float(order.total or 0) - await net_paid(db, order.id)))


async def overpaid(db: AsyncSession, order: Order) -> float:
    """Amount received beyond the order total (refundable as overpayment)."""
    return q2(max(0.0, await net_paid(db, order.id) - float(order.total or 0)))


async def cancel_open_pay_links(db: AsyncSession, order: Order) -> int:
    """Once nothing is owed, kill unpaid pay links so a stale link can't charge the customer
    again. Caller commits."""
    if await balance_due(db, order) > 0:
        return 0
    open_ = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.order_id == order.id,
        PaymentIntent.status.in_(["created", "pending"])))).scalars().all()
    for i in open_:
        i.status = "canceled"
    return len(open_)
