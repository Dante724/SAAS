"""The create pipeline for job creators outside routers/orders.py (re-review N2, audit WO-05).

Continue-as-linked-job, split-an-item, chat -> job and the e-commerce order import build their
Order themselves (they copy or move things a counter create doesn't have), then run the same steps
as every other new job, in this order:

    order = Order(..., status=await initial_job_status(...), assigned_to_id=await active_employee_id(...))
    await begin_new_job(db, current, order)          # barcode + track link, saved, first status log
    ...                                              # the creator's own work (move lines, link chat)
    pushes = await seal_new_job(db, current, order)  # order.create audit row + "created" messages
    await db.commit()
    await after_new_job(db, current, order, pushes)  # checklists, webhook, commissions, automations

Checks belong before begin_new_job: assert_can_add_job (work orders) and assert_asset_free.
"""
from typing import Optional

from backend.models import OrderStatusLog


async def begin_new_job(db, current, order, *, note: Optional[str] = None) -> None:
    """Give a new job the ids every job has from birth, save it and log its first status."""
    from backend.routers.orders import stamp_new_job
    await stamp_new_job(db, order)
    db.add(order)
    await db.flush()
    db.add(OrderStatusLog(order_id=order.id, from_status=None, to_status=order.status,
                          changed_by_id=current.id, note=note))


async def seal_new_job(db, current, order, **audit_diff) -> list:
    """In the creating transaction, just before its commit: the `order.create` audit row and the
    "created" customer/staff messages (audit WO-08). Returns the staff pushes for after_new_job."""
    from backend.routers.orders import _queue_created_messages, record_job_created
    await record_job_created(db, current.org_id, current.id, order, **audit_diff)
    return await _queue_created_messages(db, current, order)


async def after_new_job(db, current, order, pushes: list) -> None:
    """After the commit: the shared post-create steps (each best-effort, each committing alone)."""
    from backend.routers.orders import _new_job_steps
    await _new_job_steps(db, current, order, pushes=pushes)
