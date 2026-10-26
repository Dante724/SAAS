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


async def credited(db: AsyncSession, order_id: str) -> float:
    """Issued credit notes on the (non-cancelled) invoices billing this order — they lower what's
    owed. An invoice billing several orders shares its credit between them by order total."""
    from backend.models import InvoiceOrder
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order_id)
    invs = (await db.execute(select(Invoice).where(
        (Invoice.order_id == order_id) | Invoice.id.in_(linked), Invoice.credited > 0,
        Invoice.status.notin_(("void", "voided", "cancelled", "canceled"))))).scalars().all()
    total = 0.0
    for inv in invs:
        # Every order the invoice bills: its links plus the primary order_id (invoices created by a
        # payment had no link row for it, so attaching a second order left the primary unlinked).
        oids = set((await db.execute(select(InvoiceOrder.order_id).where(
            InvoiceOrder.invoice_id == inv.id))).scalars().all()) | ({inv.order_id} if inv.order_id else set())
        if len(oids) <= 1:
            total += float(inv.credited or 0)
            continue
        amounts = dict((await db.execute(select(Order.id, Order.total).where(Order.id.in_(oids)))).all())
        whole = sum(float(v or 0) for v in amounts.values())
        share = float(amounts.get(order_id) or 0) / whole if whole else 1 / len(oids)
        total += float(inv.credited or 0) * share
    return q2(total)


async def resync_write_off(db: AsyncSession, order: Order, actor_id: str | None = None) -> None:
    """A dropped job's write-off follows the money the shop actually kept: when money comes back
    to the customer (a refund) after the job was dropped, the written-off part grows by the same
    amount and the customer's ledger charge shrinks with it — so a refund never revives a debt.
    No-op for any job that isn't in a dropped status. Caller commits."""
    from backend.models import OrderStatus
    grp = (await db.execute(select(OrderStatus.status_group).where(
        OrderStatus.org_id == order.org_id, OrderStatus.key == order.status,
        OrderStatus.record_type == "work_order"))).scalars().first()
    if grp != "dropped" or order.is_estimate:
        return
    order.written_off = q2(max(0.0, float(order.total or 0) - await credited(db, order.id)
                               - await net_paid(db, order.id)))
    if order.client_id:
        from backend.services.ledger import order_charge_net, post_contact_ledger
        delta = q2(float(order.total or 0) - float(order.written_off) - await order_charge_net(db, order.client_id, order.id))
        if delta:
            await post_contact_ledger(db, order.org_id, order.client_id, "charge", delta,
                                      note=f"Order {order.order_number} dropped: write-off updated",
                                      ref_type="order", ref_id=order.id, created_by_id=actor_id)


async def _unsettled(db: AsyncSession, order: Order) -> float:
    return q2(max(0.0, float(order.total or 0) - await net_paid(db, order.id) - await credited(db, order.id)))


async def balance_due(db: AsyncSession, order: Order) -> float:
    """Amount still owed on the order (never negative), net of payments, credit notes and any
    balance written off when the job was dropped."""
    return q2(max(0.0, await _unsettled(db, order) - float(getattr(order, "written_off", 0) or 0)))


async def sync_write_off(db: AsyncSession, order: Order, *, dropped: bool,
                         actor_id: str | None = None) -> float:
    """Keep a dropped job's write-off equal to what was still unpaid, so it stops showing as
    owed. Leaving the dropped group (reopen, or moving on to a closed status) reverses it.
    The write-off is recorded on the order, in the client's ledger and in the order feed.
    Idempotent. Returns the change (+ = more written off). Caller commits."""
    current = q2(float(getattr(order, "written_off", 0) or 0))
    target = await _unsettled(db, order) if dropped else 0.0
    delta = q2(target - current)
    if not delta:
        return 0.0
    order.written_off = target
    from backend.services.ledger import post_contact_ledger
    if order.client_id and not order.is_estimate:
        await post_contact_ledger(
            db, order.org_id, order.client_id, "write_off", -delta,
            note=(f"Unpaid balance on {order.order_number} written off (job dropped)" if delta > 0
                  else f"Write-off on {order.order_number} reversed"),
            ref_type="order", ref_id=order.id, created_by_id=actor_id)
    from backend.routers.feed import log_system_event
    await log_system_event(db, order.org_id, "order", order.id,
                           f"Unpaid balance {target:.2f} written off" if delta > 0
                           else f"Write-off of {current:.2f} reversed")
    from backend.services.audit import record as _audit
    await _audit(db, org_id=order.org_id, actor_id=actor_id, action="order.write_off",
                 entity_type="order", entity_id=order.id, diff={"written_off": [current, target]})
    return delta


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
        await revoke_pay_link(db, i)
    return len(open_)


async def revoke_links_above_balance(db: AsyncSession, inv: Invoice) -> int:
    """After a credit note lowers what's owed, a pay link sent for the old amount would charge
    the customer too much: revoke every open link (on the invoice or the orders it bills) for more
    than the new balance. Staff can send a fresh link for the right amount. Caller commits."""
    from backend.models import InvoiceOrder
    from backend.services.invoicing import balance_due as invoice_due
    oids = set((await db.execute(select(InvoiceOrder.order_id).where(
        InvoiceOrder.invoice_id == inv.id))).scalars().all()) | ({inv.order_id} if inv.order_id else set())
    due = {None: invoice_due(inv)}
    for o in (await db.execute(select(Order).where(Order.id.in_(oids)))).scalars().all() if oids else []:
        due[o.id] = await balance_due(db, o)
    cond = PaymentIntent.invoice_id == inv.id
    if oids:
        cond = cond | PaymentIntent.order_id.in_(oids)
    open_ = (await db.execute(select(PaymentIntent).where(
        cond, PaymentIntent.status.in_(["created", "viewed", "pending"])))).scalars().all()
    n = 0
    for i in open_:
        limit = due.get(i.order_id, due[None]) if i.order_id else due[None]
        if q2(float(i.amount or 0)) > q2(limit):
            await revoke_pay_link(db, i)
            n += 1
    return n


async def revoke_pay_link(db: AsyncSession, intent: PaymentIntent) -> None:
    """Cancel an unpaid link here AND close it at the provider. Cancelling only our row left the
    Stripe/Square checkout payable, so a customer holding an old link could still pay it.
    Caller commits."""
    intent.status = "canceled"
    if intent.provider == "stripe" and intent.provider_ref:
        await _expire_stripe_checkout(db, intent)
    elif intent.provider == "square" and intent.provider_link_id:
        await _delete_square_link(db, intent)


async def _expire_stripe_checkout(db: AsyncSession, intent: PaymentIntent) -> bool:
    """Close the Stripe-hosted checkout too, so a customer holding the old link can't pay on
    Stripe's page after the order is settled (B3). Best effort: if Stripe can't be reached the
    link is still cancelled here, and a late payment is still recorded (as an overpayment)
    rather than lost."""
    import logging

    import httpx

    from backend.config import settings
    from backend.services import org_creds
    from backend.services.payment_guard import LivePaymentsRefused
    try:
        key = (await org_creds.effective(db, intent.org_id, "stripe")).get("secret_key") or settings.STRIPE_SECRET_KEY
    except LivePaymentsRefused:
        return False   # F4: live keys refused on this server; the link is still cancelled here
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
    from backend.services.payment_guard import LivePaymentsRefused
    from backend.services.payments import SquareGateway
    try:
        c = await org_creds.effective(db, intent.org_id, "square")
    except LivePaymentsRefused:
        return False   # F4: live keys refused on this server; the link is still cancelled here
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


# Reader charges still in flight: "pending" waits for the card, "processing" is approved but not
# yet posted. While one exists, recording other money (or changing the balance) collects twice.
READER_IN_FLIGHT = ("pending", "processing")


async def reader_charge_in_flight(db: AsyncSession, order_id: str | None) -> bool:
    if not order_id:
        return False
    from backend.models import TerminalCharge
    return (await db.execute(select(TerminalCharge.id).where(
        TerminalCharge.order_id == order_id, TerminalCharge.status.in_(READER_IN_FLIGHT))
        .limit(1))).scalar_one_or_none() is not None


async def unrefunded_reader_money(db: AsyncSession, order_id: str) -> float:
    """Card money taken on a reader for this order and not yet given back on the reader."""
    from backend.models import TerminalCharge
    rows = (await db.execute(select(TerminalCharge.amount, TerminalCharge.refunded_amount).where(
        TerminalCharge.order_id == order_id, TerminalCharge.status == "succeeded"))).all()
    return q2(sum(float(a or 0) - float(r or 0) for a, r in rows))
