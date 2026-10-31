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
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.models import AutomationRule, Task
from backend.services.dates import now_utc

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


async def _do_action(db, org_id, rule: AutomationRule, order, part) -> Optional[str]:
    """Perform the rule's action. Returns a short human summary for the feed."""
    try:
        cfg = json.loads(rule.action_config) if rule.action_config else {}
    except (ValueError, TypeError):
        cfg = {}
    at = rule.action_type

    if at == "create_task" and order is not None:
        title = cfg.get("title") or f"Follow up on {order.order_number}"
        db.add(Task(org_id=org_id, title=title, description=cfg.get("description"),
                    assigned_to_id=cfg.get("employee_id") or order.assigned_to_id,
                    order_id=order.id, client_id=order.client_id,
                    location_id=order.location_id, status="open"))
        return f"created task “{title}”"

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
        payload = {"event": rule.event, "rule": rule.name,
                   "order_id": getattr(order, "id", None),
                   "order_number": getattr(order, "order_number", None),
                   "status": getattr(order, "status", None)}
        try:
            # Build 144cq (SSRF fix): reject private/loopback/link-local hosts and cloud-metadata
            # addresses, then connect to the exact address that was checked (as tenant webhooks
            # do), so a DNS answer that changes in between can't point the call inside (audit M11).
            import asyncio

            from backend.core.ssrf import UnsafeURLError, pinned_target
            try:
                target, host_header, extensions = await asyncio.to_thread(pinned_target, cfg["url"])
            except UnsafeURLError as ue:
                return f"webhook blocked (unsafe url): {ue}"
            import httpx
            async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
                await client.post(target, json=payload, headers=host_header, extensions=extensions)
            return f"called webhook {cfg['url']}"
        except Exception as e:   # network best-effort — never fail the request
            return f"webhook {cfg['url']} failed: {type(e).__name__}"

    if at == "notify":
        msg = cfg.get("message") or "Automation notification"
        return f"notify: {msg}"

    return None


async def run(db: AsyncSession, org_id: str, event: str, *, order=None, part=None) -> dict:
    """Fire every matching active rule for `event`. Caller owns the commit."""
    if event not in VALID_EVENTS:
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
        summary = await _do_action(db, org_id, rule, order, part)
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
                except Exception:
                    pass
    return {"matched": len(rules), "fired": fired, "summaries": summaries}
