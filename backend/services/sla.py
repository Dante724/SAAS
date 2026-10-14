"""Per-status SLA automation.

`OrderStatus.time_limit_min` defines how long a work order may sit in a status
before it breaches SLA. `Order.status_entered_at` records when the current
status began. This module turns those two model fields into a live signal:

  * find_breaches()  — current breaches for an org (used by the API + UI)
  * sweep_breaches() — records a one-time feed event per breach (used by the
                       periodic background job), deduped so a single breach is
                       not re-announced on every sweep.

Terminal statuses and closed orders are never considered breaching.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EventFeedItem, Order, OrderStatus

_SLA_MARKER = "[sla]"   # prefix used to recognise (and dedupe) SLA feed events


async def _status_index(db: AsyncSession, org_id: str) -> dict:
    rows = (await db.execute(
        select(OrderStatus).where(OrderStatus.org_id == org_id)
    )).scalars().all()
    return {s.key: s for s in rows}


def _minutes_in_status(order: Order, now: datetime) -> Optional[int]:
    if not order.status_entered_at:
        return None
    return int((now - order.status_entered_at).total_seconds() // 60)


async def find_breaches(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> list[dict]:
    """Return current SLA breaches for one org."""
    now = now or datetime.utcnow()
    statuses = await _status_index(db, org_id)
    open_orders = (await db.execute(
        select(Order).where(and_(Order.org_id == org_id, Order.closed_at.is_(None)))
    )).scalars().all()
    breaches = []
    for o in open_orders:
        st = statuses.get(o.status)
        if not st or st.is_terminal or not st.time_limit_min:
            continue
        mins = _minutes_in_status(o, now)
        if mins is None or mins <= st.time_limit_min:
            continue
        breaches.append({
            "order_id": o.id,
            "order_number": o.order_number,
            "status": o.status,
            "status_name": st.name,
            "minutes_in_status": mins,
            "limit_min": st.time_limit_min,
            "over_by_min": mins - st.time_limit_min,
        })
    return breaches


async def sweep_breaches(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Record a one-time client-internal feed event for each new breach across
    all orgs. Returns the number of new events written."""
    now = now or datetime.utcnow()
    org_ids = [r[0] for r in (await db.execute(select(Order.org_id).distinct())).all()]
    written = 0
    for org_id in org_ids:
        for b in await find_breaches(db, org_id, now):
            o = (await db.execute(select(Order).where(Order.id == b["order_id"]))).scalar_one()
            # dedupe: skip if we already flagged this order since it entered the status
            existing = (await db.execute(
                select(EventFeedItem.id).where(
                    EventFeedItem.entity_type == "order",
                    EventFeedItem.entity_id == o.id,
                    EventFeedItem.item_type == "system",
                    EventFeedItem.body.like(f"{_SLA_MARKER}%"),
                    EventFeedItem.created_at >= (o.status_entered_at or now),
                )
            )).first()
            if existing:
                continue
            db.add(EventFeedItem(
                org_id=org_id, entity_type="order", entity_id=o.id,
                item_type="system", visibility="internal",
                body=f"{_SLA_MARKER} SLA breach: {b['over_by_min']}m over the "
                     f"{b['limit_min']}m limit for status '{b['status_name']}'.",
                author_name="system",
            ))
            written += 1
    if written:
        await db.commit()
    return written
