"""Build 144dr — the invoice loop.

The differentiator over Orderry: instead of making staff hand-build an invoice
after every job, Tekly can auto-draft one the moment a work order closes
(org opt-in), pre-linked to the order and carrying its totals + the org's default
display mode. Combined with the existing order payment-link + gateway webhook,
a shop can go from "close job" to "client paid" with no document-building step.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, Order, Organisation
from backend.services.money import d2

VOID_STATUSES = ("cancelled", "canceled", "void", "voided")
DISPLAY_MODES = ("detailed", "simplified", "one_line")


def balance_due(inv: "Invoice") -> float:
    """What the client still owes on an invoice: total − paid − issued credit notes, never below 0."""
    owed = d2(inv.total or 0) - d2(inv.amount_paid or 0) - d2(getattr(inv, "credited", 0) or 0)
    return float(max(owed, d2(0)))


def settle_status(inv: "Invoice") -> None:
    """Set paid / partial from what's covered (payments + credit notes). An invoice whose payments
    and credits were all reversed goes back to issued if it was sent, else draft; a cancelled
    invoice is never touched."""
    if (inv.status or "") in VOID_STATUSES:
        return
    total = d2(inv.total or 0)
    covered = d2(inv.amount_paid or 0) + d2(getattr(inv, "credited", 0) or 0)
    if total > 0 and covered >= total:
        inv.status = "paid"
    elif covered > 0:
        inv.status = "partial"
    elif inv.status in ("paid", "partial"):
        inv.status = "issued" if getattr(inv, "sent_at", None) else "draft"


async def invoice_orders(db: AsyncSession, inv: "Invoice") -> list["Order"]:
    """Build 144du — every work order an invoice bills, in sort order. Reads the
    invoice_orders link table; falls back to the primary order_id when there are
    no links (older single-order invoices)."""
    from backend.models import InvoiceOrder, Order
    links = (await db.execute(select(InvoiceOrder).where(
        InvoiceOrder.invoice_id == inv.id).order_by(InvoiceOrder.sort_order))).scalars().all()
    oids = [l.order_id for l in links]
    if inv.order_id and inv.order_id not in oids:     # older invoices had no link for their primary job
        oids.insert(0, inv.order_id)
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


async def display_lines(db: AsyncSession, inv: "Invoice", *, itemised: bool = False) -> list[dict]:
    """Build 144dr/144dt/144du/144ed — resolve an invoice's lines according to its
    display_mode (the Orderry "Order display" feature), fanning out across EVERY
    work order the invoice bills AND its own free-form lines. detailed = every order
    line + every free line; simplified = one line per source order + each free line;
    one_line = a single line for the whole invoice. Computed on the fly so changing
    the mode never rewrites stored data.

    Detailed rows go through kit_print.customer_lines: a kit set to print as one line is folded
    (only over the lines this invoice bills); itemised=True is the "Print itemised copy" path."""
    from backend.models import InvoiceLine, OrderLine
    mode = "detailed" if itemised else (getattr(inv, "display_mode", "detailed") or "detailed")
    orders = await invoice_orders(db, inv)
    free = await free_lines(db, inv)
    # An invoice that bills only SOME of a job's lines (per-line sub-invoice, or several items
    # per job "Collect & invoice") lists exactly those lines — never the whole job again.
    billed_pos = {lid: pos for lid, pos in (await db.execute(select(InvoiceLine.order_line_id, InvoiceLine.sort_order).where(
        InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.isnot(None)))).all()}
    billed = set(billed_pos)

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
        sums: dict = {}
        if billed:
            for oid, tot in (await db.execute(select(OrderLine.order_id, OrderLine.total).where(
                    OrderLine.id.in_(billed)))).all():
                sums[oid] = sums.get(oid, 0.0) + float(tot or 0)
        for o in orders:
            amt = round(sums.get(o.id, 0.0), 2) if billed else _order_amount(o)
            desc = f"{o.order_number}" + (f" — {o.complaint}" if getattr(o, "complaint", None) else "")
            out.append({"description": desc, "qty": 1, "unit_price": amt, "total": amt})
        return out + free

    # detailed (default): every order line across all orders, then free lines
    out = []
    if orders:
        oids = [o.id for o in orders]
        q = select(OrderLine).where(OrderLine.order_id.in_(oids))
        if billed:
            q = q.where(OrderLine.id.in_(billed))
        lines = list((await db.execute(q)).scalars().all())
        if billed:
            lines.sort(key=lambda l: billed_pos.get(l.id) or 0)   # the order they were billed in
        order_pos = {oid: i for i, oid in enumerate(oids)}
        # Several items per job: lines carry their item, grouped item by item, whole-job last.
        from backend.services.multi_asset import item_names
        names = await item_names(db, inv.org_id, [getattr(l, "order_item_id", None) for l in lines])

        def _k(l):
            n = names.get(getattr(l, "order_item_id", None))
            return (order_pos.get(l.order_id, 0), 0 if n else 1, n[1] if n else 0, n[0] if n else "")
        lines = sorted(lines, key=_k)
        from backend.services import kit_print
        for l in kit_print.customer_lines(lines, itemised=itemised):
            row = kit_print.row(l)
            if names:
                n = names.get(getattr(l, "order_item_id", None))
                row["item_id"] = l.order_item_id if n else None
                row["item"] = n[0] if n else None
            out.append(row)
    return out + free


async def build_invoice_pdf(db: AsyncSession, inv: "Invoice", *, org_name: str, lang: str = "en",
                            itemised: bool = False) -> bytes:
    """Build 144dw — render an invoice to PDF with its lines resolved through the
    Order-display mode across every linked order. Shared by the print endpoint and
    the send-by-email action so both produce an identical document."""
    from backend.models import Client
    from backend.services import printing
    lines = await display_lines(db, inv, itemised=itemised)
    orders = await invoice_orders(db, inv)
    from backend.services.multi_asset import group_rows
    rows = []
    for r in group_rows(lines):
        if r["row"] == "group":
            rows.append([r["item"], "", "", ""])
        elif r["row"] == "subtotal":
            rows.append(["", "", f"{r['item']} subtotal", f"{float(r['total'] or 0):.2f}"])
        else:
            rows.append([("   " if "item" in r else "") + (r["description"] or ""),
                         f"{float(r['qty'] or 0):g}", f"{float(r['unit_price'] or 0):.2f}", f"{float(r['total'] or 0):.2f}"])
    payer_id = getattr(inv, "payer_client_id", None) or inv.client_id
    payer = None
    if payer_id:
        payer = (await db.execute(select(Client).where(Client.id == payer_id))).scalar_one_or_none()
    mode_label = "Itemised copy" if itemised else {"detailed": "Detailed", "simplified": "Simplified (per order)",
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
    # already invoiced (on its own or a shared invoice)? don't duplicate. A cancelled one doesn't count.
    from backend.services.invoice_money import invoices_for_order
    existing = next(iter(await invoices_for_order(db, order.id)), None)
    if existing:
        return await _draft_remainder(db, org, order) or existing

    from backend.services.invoice_money import new_invoice
    due_days = getattr(org, "default_due_days", None) or 30
    return await new_invoice(db, org_id=order.org_id, orders=[order],
                             display_mode=getattr(org, "default_invoice_display", "detailed"),
                             auto_drafted=True, due_at=datetime.utcnow() + timedelta(days=due_days))


async def _draft_remainder(db: AsyncSession, org, order: Order):
    """Several items per job: items collected earlier were billed line by line ("Collect &
    invoice"). On close, draft the lines nobody billed yet — unless a whole-job invoice exists."""
    from backend.models import InvoiceLine, OrderLine
    from backend.services import multi_asset as ma
    billed = await ma.invoiced_line_ids(db, order.org_id, order.id)
    if not billed:
        return None
    from backend.models import InvoiceOrder
    partial = select(InvoiceLine.invoice_id).where(InvoiceLine.order_line_id.isnot(None))
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order.id)
    whole = (await db.execute(select(Invoice.id).where(
        Invoice.org_id == order.org_id, (Invoice.order_id == order.id) | Invoice.id.in_(linked),
        Invoice.status.notin_(ma.VOID_INVOICE), Invoice.id.notin_(partial)))).scalars().first()
    if whole:
        return None
    rest = [l for l in (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
            if l.id not in billed]
    if not rest:
        return None
    inv = await ma.bill_lines(db, order, rest, display_mode=getattr(org, "default_invoice_display", "detailed") or "detailed")
    inv.auto_drafted = True
    return inv
