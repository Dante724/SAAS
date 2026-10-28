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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Tax, TaxException, TaxRule
from backend.services.money import q2


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


def line_tax(*, qty, unit_price, discount=0.0, discount_type=None, tax_rate=0.0, tax_mode=None) -> float:
    """The tax portion of one line after its discount (same maths as orders._line_tax_amount, but
    null-safe for stored rows). Imposed tax is added on top of the line; included tax is the VAT
    already inside the price."""
    base = float(qty or 0) * float(unit_price or 0)
    disc = float(discount or 0)
    after = max(base - disc, 0.0) if discount_type == "fixed" else base * (1 - disc / 100)
    rate = float(tax_rate or 0) / 100
    if not rate:
        return 0.0
    if (tax_mode or "imposed") == "included":
        return q2(after - after / (1 + rate))
    return q2(after * rate)


async def pos_line_tax(db: AsyncSession, org_id: str, *, kind: str, ref_id: str | None,
                       net_amount: float, location_id: str | None = None) -> dict:
    """Tax for one POS (counter sale) line: the org's sales stack with product/service exceptions,
    the same rules a work order line uses. Returns {"rate", "mode", "tax_id", "amount"}; for an
    imposed tax `amount` is added on top of net_amount, for an included tax it is the VAT inside it.

    Kept public for the POS screen's quote dry-run (stream C): call it per cart line."""
    item_type = "product" if kind == "product" else ("service" if kind == "service" else None)
    res = await resolve_taxes(db, org_id, doc_family="tickets_sales", net_amount=float(net_amount or 0),
                              item_type=item_type, item_id=ref_id, location_id=location_id)
    taxes = res.get("taxes") or []
    if res.get("exempt") or not taxes:
        return {"rate": 0.0, "mode": "imposed", "tax_id": None, "amount": 0.0}
    rate = round(sum(float(t["rate"] or 0) for t in taxes), 4)
    mode = taxes[0]["mode"] or "imposed"
    amount = line_tax(qty=1, unit_price=net_amount, tax_rate=rate, tax_mode=mode)
    return {"rate": rate, "mode": mode, "tax_id": taxes[0]["tax_id"], "amount": amount}
