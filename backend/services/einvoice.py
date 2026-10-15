"""144if / 144js — in-house e-invoicing (region-aware).

Generates a standards-based UBL 2.1 Invoice XML from a RepairOS invoice. For EU
orgs the output targets **EN 16931 / Peppol BIS Billing 3.0** (the profile the EU
e-invoicing mandates build on): correct CustomizationID + ProfileID, party country
codes and legal entity, a real TaxTotal→TaxSubtotal with category + percent, and a
ClassifiedTaxCategory on every line. Non-EU orgs get the EN 16931 base customization
without the Peppol profile.

Fully deterministic and dependency-free — no third-party service. We do NOT submit
to any tax authority: live clearance (Peppol Access Point, or national systems like
Italy's SdI) needs certified credentials; status beyond "issued" is recorded via the
status endpoint. This produces the compliant document the shop hands to its Access
Point / accountant / customer.

The XML assembly (`render_ubl`) is a pure function over a plain dict so it can be
unit-tested without a database.
"""
from datetime import datetime
from xml.sax.saxutils import escape

from sqlalchemy import select

from backend.models import Client, Invoice, LegalEntity, Organisation
from backend.services import regions
from backend.services.invoicing import display_lines
from backend.services.money import q2


def _d(dt) -> str:
    return (dt or datetime.utcnow()).strftime("%Y-%m-%d")


def _money(n) -> str:
    return f"{float(n or 0):.2f}"


def _e(v) -> str:
    return escape(str(v)) if v not in (None, "") else ""


PEPPOL_CUSTOMIZATION = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PEPPOL_PROFILE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
EN16931_BASE = "urn:cen.eu:en16931:2017"


def _party_xml(tag: str, p: dict, currency: str) -> str:
    country = f"<cac:Country><cbc:IdentificationCode>{_e(p['country'])}</cbc:IdentificationCode></cac:Country>" if p.get("country") else ""
    tax_scheme = (f"<cac:PartyTaxScheme><cbc:CompanyID>{_e(p['tax_id'])}</cbc:CompanyID>"
                  f"<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>") if p.get("tax_id") else ""
    return f"""  <cac:{tag}><cac:Party>
    <cac:PartyName><cbc:Name>{_e(p['name'])}</cbc:Name></cac:PartyName>
    <cac:PostalAddress><cbc:StreetName>{_e(p.get('address'))}</cbc:StreetName>{country}</cac:PostalAddress>
    {tax_scheme}
    <cac:PartyLegalEntity><cbc:RegistrationName>{_e(p['name'])}</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:{tag}>"""


def render_ubl(data: dict) -> str:
    """Pure: build the UBL XML from a plain data dict.

    data = {standard, currency, doc_id, issue_date, due_date, supplier{}, customer{},
            subtotal, tax, total, tax_pct, tax_category, lines[{description,qty,unit_price,total}]}
    """
    cur = data["currency"]
    is_eu = data.get("standard") == "en16931"
    customization = PEPPOL_CUSTOMIZATION if is_eu else EN16931_BASE
    profile = f"  <cbc:ProfileID>{PEPPOL_PROFILE}</cbc:ProfileID>\n" if is_eu else ""
    pct, cat = float(data.get("tax_pct") or 0), data.get("tax_category") or ("S" if float(data.get("tax") or 0) > 0 else "Z")

    line_xml = []
    for i, l in enumerate(data["lines"], start=1):
        qty = float(l.get("qty") or 1)
        price = float(l.get("unit_price") or 0)
        amt = float(l.get("total") or qty * price)
        line_xml.append(
            f"""  <cac:InvoiceLine>
    <cbc:ID>{i}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">{qty:g}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{cur}">{_money(amt)}</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>{_e(l.get('description') or 'Item')}</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:ID>{cat}</cbc:ID><cbc:Percent>{pct:g}</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="{cur}">{_money(price)}</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>""")

    subtotal, tax, total = data["subtotal"], data["tax"], data["total"]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>{customization}</cbc:CustomizationID>
{profile}  <cbc:ID>{_e(data['doc_id'])}</cbc:ID>
  <cbc:IssueDate>{data['issue_date']}</cbc:IssueDate>
  <cbc:DueDate>{data['due_date']}</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>{cur}</cbc:DocumentCurrencyCode>
{_party_xml("AccountingSupplierParty", data["supplier"], cur)}
{_party_xml("AccountingCustomerParty", data["customer"], cur)}
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{cur}">{_money(tax)}</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="{cur}">{_money(subtotal)}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="{cur}">{_money(tax)}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>{cat}</cbc:ID><cbc:Percent>{pct:g}</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{cur}">{_money(subtotal)}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{cur}">{_money(subtotal)}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{cur}">{_money(total)}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{cur}">{_money(total)}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
{chr(10).join(line_xml)}
</Invoice>
"""


async def build_ubl(db, org_id: str, inv: Invoice) -> str:
    """Gather invoice data from the DB and render region-aware UBL."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    client = (await db.execute(select(Client).where(Client.id == inv.client_id))).scalar_one_or_none() if inv.client_id else None
    legal = None
    if inv.legal_entity_id:
        legal = (await db.execute(select(LegalEntity).where(LegalEntity.id == inv.legal_entity_id))).scalar_one_or_none()
    if legal is None:
        legal = (await db.execute(select(LegalEntity).where(
            LegalEntity.org_id == org_id, LegalEntity.is_default == True))).scalars().first()  # noqa: E712

    pack = regions.pack_for(getattr(org, "country", None), getattr(org, "region", None),
                            getattr(org, "currency_code", None), getattr(org, "currency", None))
    currency = pack["currency_code"] or "USD"
    org_country = (getattr(org, "country", "") or "").upper() or None

    dl = await display_lines(db, inv)
    subtotal = float(inv.subtotal or sum(l["total"] for l in dl) or 0)
    tax = float(inv.tax or 0)
    total = float(inv.total or (subtotal + tax))
    tax_pct = q2(tax / subtotal * 100) if subtotal else float(pack["tax"]["rate"] or 0)

    data = {
        "standard": pack["einvoice"]["standard"],
        "currency": currency,
        "doc_id": inv.einvoice_number or inv.invoice_number or inv.id,
        "issue_date": _d(inv.einvoice_issued_at or inv.issued_at or inv.created_at),
        "due_date": _d(getattr(inv, "due_at", None)),
        "supplier": {
            "name": (legal.name if legal else (org.name if org else "")) or "Supplier",
            "tax_id": (getattr(legal, "tax_id", "") if legal else "") or (getattr(legal, "vat_no", "") if legal else ""),
            "address": (getattr(legal, "address", "") if legal else "") or (getattr(org, "company_address", "") if org else ""),
            "country": org_country,
        },
        "customer": {
            "name": (client.name if client else "") or "Customer",
            "tax_id": (getattr(client, "tax_id", "") if client else ""),
            "address": (getattr(client, "address", "") if client else ""),
            "country": (getattr(client, "country", "") if client else "") or org_country,
        },
        "subtotal": subtotal, "tax": tax, "total": total,
        "tax_pct": tax_pct, "tax_category": "S" if tax > 0 else "Z",
        "lines": dl,
    }
    return render_ubl(data)
