"""Nightly integrity sweep: stored running totals must equal the ledgers they summarise.

The hardening council's High findings (H1, H4, H5, H7) were all "a running total drifted from
its own ledger" — the kind of bug nothing in the UI can show and nothing repairs. This sweep
re-derives each total from its ledger with read-only aggregates and reports every row that
disagrees, so drift is caught within a day instead of by a customer.

Invariants (each is one grouped query; nothing is locked or written):

- ``account``: ``Account.balance`` = Σ ``Transaction.amount`` that isn't ``deleted``
  (a corrected original still counts; its correction delta row carries the difference).
- ``client``: ``Client.balance`` = Σ ``ClientLedgerEntry.amount``.
- ``invoice``: ``Invoice.amount_paid`` = Σ payments on the invoice − Σ refunds on it.
- ``stock``: Σ ``StockLevel.qty_available`` per (product, location) = Σ ``StockMovement.qty``.
- ``split_leg`` (audit FIN-02): the cash leg of a client balance movement or a supplier payment
  must move with its contact side, which nothing voids or corrects from Cash. A leg voided on its
  own (``deleted``), or a correction delta posted on one, moved the till but not the contact, so
  it is reported (``entity_id`` is the transaction; stored = what the till holds of it, derived =
  what the contact side still counts). Restoring a deleted leg clears it.

A mismatch is reported, never "fixed": which side is wrong needs a person. Amounts compare at
cent precision (quantities at 1e-6), so float/Numeric rounding is not reported as drift.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Account,
    Client,
    ClientLedgerEntry,
    Invoice,
    Location,
    Payment,
    StockLevel,
    StockMovement,
    Transaction,
)

log = logging.getLogger("tekly.reconcile")

CHECKS = ("account", "client", "invoice", "stock", "split_leg")
_MONEY_EPS = 0.005
_QTY_EPS = 1e-6


@dataclass(frozen=True)
class Mismatch:
    check: str          # one of CHECKS
    org_id: str
    entity_id: str      # account / client / invoice id, or "part_id@location_id" for stock
    stored: float       # the running total as stored
    derived: float      # the same total re-derived from its ledger

    @property
    def diff(self) -> float:
        return round(self.stored - self.derived, 6)

    def as_dict(self) -> dict:
        return {**asdict(self), "diff": self.diff}


def _org_filter(col, org_id: str | None):
    return [col == org_id] if org_id else []


async def _accounts(db: AsyncSession, org_id: str | None) -> list[Mismatch]:
    ledger = (select(Transaction.account_id.label("aid"), func.sum(Transaction.amount).label("s"))
              .where(Transaction.status != "deleted", *_org_filter(Transaction.org_id, org_id))
              .group_by(Transaction.account_id).subquery())
    derived = func.coalesce(ledger.c.s, 0)
    rows = (await db.execute(
        select(Account.org_id, Account.id, func.coalesce(Account.balance, 0), derived)
        .outerjoin(ledger, ledger.c.aid == Account.id)
        .where(func.abs(func.coalesce(Account.balance, 0) - derived) > _MONEY_EPS,
               *_org_filter(Account.org_id, org_id))
        .execution_options(skip_tenant_filter=True))).all()   # cross-shop by design: a platform sweep
    return [Mismatch("account", o, i, float(s), float(d)) for o, i, s, d in rows]


async def _clients(db: AsyncSession, org_id: str | None) -> list[Mismatch]:
    ledger = (select(ClientLedgerEntry.client_id.label("cid"), func.sum(ClientLedgerEntry.amount).label("s"))
              .where(*_org_filter(ClientLedgerEntry.org_id, org_id))
              .group_by(ClientLedgerEntry.client_id).subquery())
    derived = func.coalesce(ledger.c.s, 0)
    rows = (await db.execute(
        select(Client.org_id, Client.id, func.coalesce(Client.balance, 0), derived)
        .outerjoin(ledger, ledger.c.cid == Client.id)
        .where(func.abs(func.coalesce(Client.balance, 0) - derived) > _MONEY_EPS,
               *_org_filter(Client.org_id, org_id))
        .execution_options(skip_tenant_filter=True))).all()   # cross-shop by design: a platform sweep
    return [Mismatch("client", o, i, float(s), float(d)) for o, i, s, d in rows]


async def _invoices(db: AsyncSession, org_id: str | None) -> list[Mismatch]:
    signed = func.sum(func.coalesce(Payment.amount, 0) * case((Payment.kind == "refund", -1), else_=1))
    # payments carry no org_id: for one shop, only its invoices' payments are summed (PERF-17)
    mine = ([Payment.invoice_id.in_(select(Invoice.id).where(Invoice.org_id == org_id))]
            if org_id else [])
    ledger = (select(Payment.invoice_id.label("iid"), signed.label("s"))
              .where(Payment.invoice_id.isnot(None), *mine)
              .group_by(Payment.invoice_id).subquery())
    derived = func.coalesce(ledger.c.s, 0)
    rows = (await db.execute(
        select(Invoice.org_id, Invoice.id, func.coalesce(Invoice.amount_paid, 0), derived)
        .outerjoin(ledger, ledger.c.iid == Invoice.id)
        .where(func.abs(func.coalesce(Invoice.amount_paid, 0) - derived) > _MONEY_EPS,
               *_org_filter(Invoice.org_id, org_id))
        .execution_options(skip_tenant_filter=True))).all()   # cross-shop by design: a platform sweep
    return [Mismatch("invoice", o, i, float(s), float(d)) for o, i, s, d in rows]


async def _stock(db: AsyncSession, org_id: str | None) -> list[Mismatch]:
    # StockLevel and StockMovement carry no org_id: the shop comes from the location.
    levels = select(StockLevel.part_id.label("p"), StockLevel.location_id.label("l"),
                    StockLevel.qty_available.label("lv"), literal(0.0).label("mv"))
    moves = select(StockMovement.part_id.label("p"), StockMovement.location_id.label("l"),
                   literal(0.0).label("lv"), StockMovement.qty.label("mv"))
    if org_id:
        # cut both sides to the shop's locations before anything is summed (PERF-17: the whole
        # platform's movement history was aggregated, then filtered)
        levels = levels.join(Location, Location.id == StockLevel.location_id).where(Location.org_id == org_id)
        moves = moves.join(Location, Location.id == StockMovement.location_id).where(Location.org_id == org_id)
    both = union_all(levels, moves).subquery()
    sums = (select(both.c.p, both.c.l, func.sum(func.coalesce(both.c.lv, 0)).label("lv"),
                   func.sum(func.coalesce(both.c.mv, 0)).label("mv"))
            .group_by(both.c.p, both.c.l).subquery())
    rows = (await db.execute(
        select(Location.org_id, sums.c.p, sums.c.l, sums.c.lv, sums.c.mv)
        .join(Location, Location.id == sums.c.l)
        .where(func.abs(sums.c.lv - sums.c.mv) > _QTY_EPS, *_org_filter(Location.org_id, org_id))
        .execution_options(skip_tenant_filter=True))).all()   # cross-shop by design: a platform sweep
    return [Mismatch("stock", o, f"{part}@{loc}", float(lv), float(mv)) for o, part, loc, lv, mv in rows]


_CONTACT_LEG_TYPES = ("client", "supplier_payment")   # routers/cash._CONTACT_LEGS


async def _split_legs(db: AsyncSession, org_id: str | None) -> list[Mismatch]:
    voided = (Transaction.status == "deleted") & Transaction.reverses_txn_id.is_(None)
    delta = (Transaction.status != "deleted") & Transaction.reverses_txn_id.isnot(None)
    rows = (await db.execute(
        select(Transaction.org_id, Transaction.id, Transaction.amount, voided)
        .where(Transaction.ref_type.in_(_CONTACT_LEG_TYPES), voided | delta,
               func.abs(func.coalesce(Transaction.amount, 0)) > _MONEY_EPS,
               *_org_filter(Transaction.org_id, org_id))
        .execution_options(skip_tenant_filter=True))).all()   # cross-shop by design: a platform sweep
    # a voided leg: the till holds 0 of what the contact counts; a correction delta: the till holds
    # an amount the contact never saw
    return [Mismatch("split_leg", o, i, 0.0 if v else float(a), float(a) if v else 0.0)
            for o, i, a, v in rows]


_RUNNERS = {"account": _accounts, "client": _clients, "invoice": _invoices, "stock": _stock,
            "split_leg": _split_legs}


async def find_mismatches(db: AsyncSession, *, org_id: str | None = None,
                          checks: tuple[str, ...] = CHECKS) -> list[Mismatch]:
    """Every row whose stored total disagrees with its ledger, for one shop or all shops."""
    out: list[Mismatch] = []
    for name in checks:
        out.extend(await _RUNNERS[name](db, org_id))
    return out


# Last sweep's counts per check, for /metrics (this process's view; the sweep runs on one worker).
LAST_COUNTS: dict[str, int] = {}
# Shops the last sweep could not check (a statement failed or timed out): a sweep that silently
# stops checking is the failure PERF-17 is about, so it is counted and logged at ERROR.
LAST_FAILED: int = 0
# Shops read per page of the shop list.
ORG_PAGE = 200


async def _org_ids(db: AsyncSession):
    """Every shop id, a page at a time (keyset on id, so the list is never held whole)."""
    from backend.models import Organisation
    after = ""
    while True:
        page = list((await db.execute(select(Organisation.id).where(Organisation.id > after)
                                      .order_by(Organisation.id).limit(ORG_PAGE)
                                      .execution_options(skip_tenant_filter=True))).scalars().all())
        if not page:
            return
        for oid in page:
            yield oid
        after = page[-1]


async def sweep(db: AsyncSession) -> list[Mismatch]:
    """Run every check, shop by shop, and log what disagrees. Returns the mismatches.

    PERF-17: each statement reads one shop's rows (they were platform-wide aggregates, which at
    scale outrun the 15 s statement timeout and would be cancelled every night). A shop that
    can't be checked is logged at ERROR and counted (LAST_FAILED); the others are still checked."""
    global LAST_FAILED
    found: list[Mismatch] = []
    failed = 0
    async for org_id in _org_ids(db):
        try:
            async with db.begin_nested():     # a failed statement must not end the whole sweep
                found.extend(await find_mismatches(db, org_id=org_id))
        except Exception:  # noqa: BLE001 — one shop's failure is reported, the sweep goes on
            failed += 1
            log.exception("integrity sweep: could not check shop %s", org_id)
    await db.rollback()                        # read-only: end the transaction
    counts = {c: 0 for c in CHECKS}
    for m in found:
        counts[m.check] += 1
    LAST_COUNTS.clear()
    LAST_COUNTS.update(counts)
    LAST_FAILED = failed
    if found:
        # ERROR so it pages: ids and amounts only (no names, no PII). One line per mismatch, capped.
        log.error("integrity sweep: %d mismatch(es) %s", len(found), counts)
        for m in found[:200]:
            log.error("integrity mismatch check=%s org=%s id=%s stored=%.6f derived=%.6f diff=%.6f",
                      m.check, m.org_id, m.entity_id, m.stored, m.derived, m.diff)
    elif not failed:
        log.info("integrity sweep: all totals match their ledgers")
    if failed:
        log.error("integrity sweep: %d shop(s) could not be checked", failed)
    return found


__all__ = ["CHECKS", "Mismatch", "find_mismatches", "sweep", "LAST_COUNTS", "LAST_FAILED"]
