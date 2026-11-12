"""Voided money rows stay on record but out of every total (audit DB-11).

A refund that is voided keeps its refund ``Payment`` rows: each gets ``voided_at`` /
``voided_by_id`` instead of being deleted, so the payments table keeps the history the cash
ledger keeps, and a synced accounting mapping never dangles. Rather than trust every one of the
~100 queries that read ``payments`` (paid/refunded sums, the shift's expected cash, invoices,
revenue reports, the nightly reconcile…) to remember a ``voided_at IS NULL`` filter, every ORM
read gets it here, the same way ``db/tenancy.py`` adds the shop filter.

Opt out with ``.execution_options(include_voided=True)`` where the voided rows are wanted (the
void itself, an audit view). Column loads (refreshing a row the session already holds) are left
alone, so an object that was just voided can still be read and flushed.
"""
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria


@event.listens_for(Session, "do_orm_execute")
def _hide_voided_payments(state: ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load:
        return
    if state.execution_options.get("include_voided"):
        return
    from backend.models import Payment
    state.statement = state.statement.options(
        with_loader_criteria(Payment, Payment.voided_at.is_(None), include_aliases=True))
