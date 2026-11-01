"""Build 122 — multi-currency resolution.

Stored money is always in the org base currency. This module resolves which currency a
document should be *displayed* in and the rate to convert at, following the precedence:

    order override  →  client preference  →  org base currency

The rate is "units of the display currency per 1 unit of base" (e.g. base INR, display USD
at 0.012 means 1 INR = 0.012 USD). Rates come from the org's ``currencies_json`` list of
``{code, symbol, rate}``. The base currency always resolves to rate 1.0.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Client, Currency, Organisation
from backend.services.money import ZERO_DECIMAL, d2

# Common ISO-4217 code → display symbol (falls back to the code itself).
CURRENCY_SYMBOLS = {
    "USD": "$", "CAD": "$", "AUD": "$", "NZD": "$", "SGD": "$", "HKD": "$", "MXN": "$",
    "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "\u20b9",
    "AED": "AED", "SAR": "SAR", "QAR": "QAR", "KWD": "KWD", "BHD": "BHD", "OMR": "OMR",
    "CHF": "CHF", "SEK": "kr", "NOK": "kr", "DKK": "kr", "ZAR": "R", "BRL": "R$", "TRY": "₺",
}


def symbol_for(code: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    """Display symbol for a currency code; `fallback` when there is no code."""
    if not code:
        return fallback
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())


def _base(org: Optional[Organisation]) -> dict:
    """The org's base currency, with the symbol derived from the code when unset."""
    code = (org.currency_code if org else None) or ""
    return {"code": code, "symbol": (org.currency if org else None) or symbol_for(code, ""), "rate": 1.0}


def format_money(amount, symbol: Optional[str], code: Optional[str] = None) -> str:
    """`$1,234.50` — symbol + grouped amount, cents kept (none for zero-decimal currencies)."""
    v = d2(amount or 0)
    num = f"{v:,.0f}" if (code or "").lower() in ZERO_DECIMAL else f"{v:,.2f}"
    sym = symbol or ""
    if sym and len(sym) > 1 and sym.isalpha():        # "AED 1,200.00", "kr 99.00"
        return f"{sym} {num}"
    return f"{sym}{num}"


async def org_money(db: AsyncSession, org_id: str):
    """A formatter bound to the org's base currency: `fmt(12.5) -> "$12.50"`."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    b = _base(org)
    return lambda amount: format_money(amount, b["symbol"], b["code"])


async def _registry(db: AsyncSession, org_id: str, base_code: str, code: str) -> Optional[dict]:
    """Build 144cr/Stage-2: read the Currencies registry (single source of truth).

    The registry stores ``rate`` as *base per 1 unit of this currency*; this
    module needs *display per 1 unit of base*, so we return the reciprocal.
    """
    if not code or code.upper() == (base_code or "").upper():
        return None
    c = (await db.execute(select(Currency).where(
        Currency.org_id == org_id, Currency.code == code.upper(),
        Currency.active == True))).scalar_one_or_none()  # noqa: E712
    if not c or not c.rate:
        return None
    return {"code": c.code, "symbol": c.symbol or c.code, "rate": round(1.0 / float(c.rate), 6)}


def _lookup(org: Organisation, code: str) -> Optional[dict]:
    """Legacy fallback: the old ``currencies_json`` list (display-per-base)."""
    if not code:
        return None
    if code == (org.currency_code or "INR"):   # NULL code = the model's default base
        return _base(org)
    for c in (org.currencies_json or []):
        if (c.get("code") or "").upper() == code.upper():
            return {"code": c["code"], "symbol": c.get("symbol") or c["code"], "rate": float(c.get("rate") or 1.0)}
    return None


async def resolve_for_client(
    db: AsyncSession, org_id: str, client_id: Optional[str],
    override_code: Optional[str] = None,
) -> dict:
    """Resolve the effective display currency for a new document.

    Returns ``{code, symbol, rate}`` in base currency terms. ``override_code`` (a per-document
    choice) wins; otherwise the client's preference; otherwise the org base.
    """
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    base = _base(org)
    if not org:
        return base

    code = override_code
    if not code and client_id:
        c = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if c and c.currency_code:
            code = c.currency_code
    if not code:
        return base
    return (await _registry(db, org_id, base["code"], code)) or _lookup(org, code) or base


async def rate_for_code(db: AsyncSession, org_id: str, code: str) -> dict:
    """Look up a currency's ``{code, symbol, rate}`` by code, base-relative."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not org:
        return {"code": code, "symbol": code, "rate": 1.0}
    return ((await _registry(db, org_id, org.currency_code or "", code)) or _lookup(org, code)
            or _base(org))
