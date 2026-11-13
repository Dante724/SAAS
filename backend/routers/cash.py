"""
Cash management — the cashbox system.

Accounts hold balances and gate which payment methods appear at checkout.
Every money movement posts a Transaction. Closing a paid order posts an
income transaction to the chosen account (or to client debt).
"""
import math
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import day_end_utc, day_start_utc, now_utc, parse_dt, shop_today, shop_tzname, today_in
from backend.models import (
    Account,
    CashFlowItem,
    Client,
    Employee,
    Invoice,
    InvoiceOrder,
    Location,
    Order,
    Organisation,
    Payment,
    Transaction,
)
from backend.services.auth import assert_perm, get_current_employee, has_perm, require_perm, record_scope
from backend.core import paging as _paging
from backend.services.money import MAX_MONEY, add_to_column, check_money, claim_status, q2
from backend.services import scoping
from backend.services.money import Pct, Rate  # NUMERIC input bounds (N3)


async def _check_backdating(db, org_id, issued_at: Optional[datetime] = None):
    """Audit FIN-06: with "Prevent backdating invoices" on, a new invoice may not be dated before
    the newest invoice already issued, nor in the future. ``issued_at`` is the date a person typed
    (a manual invoice); None means the invoice is dated now. Compared as shop days: the check used to
    compare the clock with the last invoice's date, so it never caught a backdated manual invoice
    and only fired after someone had future-dated one."""
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not (org and org.prevent_backdating_invoices):
        return
    tz = await shop_tzname(db, org_id)
    today = today_in(tz)
    new_day = _invoice_day(issued_at, tz) if issued_at else today
    if new_day > today:
        raise HTTPException(422, "Backdating is prevented, so an invoice can't be dated in the future either")
    last = (await db.execute(select(Invoice.issued_at).where(
        Invoice.org_id == org_id, Invoice.issued_at.isnot(None),
        func.coalesce(Invoice.status, "").not_in(("cancelled", "canceled")))
        .order_by(Invoice.issued_at.desc()).limit(1))).scalar_one_or_none()
    if last is not None and new_day < _invoice_day(last, tz):
        raise HTTPException(422, f"Backdating invoices is not allowed: the last invoice is dated "
                                 f"{_invoice_day(last, tz).isoformat()}")


def _invoice_day(value: datetime, tz: str):
    """The shop day an invoice is dated. A date a person picked is stored as that day's midnight;
    an invoice made by the system is stamped with the UTC instant."""
    from backend.services.dates import utc_to_wall
    if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
        return value.date()
    return utc_to_wall(value, tz).date()

router = APIRouter(prefix="/api/cash", tags=["cash"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


# ── helpers ──────────────────────────────────────────

class system_posting:
    """Re-review money N4: postings into an account the cashier didn't choose (the safe a cash drop
    goes into, the cashbox a job's payment came from when it is refunded on archive, the online
    account a customer's payment settles into) are not limited by the role's cashbox list. The
    scope is lifted only inside the block and put back after, even on error."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.prev = scoping.session_allowed_accounts(self.db)
        scoping.bind_account_scope(self.db, None)
        return self

    def __exit__(self, *exc):
        scoping.bind_account_scope(self.db, sorted(self.prev) if self.prev is not None else None)
        return False


async def post_transaction(db, org_id, account_id, txn_type, amount, *,
                           cash_flow_item_id=None, comment=None, ref_type=None,
                           ref_id=None, employee_id=None, transfer_pair_id=None,
                           fx_rate=None, is_retroactive=False, actual_date=None,
                           native_amount=False):
    """Create a transaction and update the account balance in one step.

    Documents (orders, invoices, sales, postings, refunds, client/supplier settlements) are
    priced in the org's base currency. Posting such an amount into a till held in another
    currency added e.g. 100 GBP-worth of order money as 100 USD, so a document leg must hit a
    base-currency account. Manual entries and transfers (which carry fx_rate) are native already;
    corrections pass native_amount=True because their delta is typed against the account itself.
    """
    # Scope the account to the org so a client-supplied account_id can't corrupt another
    # tenant's balance. An unknown or other shop's account is refused before anything is written
    # (it used to record a transaction against an account nobody here could see).
    acc = (await db.execute(select(Account).where(
        Account.id == account_id, Account.org_id == org_id))).scalar_one_or_none() if account_id else None
    if acc is None:
        raise HTTPException(404, "Account not found")
    # Audit SEC-02 / FIN-05: a role limited to some cashboxes can't move money in any other.
    scoping.assert_account_allowed(db, acc.id)
    txn = Transaction(
        org_id=org_id, account_id=account_id, txn_type=txn_type, amount=amount,
        cash_flow_item_id=cash_flow_item_id, comment=comment, ref_type=ref_type,
        ref_id=ref_id, employee_id=employee_id, transfer_pair_id=transfer_pair_id,
        fx_rate=fx_rate,
        is_retroactive=is_retroactive, actual_date=actual_date or now_utc(),
    )
    db.add(txn)
    if acc and acc.currency_code and ref_type not in (None, "manual") and fx_rate is None \
            and not native_amount:
        base = await _org_base_code(db, org_id)
        if acc.currency_code.upper() != base.upper():
            raise HTTPException(422, f"“{acc.name}” holds {acc.currency_code}, but this document is in "
                                     f"{base}. Take the payment into a {base} account, then transfer "
                                     "between accounts if the money is held in another currency.")
    if acc:
        from backend.services.money import add_to_column
        txn.balance_after = q2(await add_to_column(db, acc, "balance", amount))  # A1: running-balance snapshot
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
    if len(shifts) != 1:
        return None
    # Audit FIN-12: a close running now claims the shift (UPDATE ... WHERE closed_at IS NULL) and then
    # totals what the drawer should hold. Lock the shift row (FOR SHARE on Postgres: payments on the
    # same drawer don't wait for each other, a close does) and re-check it is still open in the same
    # statement. Either this payment's transaction ends first and the close counts it, or the close
    # committed first and the payment links to no shift; never to one that has been totalled already.
    # SQLite ignores the lock; it serialises writers anyway.
    return (await db.execute(select(CashShift.id).where(
        CashShift.id == shifts[0].id, CashShift.closed_at.is_(None))
        .with_for_update(read=True))).scalar_one_or_none()


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
    fee_pct: Pct = 0.0
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
    rows = [a for a in rows if scoping.account_allowed(db, a.id)]
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
    rows = (await db.execute(scoping.scope_accounts(select(Account).where(
        Account.org_id == current.org_id, Account.active == True), db, Account.id))).scalars().all()  # noqa: E712
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
    current: Employee = Depends(require_perm("can_manage_accounts")),
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
    # Audit FIN-10: a cashbox's method and fees are set up like the cashbox itself (create and
    # delete need this right); "View finance" alone could turn a cash till into a card account.
    current: Employee = Depends(require_perm("can_manage_accounts")),
):
    a = (await db.execute(select(Account).where(Account.id == account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Account not found")
    scoping.assert_account_allowed(db, a.id)
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    a.name, a.icon, a.payment_option = data.name, data.icon, data.payment_option
    a.is_company, a.is_virtual = data.is_company, data.is_virtual
    a.fee_pct, a.fee_fixed = data.fee_pct, data.fee_fixed
    await db.commit()
    return _ser_account(a)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_accounts")),
):
    a = (await db.execute(select(Account).where(Account.id == account_id, Account.org_id == current.org_id)
                          .with_for_update())).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Account not found")
    scoping.assert_account_allowed(db, a.id)
    # Audit FIN-10: a deactivated account drops out of Total money and every cashbox list, so
    # money still in it vanished from the reports. Move it out first (a transfer), then close it.
    if q2(a.balance or 0) != 0:
        raise HTTPException(409, f"“{a.name}” still holds {q2(a.balance or 0):.2f}. Transfer the money to "
                                 "another account first, then deactivate it.")
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

    @field_validator("amount")
    @classmethod
    def _amount(cls, v):   # audit FIN-11: finite, positive, below the money cap
        check_money(v, allow_negative=True)
        if q2(abs(v)) <= 0:
            raise ValueError("Amount must be greater than zero")
        return v


@router.post("/transactions", status_code=201)
async def create_transaction(
    data: TxnIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """A manual income or expense. A retry with the same Idempotency-Key gets the first answer
    back instead of posting twice."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "cash.transaction", data,
                          lambda: _create_transaction(data, db, current))


async def _create_transaction(data: TxnIn, db: AsyncSession, current: Employee):
    if data.txn_type not in ("income", "expense"):
        raise HTTPException(400, "Use income or expense (transfer has its own endpoint)")
    await assert_perm(db, current, "can_create_incomes" if data.txn_type == "income" else "can_create_expenses")
    amt = q2(abs(data.amount)) * (1 if data.txn_type == "income" else -1)
    retro = False
    actual = None
    if data.actual_date:
        actual = parse_dt(data.actual_date, "actual_date")
        today = await shop_today(db, current.org_id)
        if actual.date() > today:   # audit FIN-11: money isn't recorded before it moves
            raise HTTPException(422, "The date can't be in the future")
        retro = actual.date() < today
        if retro:
            await assert_perm(db, current, "can_retroactive_cash")
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
_DOCUMENT_LEGS = {"order": "work order payment", "invoice": "invoice payment", "sale": "sale",
                  "payroll_run": "payroll run"}


_CHANGED = "Someone else just changed this transaction. Refresh and try again."


SUPPLIER_PAYMENT_REVERSAL = "supplier_payment_reversal"   # Transaction.ref_type of a reversal row

# Audit FIN-02: cash legs with a twin on a contact's books. A client advance or payout (and an
# invoice overpayment kept as credit) also moved the client's balance; a supplier payment also
# lowered what we owe them. Voiding or correcting only the cash row reversed the till while the
# client kept the credit (and could spend it) or the supplier stayed paid, and each side still
# matched its own ledger. Like the document legs above, they're changed from the contact side.
# Restore stays allowed: since this guard, such a leg can't be deleted, so a deleted one was
# voided on its own before it, and restoring it puts the till back in line with the contact
# (reconcile reports these as "split_leg").
_CONTACT_LEGS = {
    "client": "This transaction is the cash side of a client balance movement (an advance, a payout or "
              "an overpayment kept as credit). Undo it from the client's Balance tab with the opposite "
              "movement, so the till and their credit stay in sync.",
    "supplier_payment": "This transaction is the cash side of a supplier payment, which also lowered what "
                        "you owe that supplier. It can't be undone from Cash on its own, or the supplier "
                        "would still show as paid.",
    # The till side of a reversed supplier payment (routers/suppliers.reverse_supplier_payment): it put
    # the money back and the supplier balance up again, so voiding it alone would split them again.
    SUPPLIER_PAYMENT_REVERSAL: "This transaction reversed a supplier payment, which also put what you owe "
                               "that supplier back. It can't be undone from Cash on its own; pay the "
                               "supplier again instead.",
    # Audit FIN-09: the money a gift card was sold for. Voiding it alone leaves the card spendable.
    "gift_card": "This transaction is the money a gift card was sold for. It can't be undone from Cash on "
                 "its own, or the card would stay spendable with no money behind it.",
}


def _guard_document_leg(t) -> None:
    if t.ref_type in _DOCUMENT_LEGS:
        raise HTTPException(409, f"This transaction is the cash side of a {_DOCUMENT_LEGS[t.ref_type]}. "
                                 "Refund or void it from that document so everything stays in sync.")
    if t.ref_type in _CONTACT_LEGS:
        raise HTTPException(409, _CONTACT_LEGS[t.ref_type])


@router.post("/transactions/{txn_id}/correct", status_code=201)
async def correct_transaction(
    txn_id: str,
    data: CorrectionIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_correct_transactions")),
):
    """Correct a transaction by posting a signed delta row that references the
    original. The original is never mutated (amount/method stay put); the ledger
    stays append-only. Reason is mandatory. Audit FIN-13: a retry with the same Idempotency-Key
    gets the first answer back; without a key the same correction again within a few seconds is
    refused as a double click."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "cash.correct", {"txn_id": txn_id, "body": data},
                          lambda: _correct_transaction(txn_id, data, db, current, keyed=_keyed(request)))


async def _correct_transaction(txn_id: str, data: CorrectionIn, db: AsyncSession, current: Employee, *,
                               keyed: bool):
    orig = await _get_txn(db, current.org_id, txn_id)
    if orig.status == "deleted":
        raise HTTPException(400, "Cannot correct a deleted transaction")
    _guard_document_leg(orig)
    scoping.assert_account_allowed(db, orig.account_id)
    # Audit FIN-13: shifting one half of a transfer left the pair no longer netting to zero.
    if orig.transfer_pair_id:
        raise HTTPException(409, "This is one side of a transfer. Delete the transfer and make it again "
                                 "with the right amount, or transfer the difference.")
    if not (data.reason or "").strip():
        raise HTTPException(400, "A reason is required")
    delta = q2(data.delta) if data.delta is not None and math.isfinite(data.delta) else 0
    if delta == 0:
        raise HTTPException(400, "Delta must be non-zero")
    if abs(delta) >= MAX_MONEY:
        raise HTTPException(422, "Amount is too large")
    # Serialise corrections of one transaction (row lock; a write on SQLite), then refuse the
    # same delta posted again a moment ago without a key.
    from sqlalchemy import update
    await db.execute(update(Transaction).where(Transaction.id == orig.id)
                     .values(status=Transaction.status).execution_options(synchronize_session=False))
    if not keyed:
        since = now_utc() - timedelta(seconds=_DOUBLE_CLICK_S)
        if (await db.execute(select(Transaction.id).where(
                Transaction.org_id == current.org_id, Transaction.reverses_txn_id == orig.id,
                Transaction.amount == delta, Transaction.created_at >= since).limit(1))).scalar_one_or_none():
            raise HTTPException(409, "This correction was just posted. If it's needed twice, post it again in a moment.")
    corr = await post_transaction(
        db, current.org_id, orig.account_id, orig.txn_type, delta,
        cash_flow_item_id=orig.cash_flow_item_id,
        comment=f"Correction: {data.reason.strip()}",
        ref_type=orig.ref_type, ref_id=orig.ref_id, employee_id=current.id,
        native_amount=True,
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
        # by id, so two requests on the two legs lock them in the same order (no deadlock)
        legs = (await db.execute(select(Transaction).where(
            Transaction.org_id == current.org_id,
            Transaction.transfer_pair_id == t.transfer_pair_id).order_by(Transaction.id))).scalars().all()
    for leg in legs:   # SEC-02: both sides of a transfer must be accounts this role may use
        scoping.assert_account_allowed(db, leg.account_id)
    for leg in legs:
        if leg.status == "deleted":
            continue
        # Claim the leg first: a second identical request that read it as active finds it
        # already deleted here and reverses nothing.
        if not await claim_status(db, leg, leg.status, "deleted",
                                  void_reason=reason, voided_by_id=current.id):
            await db.rollback()
            raise HTTPException(409, _CHANGED)
        acc = (await db.execute(select(Account).where(Account.id == leg.account_id))).scalar_one_or_none()
        if acc:
            # reverse; do NOT touch other rows' balance_after
            await add_to_column(db, acc, "balance", -float(leg.amount or 0))
    await db.commit()
    return {"ok": True, "voided": len(legs)}


SPLIT_LEG_ACCEPTED = "cash.split_leg_accepted"   # services/reconcile reads it


class AcceptSplitIn(BaseModel):
    reason: str


# Contact legs that accept-split takes, and the shape that makes one split: voided on its own, or a
# correction delta the contact never saw (services/reconcile._split_legs reports the same rows).
_SPLIT_LEG_TYPES = ("client", "supplier_payment")


def _is_split_leg(t) -> bool:
    if t.ref_type not in _SPLIT_LEG_TYPES:
        return False
    return bool((t.status == "deleted" and not t.reverses_txn_id) or (t.status != "deleted" and t.reverses_txn_id))


def supplier_payment_reversible(t) -> bool:
    """A supplier payment as booked (not a correction delta), still standing."""
    return (t.ref_type == "supplier_payment" and t.status == "active" and not t.reverses_txn_id
            and float(t.amount or 0) < 0)


async def _split_leg_accepted(db, org_id: str, txn_id: str) -> bool:
    from backend.models import AuditLog
    return (await db.execute(select(AuditLog.id).where(
        AuditLog.org_id == org_id, AuditLog.action == SPLIT_LEG_ACCEPTED,
        AuditLog.entity_type == "transaction", AuditLog.entity_id == txn_id).limit(1))).first() is not None


@router.post("/transactions/{txn_id}/accept-split")
async def accept_split_leg(
    txn_id: str,
    data: AcceptSplitIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_correct_transactions")),
):
    """Re-review money N7: a client/supplier cash leg voided on its own, or corrected, before those
    were guarded (#213) moved the till but not the contact, and can no longer be deleted or corrected
    (409). Once the contact side has been settled by hand, the owner accepts it here with a reason:
    the nightly split_leg sweep stops reporting it, and a voided one can't be restored any more
    (that would put the till off by the same amount again). Nothing moves; the record says who and why."""
    t = await _get_txn(db, current.org_id, txn_id)
    if t.ref_type not in _SPLIT_LEG_TYPES:
        raise HTTPException(409, "Only the cash side of a client balance movement or a supplier payment is "
                                 "checked against its contact.")
    if not _is_split_leg(t):
        raise HTTPException(409, "This transaction is in step with its contact; there's nothing to accept.")
    scoping.assert_account_allowed(db, t.account_id)
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(400, "A reason is required")
    if await _split_leg_accepted(db, current.org_id, t.id):
        return {"ok": True, "accepted": True}
    from backend.models import AuditLog
    db.add(AuditLog(org_id=current.org_id, actor_id=current.id, action=SPLIT_LEG_ACCEPTED,
                    entity_type="transaction", entity_id=t.id, diff={"reason": reason[:300]}))
    await db.commit()
    return {"ok": True, "accepted": True}


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
    if t.ref_type == "payroll_run":   # voided with its run; reviving it alone would pay twice
        raise HTTPException(409, "This was the payout of a voided payroll run. Accrue and pay the period again instead.")
    # Same guards as delete: a sale, payment or refund leg was voided with its document, and
    # bringing back only the cash row would put money in the till while the document stays void.
    if t.ref_type in ("refund", "client_return", "purchase_return"):
        raise HTTPException(409, f"This transaction settled a {t.ref_type.replace('_', ' ')} that was "
                                 "deleted. Create the return/refund again instead.")
    if t.ref_type in _DOCUMENT_LEGS:
        raise HTTPException(409, f"This transaction is the cash side of a {_DOCUMENT_LEGS[t.ref_type]} "
                                 "that was voided. Take the payment again from that document instead.")
    # Re-review N7: a contact leg accepted as settled by hand stays voided; restoring it would count
    # the money a second time.
    if t.ref_type in _CONTACT_LEGS and await _split_leg_accepted(db, current.org_id, t.id):
        raise HTTPException(409, "This transaction was accepted as settled on the contact's side, so restoring "
                                 "it would count the money twice.")
    legs = [t]
    if t.transfer_pair_id:
        legs = (await db.execute(select(Transaction).where(
            Transaction.org_id == current.org_id,
            Transaction.transfer_pair_id == t.transfer_pair_id).order_by(Transaction.id))).scalars().all()
    for leg in legs:
        scoping.assert_account_allowed(db, leg.account_id)
    for leg in legs:
        if leg.status != "deleted":
            continue
        if not await claim_status(db, leg, "deleted", "active", void_reason=None, voided_by_id=None):
            await db.rollback()
            raise HTTPException(409, _CHANGED)
        acc = (await db.execute(select(Account).where(Account.id == leg.account_id))).scalar_one_or_none()
        if acc:
            await add_to_column(db, acc, "balance", float(leg.amount or 0))
    await db.commit()
    return {"ok": True, "restored": len(legs)}


class TransferIn(BaseModel):
    from_account_id: str
    to_account_id: str
    amount: float
    rate: Optional[Rate] = None       # Build 144cr: exchange rate for cross-currency (1 secondary = rate base)
    comment: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def _amount(cls, v):   # audit FIN-11
        return check_money(v, allow_negative=True)


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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_transfer_money")),
):
    """Move money between two accounts. A retry with the same Idempotency-Key gets the first
    answer back instead of moving it twice."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "cash.transfer", data,
                          lambda: _transfer(data, db, current))


async def _transfer(data: TransferIn, db: AsyncSession, current: Employee):
    if data.from_account_id == data.to_account_id:
        raise HTTPException(400, "Choose two different accounts")
    if q2(data.amount) <= 0:
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
    # The money must come out of a cashbox this role may use (checked above); it may go into any
    # (a cash drop from the till into the safe). The role still can't see the safe's balance.
    with system_posting(db):
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
    current: Employee = Depends(get_current_employee),
):
    """What the close-order payment window needs: order total, and the accounts
    available per payment method. Open to anyone who can take payments, not only finance."""
    if not (await has_perm(db, current, "can_take_payments") or await has_perm(db, current, "can_view_finance")):
        raise HTTPException(403, "Your role does not allow this action (can_take_payments)")
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    accts = (await db.execute(select(Account).where(Account.org_id == current.org_id, Account.active == True))).scalars().all()  # noqa: E712
    by_method = {"cash": [], "cashless": [], "card": []}
    for a in accts:
        if scoping.account_allowed(db, a.id):   # SEC-02: only the cashboxes this role may use
            by_method.setdefault(a.payment_option, []).append(_ser_account(a))
    # Paid net of refunds, and what's still owed net of credit notes: the shared definitions the
    # pay endpoint enforces, so the pre-filled amount is never more than the balance due.
    from backend.services.order_money import balance_due, credited, net_paid, overpaid
    prepaid = (await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).select_from(Payment)
        .where(Payment.order_id == order_id, Payment.kind == "prepayment")
    )).scalar() or 0
    return {
        "order_total": o.total or 0,
        "location_id": o.location_id,
        "item_cost": (o.labour_total or 0) + (o.parts_total or 0),
        "already_paid": await net_paid(db, order_id),
        "credited": await credited(db, order_id),
        "prepaid": prepaid,
        "remaining": await balance_due(db, o),
        "overpaid": await overpaid(db, o),   # refund due if prepayments exceed total
        "written_off": float(o.written_off or 0),   # unpaid balance of a dropped job
        "accounts_by_method": by_method,
    }


async def _before_new_invoice(db, org_id, o, check_backdating):
    """A payment on a job with no invoice yet creates one: that's a new document, so the
    backdating rule applies (captured card money skips it)."""
    from backend.services.invoice_money import invoices_for_order
    if check_backdating and not await invoices_for_order(db, o.id):
        await _check_backdating(db, org_id)


async def post_order_payment(db, org_id, o, amount, acc, method, *, actor_id=None, reference=None,
                             provider_ref=None, comment=None, check_backdating=True):
    """Money in for a work order, into a cashbox: income transaction (+ the account's processing
    fee), Payments on the job's invoices (services/invoice_money: each invoice up to its balance),
    the client's ledger, and any open pay links closed once nothing is owed. One poster for the
    Take payment dialog and for card readers (services/terminal), so the two never drift. Caller
    logs, commits and notifies. Returns (first payment, fee)."""
    from backend.services.invoice_money import apply_order_payment
    from backend.services.order_money import cancel_open_pay_links
    await _before_new_invoice(db, org_id, o, check_backdating)

    # default cash flow item: org's per-doc-type default (2b), else the "Sales" category
    _org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    sales_item = None
    if _org and _org.cash_category_tickets_id:
        sales_item = (await db.execute(select(CashFlowItem).where(
            CashFlowItem.id == _org.cash_category_tickets_id))).scalar_one_or_none()
    if not sales_item:
        sales_item = (await db.execute(
            select(CashFlowItem).where(CashFlowItem.org_id == org_id, CashFlowItem.name == "Sales")
        )).scalar_one_or_none()

    # income transaction to the account
    await post_transaction(db, org_id, acc.id, "invoice_payment", abs(amount),
                           cash_flow_item_id=sales_item.id if sales_item else None,
                           comment=comment or f"Payment for {o.order_number}", ref_type="order",
                           ref_id=o.id, employee_id=actor_id)

    # transaction fee (auto-deduct as expense)
    fee = 0.0
    if acc.fee_pct or acc.fee_fixed:
        fee = q2(abs(amount) * acc.fee_pct / 100 + acc.fee_fixed)
        if fee > 0:
            fee_item = (await db.execute(
                select(CashFlowItem).where(CashFlowItem.org_id == org_id, CashFlowItem.name == "Transaction fees")
            )).scalar_one_or_none()
            await post_transaction(db, org_id, acc.id, "fee", -fee,
                                   cash_flow_item_id=fee_item.id if fee_item else None,
                                   comment=f"Processing fee for {o.order_number}", ref_type="order",
                                   ref_id=o.id, employee_id=actor_id)

    pays = await apply_order_payment(
        db, o, amount, method=method, account_id=acc.id, received_by_id=actor_id,
        reference=reference, provider_ref=provider_ref,
        # cash into a till counts toward that drawer's close (was never linked → a short
        # drawer looked balanced or "over")
        shift_id=await open_drawer_shift(db, org_id, acc) if method == "cash" else None)
    if o.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, org_id, o.client_id, "income", -abs(amount),
                                  note=f"Payment for {o.order_number}", ref_type="payment",
                                  ref_id=pays[0].id, created_by_id=actor_id)
    await db.flush()
    await cancel_open_pay_links(db, o)   # a link sent earlier must not charge the customer again
    return pays[0], fee


# W3-C: the same payment for a job, into the same cashbox, this close together and without an
# Idempotency-Key, is one click sent twice: the second is refused (as till returns are, #82). A
# deliberate second payment comes from the payment dialog with its own key, so it goes through.
_DOUBLE_CLICK_S = 10


def _keyed(request) -> bool:
    return bool(request is not None and (request.headers.get("Idempotency-Key") or "").strip())


async def _refuse_double_click(db, org_id, order_id, account_id, amount) -> None:
    """Called under the job's lock, so a second identical request sees the first one's posting."""
    since = now_utc() - timedelta(seconds=_DOUBLE_CLICK_S)
    hit = (await db.execute(select(Transaction.id).where(
        Transaction.org_id == org_id, Transaction.account_id == account_id, Transaction.ref_type == "order",
        Transaction.ref_id == order_id, Transaction.txn_type == "invoice_payment", Transaction.status != "deleted",
        Transaction.amount == q2(abs(amount)), Transaction.created_at >= since).limit(1))).scalar_one_or_none()
    if hit:
        raise HTTPException(409, f"{q2(abs(amount)):.2f} was just taken for this job into this account. "
                                 "If the customer is paying again, take it again in a moment.")


@router.post("/orders/{order_id}/pay")
async def pay_order(
    order_id: str,
    data: OrderPaymentIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_take_payments")),
):
    # A retried click with the same Idempotency-Key returns the first payment's answer: the row
    # lock below only stops a second "pay the balance", not a part payment sent twice.
    from backend.services.idempotency import idem_begin
    claim = await idem_begin(db, current.org_id, request, "cash.pay_order")
    if claim.replay is not None:
        return claim.replay
    try:
        result = await _pay_order(order_id, data, db, current, keyed=_keyed(request))
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


async def _pay_order(order_id: str, data: OrderPaymentIn, db: AsyncSession, current: Employee, *,
                     keyed: bool = True):
    # Lock the order row so two concurrent "pay" requests serialize (Postgres) — combined with the
    # remaining<=0 guard below this prevents a double-click / webhook-race from double-posting a
    # payment. (with_for_update is a no-op on SQLite, which already serializes writes.)
    # FOR NO KEY UPDATE, not FOR UPDATE: invoices and payments reference the job by foreign key,
    # so writing one of them takes FOR KEY SHARE on the job, which a plain FOR UPDATE held by the
    # other job's payment blocks — two jobs billed on one invoice and paid together deadlocked.
    # NO KEY UPDATE still serialises two payments on one job. Locking the job's invoices before
    # the job instead (Invoice -> Order) deadlocked against prepayments, pay links, the card
    # reader and refunds, which all take Order -> Invoice.
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id)
        .with_for_update(key_share=True))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    # remaining = total minus everything already paid, net of refunds and credit notes (shared definition)
    from backend.services.order_money import balance_due, cancel_open_pay_links, reader_charge_in_flight, refuse_archived
    refuse_archived(o)
    if await reader_charge_in_flight(db, order_id):
        raise HTTPException(409, "A card reader charge for this order is still in progress. "
                                 "Finish or cancel it on the reader first.")
    remaining = await balance_due(db, o)
    amount = data.amount if data.amount is not None else max(remaining, 0)
    # Guard: positive amounts only, and don't let a "pay the balance" tap double-post once the
    # order is already settled (double-click / retry → remaining 0 → amount 0 → rejected here).
    if amount is None or amount <= 0:
        raise HTTPException(400, "Nothing to pay — this order is already settled" if remaining <= 0 else "Payment amount must be positive")
    # Never more than the balance due (credit notes included): money for later goes in as a
    # prepayment, an invoice overpayment through Invoices › Record payment.
    if q2(amount) > q2(remaining):
        raise HTTPException(400, f"That's more than is owed. The balance due is {q2(max(remaining, 0)):.2f}.")

    from backend.routers.feed import log_system_event

    from backend.services.invoice_money import apply_order_payment
    if data.method == "balance":
        # Paid out of credit the client already has with the shop (an advance, an overpayment).
        # No money moves: posting "income" here erased real debt when the client had no credit at
        # all (B3). The credit is spent by charging the job up front (spend_credit), so the same
        # credit can't pay a second job before this one closes.
        from backend.services.ledger import available_credit, spend_credit
        credit = await available_credit(db, current.org_id, o.client_id, order_id)
        if q2(amount) > credit:
            raise HTTPException(400, f"This client has only {credit:.2f} of credit to pay from. "
                                     "Take the rest in cash or by card.")
        await _before_new_invoice(db, current.org_id, o, True)
        await apply_order_payment(db, o, amount, method="balance", received_by_id=current.id,
                                  reference="Paid from client credit")
        await spend_credit(db, current.org_id, o.client_id, amount, order_id=order_id,
                           parts=[(order_id, amount)], label=o.order_number, created_by_id=current.id)
        await log_system_event(db, current.org_id, "order", order_id,
                               f"Paid from client credit: {amount}")
        await db.flush()
        await cancel_open_pay_links(db, o)
        from backend.services.notification_rules import queue_payment_received
        _pushes: list = []
        await queue_payment_received(db, current.org_id, [order_id], current.id, push_later=_pushes)   # WO-08
        await db.commit()
        from backend.services.push import dispatch_deferred
        await dispatch_deferred(db, _pushes)
        return {"ok": True, "method": "balance", "amount": amount}

    # cash/cashless/card -> must hit an account
    if not data.account_id:
        raise HTTPException(400, "Select a cashbox/account")
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")
    scoping.assert_account_allowed(db, acc.id)
    if not keyed:
        from backend.services.invoice_money import lock_order
        await lock_order(db, o.id)
        await _refuse_double_click(db, current.org_id, o.id, acc.id, amount)

    _pay, fee = await post_order_payment(db, current.org_id, o, amount, acc, data.method, actor_id=current.id)
    await log_system_event(db, current.org_id, "order", order_id,
                           f"Payment {amount} via {data.method} to {acc.name}")
    # 144jc: payment_received notifications (client receipt + staff), saved with the payment.
    from backend.services.notification_rules import queue_payment_received
    _pushes: list = []
    await queue_payment_received(db, current.org_id, [order_id], current.id, push_later=_pushes)   # WO-08
    await db.commit()
    from backend.services.push import dispatch_deferred
    await dispatch_deferred(db, _pushes)
    return {"ok": True, "method": data.method, "amount": amount, "fee": fee, "account": acc.name}


class PrepayIn(BaseModel):
    amount: float
    account_id: str
    method: str = "cash"

    @field_validator("amount")
    @classmethod
    def _amount(cls, v):   # audit FIN-11: finite and below the cap; > 0 is checked with a clear 400
        return q2(check_money(v, allow_negative=True))


@router.post("/orders/{order_id}/prepay", status_code=201)
async def prepay_order(
    order_id: str,
    data: PrepayIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Take a prepayment on an order. Posts income to the chosen cashbox and
    records a Payment(kind=prepayment) that the close-window math subtracts. It lands on the
    job's open invoice now, or on the job's next invoice when it gets one (G4).
    A double click with the same Idempotency-Key posts once and gets the first answer back."""
    from backend.services.idempotency import idem_begin
    claim = await idem_begin(db, current.org_id, request, "cash.prepay_order")
    if claim.replay is not None:
        return claim.replay
    try:
        result = await _prepay_order(order_id, data, db, current, keyed=_keyed(request))
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


async def _prepay_order(order_id: str, data: PrepayIn, db: AsyncSession, current: Employee, *,
                        keyed: bool = True):
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    # The job's lock (a real one on SQLite too), then a fresh read: two prepayments at once both
    # count in prepayment_total below.
    from backend.services.invoice_money import lock_invoices_of, lock_order
    await lock_order(db, o.id)
    await lock_invoices_of(db, o.id)     # then its invoices, before the till and the ledger (M22)
    await db.refresh(o)
    from backend.services.order_money import refuse_archived
    refuse_archived(o)
    if data.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")
    scoping.assert_account_allowed(db, acc.id)
    if not keyed:
        await _refuse_double_click(db, current.org_id, o.id, acc.id, data.amount)
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
    o.prepayment_total = (o.prepayment_total or 0) + data.amount
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order_id, f"Prepayment {data.amount} to {acc.name}")
    from backend.services.invoice_money import apply_order_money
    from backend.services.order_money import cancel_open_pay_links, resync_write_off
    await db.flush()
    # The invoice before the client ledger (Invoice -> Client), the order Invoices › Record
    # payment takes: posting the ledger first deadlocked against an invoice payment taken at the
    # same moment.
    await apply_order_money(db, o)
    if o.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, o.client_id, "prepayment", -abs(data.amount),
                                  note=f"Prepayment for {o.order_number}", ref_type="payment",
                                  ref_id=pay.id, created_by_id=current.id)
    await resync_write_off(db, o, current.id)   # paid after dropping: less is written off
    await cancel_open_pay_links(db, o)
    from backend.services.notification_rules import queue_payment_received
    _pushes: list = []
    await queue_payment_received(db, current.org_id, [order_id], current.id, push_later=_pushes)   # WO-08
    await db.commit()
    from backend.services.push import dispatch_deferred
    await dispatch_deferred(db, _pushes)
    return {"ok": True, "prepaid": o.prepayment_total}


@router.post("/orders/{order_id}/refund", status_code=201)
async def refund_overpayment(
    order_id: str,
    data: PrepayIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Audit FIN-01: a retry with the same Idempotency-Key gets the first answer back instead of
    paying out again; without a key, the same refund again within a few seconds is refused."""
    from backend.services.idempotency import run_once
    return await run_once(db, current.org_id, request, "cash.refund_overpayment",
                          {"order_id": order_id, "body": data},
                          lambda: _refund_overpayment(order_id, data, db, current, keyed=_keyed(request)))


async def _refund_overpayment(order_id: str, data: PrepayIn, db: AsyncSession, current: Employee, *, keyed: bool):
    """Give money back on a job without changing its price, from a cashbox: an overpayment
    (prepayments exceeded the order total) or (W3-C) a deposit on a job that isn't closed yet —
    the customer pays at pickup instead. The job's total stays; only what was paid goes down. It's
    a refund document like any other (listed under refunds, deletable). A goodwill refund or
    returned goods go through /api/returns/refunds, which also lowers the bill."""
    await assert_perm(db, current, "can_create_returns")   # paying money back, not just seeing it
    # Row lock (B11): two refunds at once both read the same overpayment and paid it out twice.
    # NO KEY UPDATE: the refund's Payment row references the job (see _pay_order).
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id)
                          .with_for_update(key_share=True))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    from backend.services.invoice_money import lock_order
    await lock_order(db, o.id)          # SQLite ignores FOR UPDATE: two refunds queue up here
    await db.refresh(o)
    acc = (await db.execute(select(Account).where(Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Account not found")
    scoping.assert_account_allowed(db, acc.id)
    # Only money actually received beyond the total, or a deposit still held on an open job, can go
    # back out here — this endpoint used to pay any amount (or a negative one) out of the till.
    from backend.routers.returns import book_refund, refundable_without_discount
    from backend.services.order_money import overpaid
    excess = await overpaid(db, o)
    room = max(excess, await refundable_without_discount(db, o))
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "Refund amount must be positive")
    if data.amount > room + 0.005:
        raise HTTPException(400, f"Only the overpayment can be refunded here ({excess:g})" if room <= excess + 0.005
                                 else f"Only the deposit still held can be refunded here ({room:g})")
    if not keyed:
        from backend.routers.returns import refuse_double_refund
        await refuse_double_refund(db, current.org_id, o.id, acc.id, data.amount)
    what = "Overpayment refund" if data.amount <= excess + 0.005 else "Deposit refund"
    refund = await book_refund(db, current, o, acc, data.amount, reason=what, reference=what)
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "order", order_id, f"{what} {data.amount:g} from {acc.name}")
    await db.commit()
    return {"ok": True, "refunded": data.amount, "id": refund.id}


class SubInvoiceIn(BaseModel):
    line_ids: list[str] = []


@router.post("/orders/{order_id}/invoices", status_code=201)
async def create_sub_invoice(
    order_id: str,
    data: SubInvoiceIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    # the same right as POST /finance/invoices: invoicing isn't taking money
    current: Employee = Depends(require_perm("can_create_invoices")),
):
    """Create an invoice billing a SUBSET of the order's lines (multi-invoice per order)."""
    from backend.models import OrderLine
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    from backend.services.order_money import refuse_archived
    refuse_archived(o)
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
    # A line is billed once. No lines picked = bill the rest (every line not on an invoice yet).
    from backend.services.invoice_money import billed_on, lock_order
    await lock_order(db, o.id)
    whole, billed = await billed_on(db, o.id)
    if whole:
        if data.line_ids:
            raise HTTPException(409, f"This job is billed in full on invoice {whole.invoice_number}, "
                                     "which follows the job. Edit that invoice instead.")
        response.status_code = 200
        return {"ok": True, "invoice_id": whole.id, "invoice_number": whole.invoice_number,
                "total": whole.total, "existing": True}
    again = [l for l in lines if l.id in billed and l.id in (data.line_ids or [])]
    if again:
        raise HTTPException(409, f"'{again[0].description}' is already on invoice {billed[again[0].id].invoice_number}.")
    chosen = [l for l in lines if (l.id in data.line_ids if data.line_ids else l.id not in billed)]
    if not chosen:
        raise HTTPException(400, "Every line on this job is already billed" if billed else "No lines selected")
    await _check_backdating(db, current.org_id)
    from backend.services.invoice_money import new_invoice
    inv = await new_invoice(db, org_id=current.org_id, orders=[o], lines=chosen)
    await db.commit()
    return {"ok": True, "invoice_id": inv.id, "invoice_number": inv.invoice_number, "total": inv.total}


@router.get("/orders/{order_id}/invoices")
async def list_order_invoices(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_invoices")),   # as GET /finance/invoices
):
    await assert_perm(db, current, "can_view_invoices_tab")   # the work order's Invoices and payments tab
    if not (await db.execute(select(Order.id).where(
            Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none():
        raise HTTPException(404, "Order not found")
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order_id)   # shared invoices too
    invs = (await db.execute(select(Invoice).where(
        (Invoice.order_id == order_id) | Invoice.id.in_(linked), Invoice.org_id == current.org_id)
        .order_by(Invoice.created_at))).scalars().all()
    pays = (await db.execute(select(Payment).where(
        Payment.order_id == order_id, Payment.org_id == current.org_id))).scalars().all()
    return {
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "total": i.total,
                      "amount_paid": i.amount_paid, "status": i.status} for i in invs],
        "payments": [{"id": p.id, "kind": p.kind, "method": p.method, "amount": p.amount,
                      "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in pays],
    }


# ── Account report / ledger ──────────────────────────

async def void_document_legs(db, org_id, ref_type, ref_id, reason, actor_id) -> int:
    """Soft-delete the cash legs a document posted (a sale's takings, a refund's payout) when
    the document itself is voided: each leg's amount comes back out of its account balance and
    the row stays, struck through. The document-side caller has already undone everything else."""
    # In account order: two voids whose documents hit tills A and B in opposite order each took
    # one till's row lock and waited for the other's (Postgres then failed one with a deadlock).
    legs = (await db.execute(select(Transaction).where(
        Transaction.org_id == org_id, Transaction.ref_type == ref_type, Transaction.ref_id == ref_id,
        func.coalesce(Transaction.status, "active") != "deleted")
        .order_by(Transaction.account_id, Transaction.id))).scalars().all()
    # SEC-02 (re-review): every cashbox the document touched must be one this role may use, checked
    # before any leg moves (the caller's other undo steps roll back with the 403).
    for leg in legs:
        scoping.assert_account_allowed(db, leg.account_id)
    for leg in legs:
        acc = (await db.execute(select(Account).where(
            Account.id == leg.account_id, Account.org_id == org_id))).scalar_one_or_none()
        if acc:
            # Atomic like post_transaction: a read-modify-write here lost a sale that posted
            # into the same till between the read and the commit.
            await add_to_column(db, acc, "balance", -float(leg.amount or 0))
        leg.status = "deleted"
        leg.void_reason = reason
        leg.voided_by_id = actor_id
    return len(legs)


async def cash_flow_floor(db, current) -> Optional[datetime]:
    """Without "View the cash flow for any period" a role sees today's money movements only:
    the moment the shop's today began (as naive UTC) to filter from, or None when every period
    is open to them."""
    if await has_perm(db, current, "can_view_cashflow_any_period"):
        return None
    tz = await shop_tzname(db, current.org_id)
    return day_start_utc(today_in(tz), tz)



@router.get("/transactions")
async def list_transactions(
    account_id: Optional[str] = None,
    skip: int = _paging.skip_query(),
    limit: int = _paging.limit_query(100),
    summary: bool = False,          # also return totals over every matching row (not just this page)
    day: Optional[str] = None,      # YYYY-MM-DD for the summary's "today" in/out
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    limit = _paging.clamp(limit)
    q = select(Transaction).where(Transaction.org_id == current.org_id)
    if account_id:
        scoping.assert_account_allowed(db, account_id)
        q = q.where(Transaction.account_id == account_id)
    q = scoping.scope_accounts(q, db, Transaction.account_id)   # SEC-02: only usable cashboxes
    since = await cash_flow_floor(db, current)
    if since:
        q = q.where(Transaction.actual_date >= since)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    tz = None
    if summary and day:
        # "In today" is the shop's day: the day starts at the branch's midnight, not UTC's (B3).
        loc = (await db.execute(select(Account.location_id).where(
            Account.id == account_id, Account.org_id == current.org_id))).scalar() if account_id else None
        tz = await shop_tzname(db, current.org_id, loc)
    extra = {"summary": await _txn_summary(db, q, day, tz)} if summary else {}
    q = q.order_by(desc(Transaction.actual_date), desc(Transaction.id)).offset(skip).limit(limit)
    rows = (await db.execute(q)).scalars().all()

    # name lookups
    accts = {a.id: a.name for a in (await db.execute(select(Account).where(Account.org_id == current.org_id))).scalars().all()}
    emps = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == current.org_id))).scalars().all()}
    items = {c.id: c.name for c in (await db.execute(select(CashFlowItem).where(CashFlowItem.org_id == current.org_id))).scalars().all()}
    # Re-review N7: which rows on this page are a split contact leg nobody has accepted yet, so the UI
    # offers "Accept" only there (POST /transactions/{id}/accept-split).
    split_ids = {t.id for t in rows if _is_split_leg(t)}
    if split_ids:
        from backend.models import AuditLog
        split_ids -= set((await db.execute(select(AuditLog.entity_id).where(
            AuditLog.org_id == current.org_id, AuditLog.action == SPLIT_LEG_ACCEPTED,
            AuditLog.entity_type == "transaction", AuditLog.entity_id.in_(sorted(split_ids))))).scalars().all())

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
        "split_pending": t.id in split_ids,
        "supplier_payment_reversible": supplier_payment_reversible(t),
    } for t in rows], "total": total, "skip": skip, "limit": limit, **extra}


async def _txn_summary(db, q, day: Optional[str], tz: Optional[str] = None) -> dict:
    """Totals the cash panel shows under a paged ledger, computed in SQL over all matching
    rows: live (not deleted) count, money in, money out, and in/out on `day` (a day on the shop's
    clock `tz`; actual_date is naive UTC)."""
    from datetime import date as _date

    from sqlalchemy import case
    sub = q.where(func.coalesce(Transaction.status, "") != "deleted").subquery()
    # Money in or out is the sign, whatever the type: sales post as invoice_payment and refunds as
    # refund/expense, and counting only "income" showed a day of sales as "In today 0" (B3).
    # Transfers between the shop's own accounts are neither.
    absamt = func.abs(sub.c.amount)
    moves = func.coalesce(sub.c.txn_type, "") != "transfer"
    is_in, is_out = moves & (sub.c.amount > 0), moves & (sub.c.amount < 0)
    cols = [func.count(),
            func.coalesce(func.sum(case((is_in, sub.c.amount), else_=0)), 0),
            func.coalesce(func.sum(case((is_out, absamt), else_=0)), 0)]
    if day:
        try:
            d = _date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, "day must be YYYY-MM-DD")
        on_day = (sub.c.actual_date >= day_start_utc(d, tz)) & (sub.c.actual_date < day_end_utc(d, tz))
        cols += [func.coalesce(func.sum(case((on_day & is_in, absamt), else_=0)), 0),
                 func.coalesce(func.sum(case((on_day & is_out, absamt), else_=0)), 0)]
    r = (await db.execute(select(*cols).select_from(sub))).one()
    out = {"count": r[0], "income": float(r[1] or 0), "expense": float(r[2] or 0)}
    if day:
        out["today_in"], out["today_out"] = float(r[3] or 0), float(r[4] or 0)
        out["today_net"] = out["today_in"] - out["today_out"]
    return out


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
    accts = (await db.execute(scoping.scope_accounts(select(Account).where(
        Account.org_id == current.org_id, Account.active == True), db, Account.id))).scalars().all()  # noqa: E712
    total = sum(a.balance for a in accts if not a.is_virtual)
    # Client balance > 0 means the client owes the shop (services/ledger.py sign convention).
    debt = (await db.execute(
        select(func.coalesce(func.sum(Client.balance), 0)).where(Client.org_id == current.org_id, Client.balance > 0)
    )).scalar() or 0
    return {
        "total_cash": total,
        "client_debt": abs(debt),
        "accounts": [_ser_account(a) for a in accts],
    }
