"""Build 144w — workflow automation rules API.

CRUD for AutomationRule plus a time-based sweep (overdue orders / low stock)
that a scheduled task can hit, and a dry-run tester so users can see which
orders a rule would fire on before enabling it.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.database import get_db
from backend.models import AutomationRule, Employee, Order, Part, StockLevel
from backend.services.auth import get_current_employee, require_perm
from backend.services import automation as auto

router = APIRouter(prefix="/api/automations", tags=["automations"])

# what the editor advertises
EVENTS = [
    ("order_created", "Work order created"),
    ("status_changed", "Status changed to…"),
    ("estimate_approved", "Estimate approved"),
    ("payment_received", "Payment received"),
    ("order_overdue", "Work order overdue"),
    ("low_stock", "Product low on stock"),
]
ACTIONS = [
    ("create_task", "Create a task"),
    ("assign", "Assign a technician"),
    ("add_tag", "Add a tag to the order"),
    ("notify", "Send a notification"),
    ("webhook", "Call a webhook"),
]
CONDITION_FIELDS = ["status", "priority", "total", "location_id", "type_id", "is_estimate"]


def _ser(r: AutomationRule) -> dict:
    def _load(s):
        try:
            return json.loads(s) if s else None
        except (ValueError, TypeError):
            return None
    return {
        "id": r.id, "name": r.name, "event": r.event, "trigger_status": r.trigger_status,
        "conditions": _load(r.conditions) or [], "action_type": r.action_type,
        "action_config": _load(r.action_config) or {}, "active": r.active,
        "sort_order": r.sort_order, "fire_count": r.fire_count or 0,
        "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None,
    }


class RuleIn(BaseModel):
    name: str
    event: str
    trigger_status: Optional[str] = None
    conditions: list = []
    action_type: str
    action_config: dict = {}
    active: bool = True
    sort_order: int = 0


def _validate(data: RuleIn):
    if data.event not in auto.VALID_EVENTS:
        raise HTTPException(422, f"event must be one of {sorted(auto.VALID_EVENTS)}")
    if data.action_type not in auto.VALID_ACTIONS:
        raise HTTPException(422, f"action_type must be one of {sorted(auto.VALID_ACTIONS)}")


@router.get("/meta")
async def meta(current: Employee = Depends(get_current_employee)):
    return {"events": [{"key": k, "label": v} for k, v in EVENTS],
            "actions": [{"key": k, "label": v} for k, v in ACTIONS],
            "condition_fields": CONDITION_FIELDS, "ops": sorted(auto.VALID_OPS)}


@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(AutomationRule).where(
        AutomationRule.org_id == current.org_id)
        .order_by(AutomationRule.sort_order, AutomationRule.created_at))).scalars().all()
    return {"items": [_ser(r) for r in rows]}


@router.post("", status_code=201)
async def create_rule(data: RuleIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    _validate(data)
    r = AutomationRule(
        org_id=current.org_id, name=data.name, event=data.event,
        trigger_status=data.trigger_status,
        conditions=json.dumps(data.conditions) if data.conditions else None,
        action_type=data.action_type,
        action_config=json.dumps(data.action_config) if data.action_config else None,
        active=data.active, sort_order=data.sort_order)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id}


@router.put("/{rule_id}")
async def update_rule(rule_id: str, data: RuleIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    _validate(data)
    r = (await db.execute(select(AutomationRule).where(
        AutomationRule.id == rule_id, AutomationRule.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    r.name = data.name
    r.event = data.event
    r.trigger_status = data.trigger_status
    r.conditions = json.dumps(data.conditions) if data.conditions else None
    r.action_type = data.action_type
    r.action_config = json.dumps(data.action_config) if data.action_config else None
    r.active = data.active
    r.sort_order = data.sort_order
    await db.commit()
    return {"ok": True}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    r = (await db.execute(select(AutomationRule).where(
        AutomationRule.id == rule_id, AutomationRule.org_id == current.org_id))).scalar_one_or_none()
    if r:
        await db.delete(r)
        await db.commit()
    return {"ok": True}


@router.get("/{rule_id}/test")
async def test_rule(rule_id: str, db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    """Dry run: how many current orders would this rule's conditions match?"""
    r = (await db.execute(select(AutomationRule).where(
        AutomationRule.id == rule_id, AutomationRule.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.archived == False).limit(500))).scalars().all()  # noqa: E712
    matched = []
    for o in orders:
        if r.event == "status_changed" and r.trigger_status and o.status != r.trigger_status:
            continue
        if auto.conditions_met(r.conditions, o):
            matched.append({"order_number": o.order_number, "status": o.status})
    return {"would_match": len(matched), "sample": matched[:10]}


@router.post("/sweep")
async def sweep(db: AsyncSession = Depends(get_db),
                current: Employee = Depends(require_perm("can_manage_settings"))):
    """Fire time-based automations: overdue work orders and low-stock products.
    Intended to be called on a schedule (e.g. hourly)."""
    now = datetime.utcnow()
    fired = 0

    # overdue orders: past their promised time (due_at or scheduled_to) and still open
    overdue = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.archived == False,  # noqa: E712
        Order.closed_at == None,  # noqa: E711
        or_(and_(Order.due_at != None, Order.due_at < now),          # noqa: E711
            and_(Order.scheduled_to != None, Order.scheduled_to < now)))  # noqa: E711
        .options(selectinload(Order.client)).limit(500))).scalars().all()
    for o in overdue:
        res = await auto.run(db, current.org_id, "order_overdue", order=o)
        fired += res["fired"]

    # low stock: available qty at/below a product's min_stock
    low = (await db.execute(
        select(Part, func.coalesce(func.sum(StockLevel.qty_available), 0).label("qty"))
        .join(StockLevel, StockLevel.part_id == Part.id, isouter=True)
        .where(Part.org_id == current.org_id, Part.active == True)  # noqa: E712
        .group_by(Part.id))).all()
    for part, qty in low:
        if (part.min_stock or 0) > 0 and (qty or 0) <= part.min_stock:
            res = await auto.run(db, current.org_id, "low_stock", part=part)
            fired += res["fired"]

    await db.commit()
    return {"fired": fired}
