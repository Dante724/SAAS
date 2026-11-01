"""Tax resolution.

Resolve the taxes applicable to a document line, honouring:
  * per-item / per-category exceptions (precedence: product|service|labor >
    category > default stack),
  * the ordered default stack per document family (TaxRule.priority asc),
  * inclusive vs exclusive mode (Tax.mode included|imposed),
  * location scope.
Returns a list of applied taxes with the computed amount for the line's net.
"""
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Tax, TaxException, TaxRule
from backend.services.money import q2

JOB_FAMILY = "tickets_sales"


def _in_scope(tax: Tax, location_id: str | None) -> bool:
    if tax.location_scope != "selected":
        return True
    if not location_id:
        return False
    try:
        return location_id in json.loads(tax.location_ids or "[]")
    except Exception:
        return False


async def _default_stack(db, org_id, doc_family):
    rules = (await db.execute(select(TaxRule).where(
        TaxRule.org_id == org_id, TaxRule.doc_family == doc_family)
        .order_by(TaxRule.priority.asc()))).scalars().all()
    return [r.tax_id for r in rules]


async def resolve_taxes(db: AsyncSession, org_id: str, *, doc_family: str,
                        net_amount: float, item_type: str | None = None,
                        item_id: str | None = None, category_id: str | None = None,
                        location_id: str | None = None) -> dict:
    """Return {'taxes': [{tax_id,name,rate,mode,amount}], 'tax_total', 'exempt'}.

    net_amount is the pre-tax line total. For inclusive taxes the amount is the
    tax *contained* in the price; for exclusive it's added on top.
    """
    # 1) exception match — most specific wins
    exc = None
    if item_id:
        exc = (await db.execute(select(TaxException).where(
            TaxException.org_id == org_id, TaxException.doc_family == doc_family,
            TaxException.target_type == item_type, TaxException.target_id == item_id))).scalar_one_or_none()
    if not exc and category_id:
        exc = (await db.execute(select(TaxException).where(
            TaxException.org_id == org_id, TaxException.doc_family == doc_family,
            TaxException.target_type == "category", TaxException.target_id == category_id))).scalar_one_or_none()

    if exc:
        if exc.exempt or not exc.tax_id:
            return {"taxes": [], "tax_total": 0.0, "exempt": True}
        tax_ids = [exc.tax_id]
    else:
        tax_ids = await _default_stack(db, org_id, doc_family)

    applied, total = [], 0.0
    for tid in tax_ids:
        tax = (await db.execute(select(Tax).where(Tax.id == tid))).scalar_one_or_none()
        if not tax or not tax.active or not _in_scope(tax, location_id):
            continue
        rate = (tax.rate or 0.0) / 100.0
        if tax.mode == "included":
            amount = q2(net_amount - net_amount / (1 + rate)) if rate else 0.0
        else:  # imposed / exclusive
            amount = q2(net_amount * rate)
        applied.append({"tax_id": tax.id, "name": tax.name_for_document or tax.name,
                        "rate": tax.rate, "mode": tax.mode, "amount": amount})
        total += amount
    return {"taxes": applied, "tax_total": q2(total), "exempt": False}



async def job_stack(db: AsyncSession, org_id: str) -> list[str]:
    """The taxes jobs and sales charge, in order. The one source of truth for "the default tax":
    it is the first one here (it has priority, as in Orderry)."""
    return await _default_stack(db, org_id, JOB_FAMILY)


async def set_default_tax(db: AsyncSession, org_id: str, tax: Tax, is_default: bool) -> None:
    """Tick/untick "default" on a tax by editing the job tax stack (QA 2026-10-31 B).

    Ticking puts the tax first, in place of the old default; other stacked taxes stay.
    Unticking the default takes it out, and the next tax becomes the default; the last tax
    can't be removed this way, so jobs never silently stop charging tax. Caller commits."""
    rules = (await db.execute(select(TaxRule).where(
        TaxRule.org_id == org_id, TaxRule.doc_family == JOB_FAMILY)
        .order_by(TaxRule.priority.asc()))).scalars().all()
    stack = [r.tax_id for r in rules]
    if is_default:
        new = [tax.id] + [tid for tid in stack[1:] if tid != tax.id]
    else:
        if not stack or stack[0] != tax.id:
            return                                  # it isn't the default: nothing to change
        if len(stack) == 1:
            raise HTTPException(422, f"{tax.name} is the only tax charged on jobs. Make another "
                                     "tax the default instead, or clear the list under Tax rules.")
        new = stack[1:]
    if new == stack:
        return
    for r in rules:
        await db.delete(r)
    await db.flush()
    for i, tid in enumerate(new):
        db.add(TaxRule(org_id=org_id, doc_family=JOB_FAMILY, tax_id=tid, priority=i))
