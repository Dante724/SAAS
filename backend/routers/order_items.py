"""Several items per job — the items on a work order (tools / equipment / device shops).

GET    /api/orders/{id}/items             list (+ whether the feature is available / on)
GET    /api/orders/{id}/items/candidates  the job customer's other items you could add
POST   /api/orders/{id}/items             add an item: a customer asset, or a quick label
PATCH  /api/orders/{id}/items/{item_id}   complaint, label, outcome, collected, order
DELETE /api/orders/{id}/items/{item_id}   remove (not item #1); its lines go back to "whole job"

Every query matches org_id AND order_id. Writes need the shop setting on (services/multi_asset)
and the "Create tickets" role permission.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order, OrderItem, OrderLine
from backend.services import multi_asset as ma
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/orders", tags=["order-items"])


async def _order(db: AsyncSession, current: Employee, order_id: str) -> Order:
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


def _asset_dict(d: Optional[Device]):
    if not d:
        return None
    return {"id": d.id, "brand": d.brand, "model": d.model, "serial": d.serial, "imei": d.imei,
            "asset_type": getattr(d, "asset_type", None), "client_id": d.client_id}


async def _list(db: AsyncSession, order: Order):
    items = (await db.execute(select(OrderItem).where(
        OrderItem.org_id == order.org_id, OrderItem.order_id == order.id)
        .order_by(OrderItem.sort_order, OrderItem.created_at))).scalars().all()
    asset_ids = [i.asset_id for i in items if i.asset_id]
    assets = {d.id: d for d in (await db.execute(select(Device).where(
        Device.org_id == order.org_id, Device.id.in_(asset_ids)))).scalars().all()} if asset_ids else {}
    lines = (await db.execute(select(OrderLine.order_item_id, OrderLine.total).where(
        OrderLine.order_id == order.id, OrderLine.order_item_id.isnot(None)))).all()
    totals: dict = {}
    for iid, tot in lines:
        n, s = totals.get(iid, (0, 0.0))
        totals[iid] = (n + 1, s + float(tot or 0))
    return [{"id": i.id, "asset_id": i.asset_id, "asset": _asset_dict(assets.get(i.asset_id)), "label": i.label,
             "complaint": i.complaint, "outcome": i.outcome or "open",
             "collected_at": i.collected_at.isoformat() if i.collected_at else None,
             "is_primary": bool(i.asset_id and i.asset_id == order.device_id), "sort_order": i.sort_order,
             "line_count": totals.get(i.id, (0, 0.0))[0], "total": round(totals.get(i.id, (0, 0.0))[1], 2)}
            for i in items]


@router.get("/{order_id}/items")
async def list_items(order_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    order = await _order(db, current, order_id)
    org = await ma.org_of(db, current.org_id)
    on = ma.enabled(org)
    if on:
        await ma.ensure_primary(db, order)
        await db.commit()
    return {"available": ma.available(org), "enabled": on, "items": await _list(db, order)}


@router.get("/{order_id}/items/candidates")
async def item_candidates(order_id: str, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    """The job customer's other items (this shop only) that aren't on the job yet."""
    order = await _order(db, current, order_id)
    if not order.client_id:
        return {"items": []}
    on_job = select(OrderItem.asset_id).where(OrderItem.org_id == current.org_id, OrderItem.order_id == order.id,
                                              OrderItem.asset_id.isnot(None))
    rows = (await db.execute(select(Device).where(
        Device.org_id == current.org_id, Device.client_id == order.client_id,
        Device.id.notin_(on_job), Device.id != (order.device_id or "")).limit(200))).scalars().all()
    return {"items": [_asset_dict(d) for d in rows]}


class ItemIn(BaseModel):
    asset_id: Optional[str] = None
    label: Optional[str] = Field(None, max_length=200)
    complaint: Optional[str] = Field(None, max_length=4000)


@router.post("/{order_id}/items", status_code=201)
async def add_item(order_id: str, data: ItemIn, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(require_perm("can_create_tickets"))):
    await ma.require_enabled(db, current.org_id)
    order = await _order(db, current, order_id)
    label = (data.label or "").strip() or None
    if not data.asset_id and not label:
        raise HTTPException(422, "Pick one of the customer's items or type what it is")
    if data.asset_id:
        asset = (await db.execute(select(Device).where(
            Device.id == data.asset_id, Device.org_id == current.org_id))).scalar_one_or_none()
        if not asset:
            raise HTTPException(422, "That item isn't in your shop")
        if order.client_id and asset.client_id and asset.client_id != order.client_id:
            raise HTTPException(422, "That item belongs to a different customer")
    await ma.ensure_primary(db, order)
    if data.asset_id and (await db.execute(select(OrderItem.id).where(
            OrderItem.org_id == current.org_id, OrderItem.order_id == order.id,
            OrderItem.asset_id == data.asset_id))).scalar_one_or_none():
        raise HTTPException(409, "That item is already on this job")
    n = await ma.count(db, current.org_id, order.id)
    if n >= ma.MAX_ITEMS:
        raise HTTPException(422, f"A job can hold up to {ma.MAX_ITEMS} items")
    it = OrderItem(org_id=current.org_id, order_id=order.id, asset_id=data.asset_id, label=label,
                   complaint=(data.complaint or "").strip() or None, sort_order=n)
    db.add(it)
    await db.commit()
    return {"ok": True, "id": it.id, "items": await _list(db, order)}


class ItemUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=200)
    complaint: Optional[str] = Field(None, max_length=4000)
    outcome: Optional[str] = None
    collected: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=10000)


@router.patch("/{order_id}/items/{item_id}")
async def update_item(order_id: str, item_id: str, data: ItemUpdate, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_create_tickets"))):
    await ma.require_enabled(db, current.org_id)
    order = await _order(db, current, order_id)
    it = await ma.item_for_order(db, current.org_id, order.id, item_id)
    p = data.model_dump(exclude_unset=True)
    if "outcome" in p:
        if p["outcome"] not in ma.OUTCOMES:
            raise HTTPException(422, f"Outcome must be one of {list(ma.OUTCOMES)}")
        it.outcome = p["outcome"]
    if "label" in p:
        it.label = (p["label"] or "").strip() or None
        if not it.asset_id and not it.label:
            raise HTTPException(422, "An item needs a name")
    if "complaint" in p:
        it.complaint = (p["complaint"] or "").strip() or None
    if "collected" in p:
        it.collected_at = datetime.utcnow() if p["collected"] else None
    if "sort_order" in p:
        it.sort_order = p["sort_order"]
    await db.commit()
    return {"ok": True, "items": await _list(db, order)}


@router.delete("/{order_id}/items/{item_id}")
async def remove_item(order_id: str, item_id: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_create_tickets"))):
    await ma.require_enabled(db, current.org_id)
    order = await _order(db, current, order_id)
    it = await ma.item_for_order(db, current.org_id, order.id, item_id)
    if it.asset_id and it.asset_id == order.device_id:
        raise HTTPException(409, "The job's main item can't be removed")
    await db.execute(update(OrderLine).where(OrderLine.order_id == order.id, OrderLine.order_item_id == it.id)
                     .values(order_item_id=None))
    await db.delete(it)
    await db.commit()
    return {"ok": True, "items": await _list(db, order)}
