"""Tekly Sense — suggestions learned from one shop's own history.

No AI service, no GPU, no outside data: plain SQL plus a little Python over the shop's
closed jobs. Everything here suggests; nothing changes a job, sends a message or blocks
anyone. Each answer carries a "why" (how many of the shop's own jobs it rests on), and a
shop stays in "learning" until it has enough closed jobs for the numbers to mean something.

Every query is filtered by org_id, windowed in time and capped, so a busy shop can't make
a request slow and one shop's history never shapes another shop's suggestions.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AutomationSetting, Device, Order, OrderLine, Recommendation

SETTING_KEY = "sense"
FEATURES = ("job_suggestions", "deferred", "ready_time_why", "margin_check")
MIN_CLOSED_JOBS = 20      # before suggestions appear at all
HISTORY_DAYS = 730        # job suggestions look back two years
CANDIDATE_CAP = 500       # most recent closed jobs considered per request
SIMILAR_CAP = 30          # most similar jobs used for the line counts
MIN_SIMILAR = 3           # fewer similar jobs than this: say nothing
MIN_SHARE = 0.3           # a line must appear on 30% of similar jobs...
MIN_USED = 2              # ...and on at least two of them
MAX_ITEMS = 5
MARGIN_DAYS = 365
MARGIN_GAP = 15.0         # flag a job this many points under the shop's usual margin

_STOP = {"the", "and", "for", "with", "not", "has", "was", "are", "but", "its", "his", "her",
         "on", "in", "is", "it", "of", "to", "a", "an", "my", "no", "won't", "wont", "doesn't",
         "does", "doesnt", "cant", "can't", "when", "very", "still", "from", "after", "into"}


# ── Settings ───────────────────────────────────────────────────────────────────
async def _row(db: AsyncSession, org_id: str) -> Optional[AutomationSetting]:
    return (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == org_id, AutomationSetting.key == SETTING_KEY))).scalars().first()


def _shape(row: Optional[AutomationSetting]) -> dict:
    cfg = (row.config or {}) if row else {}
    feats = cfg.get("features") or {}
    return {"enabled": True if row is None else bool(row.enabled),
            "features": {f: bool(feats.get(f, True)) for f in FEATURES}}


async def get_settings(db: AsyncSession, org_id: str) -> dict:
    return _shape(await _row(db, org_id))


async def save_settings(db: AsyncSession, org_id: str, *, enabled: Optional[bool],
                        features: Optional[dict]) -> dict:
    row = await _row(db, org_id)
    if row is None:
        row = AutomationSetting(org_id=org_id, key=SETTING_KEY, enabled=True, config={})
        db.add(row)
    cur = _shape(row if row.id else None)
    if enabled is not None:
        row.enabled = bool(enabled)
    feats = dict(cur["features"])
    for k, v in (features or {}).items():
        if k in FEATURES:
            feats[k] = bool(v)
    row.config = {**(row.config or {}), "features": feats}
    return _shape(row)


async def is_on(db: AsyncSession, org_id: str, feature: str) -> bool:
    s = await get_settings(db, org_id)
    return s["enabled"] and s["features"].get(feature, False)


async def closed_jobs(db: AsyncSession, org_id: str) -> int:
    return (await db.execute(select(func.count(Order.id)).where(
        Order.org_id == org_id, Order.closed_at.isnot(None),
        Order.is_estimate == False, Order.archived == False))).scalar_one()  # noqa: E712


async def learning(db: AsyncSession, org_id: str) -> dict:
    n = await closed_jobs(db, org_id)
    return {"closed_jobs": n, "needed": MIN_CLOSED_JOBS, "ready": n >= MIN_CLOSED_JOBS}


# ── Job suggestions ─────────────────────────────────────────────────────────────
def words(text: str) -> set[str]:
    """Meaningful words of a complaint in any script ("экран", "écran", "screen"). Short
    words are dropped unless they hold a digit, so model numbers like "13" or "s9" count."""
    out = set()
    for w in re.findall(r"\w+", (text or "").casefold()):
        if w in _STOP or w.isdigit() and len(w) < 2:
            continue
        if len(w) >= 3 or (len(w) >= 2 and any(ch.isdigit() for ch in w)):
            out.add(w)
    return out


def _norm(desc: str) -> str:
    return re.sub(r"\s+", " ", (desc or "").strip().casefold())


def _similarity(want: set[str], have: set[str]) -> float:
    """Share of the shorter complaint's words found in the other. A long, chatty complaint
    ("customer dropped her phone yesterday, screen cracked…") still matches a short one
    ("cracked screen"); at least two shared words are needed unless one side is one word."""
    shared = len(want & have)
    small = min(len(want), len(have))
    if not shared or (shared < 2 and small > 1):
        return 0.0
    return shared / small


async def draft_lines(db: AsyncSession, org_id: str, complaint: str,
                      device: Optional[Device] = None) -> dict:
    """Lines this shop usually puts on jobs like this one, with the usual price and why.

    Deliberately shop-level knowledge (like the catalog): it pools every closed job, whoever
    worked it, but returns only line names that recur, a median price (stripped for roles
    that can't see prices) and counts — never another job's number, customer or notes."""
    if not await learning_ready(db, org_id):
        return {"learning": True, "items": []}
    want = words(complaint)
    if not want:
        return {"learning": False, "items": []}
    since = datetime.utcnow() - timedelta(days=HISTORY_DAYS)
    rows = (await db.execute(
        select(Order.id, Order.complaint, Device.brand, Device.model)
        .outerjoin(Device, Device.id == Order.device_id)
        .where(Order.org_id == org_id, Order.closed_at.isnot(None), Order.closed_at >= since,
               Order.is_estimate == False, Order.archived == False,  # noqa: E712
               Order.complaint.isnot(None))
        .order_by(Order.closed_at.desc()).limit(CANDIDATE_CAP))).all()
    brand = _norm(device.brand) if device and device.brand else ""
    model = _norm(device.model) if device and device.model else ""
    scored = []
    for oid, text, b, m in rows:
        score = _similarity(want, words(text))
        if score < 0.5:
            continue
        if brand and _norm(b or "") == brand:
            score += 0.15
        if model and _norm(m or "") == model:
            score += 0.15
        scored.append((score, oid))
    scored.sort(reverse=True)
    similar = [oid for _, oid in scored[:SIMILAR_CAP]]
    if len(similar) < MIN_SIMILAR:
        return {"learning": False, "items": []}

    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id.in_(similar)))).scalars().all()
    # One entry per (line name, job): a catalog line and a hand-typed line with the same name are
    # the same work, and a line repeated on one job counts once, at that job's price.
    per_job: dict[str, dict[str, OrderLine]] = defaultdict(dict)
    for ln in lines:
        key = _norm(ln.description)
        if key and ln.order_id not in per_job[key]:
            per_job[key][ln.order_id] = ln
    items = []
    for key, jobs in per_job.items():
        used = len(jobs)
        if used < MIN_USED or used / len(similar) < MIN_SHARE:
            continue
        sample = list(jobs.values())
        refs = [ln.ref_id for ln in sample if ln.ref_id]
        first = sample[0]
        items.append({"description": first.description.strip(), "kind": first.kind,
                      "line_type": first.line_type,
                      "ref_id": max(set(refs), key=refs.count) if refs else None,
                      "unit_price": round(median(float(ln.unit_price or 0) for ln in sample), 2),
                      "why": {"used": used, "similar": len(similar)}})
    items.sort(key=lambda i: (-i["why"]["used"], i["description"]))
    return {"learning": False, "items": items[:MAX_ITEMS]}


async def learning_ready(db: AsyncSession, org_id: str) -> bool:
    return (await closed_jobs(db, org_id)) >= MIN_CLOSED_JOBS


# ── Deferred last time ──────────────────────────────────────────────────────────
async def deferred_for(db: AsyncSession, order: Order, can_view=None) -> list[dict]:
    """Work the customer said "not now" to on earlier jobs for the same item (or, when this
    job has no item, for the same customer). Each piece of work appears once (the latest
    time it was deferred) and drops off once a later job did it. `can_view(order)` drops
    earlier jobs the caller isn't allowed to open (own-orders-only roles, other branches,
    hidden statuses)."""
    if order.device_id:
        same = Order.device_id == order.device_id
    elif order.client_id:
        same = Order.client_id == order.client_id
    else:
        return []
    rows = (await db.execute(
        select(Recommendation, Order)
        .join(Order, Order.id == Recommendation.order_id)
        .where(Recommendation.org_id == order.org_id, Order.org_id == order.org_id, same,
               Recommendation.status == "declined", Recommendation.decline_reason == "not_now",
               Recommendation.order_id != order.id)
        .order_by(Order.created_at.desc(), Recommendation.created_at.desc()).limit(50))).all()
    if not rows:
        return []
    # Work done since: lines on this item's/customer's jobs, with when that job was opened.
    done = (await db.execute(
        select(OrderLine.description, Order.created_at)
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.org_id == order.org_id, same).limit(2000))).all()
    out, seen = [], set()
    for r, src in rows:
        key = _norm(r.title)
        if key in seen:
            continue
        if any(_norm(d) == key and (when or datetime.min) > (src.created_at or datetime.min) for d, when in done):
            seen.add(key)          # done on a later visit
            continue
        if can_view is not None and not await can_view(src):
            continue
        seen.add(key)
        out.append({"id": r.id, "title": r.title, "note": r.note, "kind": r.kind, "ref_id": r.ref_id,
                    "qty": r.qty, "unit_price": float(r.unit_price or 0), "urgency": r.urgency,
                    "remind_after": r.remind_after.isoformat() if r.remind_after else None,
                    "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                    "from_order_number": src.order_number})
        if len(out) == 10:
            break
    return out


# ── Margin check ────────────────────────────────────────────────────────────────
def _margin(rev: float, cost: float) -> Optional[float]:
    return round((rev - cost) / rev * 100, 1) if rev > 0 else None


def _net(ln: OrderLine) -> float:
    """What the line earns before tax: qty × price, less its discount."""
    gross = float(ln.qty or 0) * float(ln.unit_price or 0)
    d = float(ln.discount or 0)
    return gross - (gross * d / 100 if (ln.discount_type or "pct") == "pct" else d)


async def margin_check(db: AsyncSession, order: Order) -> dict:
    """This job's margin against the shop's usual margin over the last year. Revenue is
    before tax (tax isn't the shop's money) and after discounts."""
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    rev = sum(_net(ln) for ln in lines)
    cost = sum(float(ln.cost_price or 0) * float(ln.qty or 0) for ln in lines)   # a missing cost counts as 0
    mine = _margin(rev, cost)
    since = datetime.utcnow() - timedelta(days=MARGIN_DAYS)
    gross = func.coalesce(OrderLine.qty, 0) * func.coalesce(OrderLine.unit_price, 0)
    net = gross - case((func.coalesce(OrderLine.discount_type, "pct") == "pct",
                        gross * func.coalesce(OrderLine.discount, 0) / 100),
                       else_=func.coalesce(OrderLine.discount, 0))
    rows = (await db.execute(
        select(OrderLine.order_id, func.sum(net),
               func.sum(func.coalesce(OrderLine.cost_price, 0) * func.coalesce(OrderLine.qty, 0)))
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.org_id == order.org_id, Order.closed_at.isnot(None), Order.closed_at >= since,
               Order.is_estimate == False, Order.archived == False, Order.id != order.id)  # noqa: E712
        .group_by(OrderLine.order_id)
        .order_by(func.max(Order.closed_at).desc()).limit(2000))).all()   # the most recent 2,000 jobs
    past = [m for m in (_margin(float(r or 0), float(c or 0)) for _, r, c in rows) if m is not None]
    out = {"margin_pct": mine, "usual_pct": None, "sample": len(past), "flag": False, "learning": True}
    if len(past) >= MIN_CLOSED_JOBS:
        usual = round(median(past), 1)
        out.update(usual_pct=usual, learning=False,
                   flag=mine is not None and mine <= usual - MARGIN_GAP)
    return out
