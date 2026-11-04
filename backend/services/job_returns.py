"""What a job still holds of a product, net of goods the customer brought back (QA wave 3, W3-C).

A goods return against a job (POST /api/returns with order_id) may only take back what the job
used, and only once. Every path that later hands the job's parts back to stock — removing the line,
lowering its qty, dropping or deleting the job — goes through orders._reverse_fulfillment, which
caps what it returns at `still_held` so a part the customer already returned isn't restocked again.

- used by the job = its "use" movements minus its own "return" movements (ref_type "order"); jobs
  from before movements were logged fall back to their lines (qty − pending_stock);
- returned = every goods-return line for the job and product, restocked or not (a part scrapped on
  return isn't on the customer's job either).
"""
from sqlalchemy import func, select

from backend.models import ClientReturn, ClientReturnLine, OrderLine, StockMovement


async def returned_qty(db, order_id: str, part_id: str) -> float:
    return float((await db.execute(
        select(func.coalesce(func.sum(ClientReturnLine.qty), 0))
        .join(ClientReturn, ClientReturn.id == ClientReturnLine.return_id)
        .where(ClientReturn.order_id == order_id, ClientReturnLine.part_id == part_id))).scalar() or 0)


async def used_by_job(db, order_id: str, part_id: str) -> float:
    rows = (await db.execute(select(func.count(), func.coalesce(func.sum(StockMovement.qty), 0)).where(
        StockMovement.ref_type == "order", StockMovement.ref_id == order_id, StockMovement.part_id == part_id,
        StockMovement.movement_type.in_(("use", "return"))))).one()
    if rows[0]:
        return max(0.0, -float(rows[1] or 0))
    return float((await db.execute(select(func.coalesce(func.sum(
        func.coalesce(OrderLine.qty, 0) - func.coalesce(OrderLine.pending_stock, 0)), 0)).where(
        OrderLine.order_id == order_id, OrderLine.kind == "product", OrderLine.ref_id == part_id))).scalar() or 0)


async def still_held(db, order_id: str, part_id: str) -> float:
    """Units of the product the job took that are still with the customer."""
    return max(0.0, await used_by_job(db, order_id, part_id) - await returned_qty(db, order_id, part_id))
