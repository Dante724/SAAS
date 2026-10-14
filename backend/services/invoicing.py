"""Build 144dr — the invoice loop.

The differentiator over Orderry: instead of making staff hand-build an invoice
after every job, RepairOS can auto-draft one the moment a work order closes
(org opt-in), pre-linked to the order and carrying its totals + the org's default
display mode. Combined with the existing order payment-link + gateway webhook,
a shop can go from "close job" to "client paid" with no document-building step.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, Order, Organisation


def _inv_number(n: int) -> str:
    return f"INV-{n:05d}"


async def invoice_orders(db: AsyncSession, inv: "Invoice") -> list["Order"]:
    """Build 144du — every work order an invoice bills, in sort order. Reads the
    invoice_orders link table; falls back to the primary order_id when there are
    no links (older single-order invoices)."""
    from backend.models import InvoiceOrder, Order
    links = (await db.execute(select(InvoiceOrder).where(
        InvoiceOrder.invoice_id == inv.id).order_by(InvoiceOrder.sort_order))).scalars().all()
    oids = [l.order_id for l in links] or ([inv.order_id] if inv.order_id else [])
    if not oids:
        return []
    rows = (await db.execute(select(Order).where(Order.id.in_(oids)))).scalars().all()
    by_id = {o.id: o for o in rows}
    return [by_id[oid] for oid in oids if oid in by_id]


def _order_amount(order) -> float:
    return float(order.subtotal if getattr(order, "subtotal", None) else (order.total or 0))


async def free_lines(db: AsyncSession, inv: "Invoice") -> list[dict]:
    """Build 144ed — the invoice's own hand-added catalog/manual lines
    (order_line_id is null), in sort order. Independent of any work order."""
    from backend.models import InvoiceLine
    rows = (await db.execute(select(InvoiceLine).where(
        InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.is_(None))
        .order_by(InvoiceLine.sort_order))).scalars().all()
    return [{"description": l.description, "qty": l.qty, "unit_price": l.unit_price,
             "total": float(l.total or (l.qty or 0) * (l.unit_price or 0))} for l in rows]


async def display_lines(db: AsyncSession, inv: "Invoice") -> list[dict]:
    """Build 144dr/144dt/144du/144ed — resolve an invoice's lines according to its
    display_mode (the Orderry "Order display" feature), fanning out across EVERY
    work order the invoice bills AND its own free-form lines. detailed = every order
    line + every free line; simplified = one line per source order + each free line;
    one_line = a single line for the whole invoice. Computed on the fly so changing
    the mode never rewrites stored data."""
    from backend.models import OrderLine
    mode = getattr(inv, "display_mode", "detailed") or "detailed"
    orders = await invoice_orders(db, inv)
    free = await free_lines(db, inv)

    if mode == "one_line":
        first = orders[0] if orders else None
        label = (first.order_number if first else "Services & products")
        extra = (len(orders) - 1 if orders else 0) + (len(free) if not orders else len(free))
        if extra > 0:
            label += f" +{extra} more"
        amt = float(inv.subtotal or (sum(_order_amount(o) for o in orders)
                    + sum(l["total"] for l in free)) or 0)
        return [{"description": label, "qty": 1, "unit_price": amt, "total": amt}]

    if mode == "simplified":
        # one line per source order, then each free line as its own row
        out = []
        for o in orders:
            amt = _order_amount(o)
            desc = f"{o.order_number}" + (f" — {o.complaint}" if getattr(o, "complaint", None) else "")
            out.append({"description": desc, "qty": 1, "unit_price": amt, "total": amt})
        return out + free

    # detailed (default): every order line across all orders, then free lines
    out = []
    if orders:
        oids = [o.id for o in orders]
        lines = (await db.execute(select(OrderLine).where(OrderLine.order_id.in_(oids)))).scalars().all()
        order_pos = {oid: i for i, oid in enumerate(oids)}
        lines = sorted(lines, key=lambda l: order_pos.get(l.order_id, 0))
        out = [{"description": l.description, "qty": l.qty, "unit_price": l.unit_price,
                "total": float(l.total or (l.qty or 0) * (l.unit_price or 0))} for l in lines]
    return out + free


async def build_invoice_pdf(db: AsyncSession, inv: "Invoice", *, org_name: str, lang: str = "en") -> bytes:
    """Build 144dw — render an invoice to PDF with its lines resolved through the
    Order-display mode across every linked order. Shared by the print endpoint and
    the send-by-email action so both produce an identical document."""
    from backend.models import Client
    from backend.services import printing
    lines = await display_lines(db, inv)
    orders = await invoice_orders(db, inv)
    rows = [[l["description"], f"{float(l['qty'] or 0):g}",
             f"{float(l['unit_price'] or 0):.2f}", f"{float(l['total'] or 0):.2f}"] for l in lines]
    payer_id = getattr(inv, "payer_client_id", None) or inv.client_id
    payer = None
    if payer_id:
        payer = (await db.execute(select(Client).where(Client.id == payer_id))).scalar_one_or_none()
    mode_label = {"detailed": "Detailed", "simplified": "Simplified (per order)",
                  "one_line": "Single line"}.get(inv.display_mode or "detailed", "Detailed")
    meta = [("Bill to", payer.name if payer else None),
            ("Work orders", ", ".join(o.order_number for o in orders) or None),
            ("Display", mode_label),
            ("Issued", inv.issued_at.strftime("%Y-%m-%d") if inv.issued_at else None),
            ("Due", inv.due_at.strftime("%Y-%m-%d") if inv.due_at else None),
            ("Status", (inv.status or "draft").title())]
    return printing.render_document(
        title="Invoice", org_name=org_name, lang=lang, number=inv.invoice_number,
        meta=[m for m in meta if m[1]],
        headers=["Description", "Qty", "Unit", "Total"], rows=rows,
        totals=["", "", "Total", f"{float(inv.total or 0):.2f}"])


async def auto_draft_on_close(db: AsyncSession, order: Order) -> Invoice | None:
    """When the org opted in, create a draft invoice for a just-closed order.
    Idempotent: never creates a second invoice for the same order. Returns the
    invoice (new or existing) or None when the feature is off."""
    org = (await db.execute(select(Organisation).where(
        Organisation.id == order.org_id))).scalar_one_or_none()
    if not org or not getattr(org, "auto_draft_invoice_on_close", False):
        return None
    # already invoiced? don't duplicate.
    existing = (await db.execute(select(Invoice).where(
        Invoice.order_id == order.id, Invoice.org_id == order.org_id))).scalars().first()
    if existing:
        return existing

    count = (await db.execute(select(func.count()).select_from(Invoice)
             .where(Invoice.org_id == order.org_id))).scalar() or 0
    subtotal = float(getattr(order, "subtotal", 0) or 0) or float(order.total or 0)
    due_days = getattr(org, "default_due_days", None) or 30
    inv = Invoice(
        invoice_number=_inv_number(count + 1),
        org_id=order.org_id,
        order_id=order.id,
        client_id=order.client_id,
        display_mode=getattr(org, "default_invoice_display", "detailed"),
        auto_drafted=True,
        status="draft",
        subtotal=subtotal,
        discount=0.0,
        tax=float(order.tax or 0),
        total=float(order.total or subtotal),
        amount_paid=0.0,
        issued_at=datetime.utcnow(),
        due_at=datetime.utcnow() + timedelta(days=due_days),
    )
    db.add(inv)
    await db.flush()
    from backend.models import InvoiceOrder
    db.add(InvoiceOrder(invoice_id=inv.id, order_id=order.id, sort_order=0))
    await db.flush()
    return inv
