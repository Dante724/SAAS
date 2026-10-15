"""Several items per job (multi-asset).

Only for industries where one customer drops off many items at once: tools, small engine /
equipment, phones and computers. Everything here is tenant-scoped: callers pass org_id and
every query matches it (and order_id for items), so nothing can be read or attached across
shops or across jobs.

Rules
  * Available only for ELIGIBLE industry templates; the shop switches it on in Settings.
  * Switching industry to one outside ELIGIBLE makes the feature read as off (data kept,
    shown read-only); writes are refused until it's available and on again.
  * The job's own device (orders.device_id) is item #1, created on demand, so everything that
    knows one item per job keeps working. Item #1 can't be removed.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.industry import normalize_template_key

ELIGIBLE = frozenset({"tools", "small_engine", "mobile_phone", "computer"})
OUTCOMES = ("open", "fixed", "cant_fix", "declined")
MAX_ITEMS = 50


def available(org) -> bool:
    return normalize_template_key(getattr(org, "industry_template", None)) in ELIGIBLE


def enabled(org) -> bool:
    return bool(getattr(org, "multi_asset_jobs", False)) and available(org)


async def org_of(db: AsyncSession, org_id: str):
    from backend.models import Organisation
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()


async def require_enabled(db: AsyncSession, org_id: str):
    org = await org_of(db, org_id)
    if not org or not available(org):
        raise HTTPException(409, "Several items per job is only for tools, equipment, phone and computer shops")
    if not enabled(org):
        raise HTTPException(409, "Turn on 'Several items per job' in Settings first")
    return org


async def ensure_primary(db: AsyncSession, order) -> None:
    """Item #1 = the job's own device. Idempotent."""
    from backend.models import OrderItem
    if not order.device_id:
        return
    have = (await db.execute(select(OrderItem.id).where(
        OrderItem.org_id == order.org_id, OrderItem.order_id == order.id,
        OrderItem.asset_id == order.device_id))).scalar_one_or_none()
    if not have:
        db.add(OrderItem(org_id=order.org_id, order_id=order.id, asset_id=order.device_id,
                         complaint=None, sort_order=0))
        await db.flush()


async def item_for_order(db: AsyncSession, org_id: str, order_id: str, item_id: Optional[str]):
    """The item, only if it belongs to this shop AND this job (else 422 — never another tenant's)."""
    from backend.models import OrderItem
    if not item_id:
        return None
    it = (await db.execute(select(OrderItem).where(
        OrderItem.id == item_id, OrderItem.org_id == org_id, OrderItem.order_id == order_id))).scalar_one_or_none()
    if not it:
        raise HTTPException(422, "That item isn't on this job")
    return it


async def count(db: AsyncSession, org_id: str, order_id: str) -> int:
    from backend.models import OrderItem
    return (await db.execute(select(func.count()).select_from(OrderItem).where(
        OrderItem.org_id == org_id, OrderItem.order_id == order_id))).scalar() or 0
