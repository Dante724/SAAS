"""Build 95 — printing endpoints.

Label templates live under Settings; the print endpoints return a PDF stream for the
products or documents you ask for.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    EmployeeBalanceLedger,
    Invoice,
    LabelTemplate,
    Organisation,
    POLine,
    Part,
    Posting,
    PostingLine,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    StockTransfer,
    StockTransferLine,
    Stocktake,
    StocktakeLine,
    WriteOff,
    WriteOffLine,
)
from backend.services import printing
from backend.services.codes import (
    validate_barcode_options,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2

router = APIRouter(prefix="/api/print", tags=["print"])


def _pdf(data: bytes, filename: str) -> Response:
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'})


async def _org_name(db: AsyncSession, org_id: str) -> str:
    return (await db.execute(select(Organisation.name).where(
        Organisation.id == org_id))).scalar_one_or_none() or ""


async def _org_lang(db: AsyncSession, org_id: str) -> str:
    """The org's document language (Build 122) — drives printed-document translations."""
    return (await db.execute(select(Organisation.document_language).where(
        Organisation.id == org_id))).scalar_one_or_none() or "en"


# ── label templates ──────────────────────────────────────────────────────────
def _ser_tpl(t: LabelTemplate) -> dict:
    return {"id": t.id, "name": t.name, "kind": t.kind,
            "width_mm": t.width_mm, "height_mm": t.height_mm,
            "price_type_id": t.price_type_id, "is_default": t.is_default,
            "barcode_type": t.barcode_type,
            "barcode_field": t.barcode_field,   # null = first filled of barcode/sku/code
            "barcode_types": t.barcode_types if isinstance(t.barcode_types, dict) else {},
            "fields": t.fields if isinstance(t.fields, list) else []}


@router.get("/templates")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(LabelTemplate).where(
        LabelTemplate.org_id == current.org_id).order_by(LabelTemplate.name))).scalars().all()
    return {"items": [_ser_tpl(t) for t in rows], "fields": printing.FIELDS}


class TemplateIn(BaseModel):
    name: str
    kind: str = "label"                 # label|price_tag
    width_mm: float = 50
    height_mm: float = 30
    price_type_id: Optional[str] = None
    fields: list[str] = ["name", "sku", "price", "barcode"]
    barcode_type: str = "code128"       # code128|ean13|upca|qr — default for any field
    barcode_field: Optional[str] = None  # opa: barcode|sku|code|mpn (null = first filled)
    barcode_types: dict[str, str] = {}  # opa: symbology per field
    is_default: bool = False


def _validate(data: TemplateIn) -> None:
    if data.kind not in ("label", "price_tag"):
        raise HTTPException(422, "kind must be label|price_tag")
    try:
        validate_barcode_options(data.barcode_field, data.barcode_types, data.barcode_type)
    except ValueError as e:
        raise HTTPException(422, str(e))
    bad = set(data.fields) - set(printing.FIELDS)
    if bad:
        raise HTTPException(422, f"Unknown fields: {sorted(bad)}")
    if data.width_mm < 20 or data.height_mm < 10:
        raise HTTPException(422, "A label smaller than 20×10mm can't hold a scannable barcode")


@router.post("/templates", status_code=201)
async def create_template(
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    _validate(data)
    t = LabelTemplate(org_id=current.org_id, **data.model_dump())
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _ser_tpl(t)


@router.put("/templates/{tpl_id}")
async def update_template(
    tpl_id: str, data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    _validate(data)
    t = (await db.execute(select(LabelTemplate).where(
        LabelTemplate.id == tpl_id,
        LabelTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    for k, v in data.model_dump(exclude_unset=True).items():   # A1: partial bodies keep the rest
        setattr(t, k, v)
    await db.commit()
    return _ser_tpl(t)


@router.delete("/templates/{tpl_id}")
async def delete_template(
    tpl_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(LabelTemplate).where(
        LabelTemplate.id == tpl_id,
        LabelTemplate.org_id == current.org_id))).scalar_one_or_none()
    if t:
        await db.delete(t)
        await db.commit()
    return {"ok": True}


# ── print labels ─────────────────────────────────────────────────────────────
# ii144c: the PDF label sheet (POST /api/print/labels) is retired. Every label — products, bins,
# serial units, assets, clients — prints from a label DocumentTemplate through the HTML renderer
# in routers/documents.py (render-labels, render-bin-labels, render-serial-labels, …).


# ── print stock documents ────────────────────────────────────────────────────
@router.get("/postings/{doc_id}")
async def print_posting(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    d = (await db.execute(select(Posting).where(
        Posting.id == doc_id, Posting.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Posting not found")
    lines = (await db.execute(select(PostingLine, Part.name, Part.sku)
        .join(Part, PostingLine.part_id == Part.id)
        .where(PostingLine.posting_id == doc_id))).all()

    rows = [[name, sku or "", f"{ln.qty:g}", f"{float(ln.unit_cost or 0):.2f}",
             f"{float(ln.total or 0):.2f}"] for ln, name, sku in lines]
    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Goods received", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=d.number,
        meta=[("Date", d.created_at.strftime("%Y-%m-%d") if d.created_at else None),
              ("Reference", d.reference), ("Settlement", d.settlement),
              ("Status", d.status)],
        headers=["Product", "SKU", "Qty", "Unit cost", "Total"],
        rows=rows,
        totals=["", "", "", "Total", f"{float(d.total or 0):.2f}"],
        footer=d.note)
    return _pdf(pdf, f"posting-{d.number or doc_id[:8]}.pdf")


@router.get("/purchase-order/{po_id}")
async def print_purchase_order(
    po_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 132 — a printable/PDF purchase order to hand or send to the supplier."""
    po = (await db.execute(select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
          .where(PurchaseOrder.id == po_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    sup = (await db.execute(select(Supplier).where(Supplier.id == po.supplier_id))).scalar_one_or_none()
    lines = (await db.execute(select(POLine, Part.name, Part.sku)
        .join(Part, POLine.part_id == Part.id)
        .where(POLine.po_id == po_id))).all()
    rows = [[name, sku or "", f"{ln.qty_ordered:g}", f"{float(ln.unit_cost or 0):.2f}",
             f"{float((ln.qty_ordered or 0) * (ln.unit_cost or 0)):.2f}"] for ln, name, sku in lines]
    # Build 144al — show optional charges (shipping/transit/custom) as their own rows,
    # and the grand total (line subtotal + charges).
    subtotal = sum(float((ln.qty_ordered or 0) * (ln.unit_cost or 0)) for ln, _n, _s in lines)
    charges = [c for c in (po.charges or []) if isinstance(c, dict)]
    for c in charges:
        rows.append([c.get("label") or "Charge", "", "", "", f"{float(c.get('amount') or 0):.2f}"])
    grand = subtotal + sum(float(c.get("amount") or 0) for c in charges)
    meta = [("Supplier", sup.name if sup else None),
            ("Supplier invoice", po.supplier_invoice_no),
            ("Invoice date", po.supplier_invoice_date.strftime("%Y-%m-%d") if po.supplier_invoice_date else None),
            ("Ordered", po.ordered_at.strftime("%Y-%m-%d") if po.ordered_at else None),
            ("Expected", po.expected_at.strftime("%Y-%m-%d") if po.expected_at else None),
            ("Status", po.status)]
    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Purchase order", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=po.po_number,
        meta=[m for m in meta if m[1]],
        headers=["Product", "SKU", "Qty", "Unit cost", "Total"],
        rows=rows,
        totals=["", "", "", "Total", f"{grand:.2f}"],
        footer=po.notes)
    return _pdf(pdf, f"purchase-order-{po.po_number or po_id[:8]}.pdf")


@router.get("/invoices/{invoice_id}")
async def print_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_invoices")),
):
    """Build 144dv — a printable/PDF invoice whose line items honour the invoice's
    Order-display mode (detailed / simplified / one_line) across every work order it
    bills, so the printed document matches exactly what staff and the client see."""
    from backend.services.invoicing import build_invoice_pdf
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == current.org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    pdf = await build_invoice_pdf(db, inv, org_name=await _org_name(db, current.org_id),
                                  lang=await _org_lang(db, current.org_id))
    return _pdf(pdf, f"invoice-{inv.invoice_number or invoice_id[:8]}.pdf")


@router.get("/credit-notes/{cn_id}")
async def print_credit_note(
    cn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_credit_notes")),
):
    """opb — the credit note as a PDF (same layout as invoices)."""
    from sqlalchemy.orm import selectinload

    from backend.models import CreditNote
    from backend.services.credit_notes import build_credit_note_pdf
    cn = (await db.execute(select(CreditNote).where(CreditNote.id == cn_id, CreditNote.org_id == current.org_id)
                           .options(selectinload(CreditNote.lines)))).scalar_one_or_none()
    if not cn:
        raise HTTPException(404, "Credit note not found")
    pdf = await build_credit_note_pdf(db, cn, org_name=await _org_name(db, current.org_id),
                                      lang=await _org_lang(db, current.org_id))
    return _pdf(pdf, f"credit-note-{cn.number or cn_id[:8]}.pdf")


@router.get("/write-offs/{doc_id}")
async def print_write_off(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    d = (await db.execute(select(WriteOff).where(
        WriteOff.id == doc_id, WriteOff.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Write-off not found")
    lines = (await db.execute(select(WriteOffLine, Part.name, Part.sku)
        .join(Part, WriteOffLine.part_id == Part.id)
        .where(WriteOffLine.write_off_id == doc_id))).all()

    rows = [[name, sku or "", f"{ln.qty:g}", f"{float(ln.unit_cost or 0):.2f}",
             # only comments marked public belong on a document that leaves the building
             (ln.comment or "") if ln.comment_public else ""]
            for ln, name, sku in lines]
    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Write-off act", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=d.number,
        meta=[("Date", d.created_at.strftime("%Y-%m-%d") if d.created_at else None),
              ("Reason", d.reason), ("Status", d.status)],
        headers=["Product", "SKU", "Qty", "Unit cost", "Comment"],
        rows=rows,
        totals=["", "", "", "Total cost", f"{float(d.total_cost or 0):.2f}"],
        footer=d.note)
    return _pdf(pdf, f"write-off-{d.number or doc_id[:8]}.pdf")


@router.get("/returns/{doc_id}")
async def print_return(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    d = (await db.execute(select(PurchaseReturn).where(
        PurchaseReturn.id == doc_id,
        PurchaseReturn.org_id == current.org_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Return not found")
    lines = (await db.execute(select(PurchaseReturnLine, Part.name, Part.sku)
        .join(Part, PurchaseReturnLine.part_id == Part.id)
        .where(PurchaseReturnLine.return_id == doc_id))).all()

    rows = [[name, sku or "", f"{ln.qty:g}", f"{float(ln.unit_cost or 0):.2f}",
             f"{float(ln.total or 0):.2f}"] for ln, name, sku in lines]
    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Return to supplier", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=d.number,
        meta=[("Date", d.created_at.strftime("%Y-%m-%d") if d.created_at else None),
              ("Settlement", d.settlement), ("Status", d.status)],
        headers=["Product", "SKU", "Qty", "Unit cost", "Total"],
        rows=rows,
        totals=["", "", "", "Total", f"{float(d.total or 0):.2f}"],
        footer=d.note)
    return _pdf(pdf, f"return-{d.number or doc_id[:8]}.pdf")


@router.get("/transfers/{tid}")
async def print_transfer(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    lines = (await db.execute(select(StockTransferLine, Part.name, Part.sku)
        .join(Part, StockTransferLine.part_id == Part.id)
        .where(StockTransferLine.transfer_id == tid))).all()

    # the received column is what makes this a useful piece of paper at the far end
    rows = [[name, sku or "", f"{ln.qty:g}",
             "" if ln.qty_received is None else f"{ln.qty_received:g}"]
            for ln, name, sku in lines]
    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Stock transfer", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=t.transfer_number,
        meta=[("Status", t.status),
              ("Dispatched", t.dispatched_at.strftime("%Y-%m-%d %H:%M") if t.dispatched_at else None),
              ("Received", t.received_at.strftime("%Y-%m-%d %H:%M") if t.received_at else None)],
        headers=["Product", "SKU", "Shipped", "Received"],
        rows=rows,
        footer=t.note)
    return _pdf(pdf, f"transfer-{t.transfer_number or tid[:8]}.pdf")


@router.get("/stocktakes/{st_id}")
async def print_settlement_act(
    st_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 91's settlement act as the signed document it's meant to be."""
    st = (await db.execute(select(Stocktake).where(
        Stocktake.id == st_id, Stocktake.org_id == current.org_id))).scalar_one_or_none()
    if not st:
        raise HTTPException(404, "Stocktake not found")
    rows_db = (await db.execute(select(StocktakeLine, Part)
        .join(Part, StocktakeLine.part_id == Part.id)
        .where(StocktakeLine.stocktake_id == st_id,
               StocktakeLine.counted_qty.is_not(None)))).all()

    rows, net = [], 0.0
    for ln, part in rows_db:
        v = ln.variance or 0
        if not v:
            continue
        cost = float(part.avg_cost or part.cost_price or 0)
        value = q2(v * cost)
        net += value
        flags = " ".join(f for f, on in
                         (("unknown", ln.is_unknown), ("serial?", ln.serial_mismatch)) if on)
        rows.append([part.name, part.sku or "", f"{ln.system_qty or 0:g}",
                     f"{ln.counted_qty:g}", f"{v:+g}", f"{value:+.2f}", flags])

    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Stocktake settlement act", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=st_id[:8],
        meta=[("Type", st.kind), ("Status", st.status),
              ("Counted", st.created_at.strftime("%Y-%m-%d") if st.created_at else None),
              ("Posted", st.posted_at.strftime("%Y-%m-%d") if st.posted_at else None)],
        headers=["Product", "SKU", "System", "Counted", "Variance", "Value", "Flags"],
        rows=rows,
        totals=["", "", "", "", "Net", f"{net:+.2f}", ""],
        footer=st.note or ("No discrepancies found." if not rows else None))
    return _pdf(pdf, f"settlement-{st_id[:8]}.pdf")


@router.get("/payroll-statement/{employee_id}")
async def print_payroll_statement(
    employee_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 97 — the payslip. Every accrual and deduction over a period, with the
    running total that says what the employee is owed. Uses the same document layout as
    the stock papers, so it reads as one consistent family of documents.

    Access is restricted: an employee can print their own statement, and anyone who can
    manage payroll can print anyone's. Payroll figures aren't something to leak sideways.
    """
    from datetime import datetime as _dt

    emp = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")

    # default-allow, matching require_perm: only an explicit False on the role blocks it
    can_manage = True
    if current.id != employee_id and current.role_id:
        from backend.models import Role
        role = (await db.execute(select(Role).where(
            Role.id == current.role_id))).scalar_one_or_none()
        from backend.services.permissions import role_allows as _ra
        if role is None or not _ra(role, "can_manage_payroll"):
            can_manage = False
    if current.id != employee_id and not can_manage:
        raise HTTPException(403, "You can only print your own payroll statement")
    if current.id != employee_id:
        from backend.services.payroll_views import assert_payroll_scope
        await assert_payroll_scope(db, current, employee_id)

    q = select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.employee_id == employee_id)
    if date_from:
        q = q.where(EmployeeBalanceLedger.created_at >= _dt.fromisoformat(date_from))
    if date_to:
        q = q.where(EmployeeBalanceLedger.created_at <= _dt.fromisoformat(date_to))
    rows = (await db.execute(q.order_by(EmployeeBalanceLedger.created_at))).scalars().all()

    running = 0.0
    body_rows = []
    earned = deducted = 0.0
    for e in rows:
        amt = float(e.amount or 0)
        running += amt
        if amt >= 0:
            earned += amt
        else:
            deducted += amt
        body_rows.append([
            e.created_at.strftime("%Y-%m-%d") if e.created_at else "",
            (e.entry_type or "").replace("_", " "),
            e.event or "", e.note or "",
            f"{amt:+.2f}", f"{running:.2f}"])

    pdf = await asyncio.to_thread(
        printing.render_document,
        title="Payroll statement", org_name=await _org_name(db, current.org_id), lang=await _org_lang(db, current.org_id),
        number=emp.name,
        meta=[("Employee", emp.name),
              ("Period", f"{date_from or 'start'} → {date_to or 'now'}"),
              ("Earned", f"{earned:.2f}"),
              ("Deductions", f"{deducted:.2f}"),
              ("Balance owed", f"{running:.2f}")],
        headers=["Date", "Type", "Event", "Note", "Amount", "Running total"],
        rows=body_rows,
        totals=["", "", "", "Balance", "", f"{running:.2f}"],
        footer="This statement lists all payroll movements in the selected period.")
    return _pdf(pdf, f"payroll-{emp.name.replace(' ', '-')}.pdf")
