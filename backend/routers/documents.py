"""Phase F — Documents, Templates & Barcodes.
Branded HTML/PDF documents auto-filled from order data via {variables}.
Also: order barcode lookup for scanner search.

Build 144s — full Orderry-parity variable catalog: grouped variables
(Company, Legal entity, Client, Payer, Ticket, Device, Tables, Employees,
Finance, Date, Other), per-row product variables that repeat inside a
products table, pre-built table blocks ({Products Table} etc.), and
Orderry-exact default Estimate + Price-tag templates seeded for new orgs.
"""
import html as _html_mod
import re
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.db.database import get_db
from backend.models import (
    DocumentTemplate, GeneratedDocument, Order, OrderLine, Client,
    Employee, Organisation, Invoice, LegalEntity, Location, Part,
    CustomField, WorkOrderType, Device,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.codes import code128_svg, qr_svg
from backend.config import settings


class _Html(str):
    """A variable value that is deliberate, already-safe markup (tables, logo, QR/barcode
    SVG, buttons). Every other value is HTML-escaped when substituted into a document, so
    client names, complaints, notes etc. can never inject script into a printed page."""


def _esc(v) -> str:
    return _html_mod.escape(str(v), quote=True)


def _sub(v) -> str:
    return v if isinstance(v, _Html) else _esc(v)

router = APIRouter(prefix="/api/documents", tags=["documents"])

# ── Variable catalog (Orderry-style), grouped for the editor side-panel. ──────
# Every code here is resolved in _resolve() (document-level) or _row_vars()
# (per line, inside a products/services table). A new code just needs a value.
VARIABLE_GROUPS = [
    ("Company", ["{Company Name}", "{Company Address}", "{Company Phone}",
                 "{Company Email}", "{Company Website}", "{Company Logo}", "{Brand Color}",
                 "{Company Details}", "{Company Note}"]),
    ("Legal entity", ["{Legal Entity Name}", "{Legal Entity Tax ID}",
                      "{Legal Entity Address}", "{Legal Entity Details}",
                      "{Legal Entity Registration No}", "{Legal Entity VAT}",
                      "{Legal Entity Bank Name}", "{Legal Entity Bank Account}",
                      "{Legal Entity Phone}", "{Legal Entity Email}", "{Legal Entity Director}",
                      # Orderry-exact names (alias same data where present, else blank for parity)
                      "{Legal Entity Registration Number}", "{Legal Entity TIN}",
                      "{Legal Entity Phone Number}", "{Legal Entity IBAN}",
                      "{Legal Entity Additional Info}", "{Legal Entity Country}",
                      "{Legal Entity State}", "{Legal Entity City}",
                      "{Legal Entity Address Line 1}", "{Legal Entity Address Line 2}",
                      "{Legal Entity Postal Code}", "{Legal Entity Bank Address}",
                      "{Legal Entity SWIFT}", "{Legal Entity Additional Payment Info}"]),
    # 144hk — interactive blocks: print-safe CTAs that link to the live public page
    # (approve / pay / sign already work there); on paper they degrade to link + QR.
    ("Interactive", ["{Approve Button}", "{Pay Now Button}", "{Sign Here}",
                     "{Verify Badge}", "{Work Summary}"]),
    ("Client", ["{Client Name}", "{Client First Name}", "{Client Last Name}",
                "{Client Phone}", "{Client Email}", "{Client Address}", "{Client Billing Address}",
                "{Client Tax ID}", "{Client Registration No}",
                # Orderry-exact names
                "{Client Tax Identification Number}", "{Client Business Registration Number}",
                "{Client Phone Type}", "{Client Birthday}", "{Client Age}"]),
    ("Payer", ["{Payer Name}", "{Payer First Name}", "{Payer Last Name}",
               "{Payer Phone}", "{Payer Email}", "{Payer Address}", "{Payer Billing Address}",
               "{Payer Tax Identification Number}", "{Payer Business Registration Number}",
               "{Payer Phone Type}", "{Payer Birthday}", "{Payer Age}"]),
    ("Ticket", ["{Order Number}", "{Order Status}", "{Today Date}", "{Order Created Date}",
                "{Order Done Date}", "{Order Due Date}", "{Order Scheduled Date}", "{Order Complaint}",
                "{Order Conclusion}", "{Order Manager Notes}", "{Order Technician Notes}",
                "{Order Urgent}", "{Ad Campaign}", "{Location}", "{Location Address}",
                # Orderry-exact names
                "{Order Malfunction}", "{Order Comment}", "{Order Address}", "{Order Duration}",
                "{Order Resource}", "{Order Scheduled For}", "{Order Scheduled To}",
                "{Urgent}", "{Order Type}"]),
    ("Device", ["{Asset Owner}", "{Asset Warehouse}", "{Asset Type}", "{Asset Brand}",
                "{Asset Model}", "{Asset Modification}", "{Asset Color}", "{Asset Year}",
                "{Asset Cost}", "{Asset Serial Number}", "{Asset Description}",
                "{Asset Address}", "{Asset State}", "{Asset IMEI}", "{Asset VIN}"]),
    ("Tables", [
        # table blocks — expand to a full <table>
        "{All Items By Technicians}", "{All Items Table}", "{Products Table}",
        "{Products Table By Technicians}", "{Services Table}", "{Services Table By Technicians}",
        # per-row product variables — repeat inside a products table row
        "{Row Number}", "{Product Name}", "{Product Qty}", "{Product UoM}",
        "{Product Code}", "{Product SKU}", "{Product Barcode}", "{Product Serial Number}", "{Product Description}",
        "{Product Comment}", "{Product Color}", "{Product Bin Location}", "{Product Warehouse}",
        "{Product Default Supplier Name}", "{Product Client Warranty}", "{Product Expiration Date}",
        "{Product Length}", "{Product Width}", "{Product Height}", "{Product DimensionsUnit}",
        "{Product GrossWeight}", "{Product WeightUnit}",
        "{Product Price}", "{Product Price Without Discount}",
        "{Product Price Without Taxes}", "{Product Price Without Discount And Without Taxes}",
        "{Product Amount}", "{Product Amount Without Discount}",
        "{Product Amount Without Taxes}", "{Product Amount Without Discount And Without Taxes}",
        "{Product Tax Amount}", "{Product Discount Pct}", "{Product Discount Sum}",
        "{Technician Name}", "{Technician Phone}", "{Technician Job Title}",
        # document-level totals
        "{Subtotal}", "{Tax Name}", "{Tax Rate}", "{Tax Value}", "{Tax Value Spelled Out}",
        "{Total Amount}", "{Total Amount Without Discount}",
        "{Total Amount Without Discount And Without Taxes}",
        "{Total Discount}", "{Total Discount Without Taxes}", "{Total Qty}"]),
    ("Employees", ["{Current User}", "{Order Assignee Name}", "{Order Assignee Phone}",
                   "{Order Assignee Position}", "{Order Closed By}", "{Order Created By}",
                   "{Order Manager Name}", "{Order Manager Phone}", "{Order Manager Notes}",
                   "{Order Technician Notes}"]),
    ("Finance", ["{Order Total}", "{Order Paid}", "{Order Balance Due}", "{Order Prepayment}",
                 "{Order Warranty Expiration Date}", "{Currency}",
                 "{Total Amount Spelled Out}", "{Order Paid Spelled Out}", "{Balance Due Spelled Out}",
                 "{Order Payment URL}",
                 # Orderry-exact names
                 "{Estimated Price}", "{Estimated Price Spelled Out}", "{Paid Spelled Out}",
                 "{Order sum}", "{Balance Due}", "{Total Paid Amount}", "{Total Paid Tax Amount}",
                 "{Total Paid Amount Without Taxes}", "{Total Amount Without Taxes}",
                 "{Total Discount Pct}"]),
    # Invoice-document variables (resolved by the invoice renderer)
    ("Invoice", ["{Invoice Number}", "{Invoice Date}", "{Invoice Due Date}", "{Invoice Status}",
                 "{Invoice Comment}", "{Invoice Subtotal}", "{Invoice Tax}", "{Invoice Total}",
                 "{Invoice Paid}", "{Invoice Balance Due}", "{Invoice Table}", "{Invoice Payment URL}"]),
    ("Date", ["{Today Date}", "{Current Date}", "{Today Datetime}",
              "{Order Closed Date}", "{Order Due Date Time}"]),
    ("Links", ["{Tracking URL}", "{Client Feedback URL}", "{Public Order URL}",
               # Orderry-exact names + QR variants
               "{Public Page URL}", "{Order URL}", "{Public Page QR Code}",
               "{Client Feedback QR Code}", "{Order Payment QR}"]),
    ("Other", ["{Barcode}", "{QR Code}", "{Product Title}", "{Product Article}", "{Price}"]),
]

# Flat list kept for backward compatibility with older callers.
AVAILABLE_VARIABLES = [v for _g, items in VARIABLE_GROUPS for v in items]

# ── 144ho: org custom fields → document variables ────────────────────────────
_CF_PREFIX = {"order": "Order", "client": "Client", "device": "Asset"}


async def _custom_field_defs(db, org_id):
    """Every org custom field that maps to an order/client/asset document variable."""
    return (await db.execute(select(CustomField).where(
        CustomField.org_id == org_id,
        CustomField.entity_type.in_(("order", "client", "device")),
        CustomField.field_type != "header"))).scalars().all()


def _cf_varname(f) -> str:
    return "{%s %s}" % (_CF_PREFIX.get(f.entity_type, "Order"), f.field_label)


def _fmt_cf(val) -> str:
    if val is None or val == "":
        return "—"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, list):
        return ", ".join(str(x) for x in val) or "—"
    return str(val)

# doc_types that are physical labels / price tags — printed from inventory against a
# product (not an order), so they're excluded from the order Print dialog.
LABEL_DOC_TYPES = {"label", "price_tag", "product_label", "vehicle_label"}

# Row-level markers: a <tbody> containing any of these is treated as a repeating
# product row. {Technician *} are resolved both at row and document level.
_ROW_MARKER_RE = re.compile(r"\{Product [^}]+\}|\{Row Number\}")
_ROW_VAR_RE = re.compile(r"\{Product [^}]+\}|\{Row Number\}|\{Technician (?:Name|Phone|Job Title)\}")
_TBODY_RE = re.compile(r"(<tbody[^>]*>)(.*?)(</tbody>)", re.S | re.I)

# 144id — Smart-doc conditionals: {#if Var}…{#else}…{/if} and {#unless Var}…{/if}.
# The inner-group forbids a nested {#if/{#unless so we always match the INNERMOST
# block first; the render loop then peels outer levels. In-house, no template engine.
_COND_RE = re.compile(
    r"\{#(if|unless)\s+([^}]+?)\}((?:(?!\{#(?:if|unless)\b).)*?)\{/\1\}", re.S)
_EMPTYISH = {"", "—", "-", "0", "0.00", "none", "false", "n/a", "no"}


def _truthy(doc: dict, name: str) -> bool:
    """A smart-doc condition is true when its variable resolved to real content —
    not blank, the em-dash placeholder, a zero amount, or a falsey word."""
    v = doc.get("{" + name.strip() + "}")
    if v is None:
        return False
    s = str(v).strip()
    if s.lower() in _EMPTYISH:
        return False
    # money like "0.00" / "0.00 USD" / "$0.00" counts as empty
    digits = re.sub(r"\D", "", s)
    if digits and set(digits) == {"0"} and not re.search(r"[A-Za-z]", re.sub(r"[A-Za-z]{3}$", "", s)):
        return False
    return True


def _conditionals(body: str, doc: dict) -> str:
    def one(m):
        kind, name, inner = m.group(1), m.group(2), m.group(3)
        yes, _, no = inner.partition("{#else}")
        if not _:
            yes, no = inner, ""
        t = _truthy(doc, name)
        if kind == "unless":
            t = not t
        return yes if t else no
    prev = None
    while prev != body:
        prev, body = body, _COND_RE.sub(one, body)
    return body


# ── Default templates (Orderry-exact) ────────────────────────────────────────
DEFAULT_ESTIMATE_HTML = """<style>
 .b-table > thead {background: #f0f0f0; border-left: 0; border-right: 0; border-bottom: 1px solid;}
 .b-table > thead > th { border-width: 0px; border-style: none;}
 .b-table >tbody> tr:nth-child(2n) { background: #f0f0f0;}
 .b-table >tfoot>tr > td.right:nth-child(n+1) { background: #f0f0f0;}
 .b-table >tbody { border-left: 0; border-right: 0; border-bottom: 1px solid;}
 .right > * {text-align: right !important; margin-right: 0 !important; margin-left: auto !important; padding:0 !important; border:0 !important;}
</style>
<table style="border-collapse: collapse; width: 99.9839%;" border="0"><tbody>
<tr><td style="width: 50%;"><span style="font-size: 18pt;"><strong>ESTIMATE</strong></span></td>
<td style="width: 50%; text-align: right;"><p>{Company Name}</p><p>{Company Email}</p><p>{Company Address}</p></td></tr>
</tbody></table>
<p>&nbsp;</p>
<table style="border-collapse: collapse; width: 99.9839%;" border="0"><tbody>
<tr><td style="width: 15.77%;"><strong>Client</strong></td><td style="width: 31.66%;">{Client Name}</td><td style="width: 8.37%;">&nbsp;</td><td style="width: 9.85%;"><strong>Email</strong></td><td style="width: 34.25%;">{Client Email}</td></tr>
<tr><td><strong>Number</strong></td><td>{Order Number}</td><td>&nbsp;</td><td><strong>Phone</strong></td><td>{Client Phone}</td></tr>
<tr><td><strong>Date</strong></td><td>{Today Date}</td><td>&nbsp;</td><td><strong>Address</strong></td><td>{Client Address}</td></tr>
<tr><td><strong>Valid until</strong></td><td>{Order Due Date}</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</tbody></table>
<p>&nbsp;</p>
<table class="Products b-table b-table_style_simple" style="border-collapse: collapse; width: 100%; border-style: none;" border="0" cellspacing="0" cellpadding="0">
<thead><tr><th style="width: 9.34%;">#</th><th style="width: 47.35%;">Description</th><th style="width: 9.34%;">Qty</th><th style="width: 10.57%;">Unit Price</th><th style="width: 11.19%;">Tax</th><th style="width: 12.17%;">Amount</th></tr></thead>
<tbody>
<tr><td class="center" style="text-align:center;">{Row Number}</td><td>{Product Name}</td><td class="center" style="text-align:center;">{Product Qty}</td><td style="text-align:center;">{Product Price Without Taxes}</td><td class="right" style="text-align:right;">{Product Tax Amount}</td><td class="right" style="text-align:right;">{Product Amount}</td></tr>
</tbody>
<tfoot>
<tr><td style="text-align: right;" colspan="5"><strong>Subtotal:</strong></td><td class="right" style="font-weight:bold; text-align:right;">{Subtotal}</td></tr>
<tr><td style="text-align: right;" colspan="5"><strong>Total taxes {Tax Name} {Tax Rate}</strong></td><td class="right" style="text-align:right;"><strong>{Tax Value}</strong></td></tr>
<tr><td style="text-align: right;" colspan="5"><strong>Total:</strong></td><td class="right" style="font-weight:bold; text-align:right;">{Total Amount}</td></tr>
</tfoot>
</table>
<p>&nbsp;</p>
<p style="text-align: justify;">Estimates provided are an approximation of charges to you for the services requested. They are based on the anticipated work to be done. Unexpected complications may cause some deviation from the original quote. By signing, you authorize the repair work described in this estimate to be done and procuring the necessary material(s), including permission to operate testing or inspection. If any additional repairs are required, we will prepare a revised work order providing the cost of additional parts and labor and the total revised cost. All parts are new unless specified otherwise.</p>
<p><strong>TERMS &amp; CONDITIONS</strong></p>
<p>Discount applies to invoices paid on or before the due date.</p>
<p><strong>PAYMENT TERMS:</strong> Payment due within 14 days of invoice.</p>"""

DEFAULT_PRICETAG_HTML = """<style>
  body {margin: 2em;}
  tr, td {height: 2em;}
</style>
<table style="border-collapse: collapse; width: 15em; height: 11em;" border="1"><tbody>
<tr><td style="width: 50%;">{Company Name}</td><td style="width: 50%;">{Today Date}</td></tr>
<tr><td style="height: 3em; text-align: center;" colspan="2">{Product Title}</td></tr>
<tr><td style="text-align: center;" colspan="2"><span style="font-size: 14pt;"><strong>{Price} {Currency}</strong></span></td></tr>
<tr><td style="width: 50%;">{Product Code}</td><td style="width: 50%;">{Product Article}</td></tr>
<tr><td style="text-align: center;" colspan="2">{Barcode}</td></tr>
</tbody></table>"""


def _parse_statuses(raw):
    try:
        v = json.loads(raw) if raw else []
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _ser_tpl(t: DocumentTemplate) -> dict:
    return {"id": t.id, "name": t.name, "doc_type": t.doc_type, "body_html": t.body_html,
            "auto_print_on": t.auto_print_on, "is_default": t.is_default, "active": t.active,
            "page_size": getattr(t, "page_size", "a4") or "a4",
            "page_w_mm": getattr(t, "page_w_mm", None), "page_h_mm": getattr(t, "page_h_mm", None),
            "margin_mm": getattr(t, "margin_mm", None),
            "font_family": getattr(t, "font_family", "Sans-Serif") or "Sans-Serif",
            "print_statuses": _parse_statuses(getattr(t, "print_statuses", None))}


async def _ensure_defaults(db: AsyncSession, org_id: str):
    """Backfill the Orderry-style Estimate + Price tag templates.

    Idempotent by name: each default is added once per org (including existing
    orgs that already have their own templates) and never duplicated on repeat.
    Checks all rows (incl. soft-deleted) so a user who removes a default isn't re-seeded.
    """
    existing = {(t.name or "").lower() for t in (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.org_id == org_id))).scalars().all()}
    added = False
    if "estimate" not in existing:
        db.add(DocumentTemplate(org_id=org_id, name="Estimate", doc_type="estimate",
                                body_html=DEFAULT_ESTIMATE_HTML, is_default=True, page_size="a4"))
        added = True
    if "price tag" not in existing:
        db.add(DocumentTemplate(org_id=org_id, name="Price tag", doc_type="label",
                                body_html=DEFAULT_PRICETAG_HTML, page_size="label"))
        added = True
    if added:
        await db.commit()


@router.get("/templates")
async def list_templates(
    order_id: Optional[str] = None,   # build 144o: filter to docs printable for this order's status
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _ensure_defaults(db, current.org_id)
    rows = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.org_id == current.org_id, DocumentTemplate.active == True))).scalars().all()  # noqa: E712
    items = [_ser_tpl(t) for t in rows]
    if order_id:
        order = (await db.execute(select(Order).where(
            Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
        status = order.status if order else None
        # label/price-tag docs are inventory artifacts — never offered for order printing;
        # remaining docs are gated by their allowed print statuses (empty = any status).
        items = [i for i in items if i["doc_type"] not in LABEL_DOC_TYPES
                 and i["doc_type"] != "component"   # 144id — reusable blocks aren't printed on their own
                 and (not i["print_statuses"] or (status in i["print_statuses"]))]
    groups = [{"group": g, "items": items_} for g, items_ in VARIABLE_GROUPS]
    flat = list(AVAILABLE_VARIABLES)
    # 144ho — surface the org's own custom fields as pickable {…} variables.
    cf_names = sorted({_cf_varname(f) for f in await _custom_field_defs(db, current.org_id)})
    if cf_names:
        groups.append({"group": "Custom fields", "items": cf_names})
        flat += [v for v in cf_names if v not in flat]
    return {"items": items, "variables": flat, "variable_groups": groups}


class TemplateIn(BaseModel):
    name: str
    doc_type: str = "work_order"
    body_html: str = ""
    auto_print_on: Optional[str] = None
    is_default: bool = False
    page_size: str = "a4"
    page_w_mm: Optional[float] = None
    page_h_mm: Optional[float] = None
    margin_mm: Optional[float] = None
    font_family: str = "Sans-Serif"
    print_statuses: list[str] = []

    @field_validator("page_size")
    @classmethod
    def _known_page_size(cls, v: Optional[str]) -> str:
        # 144eo: persisted page_size must be one we can render (and fit the column on Postgres).
        # Empty keeps the historic "fall back to a4" behaviour; anything unknown is a 422.
        v = (v or "").strip()
        if not v:
            return "a4"
        if v not in VALID_PAGE_SIZES:
            raise ValueError(f"page_size must be one of: {', '.join(sorted(VALID_PAGE_SIZES))}")
        return v


@router.post("/templates", status_code=201)
async def create_template(
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = DocumentTemplate(org_id=current.org_id, name=data.name, doc_type=data.doc_type,
                         body_html=data.body_html, auto_print_on=data.auto_print_on,
                         is_default=data.is_default,
                         page_size=data.page_size or "a4",
                         page_w_mm=data.page_w_mm, page_h_mm=data.page_h_mm, margin_mm=data.margin_mm,
                         font_family=data.font_family or "Sans-Serif",
                         print_statuses=json.dumps(data.print_statuses) if data.print_statuses else None)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id}


@router.put("/templates/{tid}")
async def update_template(
    tid: str,
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == tid, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    t.name = data.name
    t.doc_type = data.doc_type
    t.body_html = data.body_html
    t.auto_print_on = data.auto_print_on
    t.is_default = data.is_default
    t.page_size = data.page_size or "a4"
    t.page_w_mm = data.page_w_mm; t.page_h_mm = data.page_h_mm; t.margin_mm = data.margin_mm
    t.font_family = data.font_family or "Sans-Serif"
    t.print_statuses = json.dumps(data.print_statuses) if data.print_statuses else None
    await db.commit()
    return {"ok": True}


@router.delete("/templates/{tid}")
async def delete_template(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == tid, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if t:
        t.active = False
        await db.commit()
    return {"ok": True}


# ── Rendering ────────────────────────────────────────────────────────────────
def _fmt_date(dt):
    return dt.strftime("%d %B %Y") if dt else "—"


def _money(n):
    return f"{float(n or 0):,.2f}"


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _spell_int(n: int) -> str:
    if n < 0:
        return "minus " + _spell_int(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (("-" + _ONES[n % 10]) if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ((" " + _spell_int(n % 100)) if n % 100 else "")
    for div, word in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if n >= div:
            return _spell_int(n // div) + " " + word + ((" " + _spell_int(n % div)) if n % div else "")
    return str(n)


def _spell_money(amount) -> str:
    amount = float(amount or 0)
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    words = _spell_int(whole).capitalize()
    if cents:
        words += f" and {cents:02d}/100"
    return words


def _line_numbers(line: OrderLine) -> dict:
    """Compute the per-line money values used by row + total variables.

    Honours tax_mode: when the stored unit_price already includes tax
    ("inclusive"), the pre-tax figures are derived by dividing out the rate,
    so {Product Price Without Taxes} and the ex-tax totals stay correct.
    """
    qty = float(line.qty or 0)
    unit = float(line.unit_price or 0)
    rate = float(line.tax_rate or 0) / 100.0
    inclusive = (line.tax_mode or "").lower() in ("inclusive", "incl", "included", "gross")
    unit_ex = (unit / (1 + rate)) if (inclusive and rate) else unit   # pre-tax unit price
    unit_inc = unit_ex * (1 + rate)                                   # tax-inclusive unit price
    gross_no_disc = unit_ex * qty                                    # ex-tax, before discount
    if (line.discount_type or "") in ("percent", "pct", "%"):
        disc = gross_no_disc * float(line.discount or 0) / 100.0
    else:
        disc = float(line.discount or 0)
    base = max(gross_no_disc - disc, 0)                 # taxable base, after discount
    tax = base * rate
    amount = float(line.total) if line.total is not None else base + tax
    return {"qty": qty, "unit_ex": unit_ex, "unit_inc": unit_inc, "rate": rate, "disc": disc,
            "base": base, "base_no_disc": gross_no_disc, "tax": tax, "amount": amount}


def _row_vars(i: int, line: OrderLine, part, tech) -> dict:
    n = _line_numbers(line)
    return {
        "{Row Number}": i,
        "{Product Name}": line.description or (part.name if part else "") or "—",
        "{Product Qty}": f"{n['qty']:g}",
        "{Product UoM}": (getattr(part, "uom", None) if part else "") or "",
        "{Product Code}": (getattr(part, "code", None) if part else "") or "",
        "{Product SKU}": (getattr(part, "sku", None) if part else "") or "",
        "{Product Barcode}": (getattr(part, "barcode", None) if part else "") or "",
        "{Product Serial Number}": "",
        "{Product Description}": (getattr(part, "category", None) if part else "") or (line.description or ""),
        "{Product Comment}": "",
        "{Product Color}": "",
        "{Product Bin Location}": "",
        "{Product Warehouse}": "",
        "{Product Default Supplier Name}": (getattr(part, "brand", None) if part else "") or "",
        "{Product Client Warranty}": (f"{part.warranty_days} days" if part and getattr(part, "warranty_days", None) else ""),
        "{Product Expiration Date}": "",
        "{Product Length}": "", "{Product Width}": "", "{Product Height}": "",
        "{Product DimensionsUnit}": "",
        "{Product GrossWeight}": (str(getattr(part, "weight", "")) if part and getattr(part, "weight", None) else ""),
        "{Product WeightUnit}": "",
        "{Product Price}": _money(n["unit_inc"]),
        "{Product Price Without Discount}": _money(n["unit_inc"]),
        "{Product Price Without Taxes}": _money(n["unit_ex"]),
        "{Product Price Without Discount And Without Taxes}": _money(n["unit_ex"]),
        "{Product Amount}": _money(n["amount"]),
        "{Product Amount Without Discount}": _money(n["base_no_disc"] + n["base_no_disc"] * n["rate"]),
        "{Product Amount Without Taxes}": _money(n["base"]),
        "{Product Amount Without Discount And Without Taxes}": _money(n["base_no_disc"]),
        "{Product Tax Amount}": _money(n["tax"]),
        "{Product Discount Pct}": (f"{float(line.discount or 0):g}%"
                                   if (line.discount_type or "") in ("percent", "pct", "%") else ""),
        "{Product Discount Sum}": _money(n["disc"]),
        "{Technician Name}": (tech.name if tech else "") or "",
        "{Technician Phone}": (getattr(tech, "phone", None) if tech else "") or "",
        "{Technician Job Title}": (getattr(tech, "position", None) if tech else "") or "",
    }


def _table_block(headers, rows, title=None):
    head = "".join(f"<th style='text-align:left;padding:6px;border-bottom:1px solid #ccc'>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:6px;border-bottom:1px solid #eee'>{_esc(c)}</td>" for c in r) + "</tr>"
        for r in rows) or f"<tr><td colspan='{len(headers)}' style='padding:6px;color:#999'>No items</td></tr>"
    cap = f"<caption style='text-align:left;font-weight:bold;padding:4px 0'>{_esc(title)}</caption>" if title else ""
    return _Html(f"<table style='width:100%;border-collapse:collapse'>{cap}"
                 f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def _track_url(order: Order, suffix: str = "") -> str:
    """Client-facing tracking link for an order (Orderry's {Tracking URL})."""
    token = getattr(order, "public_token", None)
    if not token:
        return "—"
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/track/{token}{suffix}"


async def _resolve(db, org_id, order: Order, current=None):
    """Return (doc_map, rows) — document-level variables and per-line row maps."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    client = order.client
    dev = order.device
    tech = order.assigned_to
    lines = list(order.lines or [])

    # Multi-currency: resolve the document's display currency (order snapshot → client → base).
    # Shadow `_money` locally so every amount in this document renders in the display currency.
    from backend.services import currency as _cur
    _ci = await _cur.resolve_for_client(db, org_id, order.client_id, order.currency_code)
    _fx = order.fx_rate or _ci["rate"] or 1.0          # display units per 1 base unit
    _disp_sym = _ci["symbol"]
    _disp_code = _ci["code"]
    _base_code = (org.currency_code if org else "") or ""
    _is_foreign = bool(_disp_code and _base_code and _disp_code != _base_code and _fx)

    def _money(n):                                       # noqa: F811 — intentional doc-currency shadow
        return f"{float(n or 0) * _fx:,.2f}"

    # resolve referenced catalog parts (for product-row attributes) & line techs
    # RepairOS line types: "part" (product), "labour"/"fee" (service).
    part_ids = [l.ref_id for l in lines if l.ref_id and (l.line_type or "") == "part"]
    parts = {}
    if part_ids:
        for p in (await db.execute(select(Part).where(Part.id.in_(part_ids)))).scalars().all():
            parts[p.id] = p
    # 144ax G7 — resolve primary AND co-technician names so split lines print both.
    tech_ids = set(l.technician_id for l in lines if l.technician_id)
    for l in lines:
        for c in (getattr(l, "co_technicians", None) or []):
            if isinstance(c, dict) and c.get("employee_id"):
                tech_ids.add(c["employee_id"])
    line_techs = {}
    if tech_ids:
        for e in (await db.execute(select(Employee).where(Employee.id.in_(list(tech_ids))))).scalars().all():
            line_techs[e.id] = e

    # per-line row maps + running totals
    rows, prod_rows, serv_rows = [], [], []
    subtotal = tax_total = total = total_qty = total_disc = base_no_disc_total = 0.0
    for i, l in enumerate(lines, start=1):
        p = parts.get(l.ref_id)
        lt = line_techs.get(l.technician_id) or tech
        # 144ax G7 — a split line prints all its technicians, e.g. "James D. + Rosa M.".
        _co = getattr(l, "co_technicians", None) or []
        if len(_co) > 1:
            import types as _types
            names = [line_techs[c["employee_id"]].name for c in _co
                     if isinstance(c, dict) and c.get("employee_id") in line_techs]
            if names:
                lt = _types.SimpleNamespace(name=" + ".join(names),
                                            phone=getattr(lt, "phone", ""), position=getattr(lt, "position", ""))
        rows.append(_row_vars(i, l, p, lt))
        n = _line_numbers(l)
        subtotal += n["base"]; tax_total += n["tax"]; total += n["amount"]
        total_qty += n["qty"]; total_disc += n["disc"]; base_no_disc_total += n["base_no_disc"]
        entry = [i, l.description or "", f"{n['qty']:g}", _money(n["unit_ex"]), _money(n["tax"]), _money(n["amount"])]
        (prod_rows if (l.line_type or "") == "part" else serv_rows).append(entry)

    hdr = ["#", "Description", "Qty", "Unit Price", "Tax", "Amount"]
    products_table = _table_block(hdr, prod_rows)
    services_table = _table_block(hdr, serv_rows)
    all_items_table = _table_block(hdr, prod_rows + serv_rows)

    # dominant tax (name/rate) for the document-level total line
    tax_name = tax_rate = ""
    for l in lines:
        if l.tax_rate:
            tax_rate = f"{float(l.tax_rate):g}%"
            tax_name = "Tax"
            break

    # warranty / paid / balance
    warranty = _fmt_date(order.warranty_until) if order.warranty_until else "No warranty"
    paid = (await db.execute(select(func.coalesce(func.sum(Invoice.amount_paid), 0))
        .where(Invoice.order_id == order.id))).scalar() or 0
    balance = max((order.total or 0) - paid, 0)

    # real, scannable vector codes from the order's barcode value
    code = order.barcode or order.order_number
    barcode_svg = _Html(code128_svg(code))
    qr_code_svg = _Html(qr_svg(code))

    # Payer: client's designated payer, else the client.
    payer = client
    if client and getattr(client, "payer_id", None):
        payer = (await db.execute(select(Client).where(Client.id == client.payer_id))).scalar_one_or_none() or client

    # Manager: the client's account manager if set.
    manager = None
    if client and getattr(client, "manager_id", None):
        manager = (await db.execute(select(Employee).where(Employee.id == client.manager_id))).scalar_one_or_none()

    # Legal entity: order location's entity, else org default.
    legal = None
    if order.location_id:
        loc = (await db.execute(select(Location).where(Location.id == order.location_id))).scalar_one_or_none()
        if loc and getattr(loc, "legal_entity_id", None):
            legal = (await db.execute(select(LegalEntity).where(LegalEntity.id == loc.legal_entity_id))).scalar_one_or_none()
    if legal is None:
        legal = (await db.execute(select(LegalEntity).where(
            LegalEntity.org_id == org_id, LegalEntity.is_default == True))).scalars().first()  # noqa: E712

    def g(obj, attr):
        return (getattr(obj, attr, None) if obj else "") or "—"

    # ── 144hk: branding, interactive CTAs, work summary ──────────────────────
    _logo = (getattr(org, "logo_data", None) if org else None) or ""
    logo_html = _Html(f'<img src="{_esc(_logo)}" alt="{_esc(org.name if org else "")}" '
                      f'style="max-height:64px;max-width:220px;object-fit:contain;" />') if _logo else ""
    accent = (getattr(org, "brand_primary_color", None) if org else None) or "#2563eb"
    track = _track_url(order)
    track_qr = _Html(qr_svg(track))

    def _cta(title: str, sub: str) -> str:
        return _Html(f'<table style="border-collapse:collapse;margin:8px 0;"><tr>'
                f'<td style="border:1.5px solid {_esc(accent)};border-radius:8px;padding:10px 14px;">'
                f'<div style="font-weight:700;color:{_esc(accent)};font-size:13px;">{title}</div>'
                f'<div style="font-size:11px;color:#444;margin-top:2px;">{sub}</div>'
                f'<div style="font-size:11px;margin-top:2px;">{_esc(track)}</div></td>'
                f'<td style="width:76px;padding-left:10px;vertical-align:middle;">'
                f'<div style="width:72px;height:72px;">{track_qr}</div></td></tr></table>')

    # Deterministic plain-English work summary from the order's own fields (no LLM,
    # so nothing is invented) — the prose techs hate writing, assembled for them.
    _svcs = [l.description for l in lines if (l.line_type or "") != "part" and l.description]
    _prts = [l.description for l in lines if (l.line_type or "") == "part" and l.description]
    _ws = []
    if order.complaint:
        _ws.append(f"Reported: {order.complaint}.")
    if _svcs:
        _ws.append("Work performed: " + ", ".join(_svcs) + ".")
    if _prts:
        _ws.append("Parts used: " + ", ".join(_prts) + ".")
    if order.diagnosis:
        _ws.append(f"Outcome: {order.diagnosis}.")
    work_summary = " ".join(_ws) or "—"

    # order location (name + address) for {Location} variables
    _loc = None
    if order.location_id:
        _loc = (await db.execute(select(Location).where(Location.id == order.location_id))).scalar_one_or_none()
    _cname = (client.name if client else "") or ""
    _first, _, _last = _cname.partition(" ")
    _pname = (payer.name if payer else "") or ""
    _pfirst, _, _plast = _pname.partition(" ")

    # order type name (Orderry {Order Type})
    _otype = ""
    if getattr(order, "type_id", None):
        _wt = (await db.execute(select(WorkOrderType).where(
            WorkOrderType.id == order.type_id))).scalar_one_or_none()
        _otype = (_wt.name if _wt else "") or ""

    def _fmt_dt(d):
        return d.strftime("%Y-%m-%d %H:%M") if d else "—"

    sign_here = _Html('<div style="margin-top:24px;">'
                 '<div style="border-bottom:1px solid #333;width:260px;height:28px;"></div>'
                 '<div style="font-size:11px;color:#555;margin-top:3px;">Signature / Date</div></div>')
    verify_badge = _Html(f'<table style="border-collapse:collapse;"><tr>'
                    f'<td style="width:64px;"><div style="width:60px;height:60px;">{track_qr}</div></td>'
                    f'<td style="font-size:10px;color:#555;padding-left:6px;vertical-align:middle;">'
                    f'Scan to verify this<br/>document is genuine</td></tr></table>')

    doc = {
        # Company
        "{Company Name}": org.name if org else "RepairOS",
        "{Company Address}": g(org, "company_address"),
        "{Company Phone}": g(org, "company_phone"),
        "{Company Email}": g(org, "company_email"),
        "{Company Website}": g(org, "website"),
        "{Company Logo}": logo_html,
        "{Brand Color}": accent,
        # Legal entity
        "{Legal Entity Name}": (legal.name if legal else (org.name if org else "")) or "—",
        "{Legal Entity Tax ID}": (getattr(legal, "tax_id", None) if legal
                                  else (getattr(org, "company_details", None) if org else "")) or "—",
        "{Legal Entity Address}": (getattr(legal, "address", None) if legal
                                   else (getattr(org, "company_address", None) if org else "")) or "—",
        "{Legal Entity Details}": (getattr(legal, "details", None) if legal
                                   else (getattr(org, "company_details", None) if org else "")) or "—",
        "{Legal Entity Registration No}": g(legal, "registration_no"),
        "{Legal Entity VAT}": g(legal, "vat_no"),
        "{Legal Entity Bank Name}": g(legal, "bank_name"),
        "{Legal Entity Bank Account}": g(legal, "bank_account"),
        "{Legal Entity Phone}": g(legal, "phone"),
        "{Legal Entity Email}": g(legal, "email"),
        "{Legal Entity Director}": g(legal, "director"),
        # Interactive (144hk)
        "{Approve Button}": _cta("Approve online", "Review &amp; approve this estimate"),
        "{Pay Now Button}": _cta("Pay online", f"Balance due {_disp_sym}{_money(balance)}"),
        "{Sign Here}": sign_here,
        "{Verify Badge}": verify_badge,
        "{Work Summary}": work_summary,
        # Client
        "{Client Name}": g(client, "name"),
        "{Client First Name}": _first or "—",
        "{Client Last Name}": _last or "—",
        "{Client Phone}": g(client, "phone"),
        "{Client Email}": g(client, "email"),
        "{Client Address}": g(client, "address"),
        "{Client Billing Address}": (getattr(client, "billing_address", None) if client else None)
                                    or g(client, "address"),
        "{Client Tax ID}": g(client, "tax_id"),
        "{Client Registration No}": g(client, "reg_no"),
        # Payer
        "{Payer Name}": g(payer, "name"),
        "{Payer Phone}": g(payer, "phone"),
        "{Payer Email}": g(payer, "email"),
        "{Payer Address}": g(payer, "address"),
        # Ticket
        "{Order Number}": order.order_number,
        "{Order Status}": order.status,
        "{Order Created Date}": _fmt_date(order.created_at),
        "{Order Done Date}": _fmt_date(order.closed_at),
        "{Order Due Date}": _fmt_date(order.due_at),
        "{Order Complaint}": order.complaint or "—",
        "{Order Conclusion}": order.diagnosis or "—",
        "{Order Manager Notes}": order.internal_notes or "—",
        "{Order Technician Notes}": order.diagnosis or "—",
        "{Order Scheduled Date}": _fmt_date(order.scheduled_to or order.scheduled_for) if (order.scheduled_to or order.scheduled_for) else "—",
        "{Order Urgent}": "Urgent" if (order.priority or 0) >= 1 else "",
        "{Ad Campaign}": g(client, "source"),
        "{Location}": g(_loc, "name"),
        "{Location Address}": g(_loc, "address"),
        # Device / asset
        "{Asset Owner}": g(client, "name"),
        "{Asset Warehouse}": "—",
        "{Asset Type}": g(dev, "asset_type"),
        "{Asset Brand}": g(dev, "brand"),
        "{Asset Model}": g(dev, "model"),
        "{Asset Modification}": g(dev, "condition"),
        "{Asset Color}": g(dev, "color"),
        "{Asset Year}": "—",
        "{Asset Cost}": _money(getattr(dev, "purchase_price", 0) if dev else 0),
        "{Asset Serial Number}": g(dev, "serial"),
        "{Asset Description}": g(dev, "asset_type"),
        "{Asset Address}": g(client, "address"),
        "{Asset State}": g(dev, "asset_status"),
        "{Asset IMEI}": g(dev, "imei"),
        "{Asset VIN}": g(dev, "vin"),
        # Tables — blocks
        "{Products Table}": products_table,
        "{Products Table By Technicians}": products_table,
        "{Services Table}": services_table,
        "{Services Table By Technicians}": services_table,
        "{All Items Table}": all_items_table,
        "{All Items By Technicians}": all_items_table,
        # Totals
        "{Subtotal}": _money(subtotal),
        "{Tax Name}": tax_name,
        "{Tax Rate}": tax_rate,
        "{Tax Value}": _money(tax_total),
        "{Tax Value Spelled Out}": _spell_money(tax_total * _fx),
        "{Total Amount}": _money(total),
        "{Total Amount Without Discount}": _money(base_no_disc_total + tax_total),
        "{Total Amount Without Discount And Without Taxes}": _money(base_no_disc_total),
        "{Total Discount}": _money(total_disc),
        "{Total Discount Without Taxes}": _money(total_disc),
        "{Total Qty}": f"{total_qty:g}",
        # Employees
        "{Current User}": (current.name if current else "") or (tech.name if tech else "—"),
        "{Order Assignee Name}": g(tech, "name"),
        "{Order Assignee Phone}": g(tech, "phone"),
        "{Order Assignee Position}": g(tech, "position"),
        "{Order Closed By}": (tech.name if (tech and order.closed_at) else "—"),
        "{Order Created By}": g(tech, "name"),
        "{Order Manager Name}": g(manager or tech, "name"),
        "{Order Manager Phone}": g(manager or tech, "phone"),
        "{Technician Name}": g(tech, "name"),
        "{Technician Phone}": g(tech, "phone"),
        "{Technician Job Title}": g(tech, "position"),
        # Finance
        "{Order Total}": _money(order.total),
        "{Order Paid}": _money(paid),
        "{Order Balance Due}": _money(balance),
        "{Order Prepayment}": _money(getattr(order, "prepayment_total", 0)),
        "{Order Warranty Expiration Date}": warranty,
        "{Total Amount Spelled Out}": _spell_money((order.total or 0) * _fx),
        "{Order Paid Spelled Out}": _spell_money(paid * _fx),
        "{Balance Due Spelled Out}": _spell_money(balance * _fx),
        "{Order Payment URL}": track,
        "{Currency}": _disp_sym,
        "{Currency Code}": _disp_code,
        "{FX Rate}": (f"1 {_disp_code} = {(1.0 / _fx):,.4f} {_base_code}" if _is_foreign else ""),
        "{FX Note}": (f"Amounts shown in {_disp_code}. {_fmt_date(order.created_at)} rate: 1 {_disp_code} = {(1.0 / _fx):,.4f} {_base_code}." if _is_foreign else ""),
        # Date
        "{Today Date}": _fmt_date(datetime.utcnow()),
        "{Current Date}": _fmt_date(datetime.utcnow()),
        # Links — client-facing URLs (Orderry parity). Base is the org's public URL;
        # falls back to a relative path when PUBLIC_BASE_URL isn't configured.
        "{Tracking URL}": _track_url(order),
        "{Client Feedback URL}": _track_url(order, suffix="#feedback"),
        "{Public Order URL}": _track_url(order),
        # Other
        "{Barcode}": barcode_svg,
        "{QR Code}": qr_code_svg,
        "{Product Title}": (lines[0].description if lines else "") or (org.name if org else ""),
        "{Product Article}": (parts.get(lines[0].ref_id).sku if lines and parts.get(lines[0].ref_id) else "") or "",
        "{Price}": _money(lines[0].unit_price if lines else 0),
    }

    # ── 144ho: Orderry-exact variable names ──────────────────────────────────
    # Existing RepairOS names are kept; these resolve to the SAME values so a
    # template authored against Orderry's catalog prints without leftover tokens.
    _feedback = _track_url(order, suffix="#feedback")
    _disc_pct = (f"{(total_disc / base_no_disc_total * 100):g}%" if base_no_disc_total else "")
    doc.update({
        # Company
        "{Company Details}": g(org, "company_details"),
        "{Company Note}": g(org, "company_note"),
        # Legal entity — exact names; real data where we store it, blank ("—") for parity otherwise
        "{Legal Entity Registration Number}": doc["{Legal Entity Registration No}"],
        "{Legal Entity TIN}": doc["{Legal Entity Tax ID}"],
        "{Legal Entity Phone Number}": doc["{Legal Entity Phone}"],
        "{Legal Entity IBAN}": doc["{Legal Entity Bank Account}"],
        "{Legal Entity Additional Info}": doc["{Legal Entity Details}"],
        "{Legal Entity Country}": "—", "{Legal Entity State}": "—", "{Legal Entity City}": "—",
        "{Legal Entity Address Line 1}": doc["{Legal Entity Address}"],
        "{Legal Entity Address Line 2}": "—", "{Legal Entity Postal Code}": "—",
        "{Legal Entity Bank Address}": "—", "{Legal Entity SWIFT}": "—",
        "{Legal Entity Additional Payment Info}": "—",
        # Client — exact names
        "{Client Tax Identification Number}": doc["{Client Tax ID}"],
        "{Client Business Registration Number}": doc["{Client Registration No}"],
        "{Client Phone Type}": "—", "{Client Birthday}": "—", "{Client Age}": "—",
        # Payer — full group (payer is a Client, so structured fields resolve)
        "{Payer First Name}": _pfirst or "—", "{Payer Last Name}": _plast or "—",
        "{Payer Tax Identification Number}": g(payer, "tax_id"),
        "{Payer Business Registration Number}": g(payer, "reg_no"),
        "{Payer Billing Address}": (getattr(payer, "billing_address", None) if payer else None)
                                   or g(payer, "address"),
        "{Payer Phone Type}": "—", "{Payer Birthday}": "—", "{Payer Age}": "—",
        # Ticket / Job — exact names
        "{Order Malfunction}": doc["{Order Complaint}"],
        "{Order Comment}": doc["{Order Manager Notes}"],
        "{Order Address}": doc["{Client Address}"],
        "{Order Duration}": (f"{order.duration_min} min" if getattr(order, "duration_min", None) else "—"),
        "{Order Resource}": "—",
        "{Order Scheduled For}": (_fmt_dt(order.scheduled_for) if order.scheduled_for else "—"),
        "{Order Scheduled To}": (_fmt_dt(order.scheduled_to) if order.scheduled_to else "—"),
        "{Urgent}": doc["{Order Urgent}"],
        "{Order Type}": _otype or "—",
        # Finance — exact names
        "{Estimated Price}": doc["{Order Total}"],
        "{Estimated Price Spelled Out}": doc["{Total Amount Spelled Out}"],
        "{Paid Spelled Out}": doc["{Order Paid Spelled Out}"],
        "{Order sum}": doc["{Order Total}"],
        "{Balance Due}": doc["{Order Balance Due}"],
        "{Total Paid Amount}": _money(paid),
        "{Total Paid Tax Amount}": "—",
        "{Total Paid Amount Without Taxes}": "—",
        "{Total Amount Without Taxes}": _money(subtotal),
        "{Total Discount Pct}": _disc_pct,
        # Date — exact names
        "{Today Datetime}": _fmt_dt(datetime.utcnow()),
        "{Order Closed Date}": doc["{Order Done Date}"],
        "{Order Due Date Time}": (_fmt_dt(order.due_at) if order.due_at else "—"),
        # Links — exact names + QR variants
        "{Public Page URL}": track,
        "{Order URL}": track,
        "{Public Page QR Code}": track_qr,
        "{Client Feedback QR Code}": _Html(qr_svg(_feedback)),
        "{Order Payment QR}": track_qr,
    })

    # ── 144ho: org custom fields become {Order/Client/Asset <label>} variables.
    # setdefault so a custom field labelled like a built-in never clobbers it.
    for f in await _custom_field_defs(db, org_id):
        src = {"order": order, "client": client, "device": dev}.get(f.entity_type)
        val = (getattr(src, "custom_data", None) or {}).get(f.field_key) if src else None
        doc.setdefault(_cf_varname(f), _fmt_cf(val))

    return doc, rows


def _render(body: str, doc: dict, rows: list) -> str:
    """Expand repeating product rows, then substitute document-level variables."""
    def repeat(m):
        open_tag, inner, close_tag = m.group(1), m.group(2), m.group(3)
        if not _ROW_MARKER_RE.search(inner):
            return m.group(0)
        # Safety: a row template must not itself contain a nested table — the flat
        # <tbody> regex can't pair nested open/close tags, so repeating would corrupt
        # the markup. Leave such a block untouched (row vars are stripped afterwards).
        if "<table" in inner.lower():
            return m.group(0)
        chunks = []
        for r in rows:
            c = inner
            for k, v in r.items():
                c = c.replace(k, _sub(v))
            chunks.append(c)
        if not rows:
            chunks.append(_ROW_VAR_RE.sub("", inner))
        return open_tag + "".join(chunks) + close_tag

    body = _conditionals(body, doc)    # 144id — resolve {#if}/{#unless} before expansion
    body = _TBODY_RE.sub(repeat, body)
    for k, v in doc.items():
        body = body.replace(k, _sub(v))
    body = _ROW_VAR_RE.sub("", body)   # strip any row vars used outside a table
    return body


_COMPONENT_RE = re.compile(r"\{\{\s*component\s*:\s*([^}]+?)\s*\}\}", re.I)


async def _expand_components(db, org_id, body: str, _depth: int = 0) -> str:
    """144id — inline reusable blocks. A template marked doc_type='component' can be
    embedded anywhere with {{component: Name}} so branding (header/footer) is authored
    once and reused. Nested components resolve up to 5 levels; unknown names drop out."""
    if not body or "{{" not in body or _depth > 5:
        return body
    if not _COMPONENT_RE.search(body):
        return body
    comps = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.org_id == org_id,
        DocumentTemplate.doc_type == "component"))).scalars().all()
    by_name = {(c.name or "").strip().lower(): (c.body_html or "") for c in comps}
    body = _COMPONENT_RE.sub(lambda m: by_name.get(m.group(1).strip().lower(), ""), body)
    return await _expand_components(db, org_id, body, _depth + 1)


def _apply(body: str, vars: dict) -> str:  # kept for backward compatibility
    out = body
    for k, v in vars.items():
        out = out.replace(k, str(v))
    return out


@router.get("/render/{order_id}/{template_id}")
async def render_document(
    order_id: str,
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
        .options(selectinload(Order.lines), selectinload(Order.client),
                 selectinload(Order.device), selectinload(Order.assigned_to)))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    doc, rows = await _resolve(db, current.org_id, order, current)
    body = await _expand_components(db, current.org_id, tpl.body_html)
    html = _render(body, doc, rows)
    db.add(GeneratedDocument(org_id=current.org_id, order_id=order.id, template_id=tpl.id,
                             name=tpl.name, rendered_html=html))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order.id, f"Document generated: {tpl.name}")
    await db.commit()
    return {"html": html, "name": tpl.name}


class PreviewIn(BaseModel):
    body_html: str = ""
    doc_type: str = "work_order"
    order_id: Optional[str] = None


@router.post("/preview")
async def preview_template(
    data: PreviewIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144hp — render an UNSAVED template body against a real sample record so the
    editor can show a live, data-filled preview. Invoices use the newest invoice;
    everything else uses the chosen order (or the newest order). Falls back to an
    empty resolve (variables render as their placeholders) when the org has no data
    yet, so the preview never errors on a fresh account."""
    if (data.doc_type or "") == "invoice":
        inv = (await db.execute(select(Invoice).where(Invoice.org_id == current.org_id)
            .order_by(Invoice.created_at.desc()))).scalars().first()
        body = await _expand_components(db, current.org_id, data.body_html)
        if inv:
            doc, rows = await _resolve_invoice(db, current.org_id, inv)
            return {"html": _render(body, doc, rows), "sample": inv.invoice_number}
        return {"html": _render(body, {}, []), "sample": None}

    q = select(Order).where(Order.id == data.order_id, Order.org_id == current.org_id) if data.order_id \
        else select(Order).where(Order.org_id == current.org_id).order_by(Order.created_at.desc())
    order = (await db.execute(q.options(
        selectinload(Order.lines), selectinload(Order.client),
        selectinload(Order.device), selectinload(Order.assigned_to)))).scalars().first()
    body = await _expand_components(db, current.org_id, data.body_html)
    if not order:
        return {"html": _render(body, {}, []), "sample": None}
    doc, rows = await _resolve(db, current.org_id, order, current)
    return {"html": _render(body, doc, rows), "sample": order.order_number}


class AiGenIn(BaseModel):
    prompt: str = ""
    doc_type: str = "estimate"
    preset: str = ""


@router.get("/smart-presets")
async def smart_presets(current: Employee = Depends(get_current_employee)):
    """144ht — RepairOS's own document presets (offline, no third-party service)."""
    from backend.services import ai as _ai
    return {"items": [{"key": k, "label": v["label"], "doc_type": v["doc_type"]}
                      for k, v in _ai.DOC_PRESETS.items()]}


@router.post("/ai-generate")
async def ai_generate_template(
    data: AiGenIn,
    current: Employee = Depends(get_current_employee),
):
    """144ht — RepairOS's built-in document builder. Fully in-house and deterministic:
    expands a named preset, or maps a plain-English description to the block library.
    No external API is ever called."""
    if not data.preset and not (data.prompt or "").strip():
        raise HTTPException(400, "Pick a preset or describe the document you want")
    from backend.services import ai as _ai
    return _ai.generate_document_template(prompt=data.prompt, doc_type=data.doc_type, preset=data.preset)


# 144ia — exact physical sizes (mm). None height = continuous roll (thermal receipts).
PAGE_PRESETS = {
    "a4": (210.0, 297.0), "letter": (215.9, 279.4),
    "receipt80": (80.0, None), "receipt58": (58.0, None),
}
# Every page_size a template may be saved with: the presets + "label" (custom width x height).
VALID_PAGE_SIZES = frozenset(PAGE_PRESETS) | {"label"}


def _page_dims(page_size: str, w, h):
    if page_size == "label" and w and h:
        return (float(w), float(h))
    if page_size in PAGE_PRESETS:
        return PAGE_PRESETS[page_size]
    if w and h:
        return (float(w), float(h))
    return PAGE_PRESETS["a4"]


def _print_doc(body_html, doc, rows, *, font="Inter", page_size="a4", w=None, h=None, margin=None) -> str:
    """A standalone, print-ready HTML document that prints at the EXACT physical size on
    any printer — the @page rule sets the real page dimensions, so a 40x30mm label or an
    80mm receipt comes out true-to-size instead of being scaled onto A4 (Orderry's gap)."""
    rendered = _render(body_html, doc, rows)
    pw, ph = _page_dims(page_size, w, h)
    size_css = (f"{pw:g}mm {ph:g}mm" if ph else f"{pw:g}mm auto")
    m = margin if margin is not None else (0 if page_size in ("label", "receipt80", "receipt58") else 10)
    css = (f"@page{{size:{size_css};margin:{m:g}mm}}"
           "html,body{margin:0;padding:0}"
           f"body{{font-family:{font},Arial,sans-serif;color:#111;font-size:13px;line-height:1.5;width:{pw:g}mm}}"
           "table{border-collapse:collapse} img{max-width:100%} *{box-sizing:border-box}")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{rendered}</body></html>")


class PrintViewIn(BaseModel):
    body_html: str = ""
    doc_type: str = "estimate"
    order_id: Optional[str] = None
    page_size: str = "a4"
    page_w_mm: Optional[float] = None
    page_h_mm: Optional[float] = None
    margin_mm: Optional[float] = None
    font_family: str = "Inter"


@router.post("/print-view")
async def print_view(
    data: PrintViewIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144ia — return a print-ready HTML document, resolved against a real sample record and
    wrapped in an exact-size @page rule so it prints true-to-size on any printer."""
    if (data.doc_type or "") == "invoice":
        inv = (await db.execute(select(Invoice).where(Invoice.org_id == current.org_id)
            .order_by(Invoice.created_at.desc()))).scalars().first()
        doc, rows = (await _resolve_invoice(db, current.org_id, inv)) if inv else ({}, [])
    else:
        q = select(Order).where(Order.id == data.order_id, Order.org_id == current.org_id) if data.order_id \
            else select(Order).where(Order.org_id == current.org_id).order_by(Order.created_at.desc())
        order = (await db.execute(q.options(
            selectinload(Order.lines), selectinload(Order.client),
            selectinload(Order.device), selectinload(Order.assigned_to)))).scalars().first()
        doc, rows = (await _resolve(db, current.org_id, order, current)) if order else ({}, [])
    body = await _expand_components(db, current.org_id, data.body_html)
    html = _print_doc(body, doc, rows, font=data.font_family or "Inter",
                      page_size=data.page_size, w=data.page_w_mm, h=data.page_h_mm, margin=data.margin_mm)
    return {"html": html}


async def _resolve_invoice(db, org_id, inv: Invoice):
    """Variable map + repeating rows for an INVOICE document — lets an invoice be
    designed in the customizable template editor (doc_type='invoice'), the same way
    work orders are. Line rows come from the invoice's display_mode (detailed /
    simplified / one_line) via the shared invoicing service."""
    from backend.services.invoicing import display_lines as _inv_display
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    client = (await db.execute(select(Client).where(Client.id == inv.client_id))).scalar_one_or_none() if inv.client_id else None
    legal = None
    if inv.legal_entity_id:
        legal = (await db.execute(select(LegalEntity).where(LegalEntity.id == inv.legal_entity_id))).scalar_one_or_none()
    if legal is None:
        legal = (await db.execute(select(LegalEntity).where(
            LegalEntity.org_id == org_id, LegalEntity.is_default == True))).scalars().first()  # noqa: E712

    def m(n):
        return f"{float(n or 0):,.2f}"

    def g(o, a):
        return (getattr(o, a, None) if o else "") or "—"

    dl = await _inv_display(db, inv)
    hdr = ["#", "Description", "Qty", "Unit Price", "Amount"]
    rows, table_rows = [], []
    for i, l in enumerate(dl, start=1):
        rows.append({"{Row Number}": i, "{Product Name}": l["description"] or "—",
                     "{Product Qty}": f"{float(l.get('qty') or 0):g}",
                     "{Product Price}": m(l["unit_price"]), "{Product Amount}": m(l["total"])})
        table_rows.append([i, l["description"] or "", f"{float(l.get('qty') or 0):g}",
                           m(l["unit_price"]), m(l["total"])])
    paid = inv.amount_paid or 0
    bal = max((inv.total or 0) - paid, 0)
    logo = (getattr(org, "logo_data", None) if org else None) or ""
    logo_html = _Html(f'<img src="{_esc(logo)}" style="max-height:64px;max-width:220px;object-fit:contain;" />') if logo else ""
    order = (await db.execute(select(Order).where(Order.id == inv.order_id))).scalar_one_or_none() if inv.order_id else None
    pay_url = _track_url(order) if order else ""
    doc = {
        "{Company Name}": org.name if org else "RepairOS", "{Company Logo}": logo_html,
        "{Company Address}": g(org, "company_address"), "{Company Phone}": g(org, "company_phone"),
        "{Company Email}": g(org, "company_email"), "{Company Website}": g(org, "website"),
        "{Brand Color}": (getattr(org, "brand_primary_color", None) if org else None) or "#2563eb",
        "{Legal Entity Name}": (legal.name if legal else (org.name if org else "")) or "—",
        "{Legal Entity Tax ID}": g(legal, "tax_id"), "{Legal Entity Address}": g(legal, "address"),
        "{Legal Entity Details}": g(legal, "details"), "{Legal Entity Registration No}": g(legal, "registration_no"),
        "{Legal Entity VAT}": g(legal, "vat_no"), "{Legal Entity Bank Name}": g(legal, "bank_name"),
        "{Legal Entity Bank Account}": g(legal, "bank_account"), "{Legal Entity Phone}": g(legal, "phone"),
        "{Legal Entity Email}": g(legal, "email"), "{Legal Entity Director}": g(legal, "director"),
        "{Client Name}": g(client, "name"), "{Client Phone}": g(client, "phone"),
        "{Client Email}": g(client, "email"), "{Client Address}": g(client, "address"),
        "{Client Billing Address}": (getattr(client, "billing_address", None) if client else None) or g(client, "address"),
        "{Client Tax ID}": g(client, "tax_id"), "{Client Registration No}": g(client, "reg_no"),
        "{Invoice Number}": inv.invoice_number or "—", "{Invoice Status}": inv.status or "—",
        "{Invoice Date}": _fmt_date(inv.issued_at or inv.created_at),
        "{Invoice Due Date}": _fmt_date(inv.due_at) if inv.due_at else "—",
        "{Invoice Comment}": inv.comment or "—",
        "{Invoice Subtotal}": m(inv.subtotal), "{Invoice Tax}": m(inv.tax), "{Invoice Total}": m(inv.total),
        "{Invoice Paid}": m(paid), "{Invoice Balance Due}": m(bal),
        "{Invoice Table}": _table_block(hdr, table_rows), "{Invoice Payment URL}": pay_url,
        "{Today Date}": _fmt_date(datetime.utcnow()), "{Current Date}": _fmt_date(datetime.utcnow()),
        "{Today Datetime}": (datetime.utcnow()).strftime("%Y-%m-%d %H:%M"),
        "{Currency}": (org.currency_code if org else "") or "",
    }
    # 144ho — Orderry-exact aliases + company/client parity on the shared catalog
    doc.update({
        "{Company Details}": g(org, "company_details"), "{Company Note}": g(org, "company_note"),
        "{Client Tax Identification Number}": doc["{Client Tax ID}"],
        "{Client Business Registration Number}": doc["{Client Registration No}"],
        "{Legal Entity Registration Number}": doc["{Legal Entity Registration No}"],
        "{Legal Entity TIN}": doc["{Legal Entity Tax ID}"],
        "{Legal Entity Phone Number}": doc["{Legal Entity Phone}"],
        "{Legal Entity IBAN}": doc["{Legal Entity Bank Account}"],
        "{Legal Entity Additional Info}": doc["{Legal Entity Details}"],
    })
    # client custom fields as {Client <label>}
    for f in await _custom_field_defs(db, org_id):
        if f.entity_type != "client":
            continue
        val = (getattr(client, "custom_data", None) or {}).get(f.field_key) if client else None
        doc.setdefault(_cf_varname(f), _fmt_cf(val))
    return doc, rows


@router.get("/render-invoice/{invoice_id}/{template_id}")
async def render_invoice_document(
    invoice_id: str, template_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Render an invoice through a customizable document template (doc_type='invoice')."""
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if not inv:
        raise HTTPException(404, "Invoice not found")
    doc, rows = await _resolve_invoice(db, current.org_id, inv)
    html = _render(tpl.body_html, doc, rows)
    return {"html": html, "name": tpl.name}


async def _resolve_part(org, part: Part, price) -> dict:
    """Variable map for a label/price-tag rendered against a single product."""
    code_val = (part.barcode or part.sku or part.code or part.name or "0")
    cur = (org.currency if org else "") or ""
    return {
        "{Product Title}": part.name or "—",
        "{Product Name}": part.name or "—",
        "{Product Code}": part.code or part.sku or "",
        "{Product SKU}": part.sku or "",
        "{Product Article}": part.sku or part.code or "",
        "{Product Description}": part.category or "",
        "{Product UoM}": part.uom or "",
        "{Product Brand}": getattr(part, "brand", "") or "",
        "{Price}": _money(price),
        "{Product Price}": _money(price),
        "{Product Price Without Taxes}": _money(price),
        "{Currency}": cur,
        "{Company Name}": org.name if org else "RepairOS",
        "{Today Date}": _fmt_date(datetime.utcnow()),
        "{Current Date}": _fmt_date(datetime.utcnow()),
        "{Barcode}": _Html(code128_svg(code_val)),
        "{QR Code}": _Html(qr_svg(code_val)),
    }


class LabelRenderIn(BaseModel):
    template_id: str
    part_ids: list[str] = []
    copies: int = 1


@router.post("/render-labels")
async def render_labels(
    data: LabelRenderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Render a label/price-tag DocumentTemplate once per product×copy into a
    single, print-ready HTML sheet (the unified replacement for the old
    mm-based PDF label system — same rich template editor, real barcodes/QR)."""
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == data.template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if not data.part_ids:
        raise HTTPException(422, "Pick at least one product")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    parts = (await db.execute(select(Part).where(
        Part.id.in_(data.part_ids), Part.org_id == current.org_id))).scalars().all()
    if not parts:
        raise HTTPException(404, "No matching products")
    copies = max(1, min(int(data.copies or 1), 500))

    tiles = []
    for p in parts:
        varmap = await _resolve_part(org, p, p.sell_price)
        body = _render(tpl.body_html, varmap, [])
        tiles.extend([f"<div class='lbl'>{body}</div>"] * copies)

    return {"html": _labels_html(tpl, tiles), "name": tpl.name, "count": len(tiles)}


def _labels_html(tpl, tiles: list[str]) -> str:
    """144ib/144ic — exact-size bulk print: one label per physical page at the template's
    true size, so a label/thermal printer feeds each tag correctly (no A4 tiling/scaling)."""
    pw, ph = _page_dims(tpl.page_size, getattr(tpl, "page_w_mm", None), getattr(tpl, "page_h_mm", None))
    size_css = (f"{pw:g}mm {ph:g}mm" if ph else f"{pw:g}mm auto")
    mv = getattr(tpl, "margin_mm", None)
    m = mv if mv is not None else (0 if tpl.page_size in ("label", "receipt80", "receipt58") else 8)
    font = getattr(tpl, "font_family", None) or "Sans-Serif"
    css = (f"@page{{size:{size_css};margin:{m:g}mm}}"
           "html,body{margin:0;padding:0}"
           f"body{{font-family:{font},Arial,sans-serif;color:#111}}"
           f".lbl{{width:{pw:g}mm;box-sizing:border-box;page-break-after:always;break-after:page;overflow:hidden}}"
           ".lbl:last-child{page-break-after:auto;break-after:auto}"
           "table{border-collapse:collapse} img{max-width:100%} *{box-sizing:border-box}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head>"
            f"<body>{''.join(tiles)}</body></html>")


async def _resolve_asset(db, org, asset: Device) -> dict:
    """144ic — variable map for an asset/vehicle label rendered against one asset."""
    code_val = (asset.serial or asset.imei or asset.vin or asset.hin or asset.id or "0")
    client = None
    if asset.client_id:
        client = (await db.execute(select(Client).where(Client.id == asset.client_id))).scalar_one_or_none()

    def g(o, a):
        return (getattr(o, a, None) if o else "") or "—"
    return {
        "{Asset Owner}": g(client, "name"), "{Asset Type}": g(asset, "asset_type"),
        "{Asset Brand}": g(asset, "brand"), "{Asset Model}": g(asset, "model"),
        "{Asset Serial Number}": g(asset, "serial"), "{Asset IMEI}": g(asset, "imei"),
        "{Asset VIN}": g(asset, "vin"), "{Asset Color}": g(asset, "color"),
        "{Asset Modification}": g(asset, "condition"), "{Asset State}": g(asset, "asset_status"),
        "{Asset Cost}": _money(getattr(asset, "purchase_price", 0)),
        "{Asset Description}": g(asset, "asset_type"), "{Asset Address}": g(client, "address"),
        "{Client Name}": g(client, "name"), "{Client Phone}": g(client, "phone"),
        "{Company Name}": org.name if org else "RepairOS",
        "{Today Date}": _fmt_date(datetime.utcnow()), "{Current Date}": _fmt_date(datetime.utcnow()),
        "{Barcode}": _Html(code128_svg(code_val)), "{QR Code}": _Html(qr_svg(code_val)),
    }


class AssetLabelIn(BaseModel):
    template_id: str
    asset_ids: list[str] = []
    copies: int = 1


@router.post("/render-asset-labels")
async def render_asset_labels(
    data: AssetLabelIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144ic — print anything: render a label template against selected ASSETS, one label
    per physical page at exact size (the asset equivalent of product price-tag bulk print)."""
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == data.template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if not data.asset_ids:
        raise HTTPException(422, "Pick at least one asset")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    assets = (await db.execute(select(Device).where(
        Device.id.in_(data.asset_ids), Device.org_id == current.org_id))).scalars().all()
    if not assets:
        raise HTTPException(404, "No matching assets")
    copies = max(1, min(int(data.copies or 1), 500))
    tiles = []
    for a in assets:
        varmap = await _resolve_asset(db, org, a)
        body = _render(tpl.body_html, varmap, [])
        tiles.extend([f"<div class='lbl'>{body}</div>"] * copies)
    return {"html": _labels_html(tpl, tiles), "name": tpl.name, "count": len(tiles)}


async def _resolve_client(db, org, client: Client) -> dict:
    """144ie — variable map for a CLIENT record (contact card / loyalty tag / mailing label)."""
    code_val = (client.phone or client.id or "0")

    def g(o, a):
        return (getattr(o, a, None) if o else "") or "—"
    return {
        "{Client Name}": g(client, "name"), "{Client Phone}": g(client, "phone"),
        "{Client Email}": g(client, "email"), "{Client Address}": g(client, "address"),
        "{Client Billing Address}": (getattr(client, "billing_address", None) or None) or g(client, "address"),
        "{Client Tax ID}": g(client, "tax_id"), "{Client Registration No}": g(client, "reg_no"),
        "{Client Tax Identification Number}": g(client, "tax_id"),
        "{Company Name}": org.name if org else "RepairOS",
        "{Today Date}": _fmt_date(datetime.utcnow()), "{Current Date}": _fmt_date(datetime.utcnow()),
        "{Barcode}": _Html(code128_svg(code_val)), "{QR Code}": _Html(qr_svg(code_val)),
    }


class ClientLabelIn(BaseModel):
    template_id: str
    client_ids: list[str] = []
    copies: int = 1


@router.post("/render-client-labels")
async def render_client_labels(
    data: ClientLabelIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144ie — print anything: render a template against selected CLIENTS, one per physical
    page at exact size (contact cards, loyalty tags, mailing labels)."""
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == data.template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if not data.client_ids:
        raise HTTPException(422, "Pick at least one client")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    clients = (await db.execute(select(Client).where(
        Client.id.in_(data.client_ids), Client.org_id == current.org_id))).scalars().all()
    if not clients:
        raise HTTPException(404, "No matching clients")
    copies = max(1, min(int(data.copies or 1), 500))
    body_tpl = await _expand_components(db, current.org_id, tpl.body_html)
    tiles = []
    for c in clients:
        varmap = await _resolve_client(db, org, c)
        body = _render(body_tpl, varmap, [])
        tiles.extend([f"<div class='lbl'>{body}</div>"] * copies)
    return {"html": _labels_html(tpl, tiles), "name": tpl.name, "count": len(tiles)}


class InvoiceBatchIn(BaseModel):
    template_id: str
    invoice_ids: list[str] = []


@router.post("/render-invoice-batch")
async def render_invoice_batch(
    data: InvoiceBatchIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144ie — print anything: render a full invoice template against many INVOICES at once,
    one invoice per physical page. Unlike labels these are full documents with line rows —
    each is resolved through _resolve_invoice, so batch statements print in a single job."""
    tpl = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.id == data.template_id, DocumentTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if not data.invoice_ids:
        raise HTTPException(422, "Pick at least one invoice")
    invoices = (await db.execute(select(Invoice).where(
        Invoice.id.in_(data.invoice_ids), Invoice.org_id == current.org_id))).scalars().all()
    if not invoices:
        raise HTTPException(404, "No matching invoices")
    body_tpl = await _expand_components(db, current.org_id, tpl.body_html)
    tiles = []
    for inv in invoices:
        doc, rows = await _resolve_invoice(db, current.org_id, inv)
        tiles.append(f"<div class='lbl'>{_render(body_tpl, doc, rows)}</div>")
    return {"html": _labels_html(tpl, tiles), "name": tpl.name, "count": len(tiles)}


@router.get("/by-barcode/{code}")
async def order_by_barcode(
    code: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Scanner search: resolve a barcode (or order number) to an order id."""
    o = (await db.execute(select(Order).where(
        Order.org_id == current.org_id,
        (Order.barcode == code) | (Order.order_number == code)))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "No order for that code")
    return {"id": o.id, "order_number": o.order_number, "status": o.status}
