"""Costs and profit stay with the people allowed to see them.

One place, not per screen: any staff API response that carries cost or margin fields is
passed through `redact` when the viewer's role lacks "View the purchase price" (the middleware
in main.py does this, using the employee get_current_employee already resolved). Writes get
the mirror rule: cost fields sent by someone who can't see costs are ignored, so a form that
never showed the cost can't wipe it (`drop_cost_fields`).
"""
from typing import Any

COST_PERM = "can_view_purchase_price"
COST_KEYS = frozenset({
    "cost_price", "avg_cost", "unit_cost", "purchase_price", "last_cost", "landed_cost",
    "supplier_price", "purchase_cost", "cost_total", "total_cost",
    "margin", "margin_pct", "usual_pct", "profit", "cogs", "received_cost", "expected_cost",
    # stock and purchasing valued at cost (transfers, write-off risk, supplier spend)
    "line_value", "total_value", "value_at_risk", "total_spend", "stock_value",
})
# Next to a unit cost, a value/amount is that cost × qty (batches, stocktake variance, serials).
VALUE_NEXT_TO_COST = frozenset({"amount", "value", "cost"})
# A product's computed prices: the basis is its cost, and basis × markup = price gives it away.
PRICES_KEY = "basis"
# A purchase order is priced at cost, so its money fields are cost too — only on a PO-shaped
# object (one carrying "po_number"), since "total" means something else everywhere else.
# QA 31 Oct 2026: a technician worked out unit cost from PO totals and the 3-way match.
PO_KEY = "po_number"
PO_COST_KEYS = frozenset({
    "subtotal", "total", "charges_total", "supplier_invoice_total",
    "freight_capitalised", "shipment_freight",
})
PO_MATCH_COST_KEYS = frozenset({"invoiced_total", "price_variance"})
# Cheap pre-check on the raw JSON before parsing it.
_NEEDLES = tuple(f'"{k}"'.encode() for k in COST_KEYS | {PO_KEY, PRICES_KEY})


def mentions_costs(body: bytes) -> bool:
    return any(n in body for n in _NEEDLES)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {k: redact(v) for k, v in obj.items() if k not in COST_KEYS}
        if "unit_cost" in obj:
            for k in VALUE_NEXT_TO_COST:
                out.pop(k, None)
        if PRICES_KEY in obj and "prices" in obj:
            out.pop(PRICES_KEY, None)
            if isinstance(out.get("prices"), list):
                out["prices"] = [{k: v for k, v in p.items() if k != "markup_pct"} if isinstance(p, dict) else p
                                 for p in out["prices"]]
        if PO_KEY in obj:
            for k in PO_COST_KEYS:
                out.pop(k, None)
            if isinstance(out.get("match"), dict):
                out["match"] = {k: v for k, v in out["match"].items() if k not in PO_MATCH_COST_KEYS}
            if isinstance(out.get("supplier"), dict):
                out["supplier"].pop("balance", None)      # what the shop owes them
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def drop_cost_fields(payload: dict) -> dict:
    for k in COST_KEYS:
        payload.pop(k, None)
    return payload


async def can_see_costs(db, employee) -> bool:
    from backend.services.auth import has_perm
    return await has_perm(db, employee, COST_PERM)
