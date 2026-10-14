"""
Unified rule surface (Smart Fields Phase B).

One code path for "a data point fires an upsell", whether the data point is a
checklist item (144go) or a custom field (144gp). Both call `sync_upsell_draft`,
so there is a single, idempotent, retract-aware lifecycle for auto-drafted
recommendations — no second engine, no ambiguity about which wins.

Lifecycle rules (identical to the original checklist behaviour):
- at most one *draft* recommendation per (order, source, source_ref_id);
- fires when `triggered` is True and nothing is already surfaced;
- retracts the still-draft recommendation when `triggered` goes False;
- never touches a recommendation the customer has already seen (sent/approved).
"""
from __future__ import annotations
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Recommendation


def numeric_rule_triggered(comparator: Optional[str], threshold: Optional[float],
                           value: Optional[str], want: Optional[str] = None) -> bool:
    """Evaluate a custom-field upsell trigger. `is`/`is_not` compare to `want`;
    `lt`/`gt` compare the numeric value to `threshold`. Empty value never fires."""
    if value is None or str(value).strip() == "":
        return False
    if comparator == "is":
        return str(value) == str(want)
    if comparator == "is_not":
        return str(value) != str(want)
    if comparator in ("lt", "gt") and threshold is not None:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return False
        return n < threshold if comparator == "lt" else n > threshold
    return False


async def sync_upsell_draft(db: AsyncSession, *, org_id: str, order_id: str,
                            source: str, source_ref_id: str, triggered: bool,
                            title: str, note: str, kind: str,
                            ref_id: Optional[str], price: float,
                            actor_id: Optional[str], actor_name: Optional[str]) -> None:
    existing = (await db.execute(select(Recommendation).where(
        Recommendation.org_id == org_id, Recommendation.order_id == order_id,
        Recommendation.source == source, Recommendation.source_ref_id == source_ref_id))).scalars().all()
    draft = next((r for r in existing if r.status == "draft"), None)
    live = any(r.status in ("sent", "approved") for r in existing)
    if triggered:
        if draft or live:
            return
        db.add(Recommendation(
            org_id=org_id, order_id=order_id, title=title, note=note,
            kind=(kind or "custom"), ref_id=ref_id, qty=1.0, unit_price=price or 0.0,
            status="draft", source=source, source_ref_id=source_ref_id,
            author_id=actor_id, author_name=actor_name))
    elif draft:
        await db.delete(draft)
