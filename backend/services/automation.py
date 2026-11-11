"""Build 144w — workflow automation engine.

WHEN an event fires (and optional conditions match) THEN run an action:
create a task, assign a technician, tag the order, call a webhook, or log a
notification intent. This complements the notification-rules engine (which owns
delayed / working-hours / reminder *messaging*); here we do the non-message
"do something" actions Orderry's automation offers.

The engine is best-effort: an action that raises is logged and skipped, never
breaking the request that triggered it. Callers pass an already-loaded Order.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.models import AutomationFire, AutomationRule
from backend.services.dates import now_utc

log = logging.getLogger("tekly.automation")

VALID_EVENTS = {
    "order_created", "status_changed", "estimate_approved",
    "payment_received", "order_overdue", "low_stock",
}
VALID_ACTIONS = {"create_task", "assign", "add_tag", "webhook", "notify"}
VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"}


def _order_field(order, field):
    if order is None:
        return None
    return {
        "status": order.status,
        "priority": order.priority,
        "total": float(order.total or 0),
        "location_id": order.location_id,
        "type_id": order.type_id,
        "client_id": order.client_id,
        "is_estimate": bool(order.is_estimate),
        "assigned_to_id": order.assigned_to_id,
    }.get(field)


def _cmp(actual, op, expected):
    # numeric comparison where both sides look numeric
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    an, en = num(actual), num(expected)
    if op == "eq":
        return str(actual) == str(expected) if an is None or en is None else an == en
    if op == "ne":
        return str(actual) != str(expected) if an is None or en is None else an != en
    if op in ("gt", "gte", "lt", "lte"):
        if an is None or en is None:
            return False
        return {"gt": an > en, "gte": an >= en, "lt": an < en, "lte": an <= en}[op]
    if op == "contains":
        return str(expected).lower() in str(actual or "").lower()
    if op == "in":
        vals = expected if isinstance(expected, list) else str(expected).split(",")
        return str(actual) in [str(v).strip() for v in vals]
    return False


def conditions_met(conditions_json: Optional[str], order) -> bool:
    try:
        conds = json.loads(conditions_json) if conditions_json else []
    except (ValueError, TypeError):
        return True
    if not isinstance(conds, list):
        return True
    for c in conds:
        field, op, val = c.get("field"), c.get("op"), c.get("value")
        if op not in VALID_OPS:
            continue
        if not _cmp(_order_field(order, field), op, val):
            return False
    return True


def task_source_ref(rule: AutomationRule, order) -> str:
    """The de-duplication key for a rule's task about one order: while that task is open, the
    same rule never creates a second one for the same status entry / overdue spell."""
    if rule.event == "status_changed":
        what = f"status:{order.status}"
    elif rule.event == "order_overdue":
        what = "overdue"
    else:
        what = rule.event
    return f"order:{order.id}:{what}:rule:{rule.id}"


async def _do_action(db, org_id, rule: AutomationRule, order, part) -> Optional[str]:
    """Perform the rule's action. Returns a short human summary for the feed."""
    try:
        cfg = json.loads(rule.action_config) if rule.action_config else {}
    except (ValueError, TypeError):
        cfg = {}
    at = rule.action_type

    if at == "create_task" and order is not None:
        title = cfg.get("title") or f"Follow up on {order.order_number}"
        from backend.services.auto_tasks import ensure_task
        _, created = await ensure_task(
            db, org_id=org_id, source="automation", source_ref=task_source_ref(rule, order),
            title=title, description=cfg.get("description"),
            assigned_to_id=cfg.get("employee_id") or order.assigned_to_id,
            order_id=order.id, client_id=order.client_id, location_id=order.location_id)
        return f"created task “{title}”" if created else None

    if at == "assign" and order is not None and cfg.get("employee_id"):
        order.assigned_to_id = cfg["employee_id"]
        return "assigned technician"

    if at == "add_tag" and order is not None and cfg.get("tag"):
        data = dict(order.custom_data or {})
        tags = list(data.get("tags") or [])
        if cfg["tag"] not in tags:
            tags.append(cfg["tag"])
        data["tags"] = tags
        order.custom_data = data
        flag_modified(order, "custom_data")
        return f"tagged “{cfg['tag']}”"

    if at == "webhook" and cfg.get("url"):
        # B2: through the signed outbox, like webhook endpoints: delivered after commit, pinned
        # to the IP that passed the SSRF check, retried on the backoff schedule, and logged.
        # The summary stays generic (B11): no addresses or exception names in the feed.
        from backend.services.webhooks import queue_automation_webhook
        try:
            return await queue_automation_webhook(db, org_id, rule, {
                "_url": cfg["url"], "rule": rule.name, "rule_id": rule.id, "trigger": rule.event,
                "order_id": getattr(order, "id", None),
                "order_number": getattr(order, "order_number", None),
                "status": getattr(order, "status", None)})
        except Exception:  # noqa: BLE001 — never fail the request that fired the rule
            import logging
            logging.getLogger("tekly.automation").exception("automation webhook could not be queued")
            return "webhook could not be queued"

    if at == "notify":
        msg = cfg.get("message") or "Automation notification"
        return f"notify: {msg}"

    return None


async def run(db: AsyncSession, org_id: str, event: str, *, order=None, part=None,
              once_key: Optional[str] = None, occurred_at: Optional[datetime] = None) -> dict:
    """Fire every matching active rule for `event`. Caller owns the commit.

    Time-based sweeps pass `once_key` (e.g. "order:<id>:overdue:<since>"): a rule fires at most
    once per key (recorded in automation_fires), so an hourly sweep never re-fires it. They may
    also pass `occurred_at` (when the subject became due): rules that went live after that moment
    skip it, so switching a rule on doesn't fire it at the whole historical backlog."""
    if event not in VALID_EVENTS:
        return {"matched": 0, "fired": 0}
    from backend.services.billing import org_has_feature
    if not await org_has_feature(db, org_id, "notification_rules"):   # Shop plan and up
        return {"matched": 0, "fired": 0}
    rules = (await db.execute(select(AutomationRule).where(
        AutomationRule.org_id == org_id, AutomationRule.event == event,
        AutomationRule.active == True).order_by(AutomationRule.sort_order))).scalars().all()  # noqa: E712
    fired = 0
    summaries = []
    for rule in rules:
        if event == "status_changed" and rule.trigger_status:
            if not order or (order.status != rule.trigger_status):
                continue
        if order is not None and not conditions_met(rule.conditions, order):
            continue
        if occurred_at is not None:
            live_since = rule.activated_at or rule.created_at
            if live_since and occurred_at < live_since:
                continue
        if once_key:
            seen = (await db.execute(select(AutomationFire.id).where(
                AutomationFire.rule_id == rule.id,
                AutomationFire.subject_ref == once_key[:200]).limit(1))).scalar_one_or_none()
            if seen:
                continue
        try:
            summary = await _do_action(db, org_id, rule, order, part)
        except Exception:  # noqa: BLE001 — one broken rule never breaks the request or the sweep
            log.exception("automation rule %s (%s) failed", rule.id, rule.action_type)
            continue
        if once_key:
            db.add(AutomationFire(org_id=org_id, rule_id=rule.id, subject_ref=once_key[:200]))
        rule.fire_count = (rule.fire_count or 0) + 1
        rule.last_fired_at = now_utc()
        fired += 1
        if summary:
            summaries.append(summary)
            if order is not None:
                try:
                    from backend.routers.feed import log_system_event
                    await log_system_event(db, org_id, "order", order.id,
                                           f"Automation “{rule.name}”: {summary}")
                except Exception:  # noqa: BLE001
                    log.warning("automation feed line failed for order %s", order.id, exc_info=True)
    return {"matched": len(rules), "fired": fired, "summaries": summaries}


TIME_EVENTS = ("order_overdue", "low_stock")


async def sweep_time_based(db: AsyncSession, org_id: str, *, backlog: bool = False) -> int:
    """Fire the time-based automations (overdue work orders, low stock) for one shop.

    Idempotent: each rule fires once per episode — an order once per overdue spell (keyed on the
    moment it became overdue, so moving the promised date starts a new spell), a part once per
    low-stock dip (the marker is cleared when stock recovers). Without `backlog` (the hourly
    scheduled run) a rule only fires for orders that became overdue after the rule went live;
    `backlog=True` (the manual "run now" endpoint) also covers older ones. Commits."""
    from sqlalchemy import and_, delete, func, or_
    from sqlalchemy.orm import selectinload

    from backend.models import Order, Part, StockLevel
    rules = (await db.execute(select(AutomationRule.event).where(
        AutomationRule.org_id == org_id, AutomationRule.active == True,  # noqa: E712
        AutomationRule.event.in_(TIME_EVENTS)).distinct())).scalars().all()
    if not rules:
        return 0
    now = now_utc()
    fired = 0

    if "order_overdue" in rules:
        # past their promised time (due_at or scheduled_to) and still open
        overdue = (await db.execute(select(Order).where(
            Order.org_id == org_id, Order.archived == False,  # noqa: E712
            Order.closed_at == None,  # noqa: E711
            or_(and_(Order.due_at != None, Order.due_at < now),          # noqa: E711
                and_(Order.scheduled_to != None, Order.scheduled_to < now)))  # noqa: E711
            .options(selectinload(Order.client)).limit(500))).scalars().all()
        for o in overdue:
            since = min(d for d in (o.due_at, o.scheduled_to) if d is not None and d < now)
            res = await run(db, org_id, "order_overdue", order=o,
                            once_key=f"order:{o.id}:overdue:{since.isoformat()}",
                            occurred_at=None if backlog else since)
            fired += res["fired"]

    if "low_stock" in rules:
        # available qty at/below a product's min_stock
        rows = (await db.execute(
            select(Part, func.coalesce(func.sum(StockLevel.qty_available), 0).label("qty"))
            .join(StockLevel, StockLevel.part_id == Part.id, isouter=True)
            .where(Part.org_id == org_id, Part.active == True)  # noqa: E712
            .group_by(Part.id))).all()
        recovered = []
        for part, qty in rows:
            if (part.min_stock or 0) > 0 and (qty or 0) <= part.min_stock:
                res = await run(db, org_id, "low_stock", part=part, once_key=f"part:{part.id}:low")
                fired += res["fired"]
            else:
                recovered.append(f"part:{part.id}:low")
        if recovered:   # stock is back: the next dip is a new episode
            for i in range(0, len(recovered), 500):
                await db.execute(delete(AutomationFire).where(
                    AutomationFire.org_id == org_id,
                    AutomationFire.subject_ref.in_(recovered[i:i + 500])))

    await db.commit()
    return fired
