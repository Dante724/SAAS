"""Client Returns (goods back to stock) and Refunds (money back) — separate
documents, per Orderry's Feb-2026 split. A refund claws back the order's
commissions via the payroll engine (idempotent negative mirrors)."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from backend.db.database import get_db
from backend.models import (
    ClientReturn, ClientReturnLine, Refund, Order, StockLevel, StockMovement,
    Account, Employee, Payment,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/returns", tags=["returns"])


class ReturnLineIn(BaseModel):
    part_id: str
    qty: float = 1
    restock: bool = True


class ReturnIn(BaseModel):
    order_id: Optional[str] = None
    client_id: Optional[str] = None
    reason: Optional[str] = None
    lines: list[ReturnLineIn] = []


@router.post("", status_code=201)
async def create_return(
    data: ReturnIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    if not data.lines:
        raise HTTPException(400, "A return needs at least one line")
    ret = ClientReturn(org_id=current.org_id, order_id=data.order_id,
                       client_id=data.client_id, location_id=current.location_id,
                       reason=data.reason, created_by_id=current.id)
    db.add(ret)
    await db.flush()
    restocked = 0
    for li in data.lines:
        db.add(ClientReturnLine(return_id=ret.id, part_id=li.part_id,
                                qty=li.qty, restock=li.restock))
        if li.restock and li.qty > 0:
            sl = (await db.execute(select(StockLevel).where(
                StockLevel.part_id == li.part_id,
                StockLevel.location_id == current.location_id))).scalars().first()
            if sl:
                sl.qty_available = (sl.qty_available or 0) + li.qty
            else:
                db.add(StockLevel(part_id=li.part_id, location_id=current.location_id,
                                  qty_available=li.qty, qty_reserved=0))
            db.add(StockMovement(part_id=li.part_id, location_id=current.location_id,
                                 movement_type="return", qty=li.qty,
                                 ref_type="client_return", ref_id=ret.id))
            restocked += 1
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "return", ret.id,
                           f"Client return: {len(data.lines)} line(s), {restocked} restocked")
    await db.commit()
    return {"id": ret.id, "restocked": restocked}


class RefundIn(BaseModel):
    account_id: str
    amount: float
    order_id: Optional[str] = None
    return_id: Optional[str] = None
    reason: Optional[str] = None


@router.post("/refunds", status_code=201)
async def create_refund(
    data: RefundIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    if data.amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    acct = (await db.execute(select(Account).where(
        Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "Cashbox not found")
    # Every refund belongs to an order (directly or via its goods return) and is capped at what that
    # order actually received net of ALL prior refunds — refunds from this endpoint, archive refunds
    # and overpayment refunds all record a refund Payment, which order_money counts. Previously the
    # cap ignored this endpoint's own refunds (repeatable) and was skipped without an order_id.
    order_id = data.order_id
    if not order_id and data.return_id:
        ret = (await db.execute(select(ClientReturn).where(
            ClientReturn.id == data.return_id, ClientReturn.org_id == current.org_id))).scalar_one_or_none()
        order_id = ret.order_id if ret else None
    if not order_id:
        raise HTTPException(400, "A refund must reference the order (or a return for an order) it repays")
    locked = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id).with_for_update())).scalar_one_or_none()
    if not locked:
        raise HTTPException(404, "Order not found")
    from backend.services.order_money import net_paid as _net_paid
    net_paid = await _net_paid(db, order_id)
    if data.amount > net_paid + 1e-6:
        raise HTTPException(400, f"Cannot refund more than was paid ({net_paid:g})")
    data.order_id = order_id
    refund = Refund(org_id=current.org_id, order_id=data.order_id, return_id=data.return_id,
                    account_id=data.account_id, amount=data.amount, reason=data.reason)
    db.add(refund)
    await db.flush()
    from backend.routers.cash import open_drawer_shift
    _shift = await open_drawer_shift(db, current.org_id, acct)
    db.add(Payment(order_id=order_id, client_id=locked.client_id, method="cash" if _shift else "refund",
                   kind="refund", amount=data.amount, account_id=data.account_id, received_by_id=current.id,
                   shift_id=_shift,   # cash handed back from a drawer reduces that drawer's expected cash
                   reference=f"Refund: {data.reason or ''}"[:200]))
    # Money out of the cashbox (signed ledger)
    from backend.routers.cash import post_transaction
    await post_transaction(db, current.org_id, data.account_id, "refund", -data.amount,
                           comment=f"Refund{(' for order' if data.order_id else '')}: {data.reason or ''}".strip(),
                           ref_type="refund", ref_id=refund.id, employee_id=current.id)
    # Refund on an order claws back its commissions (idempotent mirrors)
    if data.order_id:
        order = (await db.execute(select(Order).where(
            Order.id == data.order_id, Order.org_id == current.org_id)
            .options(selectinload(Order.lines)))).scalar_one_or_none()
        if order:
            from backend.services.payroll import CommissionEngine
            await CommissionEngine(db, current.org_id).clawback_for_order_reopen(order)
            # Reflect the refund on the customer's balance (cash went back to them), so the
            # Balance tab / Client.balance stays in sync — was previously silent.
            if order.client_id:
                from backend.services.ledger import post_contact_ledger
                await post_contact_ledger(db, current.org_id, order.client_id, "refund", abs(data.amount),
                                          note=f"Refund for {order.order_number}", ref_type="refund",
                                          ref_id=refund.id, created_by_id=current.id)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "refund", refund.id,
                           f"Refund {data.amount:g} from {acct.name}")
    await db.commit()
    return {"id": refund.id, "amount": data.amount}


@router.get("")
async def list_returns(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Order, Client, Part
    rows = (await db.execute(select(ClientReturn).where(ClientReturn.org_id == current.org_id)
        .order_by(ClientReturn.created_at.desc()).limit(200))).scalars().all()
    out = []
    for r in rows:
        lines = (await db.execute(select(ClientReturnLine, Part.name)
            .join(Part, ClientReturnLine.part_id == Part.id)
            .where(ClientReturnLine.return_id == r.id))).all()
        order_no = None
        if r.order_id:
            o = (await db.execute(select(Order.order_number).where(Order.id == r.order_id))).scalar_one_or_none()
            order_no = o
        client_name = None
        if r.client_id:
            client_name = (await db.execute(select(Client.name).where(Client.id == r.client_id))).scalar_one_or_none()
        out.append({"id": r.id, "order_id": r.order_id, "order_number": order_no,
                    "client_name": client_name, "reason": r.reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "lines": [{"part_name": name, "qty": l.qty, "restock": l.restock} for l, name in lines]})
    return {"items": out}


@router.get("/refunds")
async def list_refunds(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    from backend.models import Order, Account as Acct
    rows = (await db.execute(select(Refund).where(Refund.org_id == current.org_id)
        .order_by(Refund.created_at.desc()).limit(200))).scalars().all()
    out = []
    for r in rows:
        order_no = None
        if r.order_id:
            order_no = (await db.execute(select(Order.order_number).where(Order.id == r.order_id))).scalar_one_or_none()
        acct_name = (await db.execute(select(Acct.name).where(Acct.id == r.account_id))).scalar_one_or_none()
        out.append({"id": r.id, "amount": r.amount, "reason": r.reason,
                    "order_id": r.order_id, "order_number": order_no, "account_name": acct_name,
                    "return_id": r.return_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None})
    return {"items": out}
