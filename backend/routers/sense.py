"""Tekly Sense API — see backend/services/sense.py.

Settings: anyone signed in can read them (the app needs to know what to show); only the
shop owner can change them. Suggestions need `can_use_sense_suggestions`; the margin check
needs see-profit and purchase-price; every order route checks the order is in the caller's
shop and visible to them.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order
from backend.services import audit, scoping
from backend.services import sense
from backend.services.auth import get_current_employee, has_perm, is_shop_owner, require_perm

router = APIRouter(prefix="/api/sense", tags=["sense"])


@router.get("/settings")
async def read_settings(db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    out = await sense.get_settings(db, current.org_id)
    if await is_shop_owner(db, current):         # the learning count is for the owner's settings page
        out["learning"] = await sense.learning(db, current.org_id)
    return out


class FeaturesIn(BaseModel):
    job_suggestions: Optional[bool] = None
    deferred: Optional[bool] = None
    ready_time_why: Optional[bool] = None
    margin_check: Optional[bool] = None
    morning_brief: Optional[bool] = None
    added_together: Optional[bool] = None
    service_due: Optional[bool] = None


class BriefIn(BaseModel):
    recipients: Optional[list[str]] = Field(None, max_length=50)
    hour: Optional[int] = Field(None, ge=0, le=23)
    email: Optional[bool] = None
    push: Optional[bool] = None


class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    features: Optional[FeaturesIn] = None
    brief: Optional[BriefIn] = None


@router.put("/settings")
async def update_settings(data: SettingsIn, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_manage_settings"))):
    if not await is_shop_owner(db, current):      # and, beyond the role: the owner only
        raise HTTPException(403, "Only the shop owner can change Tekly Sense")
    brief = data.brief.model_dump(exclude_none=True) if data.brief else None
    if brief and "recipients" in brief:
        wanted = list(dict.fromkeys(brief["recipients"]))
        rows = (await db.execute(select(Employee.id, Employee.active).where(
            Employee.org_id == current.org_id, Employee.id.in_(wanted)))).all() if wanted else []
        if len(rows) != len(wanted):
            raise HTTPException(422, "Brief recipients must be people in this shop")
        active = {i for i, a in rows if a}
        brief["recipients"] = [i for i in wanted if i in active]   # people who left drop off quietly
    before = await sense.get_settings(db, current.org_id)
    after = await sense.save_settings(db, current.org_id, enabled=data.enabled,
                                      features=data.features.model_dump(exclude_none=True) if data.features else None,
                                      brief=brief)
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="sense.settings.update",
                       entity_type="organisation", entity_id=current.org_id,
                       diff={"before": before, "after": after})
    await db.commit()
    return {**after, "learning": await sense.learning(db, current.org_id)}


class DraftIn(BaseModel):
    complaint: str = Field(max_length=4000)
    device_id: Optional[str] = Field(None, max_length=64)


@router.post("/draft")
async def draft(data: DraftIn, db: AsyncSession = Depends(get_db),
                current: Employee = Depends(require_perm("can_use_sense_suggestions"))):
    if not await sense.is_on(db, current.org_id, "job_suggestions"):
        return {"enabled": False, "learning": False, "items": []}
    device = None
    if data.device_id:
        device = (await db.execute(select(Device).where(
            Device.id == data.device_id, Device.org_id == current.org_id))).scalar_one_or_none()
    out = await sense.draft_lines(db, current.org_id, data.complaint, device)
    if not await has_perm(db, current, "can_view_item_prices"):
        for i in out["items"]:
            i.pop("unit_price", None)
    return {"enabled": True, **out}


async def _order(db: AsyncSession, current: Employee, order_id: str) -> Order:
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    if not await scoping.can_view_order(db, current, o):
        raise HTTPException(403, "You can only open work orders assigned to you")
    return o


@router.get("/orders/{order_id}/deferred")
async def deferred(order_id: str, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(require_perm("can_use_sense_suggestions"))):
    o = await _order(db, current, order_id)
    if not await sense.is_on(db, current.org_id, "deferred"):
        return {"enabled": False, "items": []}
    items = await sense.deferred_for(db, o, can_view=lambda src: scoping.can_view_order(db, current, src))
    if not await has_perm(db, current, "can_view_item_prices"):
        for i in items:
            i.pop("unit_price", None)
    return {"enabled": True, "items": items}


@router.get("/orders/{order_id}/margin")
async def margin(order_id: str, db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(get_current_employee)):
    # Checked here, not only by the response redaction: margin is a profit number.
    if not (await has_perm(db, current, "can_see_profit")
            and await has_perm(db, current, "can_view_purchase_price")):
        raise HTTPException(403, "You don't have permission to see profit")
    o = await _order(db, current, order_id)
    if not await sense.is_on(db, current.org_id, "margin_check"):
        return {"enabled": False, "flag": False}
    return {"enabled": True, **await sense.margin_check(db, o)}


@router.get("/brief")
async def brief(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    """Today's brief for the owner and the people the owner chose; nobody else."""
    if not await is_shop_owner(db, current):
        s = await sense.get_settings(db, current.org_id)
        if current.id not in (s["brief"]["recipients"] or []):
            raise HTTPException(403, "The morning brief is for the people the owner chose")
    if not await sense.is_on(db, current.org_id, "morning_brief"):
        return {"enabled": False}
    money = await has_perm(db, current, "can_view_finance")
    return {"enabled": True, **await sense.build_brief(db, current.org_id, show_money=money)}


def _strip_prices(items: list, allowed: bool) -> list:
    if not allowed:
        for i in items:
            i.pop("unit_price", None)
    return items


@router.get("/orders/{order_id}/together")
async def together(order_id: str, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(require_perm("can_use_sense_suggestions"))):
    o = await _order(db, current, order_id)
    if not await sense.is_on(db, current.org_id, "added_together"):
        return {"enabled": False, "items": []}
    items = await sense.together_for(db, o)
    return {"enabled": True, "items": _strip_prices(items, await has_perm(db, current, "can_view_item_prices"))}


@router.get("/orders/{order_id}/service-due")
async def service_due(order_id: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_use_sense_suggestions"))):
    o = await _order(db, current, order_id)
    if not await sense.is_on(db, current.org_id, "service_due"):
        return {"enabled": False, "items": []}
    items = await sense.service_due_for(db, o)
    return {"enabled": True, "items": _strip_prices(items, await has_perm(db, current, "can_view_item_prices"))}
