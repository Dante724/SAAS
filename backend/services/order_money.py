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

from backend.models import Invoice, Order, Payment, PaymentIntent
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


async def primary_invoice(db: AsyncSession, order_id: str):
    """The invoice money for this order should land on: the newest one still open, else the
    newest. Orders can carry several invoices (sub-invoices, invoices attached later), and the
    old scalar_one_or_none() lookups 500'd with MultipleResultsFound once they did."""
    invs = (await db.execute(select(Invoice).where(Invoice.order_id == order_id)
                             .order_by(Invoice.created_at.desc()))).scalars().all()
    open_ = [i for i in invs if (i.status or "") not in ("paid", "void", "voided", "cancelled", "canceled")]
    return (open_ or invs or [None])[0]


async def cancel_open_pay_links(db: AsyncSession, order: Order) -> int:
    """Once nothing is owed, kill unpaid pay links so a stale link can't charge the customer
    again. Caller commits."""
    if await balance_due(db, order) > 0:
        return 0
    open_ = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.order_id == order.id,
        PaymentIntent.status.in_(["created", "viewed", "pending"])))).scalars().all()
    for i in open_:
        i.status = "canceled"
        if i.provider == "stripe" and i.provider_ref:
            await _expire_stripe_checkout(db, i)
        elif i.provider == "square" and i.provider_link_id:
            await _delete_square_link(db, i)
    return len(open_)


async def _expire_stripe_checkout(db: AsyncSession, intent: PaymentIntent) -> bool:
    """Close the Stripe-hosted checkout too, so a customer holding the old link can't pay on
    Stripe's page after the order is settled (B3). Best effort: if Stripe can't be reached the
    link is still cancelled here, and a late payment is still recorded (as an overpayment)
    rather than lost."""
    import logging

    import httpx

    from backend.config import settings
    from backend.services import org_creds
    key = (await org_creds.effective(db, intent.org_id, "stripe")).get("secret_key") or settings.STRIPE_SECRET_KEY
    if not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"https://api.stripe.com/v1/checkout/sessions/{intent.provider_ref}/expire",
                                  auth=(key, ""))
        return r.status_code == 200
    except httpx.HTTPError:
        logging.getLogger("tekly.payments").warning("could not expire Stripe checkout for intent %s", intent.id)
        return False


async def _delete_square_link(db: AsyncSession, intent: PaymentIntent) -> bool:
    """Delete the Square payment link so the customer can't pay on Square's page after the
    order is settled (B3). Best effort, like the Stripe expiry. Links created before the link
    id was stored (migration dp144q1) can't be closed this way."""
    import logging

    import httpx

    from backend.config import settings
    from backend.services import org_creds
    from backend.services.payments import SquareGateway
    c = await org_creds.effective(db, intent.org_id, "square")
    token = c.get("access_token") or settings.SQUARE_ACCESS_TOKEN
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.delete(
                f"{SquareGateway.base_url(c.get('env'))}/v2/online-checkout/payment-links/{intent.provider_link_id}",
                headers={"Authorization": f"Bearer {token}", "Square-Version": "2024-06-04"})
        return r.status_code == 200
    except httpx.HTTPError:
        logging.getLogger("tekly.payments").warning("could not delete Square link for intent %s", intent.id)
        return False
