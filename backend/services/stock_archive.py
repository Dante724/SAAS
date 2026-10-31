"""When a product may be archived (QA fix D, 31 Oct 2026).

Archiving hides a product from the catalog, the till and every stock report — so a product that
still has units on the shelf would take that stock (and its value) off the books. Every archive
path asks here first: the inventory bulk action, the product edit and the catalog bulk delete.
"""
from typing import Optional

from sqlalchemy import func, select

from backend.models import Part, StockLevel


async def archive_blocker(db, org_id: str, part: Part) -> Optional[str]:
    """Why `part` can't be archived yet, or None when it can."""
    on_hand = float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == part.id))).scalar() or 0)
    if on_hand > 1e-9:
        return f"{on_hand:g} still in stock. Sell or write them off first"
    return None
