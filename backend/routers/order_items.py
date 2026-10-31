"""Several items per job — the items on a work order (tools / equipment / device shops).

GET    /api/orders/{id}/items             list (+ whether the feature is available / on)
GET    /api/orders/{id}/items/candidates  the job customer's other items you could add
POST   /api/orders/{id}/items             add an item: a customer asset, or a quick label
PATCH  /api/orders/{id}/items/{item_id}   complaint, label, outcome, collected, order
DELETE /api/orders/{id}/items/{item_id}   remove (not item #1); its lines go back to "whole job"
POST   /api/orders/{id}/items/collect     "Collect & invoice these items": bill only their lines,
                                          mark them collected, leave the job open
POST   /api/orders/{id}/items/{item_id}/split   move the item + its lines to a new, linked job

Every query matches org_id AND order_id. Writes need the shop setting on (services/multi_asset)
and the "Create tickets" role permission.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order, OrderItem, OrderLine, OrderStatusLog, Recommendation, TimeEntry
from backend.services import multi_asset as ma
from backend.services.auth import get_current_employee, require_perm
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/orders", tags=["order-items"])


async def _order(db: AsyncSession, current: Employee, order_id: str) -> Order:
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    # M3: a role limited to its own jobs can't list, edit or invoice items on a colleague's job.
    from backend.services import scoping
    if not await scoping.can_view_order(db, current, o):
        raise HTTPException(403, "You can only act on work orders assigned to you")
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
    lines = (await db.execute(select(OrderLine.id, OrderLine.order_item_id, OrderLine.total).where(
        OrderLine.order_id == order.id, OrderLine.order_item_id.isnot(None)))).all()
    billed = await ma.invoiced_line_ids(db, order.org_id, order.id) if lines else set()
    totals: dict = {}
    unbilled: dict = {}
    for lid, iid, tot in lines:
        n, s = totals.get(iid, (0, 0.0))
        totals[iid] = (n + 1, s + float(tot or 0))
        if lid not in billed:
            c, t = unbilled.get(iid, (0, 0.0))
            unbilled[iid] = (c + 1, t + float(tot or 0))
    return [{"id": i.id, "asset_id": i.asset_id, "asset": _asset_dict(assets.get(i.asset_id)), "label": i.label,
             "complaint": i.complaint, "outcome": i.outcome or "open",
             "collected_at": i.collected_at.isoformat() if i.collected_at else None,
             "is_primary": bool(i.asset_id and i.asset_id == order.device_id), "sort_order": i.sort_order,
             "line_count": totals.get(i.id, (0, 0.0))[0], "total": round(totals.get(i.id, (0, 0.0))[1], 2),
             "unbilled_lines": unbilled.get(i.id, (0, 0.0))[0],
             "unbilled_total": round(unbilled.get(i.id, (0, 0.0))[1], 2)}
            for i in items]


async def _job_unbilled(db: AsyncSession, order: Order) -> float:
    """Whole-job lines (no item) not on an invoice yet."""
    rows = (await db.execute(select(OrderLine.id, OrderLine.total).where(
        OrderLine.order_id == order.id, OrderLine.order_item_id.is_(None)))).all()
    if not rows:
        return 0.0
    billed = await ma.invoiced_line_ids(db, order.org_id, order.id)
    return round(sum(float(t or 0) for i, t in rows if i not in billed), 2)


@router.get("/{order_id}/items")
async def list_items(order_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    order = await _order(db, current, order_id)
    org = await ma.org_of(db, current.org_id)
    on = ma.enabled(org)
    if on:
        await ma.ensure_primary(db, order)
        await db.commit()
    if not ma.available(org):   # other industries never see items, not even leftovers
        return {"available": False, "enabled": False, "items": [], "split_from": None, "split_off": [], "job_unbilled": 0.0}
    return {"available": ma.available(org), "enabled": on, "items": await _list(db, order),
            **(await _links(db, order))}


async def _links(db: AsyncSession, order: Order) -> dict:
    """Jobs linked by "Split into its own job" — this shop's only."""
    src = None
    if order.split_from_order_id:
        row = (await db.execute(select(Order.id, Order.order_number).where(
            Order.id == order.split_from_order_id, Order.org_id == order.org_id))).first()
        src = {"id": row[0], "order_number": row[1]} if row else None
    kids = (await db.execute(select(Order.id, Order.order_number).where(
        Order.org_id == order.org_id, Order.split_from_order_id == order.id)
        .order_by(Order.created_at))).all()
    return {"split_from": src, "split_off": [{"id": i, "order_number": n} for i, n in kids],
            "job_unbilled": await _job_unbilled(db, order)}


@router.get("/{order_id}/items/candidates")
async def item_candidates(order_id: str, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    """The job customer's other items (this shop only) that aren't on the job yet."""
    order = await _order(db, current, order_id)
    if not order.client_id or not ma.enabled(await ma.org_of(db, current.org_id)):
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
        it.collected_at = now_utc() if p["collected"] else None
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
    await db.execute(update(Recommendation).where(
        Recommendation.org_id == current.org_id, Recommendation.order_id == order.id,
        Recommendation.order_item_id == it.id).values(order_item_id=None))
    await db.delete(it)
    await db.commit()
    return {"ok": True, "items": await _list(db, order)}


class CollectIn(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=ma.MAX_ITEMS)
    # Whole-job lines (not tagged to an item): None = bill them only with the last items to go.
    include_job_lines: Optional[bool] = None


@router.post("/{order_id}/items/collect", status_code=201)
async def collect_items(order_id: str, data: CollectIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_create_invoices"))):
    """The customer takes some items now: one invoice for just those items' lines (not yet
    billed), the items marked collected, and the job stays open for the rest."""
    await ma.require_enabled(db, current.org_id)
    order = await _order(db, current, order_id)
    await ma.ensure_primary(db, order)
    ids = list(dict.fromkeys(data.item_ids))
    chosen = [await ma.item_for_order(db, current.org_id, order.id, i) for i in ids]
    every = (await db.execute(select(OrderItem).where(
        OrderItem.org_id == current.org_id, OrderItem.order_id == order.id))).scalars().all()
    last_out = all(i.collected_at or i.id in ids for i in every)
    with_job = last_out if data.include_job_lines is None else data.include_job_lines
    billed = await ma.invoiced_line_ids(db, current.org_id, order.id)
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    to_bill = [l for l in lines if l.id not in billed and
               ((l.order_item_id in ids) or (with_job and not l.order_item_id))]
    inv = await ma.bill_lines(db, order, to_bill) if to_bill else None
    now = now_utc()
    for it in chosen:
        it.collected_at = it.collected_at or now
    names = await ma.item_names(db, current.org_id, ids)
    from backend.routers.feed import log_system_event
    what = ", ".join(names[i][0] for i in ids if i in names)
    await log_system_event(db, current.org_id, "order", order.id,
                           f"Collected {what}" + (f" — invoice {inv.invoice_number}" if inv else " — nothing to bill"))
    await db.commit()
    return {"ok": True, "invoice": ({"id": inv.id, "invoice_number": inv.invoice_number, "total": inv.total}
                                    if inv else None),
            "line_count": len(to_bill), "items": await _list(db, order)}


@router.post("/{order_id}/items/{item_id}/split", status_code=201)
async def split_item(order_id: str, item_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_create_tickets"))):
    """Move one item and the lines tagged to it onto a new job for the same customer, linked
    back to this one. Its recommendations and logged time on those lines go with it."""
    from backend.routers.feed import log_system_event
    from backend.routers.orders import _guard_editable, _recalc_order, unique_public_token
    from backend.services.audit import record as _audit
    from backend.services.numbering import next_number
    await ma.require_enabled(db, current.org_id)
    order = await _order(db, current, order_id)
    await _guard_editable(db, order, current)
    it = await ma.item_for_order(db, current.org_id, order.id, item_id)
    if it.asset_id and it.asset_id == order.device_id:
        raise HTTPException(409, "The job's main item stays on this job — split off the others")
    if it.collected_at:
        raise HTTPException(409, "That item was already collected")
    if await ma.count(db, current.org_id, order.id) < 2:
        raise HTTPException(409, "It's the only item on this job")
    line_ids = (await db.execute(select(OrderLine.id).where(
        OrderLine.order_id == order.id, OrderLine.order_item_id == it.id))).scalars().all()
    if set(line_ids) & await ma.invoiced_line_ids(db, current.org_id, order.id):
        raise HTTPException(409, "That item's work is already on an invoice")

    number = await next_number(db, current.org_id, "order")
    if order.is_estimate:
        number = "E" + number[3:]
    status = "draft" if order.is_estimate else "new"
    name = (await ma.item_names(db, current.org_id, [it.id])).get(it.id, ("Item", 0))[0]
    new = Order(order_number=number, org_id=current.org_id, public_token=await unique_public_token(db),
                client_id=order.client_id, device_id=it.asset_id, location_id=order.location_id,
                priority=order.priority, complaint=it.complaint or order.complaint,
                assigned_to_id=order.assigned_to_id, manager_id=order.manager_id, type_id=order.type_id,
                template_key=order.template_key, status=status, is_estimate=order.is_estimate,
                due_at=order.due_at, currency_code=order.currency_code, fx_rate=order.fx_rate,
                split_from_order_id=order.id)
    db.add(new)
    await db.flush()
    db.add(OrderStatusLog(order_id=new.id, from_status=None, to_status=status, changed_by_id=current.id))
    it.order_id, it.sort_order = new.id, 0
    if line_ids:
        await db.execute(update(OrderLine).where(OrderLine.order_id == order.id, OrderLine.id.in_(line_ids))
                         .values(order_id=new.id))
        await db.execute(update(TimeEntry).where(TimeEntry.org_id == current.org_id, TimeEntry.order_id == order.id,
                                                 TimeEntry.order_line_id.in_(line_ids)).values(order_id=new.id))
    await db.execute(update(Recommendation).where(
        Recommendation.org_id == current.org_id, Recommendation.order_id == order.id,
        Recommendation.order_item_id == it.id).values(order_id=new.id))
    await db.flush()
    db.expire(order, ["lines"])
    await _recalc_order(db, order)
    await _recalc_order(db, new)
    await _audit(db, org_id=current.org_id, actor_id=current.id, action="order.split_item",
                 entity_type="order", entity_id=new.id,
                 diff={"from": order.order_number, "to": new.order_number, "item": name})
    await log_system_event(db, current.org_id, "order", order.id, f"Split {name} into its own job {new.order_number}")
    await log_system_event(db, current.org_id, "order", new.id, f"Split from {order.order_number} ({name})")
    await db.commit()
    return {"ok": True, "id": new.id, "order_number": new.order_number, "line_count": len(line_ids),
            "items": await _list(db, order), **(await _links(db, order))}
