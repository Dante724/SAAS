"""Purchase orders linked to work orders (ADR-144po).

A PO is linked to a job when it was raised from it (`PurchaseOrder.source_order_id`) or when
one of its lines is earmarked for it (`POLine.reserved_order_id`). Two things follow the link:

* `apply_receipt_to_orders` — goods that arrive clear the job's pending stock for that part
  straight away (the same posting as the manual "write off pending stock" button).
* `notify_po_change` — the job's feed gets a line and its assignee and manager get an
  in-app notification whenever the PO is sent, received, cancelled or re-dated.
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Order, OrderLine, OrderStatus, POLine, PurchaseOrder, StockLevel, StockMovement

KIND_PO_UPDATE = "inventory.po_update"
_CLOSED = {"closed", "done", "delivered", "dropped", "cancelled", "completed", "picked_up"}


async def _closed_keys(db: AsyncSession, org_id: str) -> set[str]:
    keys = set(_CLOSED)
    for (k,) in (await db.execute(select(OrderStatus.key).where(
            OrderStatus.org_id == org_id, OrderStatus.status_group.in_(["closed", "dropped"])))).all():
        if k:
            keys.add(k)
    return keys


async def linked_order_ids(db: AsyncSession, po: PurchaseOrder) -> list[str]:
    ids = [po.source_order_id] if po.source_order_id else []
    for (oid,) in (await db.execute(select(POLine.reserved_order_id).where(
            POLine.po_id == po.id, POLine.reserved_order_id.is_not(None)))).all():
        if oid not in ids:
            ids.append(oid)
    return ids


async def _live_order(db, org_id, order_id, closed):
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not o or o.is_estimate or o.archived or (o.status or "") in closed:
        return None
    return o


async def writes_off_automatically(db: AsyncSession, org_id: str) -> bool:
    """Settings → Inventory → "Parts that arrive for a job": automatic (default) or manual."""
    from backend.models import Organisation
    mode = (await db.execute(select(Organisation.inv_po_to_job).where(Organisation.id == org_id))).scalar_one_or_none()
    return (mode or "auto") != "manual"


async def apply_receipt_to_orders(db: AsyncSession, org_id: str, po: PurchaseOrder, pl: POLine,
                                  received: float) -> float:
    """Write off the job's pending stock for this part from goods just received. Returns the
    units used, so the caller only reserves / frees what's left. The line's own job goes first,
    then the job the PO was raised from."""
    if received <= 0 or not await writes_off_automatically(db, org_id):
        return 0.0
    targets = [oid for oid in (pl.reserved_order_id, po.source_order_id) if oid]
    targets = list(dict.fromkeys(targets))
    if not targets:
        return 0.0
    closed = await _closed_keys(db, org_id)
    from backend.routers.feed import log_system_event
    from backend.services import inventory_alerts as _alerts
    from backend.services.money import add_to_column
    left = received
    for oid in targets:
        order = await _live_order(db, org_id, oid, closed)
        if not order or order.location_id != po.location_id:
            continue
        lines = (await db.execute(select(OrderLine).where(
            OrderLine.order_id == oid, OrderLine.ref_id == pl.part_id, OrderLine.kind == "product",
            OrderLine.pending_stock > 0).order_by(OrderLine.id))).scalars().all()
        used_here = 0.0
        for line in lines:
            if left <= 0:
                break
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == pl.part_id, StockLevel.location_id == po.location_id
            ).with_for_update())).scalar_one_or_none()
            if not sl:
                break
            take = min(line.pending_stock or 0, left, max(sl.qty_available or 0, 0))
            if take <= 0:
                break
            await add_to_column(db, sl, "qty_available", -take, round_to=None)
            line.pending_stock = (line.pending_stock or 0) - take
            db.add(StockMovement(part_id=pl.part_id, location_id=po.location_id, movement_type="use",
                                 qty=-take, ref_type="order", ref_id=oid))
            left -= take
            used_here += take
        if used_here:
            name = lines[0].description if lines else "part"
            await log_system_event(db, org_id, "order", oid,
                                   f"{used_here:g} × {name} arrived on {po.po_number} and was written off to this job")
            await _alerts.maybe_low_stock_by_id(db, org_id, pl.part_id)
        if left <= 0:
            break
    return received - left


async def notify_po_change(db: AsyncSession, org_id: str, po: PurchaseOrder, what: str,
                           actor_id: str | None = None) -> list[str]:
    """Tell every job linked to this PO what changed: a feed line plus an in-app notification to
    its assignee and manager (not the person who made the change). Returns the order numbers."""
    from backend.models import Employee
    from backend.routers.feed import log_system_event
    from backend.services import push
    closed = await _closed_keys(db, org_id)
    nums = []
    for oid in await linked_order_ids(db, po):
        o = await _live_order(db, org_id, oid, closed)
        if not o:
            continue
        nums.append(o.order_number)
        await log_system_event(db, org_id, "order", oid, f"Purchase order {po.po_number}: {what}")
        for emp_id in dict.fromkeys([o.assigned_to_id, o.manager_id]):
            if not emp_id or emp_id == actor_id:
                continue
            emp = (await db.execute(select(Employee).where(
                Employee.id == emp_id, Employee.active == True))).scalar_one_or_none()  # noqa: E712
            if emp:
                await push.notify(db, org_id=org_id, employee_id=emp.id, kind=KIND_PO_UPDATE,
                                  title=f"{po.po_number} for {o.order_number}: {what}",
                                  body="Open the job to see its parts.",
                                  deep_link=f"/orders?open={oid}", commit=False)
    return nums


async def pos_for_order(db: AsyncSession, org_id: str, order_id: str) -> list[PurchaseOrder]:
    from backend.models import Supplier
    ids = (select(PurchaseOrder.id)
           .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
           .outerjoin(POLine, POLine.po_id == PurchaseOrder.id)
           .where(Supplier.org_id == org_id,
                  or_(PurchaseOrder.source_order_id == order_id, POLine.reserved_order_id == order_id))
           .distinct())
    return list((await db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(ids))
                                  .order_by(PurchaseOrder.po_number))).scalars().all())
