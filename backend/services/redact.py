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
    "margin", "margin_pct", "usual_pct", "profit", "cogs",
})
# Cheap pre-check on the raw JSON before parsing it.
_NEEDLES = tuple(f'"{k}"'.encode() for k in COST_KEYS)


def mentions_costs(body: bytes) -> bool:
    return any(n in body for n in _NEEDLES)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items() if k not in COST_KEYS}
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
