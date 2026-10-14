"""144js — EU EN 16931 / Peppol BIS 3.0 e-invoice rendering.

Pure test of render_ubl (no DB): EU orgs get the Peppol profile + full tax detail;
non-EU orgs get the EN 16931 base; both are well-formed XML.
"""
import xml.etree.ElementTree as ET

from backend.services.einvoice import render_ubl, PEPPOL_PROFILE

_LINES = [{"description": "Screen repair", "qty": 1, "unit_price": 100.0, "total": 100.0},
          {"description": "Part", "qty": 2, "unit_price": 25.0, "total": 50.0}]


def _data(standard, currency, country):
    return {
        "standard": standard, "currency": currency, "doc_id": "INV-1",
        "issue_date": "2026-09-10", "due_date": "2026-09-24",
        "supplier": {"name": "Fix Co", "tax_id": "DE123456789", "address": "Main St 1", "country": country},
        "customer": {"name": "Acme", "tax_id": "DE987654321", "address": "High St 2", "country": country},
        "subtotal": 150.0, "tax": 28.5, "total": 178.5, "tax_pct": 19.0, "tax_category": "S",
        "lines": _LINES,
    }


def test_eu_is_peppol_bis_and_wellformed():
    xml = render_ubl(_data("en16931", "EUR", "DE"))
    ET.fromstring(xml)  # must be well-formed
    assert "urn:fdc:peppol.eu:2017:poacc:billing:3.0" in xml   # Peppol customization
    assert PEPPOL_PROFILE in xml                                # ProfileID present for EU
    assert "<cbc:IdentificationCode>DE</cbc:IdentificationCode>" in xml  # country codes
    assert "<cac:TaxSubtotal>" in xml and "<cbc:Percent>19</cbc:Percent>" in xml
    assert xml.count("<cac:ClassifiedTaxCategory>") == 2       # one per line
    assert "<cac:PartyLegalEntity>" in xml
    assert 'currencyID="EUR"' in xml


def test_non_eu_is_base_no_profile():
    xml = render_ubl(_data("none", "USD", "US"))
    ET.fromstring(xml)
    assert "urn:cen.eu:en16931:2017" in xml
    assert PEPPOL_PROFILE not in xml                            # no Peppol profile outside EU
    assert 'currencyID="USD"' in xml


def test_zero_tax_category_z():
    d = _data("en16931", "EUR", "IE")
    d.update(tax=0.0, tax_pct=0.0, tax_category="Z", total=150.0)
    xml = render_ubl(d)
    ET.fromstring(xml)
    assert "<cbc:ID>Z</cbc:ID>" in xml
