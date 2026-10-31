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
    and credits were all reversed goes back to draft; a cancelled invoice is never touched."""
    if (inv.status or "") in VOID_STATUSES:
        return
    total = d2(inv.total or 0)
    covered = d2(inv.amount_paid or 0) + d2(getattr(inv, "credited", 0) or 0)
    if total > 0 and covered >= total:
        inv.status = "paid"
    elif covered > 0:
        inv.status = "partial"
    elif inv.status in ("paid", "partial"):
        inv.status = "draft"


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
            out.append({"description": desc, "qty": 1, "unit_price": amt, "total": amt, "order_id": o.id})
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
            row["order_id"] = getattr(l, "order_id", None)   # the PDF nets tax per source order
            if names:
                n = names.get(getattr(l, "order_item_id", None))
                row["item_id"] = l.order_item_id if n else None
                row["item"] = n[0] if n else None
            out.append(row)
    return out + free


def _pdf_money(org):
    """Money for the PDF with the shop's currency. The built-in PDF fonts have no glyph for some
    symbols (₹, ₺, ₦, …), so those print as the ISO code instead: "INR 1,200.00"."""
    from backend.services.currency import _base, format_money
    base = _base(org)
    sym, code = base["symbol"] or "", base["code"] or ""
    try:
        sym.encode("cp1252")
    except UnicodeEncodeError:
        sym = code
    return lambda v: format_money(float(v or 0), sym, code)


async def _seller(db: AsyncSession, org, inv) -> tuple[list[str], list[tuple]]:
    """Who issues the invoice: the invoice's legal entity, else the location's, else the shop's
    default one, else the company details from Settings. Returns (header lines, meta rows)."""
    from backend.models import LegalEntity, Location
    le_id = getattr(inv, "legal_entity_id", None)
    if not le_id and inv.order_id:
        le_id = (await db.execute(select(Location.legal_entity_id).join(Order, Order.location_id == Location.id)
                                  .where(Order.id == inv.order_id))).scalar()
    q = select(LegalEntity).where(LegalEntity.org_id == inv.org_id, LegalEntity.active == True)  # noqa: E712
    le = None
    if le_id:
        le = (await db.execute(q.where(LegalEntity.id == le_id))).scalar_one_or_none()
    if le is None:
        le = (await db.execute(q.where(LegalEntity.is_default == True))).scalars().first()  # noqa: E712
    if le is not None:
        lines = [le.name, le.address, le.phone, le.email]
        meta = [("VAT no.", le.vat_no), ("Tax ID", le.tax_id if le.tax_id != le.vat_no else None),
                ("Company no.", le.registration_no)]
    else:
        lines = [org.company_address if org else None, org.company_phone if org else None,
                 org.company_email if org else None, org.company_details if org else None]
        meta = []
    return [str(x) for x in lines if x], [m for m in meta if m[1]]


async def tax_summary(db: AsyncSession, inv: "Invoice") -> dict:
    """The invoice's tax, from the lines it bills: one row per tax (name, rate, mode, amount),
    the imposed tax per source order, and the total. Invoices with no taxed lines fall back to
    the stored Invoice.tax as one "Tax" row."""
    from backend.models import InvoiceLine, OrderLine, Tax
    from backend.services.tax import line_tax
    orders = await invoice_orders(db, inv)
    rows: dict = {}
    imposed_by_order: dict = {}
    if orders:
        billed = set((await db.execute(select(InvoiceLine.order_line_id).where(
            InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.isnot(None)))).scalars().all())
        q = select(OrderLine).where(OrderLine.order_id.in_([o.id for o in orders]))
        if billed:
            q = q.where(OrderLine.id.in_(billed))
        lines = (await db.execute(q)).scalars().all()
        names = {}
        tids = {l.tax_id for l in lines if l.tax_id}
        if tids:
            names = {t.id: (t.name_for_document or t.name) for t in (await db.execute(
                select(Tax).where(Tax.id.in_(tids)))).scalars().all()}
        for l in lines:
            amt = line_tax(qty=l.qty, unit_price=l.unit_price, discount=l.discount,
                           discount_type=l.discount_type, tax_rate=l.tax_rate, tax_mode=l.tax_mode)
            if not amt:
                continue
            mode = l.tax_mode or "imposed"
            key = (names.get(l.tax_id) or "Tax", float(l.tax_rate or 0), mode)
            rows[key] = rows.get(key, 0.0) + amt
            if mode != "included":
                imposed_by_order[l.order_id] = imposed_by_order.get(l.order_id, 0.0) + amt
    out = [{"name": k[0], "rate": k[1], "mode": k[2], "amount": round(v, 2)} for k, v in rows.items()]
    if not out and float(inv.tax or 0):
        out = [{"name": "Tax", "rate": None, "mode": "imposed", "amount": float(inv.tax)}]
        imposed_by_order = {None: float(inv.tax)}
    total = round(sum(r["amount"] for r in out), 2)
    imposed = round(sum(r["amount"] for r in out if r["mode"] != "included"), 2)
    return {"rows": out, "total": total, "imposed": imposed, "imposed_by_order": imposed_by_order}


async def build_invoice_pdf(db: AsyncSession, inv: "Invoice", *, org_name: str, lang: str = "en",
                            itemised: bool = False) -> bytes:
    """Build 144dw — render an invoice to PDF with its lines resolved through the
    Order-display mode across every linked order. Shared by the print endpoint and
    the send-by-email action so both produce an identical document.

    Launch B4: a usable tax invoice — the shop's currency, who issues it (address and VAT number),
    amounts before tax, one line per tax, and dates on the shop's clock."""
    from backend.models import Client, OrderLine
    from backend.services import printing
    from backend.services.dates import shop_tzname, utc_to_wall
    from backend.services.tax import line_tax
    org = (await db.execute(select(Organisation).where(Organisation.id == inv.org_id))).scalar_one_or_none()
    money = _pdf_money(org)
    tz = await shop_tzname(db, inv.org_id)
    lines = await display_lines(db, inv, itemised=itemised)
    orders = await invoice_orders(db, inv)
    taxes = await tax_summary(db, inv)

    # Line amounts are shown before imposed tax (the tax is listed once, under the subtotal).
    # VAT-inclusive prices stay as they are: the VAT is inside them.
    ol_tax: dict = {}
    if orders and (inv.display_mode or "detailed") == "detailed":
        for l in (await db.execute(select(OrderLine).where(
                OrderLine.order_id.in_([o.id for o in orders])))).scalars().all():
            if (l.tax_mode or "imposed") != "included":
                ol_tax[(l.order_id, l.description, float(l.total or 0))] = line_tax(
                    qty=l.qty, unit_price=l.unit_price, discount=l.discount, discount_type=l.discount_type,
                    tax_rate=l.tax_rate, tax_mode=l.tax_mode)

    def _net(r) -> float:
        total = float(r.get("total") or 0)
        mode = inv.display_mode or "detailed"
        if mode == "detailed":
            return total - ol_tax.get((r.get("order_id"), r.get("description"), total), 0.0)
        if mode == "simplified" and r.get("order_id"):
            return total - taxes["imposed_by_order"].get(r["order_id"], 0.0)
        if mode == "one_line":
            return total - taxes["imposed"]
        return total

    from backend.services.multi_asset import group_rows
    rows = []
    for r in group_rows(lines):
        if r["row"] == "group":
            rows.append([r["item"], "", "", ""])
        elif r["row"] == "subtotal":
            rows.append(["", "", f"{r['item']} subtotal", money(r["total"])])
        else:
            net = _net(r)
            qty = float(r["qty"] or 0)
            unit = (net / qty) if qty else float(r["unit_price"] or 0)
            rows.append([("   " if "item" in r else "") + (r["description"] or ""),
                         f"{qty:g}", money(unit), money(net)])
    payer_id = getattr(inv, "payer_client_id", None) or inv.client_id
    payer = None
    if payer_id:
        payer = (await db.execute(select(Client).where(Client.id == payer_id))).scalar_one_or_none()
    seller_lines, seller_meta = await _seller(db, org, inv)
    mode_label = "Itemised copy" if itemised else {"detailed": "Detailed", "simplified": "Simplified (per order)",
                  "one_line": "Single line"}.get(inv.display_mode or "detailed", "Detailed")

    def _day(dt):
        return utc_to_wall(dt, tz).strftime("%Y-%m-%d") if dt else None
    meta = seller_meta + [
            ("Bill to", payer.name if payer else None),
            ("Address", (getattr(payer, "billing_address", None) or getattr(payer, "address", None)) if payer else None),
            ("Customer tax ID", getattr(payer, "tax_id", None) if payer else None),
            ("Work orders", ", ".join(o.order_number for o in orders) or None),
            ("Display", mode_label),
            ("Issued", _day(inv.issued_at)),
            ("Due", _day(inv.due_at)),
            ("Status", (inv.status or "draft").title())]
    total = float(inv.total or 0)
    totals = [["", "", "Subtotal", money(total - taxes["imposed"])]]
    for t in taxes["rows"]:
        label = t["name"] + (f" {t['rate']:g}%" if t["rate"] is not None else "")
        if t["mode"] == "included":
            label += " (included)"
        totals.append(["", "", label, money(t["amount"])])
    if float(inv.discount or 0):
        totals.append(["", "", "Discount", money(inv.discount)])
    totals.append(["", "", "Total", money(total)])
    paid = float(inv.amount_paid or 0) + float(getattr(inv, "credited", 0) or 0)
    if paid:
        totals.append(["", "", "Paid", money(paid)])
        totals.append(["", "", "Balance due", money(balance_due(inv))])
    return printing.render_document(
        title="Invoice", org_name=org_name, lang=lang, number=inv.invoice_number,
        meta=[m for m in meta if m[1]], header_lines=seller_lines,
        headers=["Description", "Qty", "Unit", "Total"], rows=rows,
        totals=totals, tz=tz)


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
        return await _draft_remainder(db, org, order) or existing

    from backend.services.numbering import next_number
    subtotal = float(getattr(order, "subtotal", 0) or 0) or float(order.total or 0)
    due_days = getattr(org, "default_due_days", None) or 30
    inv = Invoice(
        invoice_number=await next_number(db, order.org_id, "invoice"),   # shared, locked sequence
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
    from backend.services.order_money import apply_deposits
    await apply_deposits(db, inv, order)   # deposits taken before the invoice count on it
    return inv


async def _draft_remainder(db: AsyncSession, org, order: Order):
    """Several items per job: items collected earlier were billed line by line ("Collect &
    invoice"). On close, draft the lines nobody billed yet — unless a whole-job invoice exists."""
    from backend.models import InvoiceLine, OrderLine
    from backend.services import multi_asset as ma
    billed = await ma.invoiced_line_ids(db, order.org_id, order.id)
    if not billed:
        return None
    partial = select(InvoiceLine.invoice_id).where(InvoiceLine.order_line_id.isnot(None))
    whole = (await db.execute(select(Invoice.id).where(
        Invoice.org_id == order.org_id, Invoice.order_id == order.id,
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
