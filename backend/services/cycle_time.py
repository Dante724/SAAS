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
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Order, OrderStatus, OrderStatusLog

MIN_N = 5            # samples in a bucket before we call a prediction trustworthy
STALL_FACTOR = 1.0   # stalled when elapsed > p80 * factor (p80 already generous)


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[idx]


async def compute_dwell_stats(db: AsyncSession, org_id: str, is_estimate: bool = False) -> dict:
    """Return dwell-time stats keyed by (status_key, type_id) and (status_key, None).

    Value per key: {"n", "avg", "p50", "p80"} in minutes. The (status_key, None)
    key is the type-agnostic fallback bucket.
    """
    rows = (await db.execute(
        select(OrderStatusLog.order_id, OrderStatusLog.to_status,
               OrderStatusLog.changed_at, Order.type_id)
        .join(Order, Order.id == OrderStatusLog.order_id)
        .where(Order.org_id == org_id, Order.is_estimate == is_estimate)  # noqa: E712
        .order_by(OrderStatusLog.order_id, OrderStatusLog.changed_at)
    )).all()

    seqs: dict[str, list] = defaultdict(list)
    type_of: dict[str, Optional[str]] = {}
    for oid, to_status, changed_at, type_id in rows:
        seqs[oid].append((to_status, changed_at))
        type_of[oid] = type_id

    # bucket -> list of dwell minutes
    typed: dict[tuple, list[float]] = defaultdict(list)
    anytype: dict[str, list[float]] = defaultdict(list)
    for oid, seq in seqs.items():
        for i in range(len(seq) - 1):
            status = seq[i][0]
            mins = (seq[i + 1][1] - seq[i][1]).total_seconds() / 60.0
            if mins < 0:
                continue
            typed[(status, type_of.get(oid))].append(mins)
            anytype[status].append(mins)

    stats: dict = {}
    for (status, tid), vals in typed.items():
        stats[(status, tid)] = {"n": len(vals), "avg": mean(vals),
                                "p50": median(vals), "p80": _percentile(vals, 0.8)}
    for status, vals in anytype.items():
        stats[(status, None)] = {"n": len(vals), "avg": mean(vals),
                                 "p50": median(vals), "p80": _percentile(vals, 0.8)}
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


async def get_order_timing(db: AsyncSession, order: Order,
                           stats: Optional[dict] = None) -> dict:
    """Timing card for one order: elapsed in current status, typical for its bucket,
    stalled flag, and predicted ready-time — each with an explicit confidence level.
    """
    if stats is None:
        stats = await compute_dwell_stats(db, order.org_id, bool(order.is_estimate))

    from backend.routers.statuses import ensure_seeded
    await ensure_seeded(db, order.org_id)
    statuses = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == order.org_id,
        OrderStatus.record_type == ("estimate" if order.is_estimate else "work_order"),
        OrderStatus.active == True)  # noqa: E712
        .order_by(OrderStatus.sort_order))).scalars().all()
    cur = next((s for s in statuses if s.key == order.status), None)

    entered = order.status_entered_at or order.created_at or datetime.utcnow()
    elapsed_min = max(0.0, (datetime.utcnow() - entered).total_seconds() / 60.0)

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
        threshold = b["p80"] * STALL_FACTOR
        if elapsed_min > threshold and threshold > 0:
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
        out["predicted_ready_at"] = (datetime.utcnow() + timedelta(minutes=remaining)).isoformat()
        out["confidence"] = "low" if thin else "high"
    return out
