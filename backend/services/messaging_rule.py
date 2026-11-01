"""Can the customer message the shop about this order right now?

One rule shared by the track page, the customer portal and both message endpoints:

- The shop switch (``Organisation.oe_allow_messages``) turns messaging off everywhere.
- Each status may say yes or no (``OrderStatus.allow_messages``). Left unset, open statuses
  allow messages and closed / dropped ones follow the grace window below.
- After an order closes, messages stay open for ``oe_msg_days_after_close`` days (default 14)
  so warranty questions still reach the shop. 0 closes messaging the moment the order closes.

When messaging is closed for an order (not for the whole shop) the customer is offered
"Start a new request" instead, which files an inquiry linked to them.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from backend.services.dates import now_utc

DEFAULT_DAYS_AFTER_CLOSE = 14
_FALLBACK_TERMINAL = ("done", "delivered", "dropped", "closed", "canceled")


async def _status_row(db, order):
    from backend.models import OrderStatus
    rt = "estimate" if order.is_estimate else "work_order"
    return (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == order.org_id, OrderStatus.key == order.status,
        OrderStatus.record_type == rt))).scalars().first()


async def messaging_state(db, order, org, now: Optional[datetime] = None) -> dict:
    """{"open": bool, "until": iso | None, "reason": None | "off" | "status" | "expired"}."""
    if org is not None and not getattr(org, "oe_allow_messages", True):
        return {"open": False, "until": None, "reason": "off"}
    s = await _status_row(db, order)
    explicit = getattr(s, "allow_messages", None) if s is not None else None
    terminal = bool(s.is_terminal) if s is not None else order.status in _FALLBACK_TERMINAL
    if explicit is True:
        return {"open": True, "until": None, "reason": None}
    if explicit is False:
        return {"open": False, "until": None, "reason": "status"}
    if not terminal:
        return {"open": True, "until": None, "reason": None}
    days = getattr(org, "oe_msg_days_after_close", None)
    days = DEFAULT_DAYS_AFTER_CLOSE if days is None else max(0, int(days))
    closed = order.closed_at or getattr(order, "status_entered_at", None)
    if days and closed:
        until = closed + timedelta(days=days)
        if (now or now_utc()) < until:
            return {"open": True, "until": until.isoformat(), "reason": None}
    return {"open": False, "until": None, "reason": "expired"}
