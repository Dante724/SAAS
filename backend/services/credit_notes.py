"""opb — credit notes: totals, issue and cancel, with their ledger and invoice postings.

A credit note is money the shop gives back to a client as a document instead of cash.

    draft     editable, no number, moves no money
    issued    numbered (numbering doc_type "credit_note") and locked
    canceled  terminal; canceling an issued note reverses everything issuing did

Issuing posts one ``credit_note`` movement of −total to the client's ledger (we owe them that
much more / they owe us that much less). When the note is linked to an invoice it also raises
``Invoice.credited``, which lowers that invoice's balance due; a standalone note is pure client
credit. Canceling posts ``credit_note_void`` (+total) and lowers ``Invoice.credited`` again.

All arithmetic is Decimal (services/money.d2); values are stored in the Numeric money columns.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CreditNote, CreditNoteLine, Invoice
from backend.services.invoicing import VOID_STATUSES, settle_status
from backend.services.money import d2

STATUSES = ("draft", "issued", "canceled")
_HUNDRED = Decimal(100)


def line_amounts(qty, unit_price, tax_rate) -> tuple[Decimal, Decimal, Decimal]:
    """(net, tax, total) for one line, each rounded half-up to the cent."""
    net = d2(Decimal(str(qty or 0)) * Decimal(str(unit_price or 0)))
    tax = d2(net * Decimal(str(tax_rate or 0)) / _HUNDRED)
    return net, tax, net + tax


def build_lines(cn: CreditNote, lines: list[dict]) -> None:
    """Replace a draft's lines and recompute its totals."""
    cn.lines.clear()
    sub = tax_total = Decimal(0)
    for i, l in enumerate(lines):
        net, tax, total = line_amounts(l.get("qty"), l.get("unit_price"), l.get("tax_rate"))
        cn.lines.append(CreditNoteLine(
            sort_order=i, description=(l.get("description") or "").strip()[:500],
            qty=float(Decimal(str(l.get("qty") or 0))), unit_price=float(d2(l.get("unit_price") or 0)),
            tax_rate=float(Decimal(str(l.get("tax_rate") or 0))),
            net=float(net), tax=float(tax), total=float(total)))
        sub += net
        tax_total += tax
    cn.subtotal, cn.tax_total, cn.total = float(sub), float(tax_total), float(sub + tax_total)


async def lock_invoice(db: AsyncSession, org_id: str, invoice_id: str) -> Invoice:
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == org_id).with_for_update())).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


def creditable(inv: Invoice) -> Decimal:
    """How much more can still be credited against an invoice (its total less issued credits)."""
    return d2(inv.total or 0) - d2(inv.credited or 0)


async def issue(db: AsyncSession, cn: CreditNote, actor_id: str | None) -> None:
    """Number, lock and post a draft. Caller commits."""
    from backend.services.ledger import post_contact_ledger
    from backend.services.numbering import next_number
    if cn.status != "draft":
        raise HTTPException(409, "Only a draft credit note can be issued")
    total = d2(cn.total or 0)
    if not cn.lines or total <= 0:
        raise HTTPException(422, "Add at least one line with an amount before issuing")
    inv = None
    if cn.invoice_id:
        inv = await lock_invoice(db, cn.org_id, cn.invoice_id)
        if (inv.status or "") in VOID_STATUSES:
            raise HTTPException(409, "That invoice was cancelled, so it can't be credited")
        room = creditable(inv)
        if total > room:
            raise HTTPException(422, f"This credit note is more than the invoice still allows ({room})")
    elif not cn.client_id:
        raise HTTPException(422, "A credit note needs a client or an invoice")
    cn.number = await next_number(db, cn.org_id, "credit_note")
    cn.status = "issued"
    cn.issued_at = datetime.utcnow()
    cn.issued_by_id = actor_id
    if inv is not None:
        inv.credited = float(d2(inv.credited or 0) + total)
        settle_status(inv)
    what = f"invoice {inv.invoice_number}" if inv is not None else "client credit"
    await post_contact_ledger(db, cn.org_id, cn.client_id, "credit_note", float(-total),
                              note=f"Credit note {cn.number} ({what})", ref_type="credit_note",
                              ref_id=cn.id, created_by_id=actor_id)


async def cancel(db: AsyncSession, cn: CreditNote, actor_id: str | None) -> None:
    """Cancel a draft (nothing to undo) or reverse an issued note. Caller commits."""
    from backend.services.ledger import post_contact_ledger
    if cn.status == "canceled":
        raise HTTPException(409, "This credit note is already canceled")
    was_issued = cn.status == "issued"
    cn.status = "canceled"
    cn.canceled_at = datetime.utcnow()
    cn.canceled_by_id = actor_id
    if not was_issued:
        return
    total = d2(cn.total or 0)
    if cn.invoice_id:
        inv = await lock_invoice(db, cn.org_id, cn.invoice_id)
        inv.credited = float(max(d2(inv.credited or 0) - total, d2(0)))
        settle_status(inv)
    await post_contact_ledger(db, cn.org_id, cn.client_id, "credit_note_void", float(total),
                              note=f"Credit note {cn.number} canceled", ref_type="credit_note",
                              ref_id=cn.id, created_by_id=actor_id)


async def build_credit_note_pdf(db: AsyncSession, cn: CreditNote, *, org_name: str, lang: str = "en") -> bytes:
    """A plain PDF of the credit note (the /print endpoint), in the same layout as invoices."""
    from backend.models import Client, Organisation
    from backend.services import printing
    currency = (await db.execute(select(Organisation.currency_code).where(
        Organisation.id == cn.org_id))).scalar_one_or_none()
    client = (await db.execute(select(Client).where(Client.id == cn.client_id))).scalar_one_or_none() \
        if cn.client_id else None
    inv = (await db.execute(select(Invoice).where(Invoice.id == cn.invoice_id))).scalar_one_or_none() \
        if cn.invoice_id else None
    rows = [[l.description or "", f"{float(l.qty or 0):g}", f"{d2(l.unit_price or 0)}",
             f"{float(l.tax_rate or 0):g}%", f"{d2(l.total or 0)}"] for l in cn.lines]
    rows.append(["", "", "", "Subtotal", f"{d2(cn.subtotal or 0)}"])
    rows.append(["", "", "", "Tax", f"{d2(cn.tax_total or 0)}"])
    meta = [("Client", client.name if client else None),
            ("Credits invoice", inv.invoice_number if inv else None),
            ("Issued", cn.issued_at.strftime("%Y-%m-%d") if cn.issued_at else None),
            ("Status", (cn.status or "draft").title()),
            ("Currency", currency),
            ("Reason", cn.reason or None)]
    return printing.render_document(
        title="Credit note", org_name=org_name, lang=lang, number=cn.number or "Draft",
        meta=[m for m in meta if m[1]],
        headers=["Description", "Qty", "Unit", "Tax", "Total"], rows=rows,
        totals=["", "", "", "Total", f"{d2(cn.total or 0)}"])
