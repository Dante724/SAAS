"""Build 122 — multi-currency resolution.

Stored money is always in the org base currency. This module resolves which currency a
document should be *displayed* in and the rate to convert at, following the precedence:

    order override  →  client preference  →  org base currency

The rate is "units of the display currency per 1 unit of base" (e.g. base INR, display USD
at 0.012 means ₹1 = $0.012). Rates come from the org's ``currencies_json`` list of
``{code, symbol, rate}``. The base currency always resolves to rate 1.0.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Client, Currency, Organisation


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
    if code == (org.currency_code or "INR"):
        return {"code": code, "symbol": org.currency or "₹", "rate": 1.0}
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
    base = {"code": (org.currency_code if org else "INR") or "INR",
            "symbol": (org.currency if org else "₹") or "₹", "rate": 1.0}
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
    base_code = org.currency_code or "INR"
    return ((await _registry(db, org_id, base_code, code)) or _lookup(org, code)
            or {"code": base_code, "symbol": (org.currency or "₹"), "rate": 1.0})
