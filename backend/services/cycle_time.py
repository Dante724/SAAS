"""
Cycle-time intelligence — dwell stats, stall detection, predicted ready-time.

Everything here is derived from `order_status_logs` (the timestamped transition
history) — no new schema. For each order we reconstruct its status sequence and
measure how long it sat in each status (dwell = time until the next transition).
We bucket those dwells by (status_key, work-order type_id) so predictions reflect
"screen jobs at this shop", not a global blur, and fall back to the status-only
bucket when a type bucket is thin.

Confidence is explicit and honest: a bucket with < MIN_N samples is "low"
confidence; a status with no samples anywhere yields no prediction at all. This
is what lets predicted ready-time stay off until the data has earned trust.
"""
from __future__ import annotations
from datetime import timedelta
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Order, OrderStatus, OrderStatusLog
from backend.services.dates import now_utc

MIN_N = 5            # samples in a bucket before we call a prediction trustworthy
STALL_FACTOR = 1.0   # stalled when elapsed > p80 * factor (p80 already generous)
STALL_FLOOR_MIN = 30  # never call a job stalled before it has sat this long in a status


def is_stalled(elapsed_min: float, typical_min: float, n: int) -> bool:
    """Stalled = past the bucket's p80 (x factor), but only once the bucket has MIN_N samples
    and the job has sat at least STALL_FLOOR_MIN. Launch QA F1: a single sample used to flag
    every new job ("Running long 0 min vs ~0 min typical")."""
    if n < MIN_N:
        return False
    return elapsed_min > max(typical_min * STALL_FACTOR, STALL_FLOOR_MIN)


DWELL_WINDOW_DAYS = 180  # QA-6: typical times come from the last ~6 months, not all history


def _dwell_minutes(dialect: str, later, earlier):
    """Minutes between two timestamps, in SQL (both engines the app runs on)."""
    if dialect == "postgresql":
        return func.extract("epoch", later - earlier) / 60.0
    return (func.julianday(later) - func.julianday(earlier)) * 1440.0


async def compute_dwell_stats(db: AsyncSession, org_id: str, is_estimate: bool = False,
                              window_days: int = DWELL_WINDOW_DAYS) -> dict:
    """Return dwell-time stats keyed by (status_key, type_id) and (status_key, None).

    Value per key: {"n", "avg", "p50", "p80"} in minutes. The (status_key, None)
    key is the type-agnostic fallback bucket.

    QA-6: this used to load every status-log row in the shop's history into Python on every
    work-order list load. Now the database does it: LEAD() gives each log row its dwell (time
    until the order's next transition), GROUP BY gives n and mean, and ROW_NUMBER() picks the
    median (mean of the middle one or two) and p80 (index round(0.8·(n-1)), as before). Only
    transitions in the last `window_days` count, so the cost follows recent volume, not
    lifetime history. The result is a few rows per status, never the log itself.
    """
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    since = now_utc() - timedelta(days=window_days)
    nxt = func.lead(OrderStatusLog.changed_at).over(
        partition_by=OrderStatusLog.order_id,
        order_by=(OrderStatusLog.changed_at, OrderStatusLog.id))
    logs = (select(OrderStatusLog.to_status.label("status"), Order.type_id.label("type_id"),
                   OrderStatusLog.changed_at.label("at"), nxt.label("next_at"))
            .join(Order, Order.id == OrderStatusLog.order_id)
            .where(Order.org_id == org_id, Order.is_estimate == is_estimate,  # noqa: E712
                   OrderStatusLog.changed_at >= since)
            .subquery())
    dw = (select(logs.c.status, logs.c.type_id,
                 _dwell_minutes(dialect, logs.c.next_at, logs.c.at).label("mins"))
          .where(logs.c.next_at.isnot(None))
          .subquery())
    dwells = select(dw.c.status, dw.c.type_id, dw.c.mins).where(dw.c.mins >= 0).subquery()

    stats: dict = {}
    # typed first: the type-agnostic pass then owns (status, None), as it always has
    for typed in (True, False):
        keys = (dwells.c.status, dwells.c.type_id) if typed else (dwells.c.status,)
        ranked = select(*keys, dwells.c.mins,
                        func.row_number().over(partition_by=keys, order_by=dwells.c.mins).label("rn"),
                        func.count().over(partition_by=keys).label("n"),
                        func.avg(dwells.c.mins).over(partition_by=keys).label("avg")).subquery()
        rn, n = ranked.c.rn, ranked.c.n
        is_mid = or_(rn == (n + 1) // 2, rn == (n + 2) // 2)          # integer division: 1 or 2 middles
        is_p80 = rn == (8 * (n - 1) + 5) // 10 + 1                   # round(0.8·(n-1)) + 1, never x.5
        gkeys = (ranked.c.status, ranked.c.type_id) if typed else (ranked.c.status,)
        q = (select(*gkeys, func.max(n), func.max(ranked.c.avg),
                    func.avg(case((is_mid, ranked.c.mins))),
                    func.max(case((is_p80, ranked.c.mins))))
             .where(or_(is_mid, is_p80)).group_by(*gkeys))
        for row in (await db.execute(q)).all():
            key = (row[0], row[1]) if typed else (row[0], None)
            cnt, avg, p50, p80 = row[-4:]
            stats[key] = {"n": int(cnt), "avg": float(avg), "p50": float(p50), "p80": float(p80)}
    return stats


def _bucket(stats: dict, status_key: str, type_id: Optional[str]) -> Optional[dict]:
    """Best available bucket for a status: typed if it has >= MIN_N, else type-agnostic,
    else typed with whatever it has, else None."""
    typed = stats.get((status_key, type_id))
    anyb = stats.get((status_key, None))
    if typed and typed["n"] >= MIN_N:
        return typed
    if anyb and anyb["n"] >= MIN_N:
        return anyb
    return typed or anyb


async def load_statuses(db: AsyncSession, org_id: str, is_estimate: bool = False) -> list:
    """The shop's active statuses for one record type, in board order (seeded on first use)."""
    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, org_id)
    return list((await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id,
        OrderStatus.record_type == ("estimate" if is_estimate else "work_order"),
        OrderStatus.active == True)  # noqa: E712
        .order_by(OrderStatus.sort_order))).scalars().all())


async def get_order_timing(db: AsyncSession, order: Order,
                           stats: Optional[dict] = None, statuses: Optional[list] = None) -> dict:
    """Timing card for one order: elapsed in current status, typical for its bucket,
    stalled flag, and predicted ready-time — each with an explicit confidence level.

    QA-6: a batch caller passes `stats` and `statuses` once for every order, so a page of
    orders costs the same few queries as one."""
    if stats is None:
        stats = await compute_dwell_stats(db, order.org_id, bool(order.is_estimate))
    if statuses is None:
        statuses = await load_statuses(db, order.org_id, bool(order.is_estimate))
    cur = next((s for s in statuses if s.key == order.status), None)

    entered = order.status_entered_at or order.created_at or now_utc()
    elapsed_min = max(0.0, (now_utc() - entered).total_seconds() / 60.0)

    out: dict = {
        "status": order.status,
        "elapsed_min": round(elapsed_min, 1),
        "typical_min": None, "typical_p80_min": None, "sample_n": 0,
        "stalled": False, "over_by_min": None,
        "predicted_ready_at": None, "confidence": "none",
        "is_terminal": bool(cur.is_terminal) if cur else False,
    }
    if cur is None or cur.is_terminal:
        return out

    b = _bucket(stats, cur.key, order.type_id)
    if b and b["n"] > 0:
        out["typical_min"] = round(b["avg"], 1)
        out["typical_p80_min"] = round(b["p80"], 1)
        out["sample_n"] = b["n"]
        if is_stalled(elapsed_min, b["p80"], b["n"]):
            out["stalled"] = True
            out["over_by_min"] = round(elapsed_min - b["avg"], 1)

    # Predicted ready = now + remaining dwell across statuses from the next one
    # up to (and including) the last non-terminal status, following sort order.
    ordered = [s for s in statuses if not s.is_terminal]
    try:
        cur_idx = next(i for i, s in enumerate(ordered) if s.key == cur.key)
    except StopIteration:
        cur_idx = -1
    remaining = 0.0
    have_any = False
    thin = False
    if cur_idx >= 0:
        # time left in current status (typical minus elapsed, floored at 0)
        if b and b["n"] > 0:
            remaining += max(0.0, b["avg"] - elapsed_min)
            have_any = True
            if b["n"] < MIN_N:
                thin = True
        for s in ordered[cur_idx + 1:]:
            sb = _bucket(stats, s.key, order.type_id)
            if sb and sb["n"] > 0:
                remaining += sb["avg"]
                have_any = True
                if sb["n"] < MIN_N:
                    thin = True
    if have_any:
        out["predicted_ready_at"] = (now_utc() + timedelta(minutes=remaining)).isoformat()
        out["confidence"] = "low" if thin else "high"
    return out
