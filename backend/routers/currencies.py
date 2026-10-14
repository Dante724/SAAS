"""Build 144cr — company currencies (Settings → Finance).

Additional (non-base) currencies with an exchange rate against the base
currency and a permanent rate-history trail. Rate convention:
``1 <code> = rate <base>``.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Account,
    Currency,
    CurrencyRateHistory,
    Employee,
    Organisation,
)
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/finance/currencies", tags=["currencies"])


async def _base(db, org_id: str) -> dict:
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    return {
        "code": (org.currency_code if org else None) or "USD",
        "symbol": (org.currency if org else None) or "$",
        "position": (org.currency_position if org else None) or "before",
    }


def _ser(c: Currency, in_use: bool = False) -> dict:
    return {
        "id": c.id, "code": c.code, "symbol": c.symbol, "position": c.position,
        "rate": round(c.rate, 6), "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "in_use": in_use,
    }


class CurrencyIn(BaseModel):
    code: str
    symbol: str = ""
    position: str = "before"       # before|after
    rate: float = 1.0


class CurrencyPatch(BaseModel):
    symbol: Optional[str] = None
    position: Optional[str] = None
    rate: Optional[float] = None


def _validate_rate(rate: float) -> float:
    if rate is None or rate <= 0:
        raise HTTPException(400, "Rate must be greater than 0")
    return round(float(rate), 6)


@router.get("")
async def list_currencies(db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_view_finance"))):
    rows = (await db.execute(select(Currency).where(
        Currency.org_id == current.org_id, Currency.active == True))).scalars().all()  # noqa: E712
    used = set((await db.execute(select(Account.currency_code).where(
        Account.org_id == current.org_id))).scalars().all())
    return {"base": await _base(db, current.org_id),
            "items": [_ser(c, c.code in used) for c in rows]}


@router.post("", status_code=201)
async def add_currency(data: CurrencyIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_view_finance"))):
    code = (data.code or "").strip().upper()
    if not code:
        raise HTTPException(400, "Currency code is required")
    base = await _base(db, current.org_id)
    if code == base["code"]:
        raise HTTPException(400, "That is already the base currency")
    dup = (await db.execute(select(Currency).where(
        Currency.org_id == current.org_id, Currency.code == code,
        Currency.active == True))).scalar_one_or_none()  # noqa: E712
    if dup:
        raise HTTPException(409, "Currency already added")
    rate = _validate_rate(data.rate)
    c = Currency(org_id=current.org_id, code=code, symbol=data.symbol or code,
                 position=data.position if data.position in ("before", "after") else "before",
                 rate=rate)
    db.add(c)
    await db.flush()
    db.add(CurrencyRateHistory(currency_id=c.id, org_id=current.org_id, rate=rate,
                               changed_by=current.id))
    await db.commit()
    await db.refresh(c)
    return _ser(c)


@router.patch("/{currency_id}")
async def edit_currency(currency_id: str, data: CurrencyPatch, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_view_finance"))):
    c = (await db.execute(select(Currency).where(
        Currency.id == currency_id, Currency.org_id == current.org_id))).scalar_one_or_none()
    if not c or not c.active:
        raise HTTPException(404, "Currency not found")
    if data.symbol is not None:
        c.symbol = data.symbol
    if data.position in ("before", "after"):
        c.position = data.position
    if data.rate is not None:
        new_rate = _validate_rate(data.rate)
        if new_rate != round(c.rate, 6):   # only history a real change
            c.rate = new_rate
            c.updated_at = datetime.utcnow()
            db.add(CurrencyRateHistory(currency_id=c.id, org_id=current.org_id,
                                       rate=new_rate, changed_by=current.id))
    await db.commit()
    await db.refresh(c)
    return _ser(c)


@router.delete("/{currency_id}")
async def delete_currency(currency_id: str, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_view_finance"))):
    c = (await db.execute(select(Currency).where(
        Currency.id == currency_id, Currency.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Currency not found")
    in_use = (await db.execute(select(Account).where(
        Account.org_id == current.org_id, Account.currency_code == c.code))).scalars().first()
    if in_use:
        raise HTTPException(409, "Cannot delete — an account still uses this currency")
    c.active = False   # hidden, not purged (history + old docs stay valid)
    await db.commit()
    return {"ok": True}


@router.get("/{currency_id}/history")
async def rate_history(currency_id: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_view_finance"))):
    c = (await db.execute(select(Currency).where(
        Currency.id == currency_id, Currency.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Currency not found")
    rows = (await db.execute(select(CurrencyRateHistory).where(
        CurrencyRateHistory.currency_id == currency_id).order_by(
        CurrencyRateHistory.changed_at.desc()))).scalars().all()
    return {"items": [{"rate": round(r.rate, 6), "changed_by": r.changed_by,
                       "changed_at": r.changed_at.isoformat() if r.changed_at else None} for r in rows]}


@router.get("/lookup-rate")
async def lookup_rate(code: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_view_finance"))):
    """Best-effort auto-rate from the Frankfurter API. Returns {rate|None}.
    Never raises on network failure — the operator can always type the rate."""
    base = await _base(db, current.org_id)
    code = (code or "").strip().upper()
    if not code or code == base["code"]:
        return {"rate": None}
    try:
        import httpx

        from backend.core.ssrf import assert_safe_url  # reuse the outbound guard
        url = f"https://api.frankfurter.dev/v1/latest?base={code}&symbols={base['code']}"
        assert_safe_url(url)
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
            r = await client.get(url)
            data = r.json()
        rate = (data.get("rates") or {}).get(base["code"])
        return {"rate": round(float(rate), 6) if rate else None}
    except Exception:
        return {"rate": None}
