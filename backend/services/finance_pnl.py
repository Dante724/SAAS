"""144jr — Owner P&L ("did I make money?").

A plain-language profit statement neither Orderry nor most repair CRMs give the
owner: net sales − cost of parts − operating expenses = profit, in the shop's own
currency, org-scoped, for any period.

Method: the shop-wide definitions in services/revenue (read its docstring), so the P&L shows
the same revenue as the dashboards and every report.
- **Net revenue** = net sales: completed jobs ex tax, counter sales and manual invoices, less
  credit notes and refunds.
- **Tax collected** — shown for context, not part of profit.
- **COGS** = the cost of the parts on those jobs (returned, restocked parts excluded), of the
  counter sales, and of costed manual-invoice lines.
- **Operating expenses** = expense records + expense cash transactions (never a refund to a
  customer). This is cash-basis while revenue is accrual — a pragmatic owner view; labelled as
  such in the UI.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, Transaction
from backend.services.money import q2
from backend.services.dates import now_utc

_EXCLUDED_INVOICE = ("draft", "void", "cancelled", "canceled")


def _end_of_day(dt: datetime) -> datetime:
    if dt.time() == datetime.min.time():
        return dt + timedelta(days=1) - timedelta(seconds=1)
    return dt


async def compute_pnl(db: AsyncSession, org_id: str, date_from: Optional[str],
                      date_to: Optional[str], location_id: Optional[str] = None) -> dict:
    df = datetime.fromisoformat(date_from) if date_from else now_utc() - timedelta(days=30)
    dt = _end_of_day(datetime.fromisoformat(date_to)) if date_to else now_utc() + timedelta(days=1)

    inv_date = func.coalesce(Invoice.issued_at, Invoice.created_at)
    inv_count = int((await db.execute(select(func.count()).where(
        Invoice.org_id == org_id, Invoice.status.notin_(_EXCLUDED_INVOICE),
        inv_date >= df, inv_date <= dt))).scalar() or 0)

    from backend.services.revenue import net_sales, operating_expenses
    ns = await net_sales(db, org_id, df, dt, location_id)
    revenue, tax_collected, gross_sales, cogs = ns["revenue"], ns["tax"], ns["gross"], ns["cogs"]
    opex = await operating_expenses(db, org_id, df, dt)

    txn_date = func.coalesce(Transaction.actual_date, Transaction.created_at)
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
