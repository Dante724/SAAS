"""F2/F3 — card readers: register Stripe/Square readers, charge a work order or a counter sale on
one, follow the charge (polling), cancel, refund, and (outside production) present a test card.
The money side lives in services/terminal.py; provider webhooks arrive in routers/payments.py."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Location, PaymentTerminal, TerminalCharge
from backend.services import terminal as svc
from backend.services.auth import assert_perm, get_current_employee, has_perm, require_perm

router = APIRouter(prefix="/api/payments/terminal", tags=["payments"])


async def _readers_viewer(db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)) -> Employee:
    """Cashiers see readers to charge on; managers see them to manage."""
    if not (await has_perm(db, current, "can_take_card_payments")
            or await has_perm(db, current, "can_manage_terminals")):
        raise HTTPException(403, "Your role does not allow this action (can_take_card_payments)")
    return current


async def _terminal(db: AsyncSession, org_id: str, terminal_id: str) -> PaymentTerminal:
    t = (await db.execute(select(PaymentTerminal).where(
        PaymentTerminal.id == terminal_id, PaymentTerminal.org_id == org_id,
        PaymentTerminal.active == True))).scalar_one_or_none()  # noqa: E712
    if not t:
        raise HTTPException(404, "Card reader not found")
    return t


async def _charge(db: AsyncSession, org_id: str, charge_id: str) -> TerminalCharge:
    ch = (await db.execute(select(TerminalCharge).where(
        TerminalCharge.id == charge_id, TerminalCharge.org_id == org_id))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Charge not found")
    return ch


async def _ser(db: AsyncSession, ch: TerminalCharge) -> dict:
    from backend.models import Order, Sale
    t = (await db.execute(select(PaymentTerminal).where(PaymentTerminal.id == ch.terminal_id))).scalar_one_or_none()
    out = svc.ser_charge(ch, t)
    out["document"] = (await db.execute(select(Order.order_number).where(Order.id == ch.order_id))).scalar() \
        if ch.order_id else (await db.execute(select(Sale.sale_number).where(Sale.id == ch.sale_id))).scalar()
    return out


@router.get("/config")
async def terminal_config(db: AsyncSession = Depends(get_db), current: Employee = Depends(_readers_viewer)):
    """Which providers can take reader payments for this shop, and whether it's test money."""
    return await svc.readiness(db, current.org_id)


@router.get("/readers")
async def list_readers(db: AsyncSession = Depends(get_db), current: Employee = Depends(_readers_viewer)):
    rows = (await db.execute(select(PaymentTerminal).where(
        PaymentTerminal.org_id == current.org_id, PaymentTerminal.active == True)  # noqa: E712
        .order_by(PaymentTerminal.created_at))).scalars().all()
    return {"items": [svc.ser_terminal(t) for t in rows]}


class AddressIn(BaseModel):
    line1: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="", max_length=2)


class ReaderIn(BaseModel):
    provider: str
    label: str = Field(default="", max_length=120)
    location_id: Optional[str] = None
    registration_code: Optional[str] = Field(default=None, max_length=120)   # Stripe
    device_id: Optional[str] = Field(default=None, max_length=120)           # Square
    address: Optional[AddressIn] = None                                       # Stripe's first reader


async def _check_location(db: AsyncSession, org_id: str, location_id: Optional[str]) -> None:
    if location_id and not (await db.execute(select(Location.id).where(
            Location.id == location_id, Location.org_id == org_id))).scalar_one_or_none():
        raise HTTPException(404, "Location not found")


@router.post("/readers", status_code=201)
async def add_reader(data: ReaderIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_terminals"))):
    await _check_location(db, current.org_id, data.location_id)
    t = await svc.register_reader(
        db, current.org_id, provider=data.provider, label=data.label, location_id=data.location_id,
        registration_code=data.registration_code, device_id=data.device_id,
        address=data.address.model_dump() if data.address else None)
    return svc.ser_terminal(t)


class ReaderPatch(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    location_id: Optional[str] = None


@router.patch("/readers/{terminal_id}")
async def edit_reader(terminal_id: str, data: ReaderPatch, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_terminals"))):
    t = await _terminal(db, current.org_id, terminal_id)
    if "label" in data.model_fields_set and data.label is not None:
        if not data.label.strip():
            raise HTTPException(422, "Give the reader a name")
        t.label = data.label.strip()
    if "location_id" in data.model_fields_set:
        await _check_location(db, current.org_id, data.location_id)
        t.location_id = data.location_id or None
    await db.commit()
    return svc.ser_terminal(t)


@router.delete("/readers/{terminal_id}")
async def delete_reader(terminal_id: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_manage_terminals"))):
    await svc.remove_reader(db, await _terminal(db, current.org_id, terminal_id))
    return {"ok": True}


@router.post("/readers/{terminal_id}/clear")
async def clear_reader(terminal_id: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_take_card_payments"))):
    """Cancel whatever charge is still waiting on this reader (e.g. one left open on another till)."""
    t = await _terminal(db, current.org_id, terminal_id)
    waiting = (await db.execute(select(TerminalCharge).where(
        TerminalCharge.terminal_id == t.id, TerminalCharge.status.in_(svc.OPEN)))).scalars().all()
    for ch in waiting:
        await svc.cancel(db, ch)
    return {"ok": True, "canceled": len(waiting)}


class ChargeIn(BaseModel):
    terminal_id: str
    order_id: Optional[str] = None
    sale_id: Optional[str] = None
    amount: Optional[float] = None       # orders only; default = balance due. Sales charge their total.
    account_id: Optional[str] = None     # cashbox the money lands in; default = the first card account


@router.post("/charge", status_code=201)
async def charge(data: ChargeIn, db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(require_perm("can_take_card_payments"))):
    """Send an amount to a reader. The reader then waits for the card; poll GET /charges/{id}."""
    if data.order_id:
        await assert_perm(db, current, "can_view_finance")   # same gate as taking any order payment
    if data.sale_id:
        await assert_perm(db, current, "can_create_sales")
    ch = await svc.start_charge(db, org_id=current.org_id, actor_id=current.id, terminal_id=data.terminal_id,
                                amount=data.amount, order_id=data.order_id, sale_id=data.sale_id,
                                account_id=data.account_id)
    return await _ser(db, ch)


@router.get("/charges")
async def list_charges(order_id: Optional[str] = None, sale_id: Optional[str] = None,
                       kind: Optional[str] = None, status: Optional[str] = None,
                       db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_take_card_payments"))):
    """Reader charges, newest first: for one order or sale, or every sale (kind=sale)."""
    q = select(TerminalCharge).where(TerminalCharge.org_id == current.org_id)
    if order_id:
        q = q.where(TerminalCharge.order_id == order_id)
    if sale_id:
        q = q.where(TerminalCharge.sale_id == sale_id)
    if kind == "sale":
        q = q.where(TerminalCharge.sale_id.is_not(None))
    elif kind == "order":
        q = q.where(TerminalCharge.order_id.is_not(None))
    if status:
        q = q.where(TerminalCharge.status == status)
    rows = (await db.execute(q.order_by(desc(TerminalCharge.created_at)).limit(50))).scalars().all()
    from backend.models import Order, Sale
    terms = {t.id: t for t in (await db.execute(select(PaymentTerminal).where(
        PaymentTerminal.id.in_({c.terminal_id for c in rows})))).scalars()}
    orders = dict((await db.execute(select(Order.id, Order.order_number).where(
        Order.id.in_({c.order_id for c in rows if c.order_id})))).all())
    sales = dict((await db.execute(select(Sale.id, Sale.sale_number).where(
        Sale.id.in_({c.sale_id for c in rows if c.sale_id})))).all())
    return {"items": [{**svc.ser_charge(c, terms.get(c.terminal_id)),
                       "document": orders.get(c.order_id) if c.order_id else sales.get(c.sale_id)} for c in rows]}


@router.get("/charges/{charge_id}")
async def get_charge(charge_id: str, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_take_card_payments"))):
    """Live status. A waiting charge is re-checked with the provider (webhooks can't reach a laptop)."""
    ch = await svc.refresh(db, await _charge(db, current.org_id, charge_id))
    return await _ser(db, ch)


@router.post("/charges/{charge_id}/cancel")
async def cancel_charge(charge_id: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_take_card_payments"))):
    ch = await svc.cancel(db, await _charge(db, current.org_id, charge_id))
    return await _ser(db, ch)


class RefundIn(BaseModel):
    amount: Optional[float] = None       # default: everything not yet refunded


@router.post("/charges/{charge_id}/refund")
async def refund_charge(charge_id: str, data: RefundIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_refund_card_payments"))):
    ch = await svc.refund(db, await _charge(db, current.org_id, charge_id), amount=data.amount, actor_id=current.id)
    return await _ser(db, ch)


class SimulateIn(BaseModel):
    outcome: str = "approve"             # approve | decline


@router.post("/charges/{charge_id}/simulate")
async def simulate_charge(charge_id: str, data: SimulateIn, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_take_card_payments"))):
    """Test mode only: present an approving or declining test card on the reader."""
    ch = await svc.simulate(db, await _charge(db, current.org_id, charge_id), data.outcome)
    return await _ser(db, ch)
