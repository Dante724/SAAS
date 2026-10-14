"""
Cash management — the cashbox system.

Accounts hold balances and gate which payment methods appear at checkout.
Every money movement posts a Transaction. Closing a paid order posts an
income transaction to the chosen account (or to client debt).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_dt
from backend.models import (
    Account,
    CashFlowItem,
    Client,
    Employee,
    Invoice,
    Location,
    Order,
    Organisation,
    Payment,
    Transaction,
)
from backend.services.auth import require_perm, record_scope
from backend.services.numbering import next_number
from backend.services.money import q2, q4


async def _check_backdating(db, org_id):
    """Raise 422 if the org forbids backdating and now precedes the last invoice."""
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if org and org.prevent_backdating_invoices:
        last = (await db.execute(select(Invoice).where(Invoice.org_id == org_id)
                .order_by(Invoice.issued_at.desc()).limit(1))).scalar_one_or_none()
        if last and last.issued_at and datetime.utcnow() < last.issued_at:
            raise HTTPException(422, "Backdating invoices is not allowed")

router = APIRouter(prefix="/api/cash", tags=["cash"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


# ── helpers ──────────────────────────────────────────

async def post_transaction(db, org_id, account_id, txn_type, amount, *,
                           cash_flow_item_id=None, comment=None, ref_type=None,
                           ref_id=None, employee_id=None, transfer_pair_id=None,
                           fx_rate=None, is_retroactive=False, actual_date=None):
    """Create a transaction and update the account balance in one step."""
    txn = Transaction(
        org_id=org_id, account_id=account_id, txn_type=txn_type, amount=amount,
        cash_flow_item_id=cash_flow_item_id, comment=comment, ref_type=ref_type,
        ref_id=ref_id, employee_id=employee_id, transfer_pair_id=transfer_pair_id,
        fx_rate=fx_rate,
        is_retroactive=is_retroactive, actual_date=actual_date or datetime.utcnow(),
    )
    db.add(txn)
    # Scope the account to the org so a client-supplied account_id can't corrupt another
    # tenant's balance (the transaction row is org-stamped, but the balance mutation wasn't scoped).
    acc = (await db.execute(select(Account).where(
        Account.id == account_id, Account.org_id == org_id))).scalar_one_or_none()
    if acc:
        acc.balance = (acc.balance or 0) + amount
        txn.balance_after = q2(acc.balance)  # A1: frozen running-balance snapshot
        # 144bh: manual cash income/expense on a till feeds the open drawer so the blind-count close
        # agrees with the cashbox balance (sales go via Payment.shift_id). Link only when it's
        # unambiguous: for a location-scoped till, that location's open shift; for a company-wide
        # till, the single org-wide open shift. 2+ candidate drawers → leave unlinked.
        if txn_type in ("income", "expense"):
            txn.shift_id = await open_drawer_shift(db, org_id, acc)
    return txn


async def open_drawer_shift(db, org_id, acc) -> Optional[str]:
    """The open cash shift a cash till belongs to, when unambiguous: for a location-scoped till,
    that location's open shift; for a company-wide till, the single org-wide open shift."""
    if not acc or acc.payment_option != "cash":
        return None
    from backend.models import CashShift
    q = (select(CashShift).join(Location, CashShift.location_id == Location.id)
         .where(Location.org_id == org_id, CashShift.closed_at.is_(None)))
    if acc.location_id:
        q = q.where(CashShift.location_id == acc.location_id)
    shifts = (await db.execute(q)).scalars().all()
    return shifts[0].id if len(shifts) == 1 else None


def _ser_account(a: Account) -> dict:
    return {
        "id": a.id, "name": a.name, "icon": a.icon, "payment_option": a.payment_option,
        "is_company": a.is_company, "is_virtual": a.is_virtual, "balance": a.balance,
        "fee_pct": a.fee_pct, "fee_fixed": a.fee_fixed, "location_id": a.location_id,
        "currency_code": a.currency_code,   # Build 144cr: null = org base currency
    }


# ── Accounts CRUD ────────────────────────────────────

class AccountIn(BaseModel):
    name: str
    icon: str = "wallet"
    payment_option: str = "cash"   # cash|cashless|card
    is_company: bool = False
    is_virtual: bool = False
    location_id: Optional[str] = None
    fee_pct: float = 0.0
    fee_fixed: float = 0.0
    currency_code: Optional[str] = None   # Build 144cr: chosen at create, locked after


@router.get("/accounts")
async def list_accounts(
    payment_option: Optional[str] = None,
    location_id: Optional[str] = None,        # build 139: scope to a branch
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    from sqlalchemy import or_
    q = select(Account).where(Account.org_id == current.org_id, Account.active == True)  # noqa: E712
    if payment_option:
        q = q.where(Account.payment_option == payment_option)
    if location_id:
        # a location sees its own accounts + company-wide ones (location_id is null)
        q = q.where(or_(Account.location_id == location_id, Account.location_id.is_(None)))
    rows = (await db.execute(q.order_by(Account.name))).scalars().all()
    # C: per-account (cashbox) ACL — a role restricted to specific accounts only
    # sees those (role rights still gate the whole finance area).
    from backend.services import scoping
    role = await scoping.role_for(db, current)
    allowed = scoping.allowed_accounts(role)
    if allowed is not None:
        rows = [a for a in rows if a.id in allowed]
    return {"items": [_ser_account(a) for a in rows]}


@router.get("/total-money")
async def total_money(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Build 144cr — Total-money report with a column per currency. Balances are
    grouped by their account currency and never summed or converted across
    currencies (matches Orderry's per-currency column report)."""
    base = await _org_base_code(db, current.org_id)
    from backend.models import Currency
    sym = {c.code: c.symbol for c in (await db.execute(select(Currency).where(
        Currency.org_id == current.org_id, Currency.active == True))).scalars().all()}  # noqa: E712
    rows = (await db.execute(select(Account).where(
        Account.org_id == current.org_id, Account.active == True))).scalars().all()  # noqa: E712
    groups: dict[str, dict] = {}
    for a in rows:
        code = a.currency_code or base
        g = groups.setdefault(code, {"code": code, "symbol": sym.get(code, ""),
                                      "is_base": code == base, "total": 0.0, "accounts": []})
        g["total"] = q2(g["total"] + (a.balance or 0))
        g["accounts"].append({"id": a.id, "name": a.name, "balance": q2(a.balance or 0)})
    # base currency first, then the rest alphabetically
    cols = sorted(groups.values(), key=lambda g: (not g["is_base"], g["code"]))
    return {"base": base, "columns": cols}


@router.post("/accounts", status_code=201)
async def create_account(
    data: AccountIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    a = Account(
        org_id=current.org_id, name=data.name, icon=data.icon,
        payment_option=data.payment_option, is_company=data.is_company,
        is_virtual=data.is_virtual, location_id=data.location_id,
        fee_pct=data.fee_pct, fee_fixed=data.fee_fixed,
        currency_code=((data.currency_code or "").strip().upper() or None),  # 144cr
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _ser_account(a)


@router.put("/accounts/{account_id}")
async def update_account(
    account_id: str, data: AccountIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    a = (await db.execute(select(Account).where(Account.id == account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Account not found")
    a.name, a.icon, a.payment_option = data.name, data.icon, data.payment_option
    a.is_company, a.is_virtual = data.is_company, data.is_virtual
    a.fee_pct, a.fee_fixed = data.fee_pct, data.fee_fixed
    await db.commit()
    return _ser_account(a)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    a = (await db.execute(select(Account).where(Account.id == account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Account not found")
    a.active = False
    await db.commit()
    return {"ok": True}


# ── Cash flow items (tags) ───────────────────────────

class CashFlowItemIn(BaseModel):
    name: str
    direction: str = "income"  # income|expense


@router.get("/flow-items")
async def list_flow_items(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    rows = (await db.execute(
        select(CashFlowItem).where(CashFlowItem.org_id == current.org_id, CashFlowItem.active == True)  # noqa: E712
        .order_by(CashFlowItem.name)
    )).scalars().all()
    return {"items": [{"id": c.id, "name": c.name, "direction": c.direction, "is_system": c.is_system} for c in rows]}


@router.post("/flow-items", status_code=201)
async def create_flow_item(
    data: CashFlowItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    c = CashFlowItem(org_id=current.org_id, name=data.name, direction=data.direction)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "direction": c.direction, "is_system": c.is_system}


# ── Manual transactions: income / expense / transfer ─

class TxnIn(BaseModel):
    account_id: str
    txn_type: str               # income|expense
    amount: float               # always positive; sign derived from type
    cash_flow_item_id: Optional[str] = None
    comment: Optional[str] = None
    actual_date: Optional[str] = None


@router.post("/transactions", status_code=201)
async def create_transaction(
    data: TxnIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    if data.txn_type not in ("income", "expense"):
        raise HTTPException(400, "Use income or expense (transfer has its own endpoint)")
    amt = abs(data.amount) * (1 if data.txn_type == "income" else -1)
    retro = False
    actual = None
    if data.actual_date:
        actual = parse_dt(data.actual_date, "actual_date")
        retro = actual.date() < datetime.utcnow().date()
    await post_transaction(
        db, current.org_id, data.account_id, data.txn_type, amt,
        cash_flow_item_id=data.cash_flow_item_id, comment=data.comment,
        ref_type="manual", employee_id=current.id,
        is_retroactive=retro, actual_date=actual,
    )
    await db.commit()
    return {"ok": True}


# ── A1: corrections + soft delete (append-only ledger) ──────────────

class CorrectionIn(BaseModel):
    delta: float                 # signed adjustment: +increase, -reduce
    reason: str                  # mandatory


async def _get_txn(db, org_id, txn_id) -> Transaction:
    t = (await db.execute(select(Transaction).where(
        Transaction.id == txn_id, Transaction.org_id == org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transaction not found")
    return t


# Cash legs posted by a payment document. Voiding/correcting only the cash row reversed the till
# while the Payment, invoice.amount_paid, client ledger and sale.paid stayed as they were.
_DOCUMENT_LEGS = {"order": "work order payment", "invoice": "invoice payment", "sale": "sale"}


def _guard_document_leg(t) -> None:
    if t.ref_type in _DOCUMENT_LEGS:
        raise HTTPException(409, f"This transaction is the cash side of a {_DOCUMENT_LEGS[t.ref_type]}. "
                                 "Refund or void it from that document so everything stays in sync.")


@router.post("/transactions/{txn_id}/correct", status_code=201)
async def correct_transaction(
    txn_id: str,
    data: CorrectionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_correct_transactions")),
):
    """Correct a transaction by posting a signed delta row that references the
    original. The original is never mutated (amount/method stay put); the ledger
    stays append-only. Reason is mandatory."""
    orig = await _get_txn(db, current.org_id, txn_id)
    if orig.status == "deleted":
        raise HTTPException(400, "Cannot correct a deleted transaction")
    _guard_document_leg(orig)
    if not (data.reason or "").strip():
        raise HTTPException(400, "A reason is required")
    if data.delta == 0:
        raise HTTPException(400, "Delta must be non-zero")
    corr = await post_transaction(
        db, current.org_id, orig.account_id, orig.txn_type, q2(data.delta),
        cash_flow_item_id=orig.cash_flow_item_id,
        comment=f"Correction: {data.reason.strip()}",
        ref_type=orig.ref_type, ref_id=orig.ref_id, employee_id=current.id,
    )
    corr.reverses_txn_id = orig.id       # link the delta row to the original
    orig.status = "corrected"            # original stays intact, just flagged
    await db.commit()
    return {"ok": True, "correction_id": corr.id, "balance_after": corr.balance_after}


@router.delete("/transactions/{txn_id}")
async def delete_transaction(
    txn_id: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_transactions")),
):
    """Soft-delete: the row stays (struck through in the UI) but its amount is
    reversed out of the live account balance. Historical balance_after snapshots
    on OTHER rows are never recomputed — the ledger is a record, not a rerun.
    Both legs of a transfer are voided together."""
    t = await _get_txn(db, current.org_id, txn_id)
    if t.status == "deleted":
        raise HTTPException(400, "Already deleted")
    # A3: don't let a refund/return settlement leg be voided on its own — that would
    # desync the parent document. Delete via the refund/return instead.
    if t.ref_type in ("refund", "client_return", "purchase_return"):
        raise HTTPException(409, f"This transaction settles a {t.ref_type.replace('_', ' ')}. "
                                 "Delete the return/refund document instead.")
    _guard_document_leg(t)
    legs = [t]
    if t.transfer_pair_id:
        legs = (await db.execute(select(Transaction).where(
            Transaction.org_id == current.org_id,
            Transaction.transfer_pair_id == t.transfer_pair_id))).scalars().all()
    for leg in legs:
        if leg.status == "deleted":
            continue
        acc = (await db.execute(select(Account).where(Account.id == leg.account_id))).scalar_one_or_none()
        if acc:
            acc.balance = q2((acc.balance or 0) - leg.amount)  # reverse; do NOT touch other rows' balance_after
        leg.status = "deleted"
        leg.void_reason = reason
        leg.voided_by_id = current.id
    await db.commit()
    return {"ok": True, "voided": len(legs)}


@router.post("/transactions/{txn_id}/restore")
async def restore_transaction(
    txn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_transactions")),
):
    """Undo a soft delete: re-apply the amount to the account balance."""
    t = await _get_txn(db, current.org_id, txn_id)
    if t.status != "deleted":
        raise HTTPException(400, "Transaction is not deleted")
    legs = [t]
    if t.transfer_pair_id:
        legs = (await db.execute(select(Transaction).where(
            Transaction.org_id == current.org_id,
            Transaction.transfer_pair_id == t.transfer_pair_id))).scalars().all()
    for leg in legs:
        if leg.status != "deleted":
            continue
        acc = (await db.execute(select(Account).where(Account.id == leg.account_id))).scalar_one_or_none()
        if acc:
            acc.balance = q2((acc.balance or 0) + leg.amount)
        leg.status = "active"
        leg.void_reason = None
        leg.voided_by_id = None
    await db.commit()
    return {"ok": True, "restored": len(legs)}


class TransferIn(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: float
    rate: Optional[float] = None       # Build 144cr: exchange rate for cross-currency (1 secondary = rate base)
    comment: Optional[str] = None


async def _org_base_code(db, org_id: str) -> str:
    from backend.routers.currencies import _base   # single source of truth
    return (await _base(db, org_id))["code"]


async def _registry_rate(db, org_id: str, code: str) -> Optional[float]:
    from backend.models import Currency
    c = (await db.execute(select(Currency).where(
        Currency.org_id == org_id, Currency.code == code,
        Currency.active == True))).scalar_one_or_none()  # noqa: E712
    return c.rate if c else None


@router.post("/transfer", status_code=201)
async def transfer(
    data: TransferIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_transfer_money")),
):
    if data.from_account_id == data.to_account_id:
        raise HTTPException(400, "Choose two different accounts")
    if data.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    frm = (await db.execute(select(Account).where(
        Account.id == data.from_account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    to = (await db.execute(select(Account).where(
        Account.id == data.to_account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not frm or not to:
        raise HTTPException(404, "Account not found")

    base = await _org_base_code(db, current.org_id)
    from_ccy = frm.currency_code or base
    to_ccy = to.currency_code or base

    import uuid
    pair = uuid.uuid4().hex
    debit = q2(abs(data.amount))

    if from_ccy == to_ccy:
        # same currency — 1:1, no exchange
        credit, fx = debit, None
    else:
        # Orderry rule: exactly one side must be the base currency.
        if from_ccy != base and to_ccy != base:
            raise HTTPException(400, "One account must be in the base currency for a cross-currency transfer")
        secondary = to_ccy if from_ccy == base else from_ccy
        rate = data.rate if (data.rate and data.rate > 0) else await _registry_rate(db, current.org_id, secondary)
        if not rate or rate <= 0:
            raise HTTPException(400, f"No exchange rate available for {secondary}")
        rate = round(float(rate), 6)
        # rate = base per 1 secondary.
        if from_ccy != base:      # secondary -> base : multiply
            credit = q2(debit * rate)
        else:                      # base -> secondary : divide
            credit = q2(debit / rate)
        fx = rate

    await post_transaction(db, current.org_id, frm.id, "transfer", -debit,
                           comment=data.comment or "Transfer out", ref_type="manual",
                           employee_id=current.id, transfer_pair_id=pair, fx_rate=fx)
    await post_transaction(db, current.org_id, to.id, "transfer", credit,
                           comment=data.comment or "Transfer in", ref_type="manual",
                           employee_id=current.id, transfer_pair_id=pair, fx_rate=fx)
    await db.commit()
    return {"ok": True, "debited": debit, "credited": credit, "rate": fx,
            "from_currency": from_ccy, "to_currency": to_ccy}


# ── Close order → payment ────────────────────────────

class OrderPaymentIn(BaseModel):
    method: str                      # cash|cashless|card|balance
    account_id: Optional[str] = None  # required unless method == balance
    amount: Optional[float] = None    # defaults to order total


@router.get("/orders/{order_id}/payment-context")
async def order_payment_context(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """What the close-order payment window needs: order total, and the accounts
    available per payment method."""
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    accts = (await db.execute(select(Account).where(Account.org_id == current.org_id, Account.active == True))).scalars().all()  # noqa: E712
    by_method = {"cash": [], "cashless": [], "card": []}
    for a in accts:
        by_method.setdefault(a.payment_option, []).append(_ser_account(a))
    # all payments + prepayments against this order (across invoices + direct)
    paid = (await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).select_from(Payment)
        .where(Payment.order_id == order_id, Payment.kind != "refund")
    )).scalar() or 0
    prepaid = (await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).select_from(Payment)
        .where(Payment.order_id == order_id, Payment.kind == "prepayment")
    )).scalar() or 0
    total = o.total or 0
    remaining = q2(total - paid)
    return {
        "order_total": total,
        "item_cost": (o.labour_total or 0) + (o.parts_total or 0),
        "already_paid": paid,
        "prepaid": prepaid,
        "remaining": max(remaining, 0),
        "overpaid": max(-remaining, 0),   # refund due if prepayments exceed total
        "accounts_by_method": by_method,
    }


@router.post("/orders/{order_id}/pay")
async def pay_order(
    order_id: str,
    data: OrderPaymentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    # Lock the order row so two concurrent "pay" requests serialize (Postgres) — combined with the
    # remaining<=0 guard below this prevents a double-click / webhook-race from double-posting a
    # payment. (with_for_update is a no-op on SQLite, which already serializes writes.)
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id).with_for_update())).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    # remaining = total minus everything already paid, net of refunds (shared definition)
    from backend.services.order_money import balance_due, cancel_open_pay_links
    remaining = await balance_due(db, o)
    amount = data.amount if data.amount is not None else max(remaining, 0)
    # Guard: positive amounts only, and don't let a "pay the balance" tap double-post once the
    # order is already settled (double-click / retry → remaining 0 → amount 0 → rejected here).
    if amount is None or amount <= 0:
        raise HTTPException(400, "Nothing to pay — this order is already settled" if remaining <= 0 else "Payment amount must be positive")

    # ensure an invoice exists for this order
    from backend.services.order_money import primary_invoice
    inv = await primary_invoice(db, order_id)
    if not inv:
        await _check_backdating(db, current.org_id)
        inv = Invoice(invoice_number=await next_number(db, current.org_id, "invoice"), org_id=current.org_id,
                      order_id=order_id, client_id=o.client_id, status="draft",
                      subtotal=o.total or 0, total=o.total or 0, issued_at=datetime.utcnow())
        db.add(inv)
        await db.flush()

    from backend.routers.feed import log_system_event

    if data.method == "balance":
        # Close on account — no cash movement; the payment reduces the client's receivable.
        # Build 124: post it through the unified ledger so the balance moves *and* the
        # movement is auditable (was a silent Client.balance write before).
        pay = Payment(invoice_id=inv.id, order_id=order_id, client_id=o.client_id, method="balance",
                      kind="payment", amount=amount, received_by_id=current.id, reference="Closed on account")
        db.add(pay)
        if o.client_id:
            await db.flush()
            from backend.services.ledger import post_contact_ledger
            await post_contact_ledger(db, current.org_id, o.client_id, "income", -abs(amount),
                                      note=f"Payment for {o.order_number}", ref_type="payment",
                                      ref_id=pay.id, created_by_id=current.id)
        inv.amount_paid = q2((inv.amount_paid or 0) + amount)   # quantize: float sums left paid invoices "partial"
        inv.status = "paid" if inv.amount_paid >= q2(inv.total) else "partial"
        await log_system_event(db, current.org_id, "order", order_id,
                               f"Closed on client balance: {amount}")
        await db.flush()
        await cancel_open_pay_links(db, o)
        await db.commit()
        return {"ok": True, "method": "balance", "amount": amount}

    # cash/cashless/card -> must hit an account
    if not data.account_id:
        raise HTTPException(400, "Select a cashbox/account")
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")

    # default cash flow item: org's per-doc-type default (2b), else the "Sales" category
    _org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    sales_item = None
    if _org and _org.cash_category_tickets_id:
        sales_item = (await db.execute(select(CashFlowItem).where(
            CashFlowItem.id == _org.cash_category_tickets_id))).scalar_one_or_none()
    if not sales_item:
        sales_item = (await db.execute(
            select(CashFlowItem).where(CashFlowItem.org_id == current.org_id, CashFlowItem.name == "Sales")
        )).scalar_one_or_none()

    # income transaction to the account
    await post_transaction(db, current.org_id, acc.id, "invoice_payment", abs(amount),
                           cash_flow_item_id=sales_item.id if sales_item else None,
                           comment=f"Payment for {o.order_number}", ref_type="order",
                           ref_id=order_id, employee_id=current.id)

    # transaction fee (auto-deduct as expense)
    fee = 0.0
    if acc.fee_pct or acc.fee_fixed:
        fee = q2(abs(amount) * acc.fee_pct / 100 + acc.fee_fixed)
        if fee > 0:
            fee_item = (await db.execute(
                select(CashFlowItem).where(CashFlowItem.org_id == current.org_id, CashFlowItem.name == "Transaction fees")
            )).scalar_one_or_none()
            await post_transaction(db, current.org_id, acc.id, "fee", -fee,
                                   cash_flow_item_id=fee_item.id if fee_item else None,
                                   comment=f"Processing fee for {o.order_number}", ref_type="order",
                                   ref_id=order_id, employee_id=current.id)

    pay = Payment(invoice_id=inv.id, order_id=order_id, client_id=o.client_id, method=data.method,
                  kind="payment", account_id=acc.id, amount=amount, received_by_id=current.id,
                  # cash into a till counts toward that drawer's close (was never linked → a short
                  # drawer looked balanced or "over")
                  shift_id=await open_drawer_shift(db, current.org_id, acc) if data.method == "cash" else None)
    db.add(pay)
    if o.client_id:
        await db.flush()
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, o.client_id, "income", -abs(amount),
                                  note=f"Payment for {o.order_number}", ref_type="payment",
                                  ref_id=pay.id, created_by_id=current.id)
    inv.amount_paid = q2((inv.amount_paid or 0) + amount)   # quantize: float sums left paid invoices "partial"
    inv.status = "paid" if inv.amount_paid >= q2(inv.total) else "partial"
    await log_system_event(db, current.org_id, "order", order_id,
                           f"Payment {amount} via {data.method} to {acc.name}")
    await db.flush()
    await cancel_open_pay_links(db, o)   # a link sent earlier must not charge the customer again
    await db.commit()
    # 144jc: payment_received notifications (client receipt + staff), best-effort.
    try:
        from backend.services import notification_rules as _nr
        await _nr.evaluate(db, org_id=current.org_id, order_id=order_id, event="payment_received")
        await _nr.notify_staff_order(db, org_id=current.org_id, order_id=order_id,
                                     event="payment_received", actor_id=current.id)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "method": data.method, "amount": amount, "fee": fee, "account": acc.name}


class PrepayIn(BaseModel):
    amount: float
    account_id: str
    method: str = "cash"


@router.post("/orders/{order_id}/prepay", status_code=201)
async def prepay_order(
    order_id: str,
    data: PrepayIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Take a prepayment on an order. Posts income to the chosen cashbox and
    records a Payment(kind=prepayment) that the close-window math subtracts."""
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    if data.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")
    sales = (await db.execute(select(CashFlowItem).where(CashFlowItem.org_id == current.org_id, CashFlowItem.name == "Sales"))).scalar_one_or_none()
    await post_transaction(db, current.org_id, acc.id, "invoice_payment", abs(data.amount),
                           cash_flow_item_id=sales.id if sales else None,
                           comment=f"Prepayment for {o.order_number}", ref_type="order",
                           ref_id=order_id, employee_id=current.id)
    pay = Payment(invoice_id=None, order_id=order_id, client_id=o.client_id, method=data.method,
                  kind="prepayment", account_id=acc.id, amount=data.amount, received_by_id=current.id,
                  reference="Prepayment",
                  shift_id=await open_drawer_shift(db, current.org_id, acc) if data.method == "cash" else None)
    db.add(pay)
    if o.client_id:
        await db.flush()
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, o.client_id, "prepayment", -abs(data.amount),
                                  note=f"Prepayment for {o.order_number}", ref_type="payment",
                                  ref_id=pay.id, created_by_id=current.id)
    o.prepayment_total = (o.prepayment_total or 0) + data.amount
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order_id, f"Prepayment {data.amount} to {acc.name}")
    from backend.services.order_money import cancel_open_pay_links
    await db.flush()
    await cancel_open_pay_links(db, o)
    await db.commit()
    return {"ok": True, "prepaid": o.prepayment_total}


@router.post("/orders/{order_id}/refund", status_code=201)
async def refund_overpayment(
    order_id: str,
    data: PrepayIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Refund an overpayment (prepayments exceeded the order total) from a cashbox."""
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")
    # Only money actually received beyond the total can go back out — this endpoint used to pay
    # any amount (or a negative one) out of the till.
    from backend.services.order_money import overpaid
    excess = await overpaid(db, o)
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    if data.amount > excess + 0.005:
        raise HTTPException(400, f"Only the overpayment can be refunded here ({excess:g})")
    await post_transaction(db, current.org_id, acc.id, "expense", -abs(data.amount),
                           comment=f"Refund overpayment {o.order_number}", ref_type="order",
                           ref_id=order_id, employee_id=current.id)
    refund_pay = Payment(invoice_id=None, order_id=order_id, client_id=o.client_id, method=data.method,
                         kind="refund", account_id=acc.id, amount=data.amount, received_by_id=current.id,
                         reference="Overpayment refund")
    db.add(refund_pay)
    if o.client_id:
        await db.flush()
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, o.client_id, "refund", abs(data.amount),
                                  note=f"Refund for {o.order_number}", ref_type="payment",
                                  ref_id=refund_pay.id, created_by_id=current.id)
    await db.commit()
    return {"ok": True, "refunded": data.amount}


class SubInvoiceIn(BaseModel):
    line_ids: list[str] = []


@router.post("/orders/{order_id}/invoices", status_code=201)
async def create_sub_invoice(
    order_id: str,
    data: SubInvoiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Create an invoice billing a SUBSET of the order's lines (multi-invoice per order)."""
    from backend.models import InvoiceLine, OrderLine
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
    chosen = [l for l in lines if not data.line_ids or l.id in data.line_ids]
    if not chosen:
        raise HTTPException(400, "No lines selected")
    subtotal = sum(l.total for l in chosen)
    await _check_backdating(db, current.org_id)
    inv = Invoice(invoice_number=await next_number(db, current.org_id, "invoice"), org_id=current.org_id, order_id=order_id,
                  client_id=o.client_id, status="draft", subtotal=subtotal, total=subtotal, issued_at=datetime.utcnow())
    db.add(inv)
    await db.flush()
    for l in chosen:
        db.add(InvoiceLine(invoice_id=inv.id, order_line_id=l.id, description=l.description,
                           qty=l.qty, unit_price=l.unit_price, total=l.total))
    await db.commit()
    return {"ok": True, "invoice_id": inv.id, "invoice_number": inv.invoice_number, "total": subtotal}


@router.get("/orders/{order_id}/invoices")
async def list_order_invoices(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    invs = (await db.execute(select(Invoice).where(Invoice.order_id == order_id, Invoice.org_id == current.org_id))).scalars().all()
    pays = (await db.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()
    return {
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "total": i.total,
                      "amount_paid": i.amount_paid, "status": i.status} for i in invs],
        "payments": [{"id": p.id, "kind": p.kind, "method": p.method, "amount": p.amount,
                      "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in pays],
    }


# ── Account report / ledger ──────────────────────────


@router.get("/transactions")
async def list_transactions(
    account_id: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    q = select(Transaction).where(Transaction.org_id == current.org_id)
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    q = q.order_by(desc(Transaction.actual_date)).limit(limit)
    rows = (await db.execute(q)).scalars().all()

    # name lookups
    accts = {a.id: a.name for a in (await db.execute(select(Account).where(Account.org_id == current.org_id))).scalars().all()}
    emps = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == current.org_id))).scalars().all()}
    items = {c.id: c.name for c in (await db.execute(select(CashFlowItem).where(CashFlowItem.org_id == current.org_id))).scalars().all()}

    return {"items": [{
        "id": t.id, "txn_type": t.txn_type, "amount": t.amount,
        "account": accts.get(t.account_id), "account_id": t.account_id,
        "cash_flow_item": items.get(t.cash_flow_item_id),
        "comment": t.comment, "ref_type": t.ref_type, "ref_id": t.ref_id,
        "employee": emps.get(t.employee_id), "is_retroactive": t.is_retroactive,
        "actual_date": t.actual_date.isoformat() if t.actual_date else None,
        # A1: append-only ledger fields
        "status": t.status, "balance_after": t.balance_after,
        "reverses_txn_id": t.reverses_txn_id, "void_reason": t.void_reason,
    } for t in rows]}


@router.get("/balances")
async def client_balances(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Clients with a non-zero balance (debt or credit)."""
    rows = (await db.execute(
        select(Client).where(Client.org_id == current.org_id, Client.balance != 0)
        .order_by(Client.balance)
    )).scalars().all()
    return {"items": [{"id": c.id, "name": c.name, "phone": c.phone, "balance": c.balance} for c in rows]}


@router.get("/summary")
async def cash_summary(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    accts = (await db.execute(select(Account).where(Account.org_id == current.org_id, Account.active == True))).scalars().all()  # noqa: E712
    total = sum(a.balance for a in accts if not a.is_virtual)
    debt = (await db.execute(
        select(func.coalesce(func.sum(Client.balance), 0)).where(Client.org_id == current.org_id, Client.balance < 0)
    )).scalar() or 0
    return {
        "total_cash": total,
        "client_debt": abs(debt),
        "accounts": [_ser_account(a) for a in accts],
    }
