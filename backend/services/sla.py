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
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EventFeedItem, Order, OrderStatus
from backend.services.dates import now_utc

_SLA_MARKER = "[sla]"   # prefix used to recognise (and dedupe) SLA feed events


def _index(rows) -> dict:
    """Statuses by key (an org's work-order and estimate pipelines share the key space here)."""
    return {s.key: s for s in rows}


async def _status_index(db: AsyncSession, org_id: str) -> dict:
    rows = (await db.execute(
        select(OrderStatus).where(OrderStatus.org_id == org_id)
    )).scalars().all()
    return _index(rows)


def _limited(statuses: dict) -> dict:
    """The statuses an order can breach in: a non-zero time limit and not terminal."""
    return {k: st for k, st in statuses.items() if not st.is_terminal and st.time_limit_min}


def _minutes_in_status(entered: Optional[datetime], now: datetime) -> Optional[int]:
    if not entered:
        return None
    return int((now - entered).total_seconds() // 60)


def _threshold(now: datetime, limit_min: int) -> datetime:
    """Latest status_entered_at that is in breach of `limit_min`. time_limit_min is not range
    checked on input, so an absurd limit clamps to the representable range instead of raising."""
    try:
        return now - timedelta(minutes=limit_min + 1)
    except OverflowError:
        return datetime.min if limit_min > 0 else datetime.max


def _breach_query(org_id: str, limited: dict, now: datetime):
    """Open orders of one org past their status limit, tested in SQL.

    A breach is whole minutes in status > limit, i.e. floor((now - entered) / 60s) >= limit + 1,
    i.e. entered <= now - (limit + 1) minutes. The threshold is computed per status here and the
    statuses OR-ed, so no interval arithmetic is needed in SQL (portable to SQLite and Postgres).
    A NULL status_entered_at never satisfies <=.
    """
    per_status = [
        and_(Order.status == key, Order.status_entered_at <= _threshold(now, st.time_limit_min))
        for key, st in limited.items()
    ]
    return select(Order.id, Order.order_number, Order.status, Order.status_entered_at).where(
        Order.org_id == org_id, Order.closed_at.is_(None), or_(*per_status))


def _breach(row, st: OrderStatus, now: datetime) -> dict:
    mins = _minutes_in_status(row.status_entered_at, now)
    return {
        "order_id": row.id,
        "order_number": row.order_number,
        "status": row.status,
        "status_name": st.name,
        "minutes_in_status": mins,
        "limit_min": st.time_limit_min,
        "over_by_min": mins - st.time_limit_min,
    }


async def find_breaches(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> list[dict]:
    """Return current SLA breaches for one org."""
    now = now or now_utc()
    limited = _limited(await _status_index(db, org_id))
    if not limited:
        return []
    rows = (await db.execute(_breach_query(org_id, limited, now))).all()
    return [_breach(r, limited[r.status], now) for r in rows]


async def sweep_breaches(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Record a one-time client-internal feed event for each new breach across
    all orgs. Returns the number of new events written.

    Only orgs with at least one status carrying a time limit are looked at, and orders already
    flagged since they entered their status are excluded in SQL, so the statement count depends
    on the number of such orgs, not on how many orders there are.
    """
    now = now or now_utc()
    orgs_with_limits = select(OrderStatus.org_id).where(
        OrderStatus.org_id.is_not(None),
        OrderStatus.time_limit_min.is_not(None), OrderStatus.time_limit_min != 0,
    ).distinct()
    by_org: dict[str, list] = {}
    for st in (await db.execute(
        select(OrderStatus).where(OrderStatus.org_id.in_(orgs_with_limits))
    )).scalars().all():
        by_org.setdefault(st.org_id, []).append(st)

    # dedupe: skip if we already flagged this order since it entered the status
    already_flagged = select(EventFeedItem.id).where(
        EventFeedItem.entity_type == "order",
        EventFeedItem.entity_id == Order.id,
        EventFeedItem.item_type == "system",
        EventFeedItem.body.like(f"{_SLA_MARKER}%"),
        EventFeedItem.created_at >= Order.status_entered_at,
    ).exists()

    written = 0
    for org_id, rows in by_org.items():
        limited = _limited(_index(rows))
        if not limited:
            continue
        for r in (await db.execute(_breach_query(org_id, limited, now).where(~already_flagged))).all():
            b = _breach(r, limited[r.status], now)
            db.add(EventFeedItem(
                org_id=org_id, entity_type="order", entity_id=r.id,
                item_type="system", visibility="internal",
                body=f"{_SLA_MARKER} SLA breach: {b['over_by_min']}m over the "
                     f"{b['limit_min']}m limit for status '{b['status_name']}'.",
                author_name="system",
            ))
            written += 1
    if written:
        await db.commit()
    return written
