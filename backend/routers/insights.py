"""Company Insights — Orderry-style build-your-own diagrams and trends.

Configuration lives in InsightCard rows; the data is aggregated live from
orders, sales and payments on each read. v1 subjects: closed orders, sales,
payments. Metrics: quantity, amount, gross profit (sales), average.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee, Order, OrderStatus, Sale, Payment, WorkOrderType, Location, InsightCard,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2
from backend.services.dates import day_start_utc, now_utc, shop_tzname, utc_to_wall

router = APIRouter(prefix="/api/reports/insights", tags=["insights"])

# ── builder config (drives the UI) ───────────────────────────────
SUBJECTS = {
    "closed_orders": {"label": "Closed orders", "metrics": ["count", "amount", "avg"],
                      "groups": ["type", "location", "status_group", "month", "quarter"]},
    "sales":         {"label": "Sales", "metrics": ["count", "amount", "gross_profit", "avg"],
                      "groups": ["seller", "location", "month", "quarter"]},
    "payments":      {"label": "Payments", "metrics": ["count", "amount"],
                      "groups": ["method", "month", "quarter"]},
}
METRIC_LABEL = {"count": "Quantity", "amount": "Amount", "gross_profit": "Gross profit", "avg": "Average"}
GROUP_LABEL = {"type": "Order type", "seller": "Seller", "method": "Payment method", "location": "Location",
               "status_group": "Status group", "month": "Month", "quarter": "Quarter"}
PERIODS_DIAGRAM = [("7d", "Last 7 days"), ("30d", "Last 30 days"), ("90d", "Last 90 days"), ("12m", "Last 12 months")]
PERIODS_TREND = [("30d", "Last 30 days"), ("24w", "Last 24 weeks"), ("24m", "Last 24 months")]


@router.get("/meta")
async def meta(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    from backend.services.auth import has_perm
    profit = await has_perm(db, current, "can_see_profit")   # don't offer what /data will refuse
    return {
        "subjects": [{"key": k, "label": v["label"],
                      "metrics": [{"key": m, "label": METRIC_LABEL[m]} for m in v["metrics"]
                                  if profit or m != "gross_profit"],
                      "groups": [{"key": g, "label": GROUP_LABEL[g]} for g in v["groups"]]}
                     for k, v in SUBJECTS.items()],
        "periods_diagram": [{"key": k, "label": l} for k, l in PERIODS_DIAGRAM],
        "periods_trend": [{"key": k, "label": l} for k, l in PERIODS_TREND],
        "metric_labels": METRIC_LABEL, "group_labels": GROUP_LABEL,
    }


# ── aggregation helpers ──────────────────────────────────────────
# RPT-02: periods and buckets are shop days (the shop's clock, services/dates), like every other
# report since PR #200; they used to be UTC days, so a sale at 01:30 in Kolkata landed on the
# previous day, or month.
_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "12m": 365, "24w": 24 * 7, "24m": 730}


def _range_start(period: str, now: datetime, tz: Optional[str] = None) -> datetime:
    """Naive-UTC start of the period: the shop midnight `days` days before the shop's today
    (the trend's first bucket starts there)."""
    days = _PERIOD_DAYS.get(period, 30)
    today = utc_to_wall(now, tz).date()
    if period == "24w":
        today = today - timedelta(days=today.weekday())     # weeks start on Monday
        return day_start_utc(today - timedelta(weeks=23), tz)
    if period == "24m":
        y, m = today.year, today.month - 23
        while m <= 0:
            m += 12
            y -= 1
        return day_start_utc(date(y, m, 1), tz)
    return day_start_utc(today - timedelta(days=days - 1), tz)


def _quarter(ts: datetime) -> str:
    return f"{ts.year} Q{(ts.month - 1) // 3 + 1}"


async def _records(db, org_id, subject, start, location_id, tz: Optional[str] = None):
    """Return [{ts, amount, profit, groups:{...}}] for the subject in range. `ts` is on the shop's
    clock (wall time), so buckets and month groups are shop days."""
    out = []
    if subject == "closed_orders":
        # RPT-02: jobs finished in the period (earned: closed or first billed, the rule every report
        # uses), on the day they were finished; it counted jobs *created* in the period that are
        # closed now. Dropped jobs stay in, as before, with only the money they kept.
        from backend.services.revenue import earned_jobs_query, job_figures
        statuses = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == org_id))).scalars().all()
        grp = {s.key: s.status_group for s in statuses}
        types = {t.id: t.name for t in (await db.execute(select(WorkOrderType).where(WorkOrderType.org_id == org_id))).scalars().all()}
        locs = {l.id: l.name for l in (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().all()}
        q = await earned_jobs_query(db, org_id, start, None, location_id, include_dropped=True, with_date=True)
        rows = (await db.execute(q)).all()
        figs = await job_figures(db, q.with_only_columns(Order.id)) if rows else {}
        for o, at in rows:
            ts = utc_to_wall(at, tz)
            amount = q2(figs[o.id]["revenue"]) if o.id in figs else 0.0
            out.append({"ts": ts, "amount": amount, "profit": 0, "groups": {
                "type": types.get(o.type_id, "No type"), "location": locs.get(o.location_id, "—"),
                "status_group": grp.get(o.status, "closed"),
                "month": ts.strftime("%Y-%m"), "quarter": _quarter(ts)}})
    elif subject == "sales":
        sellers = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == org_id))).scalars().all()}
        locs = {l.id: l.name for l in (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().all()}
        q = select(Sale).where(Sale.org_id == org_id, Sale.voided_at.is_(None))
        if location_id:
            q = q.where(Sale.location_id == location_id)
        q = q.where(Sale.created_at >= start)
        # ex tax and less what came back to the till (ClientReturn.sale_id), as in net sales
        from backend.models import ClientReturn
        back = {sid: (float(a or 0), float(t or 0), float(c or 0)) for sid, a, t, c in (await db.execute(
            select(ClientReturn.sale_id, func.sum(ClientReturn.amount), func.sum(ClientReturn.tax),
                   func.sum(ClientReturn.cost))
            .where(ClientReturn.org_id == org_id, ClientReturn.sale_id.in_(q.with_only_columns(Sale.id)))
            .group_by(ClientReturn.sale_id))).all()}
        for s in (await db.execute(q)).scalars().all():
            amt, tax, cost = back.get(s.id, (0.0, 0.0, 0.0))
            amount = q2(float(s.total or 0) - float(s.tax or 0) - (amt - tax))
            ts = utc_to_wall(s.created_at, tz)
            out.append({"ts": ts, "amount": amount,
                        "profit": q2(amount - (float(s.total_cost or 0) - cost)), "groups": {
                "seller": sellers.get(s.seller_id, "—"), "location": locs.get(s.location_id, "—"),
                "month": ts.strftime("%Y-%m"), "quarter": _quarter(ts)}})
    elif subject == "payments":
        # PERF-13: the period is filtered in SQL and only three columns come back (it read every
        # payment the shop ever took, as full rows). RPT-02: the shop's payments by the same rule
        # as "Paid" on Home and Finance (services/revenue.cash_collected): the shop's own payments
        # (payments.org_id, DB-01), and paying from a client's balance is not money in.
        from backend.services.revenue import money_in_payment, shop_payments
        q = select(Payment.paid_at, Payment.amount, Payment.method).where(
            shop_payments(org_id, location_id), money_in_payment(),
            func.coalesce(Payment.kind, "payment") != "refund", Payment.paid_at >= start)
        for paid_at, amount, method in (await db.execute(q)).all():
            ts = utc_to_wall(paid_at, tz)
            out.append({"ts": ts, "amount": amount or 0, "profit": 0, "groups": {
                "method": (method or "other").title(),
                "month": ts.strftime("%Y-%m"), "quarter": _quarter(ts)}})
    return out


def _agg(records, metric):
    n = len(records)
    if metric == "count":
        return n
    if metric == "amount":
        return q2(sum(r["amount"] for r in records))
    if metric == "gross_profit":
        return q2(sum(r["profit"] for r in records))
    if metric == "avg":
        return q2(sum(r["amount"] for r in records) / n) if n else 0
    return n


def _bucket_labels(period, now):
    """Ordered (key, label) time buckets for a trend."""
    out = []
    if period == "30d":
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i))
            out.append((d.strftime("%Y-%m-%d"), d.strftime("%d %b")))
    elif period == "24w":
        monday = now - timedelta(days=now.weekday())
        for i in range(23, -1, -1):
            d = monday - timedelta(weeks=i)
            out.append((f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}", d.strftime("%d %b")))
    else:  # 24m
        y, m = now.year, now.month
        seq = []
        for _ in range(24):
            seq.append((y, m))
            m -= 1
            if m == 0:
                m = 12; y -= 1
        for (yy, mm) in reversed(seq):
            out.append((f"{yy}-{mm:02d}", datetime(yy, mm, 1).strftime("%b %y")))
    return out


def _bucket_key(ts, period):
    if period == "30d":
        return ts.strftime("%Y-%m-%d")
    if period == "24w":
        return f"{ts.isocalendar()[0]}-W{ts.isocalendar()[1]:02d}"
    return ts.strftime("%Y-%m")


@router.get("/data")
async def data(kind: str = "diagram", subject: str = "closed_orders", metric: str = "amount",
               group: Optional[str] = None, period: str = "30d", location_id: Optional[str] = None,
               db: AsyncSession = Depends(get_db), current: Employee = Depends(require_perm("can_view_reports"))):
    if subject not in SUBJECTS:
        raise HTTPException(400, "Unknown subject")
    if metric not in SUBJECTS[subject]["metrics"]:
        metric = SUBJECTS[subject]["metrics"][0]
    if metric == "gross_profit":
        from backend.services.auth import has_perm
        if not await has_perm(db, current, "can_see_profit"):
            raise HTTPException(403, "You don't have permission to see profit")
    tz = await shop_tzname(db, current.org_id, location_id)
    now = now_utc()
    start = _range_start(period, now, tz)
    records = await _records(db, current.org_id, subject, start, location_id, tz)

    if kind == "trend":
        buckets = _bucket_labels(period, utc_to_wall(now, tz))
        by_key = {k: [] for k, _ in buckets}
        for r in records:
            bk = _bucket_key(r["ts"], period)
            if bk in by_key:
                by_key[bk].append(r)
        labels = [lbl for _, lbl in buckets]
        values = [_agg(by_key[k], metric) for k, _ in buckets]
        return {"kind": "trend", "labels": labels, "values": values,
                "metric": metric, "metric_label": METRIC_LABEL[metric]}

    # diagram: group records
    g = group or SUBJECTS[subject]["groups"][0]
    if g not in SUBJECTS[subject]["groups"]:
        g = SUBJECTS[subject]["groups"][0]
    groups: dict = {}
    for r in records:
        key = r["groups"].get(g, "—")
        groups.setdefault(key, []).append(r)
    rows = [{"label": k, "value": _agg(v, metric)} for k, v in groups.items()]
    rows.sort(key=lambda x: -x["value"])
    return {"kind": "diagram", "group": g, "group_label": GROUP_LABEL[g],
            "metric": metric, "metric_label": METRIC_LABEL[metric],
            "labels": [r["label"] for r in rows], "values": [r["value"] for r in rows],
            "table": rows}


# ── saved cards ──────────────────────────────────────────────────
def _ser_card(c: InsightCard) -> dict:
    return {"id": c.id, "kind": c.kind, "subject": c.subject, "metric": c.metric,
            "group_by": c.group_by, "period": c.period, "view": c.view, "sort_order": c.sort_order}


class CardIn(BaseModel):
    kind: str = "diagram"
    subject: str
    metric: str = "amount"
    group_by: Optional[str] = None
    period: str = "30d"
    view: str = "chart"


class ReorderIn(BaseModel):
    ordered_ids: list[str]


@router.get("/cards")
async def list_cards(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(InsightCard).where(InsightCard.org_id == current.org_id)
                             .order_by(InsightCard.sort_order, InsightCard.created_at))).scalars().all()
    return {"items": [_ser_card(c) for c in rows]}


@router.post("/cards", status_code=201)
async def create_card(data: CardIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_view_reports"))):
    if data.subject not in SUBJECTS:
        raise HTTPException(400, "Unknown subject")
    count = (await db.execute(select(func.count()).select_from(InsightCard)
                              .where(InsightCard.org_id == current.org_id))).scalar() or 0
    c = InsightCard(org_id=current.org_id, kind=data.kind, subject=data.subject, metric=data.metric,
                    group_by=data.group_by, period=data.period, view=data.view, sort_order=count)
    db.add(c); await db.commit(); await db.refresh(c)
    return _ser_card(c)


@router.delete("/cards/{cid}")
async def delete_card(cid: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_view_reports"))):
    c = (await db.execute(select(InsightCard).where(InsightCard.id == cid, InsightCard.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Card not found")
    await db.delete(c); await db.commit()
    return {"ok": True}


@router.patch("/cards/reorder")
async def reorder_cards(data: ReorderIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_view_reports"))):
    rows = {c.id: c for c in (await db.execute(select(InsightCard).where(InsightCard.org_id == current.org_id))).scalars().all()}
    for idx, cid in enumerate(data.ordered_ids):
        if cid in rows:
            rows[cid].sort_order = idx
    await db.commit()
    return {"ok": True}
