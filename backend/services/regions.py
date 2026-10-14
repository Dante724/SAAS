"""144jr — RegionPack: the pluggable localization spine.

One place that says, per country/region, how RepairOS should behave: which tax
model, currency, e-invoice standard, accountant exports, dunning channel, and
number formatting are correct there. The finance core reads a pack; adding a
market is data here (+ an e-invoice adapter later), not a rewrite.

Scope of this file: the *declarative* pack (safe to expose to the client) plus a
resolver. The e-invoice **adapters** (Peppol/EN-16931, CFDI, NF-e, …) are separate
modules that plug into `einvoice` below; several need provider sandbox creds and
are staged, so the pack advertises the standard + status without implying it's live.
"""
from typing import Optional

# ── Tax defaults (superset of routers.auth COUNTRY_VAT, +LATAM) ───────────────
# (name, rate %, mode) — mode: "included" (VAT-style, tax in the price) or
# "imposed" (US sales-tax style, added on top).
COUNTRY_TAX: dict[str, tuple[str, float, str]] = {
    # North America
    "US": ("Sales tax", 0.0, "imposed"), "CA": ("GST", 5.0, "imposed"),
    # Europe
    "GB": ("VAT", 20.0, "included"), "DE": ("VAT", 19.0, "included"), "FR": ("VAT", 20.0, "included"),
    "ES": ("IVA", 21.0, "included"), "IT": ("IVA", 22.0, "included"), "NL": ("BTW", 21.0, "included"),
    "IE": ("VAT", 23.0, "included"), "PT": ("IVA", 23.0, "included"), "PL": ("VAT", 23.0, "included"),
    "BE": ("VAT", 21.0, "included"), "SE": ("VAT", 25.0, "included"), "AT": ("VAT", 20.0, "included"),
    # LATAM
    "MX": ("IVA", 16.0, "included"), "BR": ("ICMS", 17.0, "included"), "AR": ("IVA", 21.0, "included"),
    "CL": ("IVA", 19.0, "included"), "CO": ("IVA", 19.0, "included"), "PE": ("IGV", 18.0, "included"),
    "UY": ("IVA", 22.0, "included"), "EC": ("IVA", 15.0, "included"),
}

# ── E-invoice standard per country (what the market mandates/uses) ─────────────
# status: "live" = adapter implemented; "planned" = advertised, needs provider creds.
EINVOICE: dict[str, dict] = {
    # EU — EN 16931 / Peppol lineage (extends our existing UBL pipeline)
    **{c: {"standard": "en16931", "network": "peppol", "status": "beta"} for c in
       ("GB", "DE", "FR", "ES", "NL", "IE", "PT", "PL", "BE", "SE", "AT")},
    "IT": {"standard": "fatturapa", "network": "sdi", "status": "planned"},
    # LATAM — government clearing, needs an authorized provider (PAC) per country
    "MX": {"standard": "cfdi", "network": "sat_pac", "status": "planned"},
    "BR": {"standard": "nfe", "network": "sefaz", "status": "planned"},
    "CL": {"standard": "dte", "network": "sii", "status": "planned"},
    "CO": {"standard": "factura_electronica", "network": "dian", "status": "planned"},
    "PE": {"standard": "factura_electronica", "network": "sunat", "status": "planned"},
    "AR": {"standard": "factura_electronica", "network": "afip", "status": "planned"},
    # US — no national mandate
    "US": {"standard": "none", "network": None, "status": "n/a"},
    "CA": {"standard": "none", "network": None, "status": "n/a"},
}

# ── Currency + formatting per country ─────────────────────────────────────────
COUNTRY_CURRENCY: dict[str, tuple[str, str]] = {  # ISO4217, symbol
    "US": ("USD", "$"), "CA": ("CAD", "$"),
    "GB": ("GBP", "£"), "DE": ("EUR", "€"), "FR": ("EUR", "€"), "ES": ("EUR", "€"), "IT": ("EUR", "€"),
    "NL": ("EUR", "€"), "IE": ("EUR", "€"), "PT": ("EUR", "€"), "PL": ("PLN", "zł"), "BE": ("EUR", "€"),
    "SE": ("SEK", "kr"), "AT": ("EUR", "€"),
    "MX": ("MXN", "$"), "BR": ("BRL", "R$"), "AR": ("ARS", "$"), "CL": ("CLP", "$"),
    "CO": ("COP", "$"), "PE": ("PEN", "S/"), "UY": ("UYU", "$U"), "EC": ("USD", "$"),
}

# ── Region-level fallbacks (when only region, not country, is known) ───────────
REGION_DEFAULTS: dict[str, dict] = {
    "us":    {"currency": ("USD", "$"), "dunning": "email", "exports": ["quickbooks", "xero", "csv"]},
    "ca":    {"currency": ("CAD", "$"), "dunning": "email", "exports": ["quickbooks", "xero", "csv"]},
    "eu":    {"currency": ("EUR", "€"), "dunning": "email", "exports": ["quickbooks", "xero", "datev", "csv"]},
    "latam": {"currency": ("USD", "$"), "dunning": "whatsapp", "exports": ["contabilizei", "csv"]},
    "other": {"currency": ("USD", "$"), "dunning": "email", "exports": ["csv"]},
}

# Country → which accountant exports matter there (beyond the region default).
COUNTRY_EXPORTS: dict[str, list[str]] = {
    "DE": ["datev", "quickbooks", "xero", "csv"],
    "BR": ["contabilizei", "bling", "csv"], "MX": ["contpaqi", "csv"],
    "GB": ["xero", "quickbooks", "sage", "csv"],
}

# Country → dunning channel where a non-email channel dominates.
COUNTRY_DUNNING: dict[str, str] = {
    "BR": "whatsapp", "MX": "whatsapp", "CO": "whatsapp", "AR": "whatsapp",
    "PE": "whatsapp", "CL": "whatsapp",
}

_LATAM = {"MX", "BR", "AR", "CL", "CO", "PE", "UY", "EC"}
_EU = {"GB", "DE", "FR", "ES", "IT", "NL", "IE", "PT", "PL", "BE", "SE", "AT"}


def region_of(country: Optional[str], region: Optional[str]) -> str:
    c = (country or "").upper()
    if c in _EU:
        return "eu"
    if c in _LATAM:
        return "latam"
    if c in ("US",):
        return "us"
    if c in ("CA",):
        return "ca"
    return (region or "other").lower()


def pack_for(country: Optional[str], region: Optional[str] = None,
             currency_code: Optional[str] = None, currency_symbol: Optional[str] = None) -> dict:
    """Resolve the effective RegionPack. Country wins; region fills gaps; the org's
    own stored currency (if any) overrides the pack default."""
    c = (country or "").upper()
    reg = region_of(country, region)
    rd = REGION_DEFAULTS.get(reg, REGION_DEFAULTS["other"])

    cur = COUNTRY_CURRENCY.get(c) or rd["currency"]
    code = currency_code or cur[0]
    symbol = currency_symbol or cur[1]

    tax = COUNTRY_TAX.get(c)
    tax_model = {"name": tax[0], "rate": tax[1], "mode": tax[2]} if tax else \
        {"name": "Tax", "rate": 0.0, "mode": "included" if reg in ("eu", "latam") else "imposed"}

    ei = EINVOICE.get(c, {"standard": "none", "network": None, "status": "n/a"})

    return {
        "country": c or None, "region": reg,
        "currency_code": code, "currency_symbol": symbol,
        "tax": tax_model,
        "einvoice": ei,
        "accounting_exports": COUNTRY_EXPORTS.get(c, rd["exports"]),
        "dunning_channel": COUNTRY_DUNNING.get(c, rd["dunning"]),
        "decimals": 2,
        "date_format": "MM/DD/YYYY" if reg == "us" else "DD/MM/YYYY",
    }


def catalog() -> dict:
    """All known country packs — for the signup / settings region picker."""
    return {c: pack_for(c) for c in sorted(set(COUNTRY_TAX) | set(COUNTRY_CURRENCY))}
