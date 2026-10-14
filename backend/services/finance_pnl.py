"""144jr — Owner P&L ("did I make money?").

A plain-language profit statement neither Orderry nor most repair CRMs give the
owner: net sales − cost of parts − operating expenses = profit, in the shop's own
currency, org-scoped, for any period.

Method (documented so the number is trustworthy):
- **Net revenue** = Σ(subtotal − discount) of issued invoices (status ≠ draft/void)
  by issue date — ex-tax, because collected tax isn't revenue.
- **Tax collected** = Σ(tax) — shown for context, not part of profit.
- **COGS (parts)** = Σ(qty × cost_price) over product invoice lines linked to their
  order line (which carries cost); free-form product lines with no linked cost
  contribute 0 and are flagged.
- **Operating expenses** = Σ of expense cash transactions (txn_type='expense',
  active) in the period. This is cash-basis while revenue is accrual — a pragmatic
  owner view; labelled as such in the UI.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, InvoiceLine, OrderLine, Transaction
from backend.services.money import q2, q4

_EXCLUDED_INVOICE = ("draft", "void", "cancelled", "canceled")


def _end_of_day(dt: datetime) -> datetime:
    if dt.time() == datetime.min.time():
        return dt + timedelta(days=1) - timedelta(seconds=1)
    return dt


async def compute_pnl(db: AsyncSession, org_id: str, date_from: Optional[str],
                      date_to: Optional[str], location_id: Optional[str] = None) -> dict:
    df = datetime.fromisoformat(date_from) if date_from else datetime.utcnow() - timedelta(days=30)
    dt = _end_of_day(datetime.fromisoformat(date_to)) if date_to else datetime.utcnow() + timedelta(days=1)

    inv_date = func.coalesce(Invoice.issued_at, Invoice.created_at)
    inv_conds = [Invoice.org_id == org_id, Invoice.status.notin_(_EXCLUDED_INVOICE),
                 inv_date >= df, inv_date <= dt]

    # revenue (ex-tax), tax, gross sales
    rev_row = (await db.execute(select(
        func.coalesce(func.sum(Invoice.subtotal - Invoice.discount), 0.0),
        func.coalesce(func.sum(Invoice.tax), 0.0),
        func.coalesce(func.sum(Invoice.total), 0.0),
        func.count(),
    ).where(*inv_conds))).one()
    revenue, tax_collected, gross_sales, inv_count = (float(rev_row[0]), float(rev_row[1]),
                                                      float(rev_row[2]), int(rev_row[3]))

    # COGS from product invoice-lines linked to their order line's cost_price
    cogs_row = (await db.execute(
        select(func.coalesce(func.sum(InvoiceLine.qty * OrderLine.cost_price), 0.0))
        .select_from(InvoiceLine)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(OrderLine, OrderLine.id == InvoiceLine.order_line_id)
        .where(*inv_conds, InvoiceLine.kind == "product")
    )).scalar_one()
    cogs = float(cogs_row or 0.0)

    # operating expenses (cash-basis) in the period
    txn_date = func.coalesce(Transaction.actual_date, Transaction.created_at)
    opex_row = (await db.execute(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0.0))
        .where(Transaction.org_id == org_id, Transaction.txn_type == "expense",
               Transaction.status == "active", txn_date >= df, txn_date <= dt)
    )).scalar_one()
    opex = float(opex_row or 0.0)

    # 144ba — payment-processing fees are real money the shop loses, posted as their own `fee`
    # transaction (not `expense`). They were previously excluded from the P&L, which overstated
    # profit for any card-taking shop. Report them separately AND subtract from net profit.
    fee_row = (await db.execute(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0.0))
        .where(Transaction.org_id == org_id, Transaction.txn_type == "fee",
               Transaction.status == "active", txn_date >= df, txn_date <= dt)
    )).scalar_one()
    fees = float(fee_row or 0.0)

    gross_profit = revenue - cogs
    net_profit = gross_profit - opex - fees
    pct = lambda n: round(n / revenue * 100, 1) if revenue else 0.0

    return {
        "revenue": q2(revenue),
        "tax_collected": q2(tax_collected),
        "gross_sales": q2(gross_sales),
        "cogs": q2(cogs),
        "gross_profit": q2(gross_profit),
        "gross_margin_pct": pct(gross_profit),
        "opex": q2(opex),
        "fees": q2(fees),
        "net_profit": q2(net_profit),
        "net_margin_pct": pct(net_profit),
        "invoice_count": inv_count,
        "date_from": df.strftime("%Y-%m-%d"),
        "date_to": dt.strftime("%Y-%m-%d"),
    }
