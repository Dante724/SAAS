"""Tekly Sense — suggestions learned from one shop's own history.

No AI service, no GPU, no outside data: plain SQL plus a little Python over the shop's
closed jobs. Everything here suggests; nothing changes a job, sends a message or blocks
anyone. Each answer carries a "why" (how many of the shop's own jobs it rests on), and a
shop stays in "learning" until it has enough closed jobs for the numbers to mean something.

Every query is filtered by org_id, windowed in time and capped, so a busy shop can't make
a request slow and one shop's history never shapes another shop's suggestions.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from statistics import median
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AutomationSetting, Device, Order, OrderLine, Recommendation

log = logging.getLogger("tekly.sense")

SETTING_KEY = "sense"
FEATURES = ("job_suggestions", "deferred", "ready_time_why", "margin_check",
            "morning_brief", "added_together", "service_due")
BRIEF_DEFAULTS = {"recipients": [], "hour": 7, "email": False, "push": False}
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
async def _row(db: AsyncSession, org_id: str, lock: bool = False) -> Optional[AutomationSetting]:
    q = select(AutomationSetting).where(AutomationSetting.org_id == org_id, AutomationSetting.key == SETTING_KEY)
    if lock:   # a settings save and the daily send both rewrite this row's config: one at a time
        q = q.with_for_update()
    return (await db.execute(q)).scalars().first()


def _shape(row: Optional[AutomationSetting]) -> dict:
    cfg = (row.config or {}) if row else {}
    feats = cfg.get("features") or {}
    brief = {**BRIEF_DEFAULTS, **{k: v for k, v in (cfg.get("brief") or {}).items() if k in BRIEF_DEFAULTS}}
    return {"enabled": True if row is None else bool(row.enabled),
            "features": {f: bool(feats.get(f, True)) for f in FEATURES},
            "brief": brief}


async def get_settings(db: AsyncSession, org_id: str) -> dict:
    return _shape(await _row(db, org_id))


async def save_settings(db: AsyncSession, org_id: str, *, enabled: Optional[bool],
                        features: Optional[dict], brief: Optional[dict] = None) -> dict:
    """`brief` recipients must already be checked to be active people in this shop."""
    row = await _row(db, org_id, lock=True)
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
    new_brief = dict(cur["brief"])
    for k, v in (brief or {}).items():
        if k in BRIEF_DEFAULTS and v is not None:
            new_brief[k] = v
    row.config = {**(row.config or {}), "features": feats, "brief": new_brief}
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
    time it was deferred) and drops off once a later job did it or it is already on this job. `can_view(order)` drops
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
    # Already on this job, as a recommendation (any state) or a line: nothing to bring back.
    here = {_norm(t) for (t,) in (await db.execute(select(Recommendation.title).where(
        Recommendation.org_id == order.org_id, Recommendation.order_id == order.id))).all()}
    here |= {_norm(d) for (d,) in (await db.execute(select(OrderLine.description).where(
        OrderLine.order_id == order.id))).all()}
    out, seen = [], set(here)
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


# ── Morning Brief ───────────────────────────────────────────────────────────────
STUCK_DAYS = 5            # a job sitting in one status this long is worth a look
COLD_QUOTE_DAYS = 2       # a quote unanswered this long is worth a nudge
DUE_SOON_DAYS = 14        # service due within this many days


def _tz(org) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(org, "timezone", None) or "UTC")
    except Exception:  # noqa: BLE001 — a bad zone name falls back to UTC
        return ZoneInfo("UTC")


def _local_day_utc(tz: ZoneInfo, now_utc: datetime, days_back: int = 0) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day, as naive UTC (how the database stores times)."""
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    day = (local - timedelta(days=days_back)).date()
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


async def brief_readers(db: AsyncSession, org_id: str) -> list:
    """The shop owner plus the people the owner picked, active and in this shop."""
    from backend.models import Employee, Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    ids = set((await get_settings(db, org_id))["brief"]["recipients"] or [])
    if org and org.owner_id:
        ids.add(org.owner_id)
    if not ids:
        return []
    return list((await db.execute(select(Employee).where(
        Employee.org_id == org_id, Employee.id.in_(ids), Employee.active == True))).scalars().all())  # noqa: E712


async def build_brief(db: AsyncSession, org_id: str, *, show_money: bool, now: Optional[datetime] = None) -> dict:
    from backend.models import Invoice, Organisation, Payment
    now = now or datetime.utcnow()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    tz = _tz(org)
    y0, y1 = _local_day_utc(tz, now, 1)
    t0, t1 = _local_day_utc(tz, now, 0)
    live = (Order.org_id == org_id, Order.archived == False, Order.is_estimate == False)  # noqa: E712

    closed = (await db.execute(select(func.count(Order.id)).where(
        *live, Order.closed_at >= y0, Order.closed_at < y1))).scalar_one()
    yesterday = {"jobs_closed": closed}
    if show_money:
        paid = (await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .outerjoin(Invoice, Invoice.id == Payment.invoice_id)
            .outerjoin(Order, Order.id == Payment.order_id)
            # Both sides that exist must be this shop's: a payment is never counted for a shop
            # just because one of its links points there.
            .where(or_(Invoice.id.isnot(None), Order.id.isnot(None)),
                   or_(Invoice.id.is_(None), Invoice.org_id == org_id),
                   or_(Order.id.is_(None), Order.org_id == org_id),
                   Payment.kind != "refund", Payment.paid_at >= y0, Payment.paid_at < y1))).scalar_one()
        yesterday["paid"] = round(float(paid or 0), 2)

    due_today = (await db.execute(select(func.count(Order.id)).where(
        *live, Order.closed_at.is_(None),
        or_(and_(Order.due_at >= t0, Order.due_at < t1),
            and_(Order.scheduled_for >= t0, Order.scheduled_for < t1))))).scalar_one()

    attention = []
    stuck = (await db.execute(select(func.count(Order.id)).where(
        *live, Order.closed_at.is_(None),
        Order.status_entered_at < now - timedelta(days=STUCK_DAYS)))).scalar_one()
    if stuck:
        attention.append({"key": "stuck", "count": stuck, "to": "/orders",
                          "title": f"{stuck} job{'s' if stuck != 1 else ''} in the same status for {STUCK_DAYS}+ days"})
    cold = (await db.execute(select(func.count(Order.id)).where(
        Order.org_id == org_id, Order.archived == False, Order.is_estimate == True,  # noqa: E712
        Order.estimate_approved_at.is_(None), Order.converted_order_id.is_(None),
        Order.created_at < now - timedelta(days=COLD_QUOTE_DAYS),
        Order.created_at > now - timedelta(days=60)))).scalar_one()
    if cold:
        attention.append({"key": "cold_quotes", "count": cold, "to": "/orders",
                          "title": f"{cold} quote{'s' if cold != 1 else ''} waiting {COLD_QUOTE_DAYS}+ days for an answer"})
    owed = Invoice.total - func.coalesce(Invoice.amount_paid, 0)
    n_over, amt_over = (await db.execute(select(func.count(Invoice.id), func.coalesce(func.sum(owed), 0)).where(
        Invoice.org_id == org_id, Invoice.status.notin_(("paid", "cancelled", "draft")),
        Invoice.due_at < now, owed > 0))).one()
    if n_over:
        item = {"key": "overdue_invoices", "count": n_over, "to": "/invoices",
                "title": f"{n_over} overdue invoice{'s' if n_over != 1 else ''}"}
        if show_money:
            item["amount"] = round(float(amt_over or 0), 2)
        attention.append(item)
    if await is_on(db, org_id, "service_due"):
        n_due = await devices_due(db, org_id, now=now)
        if n_due:
            attention.append({"key": "service_due", "count": n_due, "to": "/clients",
                              "title": f"{n_due} customer item{'s' if n_due != 1 else ''} due for service in the next {DUE_SOON_DAYS} days"})
    return {"date": (now.replace(tzinfo=timezone.utc).astimezone(tz)).date().isoformat(),
            "shop": org.name, "currency": getattr(org, "currency", None) or "", "yesterday": yesterday, "today": {"due": due_today}, "attention": attention}


def brief_text(b: dict, money_fmt=None) -> str:
    sym = b.get("currency") or ""
    money_fmt = money_fmt or (lambda v: f"{sym}{v:,.2f}")
    y = b["yesterday"]
    lines = [f"Good morning — {b['shop']}, {b['date']}", "",
             f"Yesterday: {y['jobs_closed']} job{'s' if y['jobs_closed'] != 1 else ''} closed"
             + (f", {money_fmt(y['paid'])} taken" if "paid" in y else "") + ".",
             f"Today: {b['today']['due']} job{'s' if b['today']['due'] != 1 else ''} due."]
    if b["attention"]:
        lines += ["", "Worth a look:"]
        for a in b["attention"]:
            lines.append(f"- {a['title']}" + (f" ({money_fmt(a['amount'])})" if "amount" in a else ""))
    else:
        lines += ["", "Nothing else needs you today."]
    lines += ["", "Tekly Sense suggests only; nothing was sent to customers.",
              "Change this in Settings → General → Tekly Sense."]
    return "\n".join(lines)


async def _send_email(db: AsyncSession, org_id: str, *, recipient: str, subject: str, body: str):
    from backend.services.notifications import send_direct_email, try_oauth_email
    res = await try_oauth_email(db, org_id, recipient=recipient, subject=subject, body=body)
    return res if res is not None else await send_direct_email(recipient, subject, body)


async def _send_push(db: AsyncSession, *, org_id: str, employee_id: str, kind: str, title: str,
                     body: str = "", deep_link: Optional[str] = None):
    from backend.services.push import notify
    return await notify(db, org_id=org_id, employee_id=employee_id, kind=kind, title=title,
                        body=body, deep_link=deep_link, commit=True)


async def send_briefs(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> int:
    """Send today's brief once, after the chosen hour in the shop's time zone. The day is
    claimed (committed) before anything is sent, so a restart mid-send never sends twice.
    Returns 1 if this call sent the brief, else 0."""
    from backend.models import Organisation
    from backend.services.auth import has_perm
    now = now or datetime.utcnow()
    s = await get_settings(db, org_id)
    b = s["brief"]
    if not (s["enabled"] and s["features"]["morning_brief"] and (b["email"] or b["push"])):
        return 0
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if org is None or not getattr(org, "active", True):
        return 0
    local = now.replace(tzinfo=timezone.utc).astimezone(_tz(org))
    if local.hour < int(b["hour"] or 0):
        return 0
    row = await _row(db, org_id, lock=True)
    today = local.date().isoformat()
    if (row.config or {}).get("brief_sent_on") == today:
        return 0
    row.config = {**(row.config or {}), "brief_sent_on": today}
    await db.commit()

    # Plain values up front: a failed send rolls the session back, which would expire loaded
    # Employee rows and break every reader after it.
    readers = [(e.id, e.email, await has_perm(db, e, "can_view_finance")) for e in await brief_readers(db, org_id)]
    built: dict[bool, dict] = {}            # one brief per money level, not one per reader
    for emp_id, email, money in readers:
        try:
            if b["email"] and email:
                if money not in built:
                    built[money] = await build_brief(db, org_id, show_money=money, now=now)
                brief = built[money]
                await _send_email(db, org_id, recipient=email,
                                  subject=f"Your morning brief — {brief['shop']}", body=brief_text(brief))
            if b["push"]:
                # Nothing private on a lock screen: the push only says the brief is ready.
                await _send_push(db, org_id=org_id, employee_id=emp_id, kind="sense.brief",
                                 title="Your morning brief is ready", body="", deep_link="/")
        except Exception:  # noqa: BLE001 — one reader's failure never stops the others
            await db.rollback()      # a failed statement must not poison the session for the next reader
            log.exception("morning brief failed for one reader in org %s", org_id)
    return 1


# ── Usually added together ──────────────────────────────────────────────────────
TOGETHER_MIN_SEEN = 5     # the line on the job must appear on this many past jobs
TOGETHER_MIN_BOTH = 3     # ...and the suggestion with it on at least this many
TOGETHER_SHARE = 0.4      # ...on 40% or more of them


async def together_for(db: AsyncSession, order: Order) -> list[dict]:
    if not await learning_ready(db, order.org_id):
        return []
    here = {_norm(d) for (d,) in (await db.execute(select(OrderLine.description).where(
        OrderLine.order_id == order.id))).all() if _norm(d)}
    if not here:
        return []
    since = datetime.utcnow() - timedelta(days=HISTORY_DAYS)
    # The shop's most recent closed jobs; matched in Python with the same normalisation as
    # everywhere else (case in any script, extra spaces), which SQL lower/trim can't promise.
    jobs = (await db.execute(
        select(Order.id).where(Order.org_id == order.org_id, Order.closed_at.isnot(None),
                               Order.closed_at >= since, Order.is_estimate == False,  # noqa: E712
                               Order.archived == False, Order.id != order.id)  # noqa: E712
        .order_by(Order.closed_at.desc()).limit(2000))).scalars().all()
    if not jobs:
        return []
    per_job: dict[str, dict[str, OrderLine]] = defaultdict(dict)
    for ln in (await db.execute(select(OrderLine).where(OrderLine.order_id.in_(jobs)))).scalars().all():
        k = _norm(ln.description)
        if k:
            per_job[ln.order_id].setdefault(k, ln)
    best: dict[str, dict] = {}
    for a in here:
        with_a = [j for j in per_job.values() if a in j]
        if len(with_a) < TOGETHER_MIN_SEEN:
            continue
        counts: dict[str, list[OrderLine]] = defaultdict(list)
        for j in with_a:
            for k, ln in j.items():
                if k not in here:
                    counts[k].append(ln)
        for k, lns in counts.items():
            share = len(lns) / len(with_a)
            if len(lns) >= TOGETHER_MIN_BOTH and share >= TOGETHER_SHARE and share > best.get(k, {}).get("_share", 0):
                a_name = with_a[0][a].description.strip()
                refs = [ln.ref_id for ln in lns if ln.ref_id]
                best[k] = {"_share": share, "description": lns[0].description.strip(), "kind": lns[0].kind,
                           "line_type": lns[0].line_type, "ref_id": max(set(refs), key=refs.count) if refs else None,
                           "unit_price": round(median(float(ln.unit_price or 0) for ln in lns), 2),
                           "why": {"with": a_name, "together": len(lns), "of": len(with_a)}}
    items = sorted(best.values(), key=lambda i: (-i["_share"], i["description"]))[:3]
    for i in items:
        i.pop("_share")
    return items


# ── Service due ─────────────────────────────────────────────────────────────────
INTERVAL_MIN_GAPS = 5
INTERVAL_DAYS = (14, 1095)
SERVICE_HISTORY_DAYS = 1095


_HISTORY_CACHE: dict[str, tuple[float, tuple]] = {}
HISTORY_TTL_S = 600


async def _service_history(db: AsyncSession, org_id: str):
    """(key -> {device_id -> visit dates}), (key -> price list), (key -> display name).
    Cached per shop for ten minutes: it reads up to 20,000 rows, and every work order that
    opens asks for it. Keyed by org_id, so one shop's history never serves another."""
    import time as _time
    hit = _HISTORY_CACHE.get(org_id)
    if hit and _time.monotonic() - hit[0] < HISTORY_TTL_S:
        return hit[1]
    out = await _read_service_history(db, org_id)
    if len(_HISTORY_CACHE) > 5000:
        _HISTORY_CACHE.clear()
    _HISTORY_CACHE[org_id] = (_time.monotonic(), out)
    return out


async def _read_service_history(db: AsyncSession, org_id: str):
    since = datetime.utcnow() - timedelta(days=SERVICE_HISTORY_DAYS)
    rows = (await db.execute(
        select(Order.device_id, Order.closed_at, OrderLine.description, OrderLine.unit_price)
        .join(OrderLine, OrderLine.order_id == Order.id)
        .where(Order.org_id == org_id, Order.device_id.isnot(None), Order.closed_at.isnot(None),
               Order.closed_at >= since, Order.is_estimate == False, Order.archived == False)  # noqa: E712
        .order_by(Order.closed_at.desc()).limit(20000))).all()
    visits: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    prices: dict[str, list] = defaultdict(list)
    names: dict[str, str] = {}
    for dev, when, desc, price in rows:
        k = _norm(desc)
        if not k:
            continue
        visits[k][dev].add(when.date())
        prices[k].append(float(price or 0))
        names.setdefault(k, desc.strip())
    return visits, prices, names


def _intervals(visits) -> dict[str, int]:
    out = {}
    for k, by_dev in visits.items():
        gaps = []
        for days in by_dev.values():
            ds = sorted(days)
            gaps += [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days >= 7]
        if len(gaps) >= INTERVAL_MIN_GAPS:
            every = int(median(gaps))
            if INTERVAL_DAYS[0] <= every <= INTERVAL_DAYS[1]:
                out[k] = every
    return out


async def _shop_today(db: AsyncSession, org_id: str, now: Optional[datetime] = None):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    return (now or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(_tz(org)).date()


async def service_due_for(db: AsyncSession, order: Order) -> list[dict]:
    if not order.device_id or not await learning_ready(db, order.org_id):
        return []
    visits, prices, names = await _service_history(db, order.org_id)
    every = _intervals(visits)
    here = {_norm(d) for (d,) in (await db.execute(select(OrderLine.description).where(
        OrderLine.order_id == order.id))).all()}
    here |= {_norm(t) for (t,) in (await db.execute(select(Recommendation.title).where(
        Recommendation.org_id == order.org_id, Recommendation.order_id == order.id))).all()}
    today = await _shop_today(db, order.org_id)
    items = []
    for k, days in every.items():
        done = visits[k].get(order.device_id)
        if not done or k in here:
            continue
        ago = (today - max(done)).days
        if ago >= days - DUE_SOON_DAYS:
            items.append({"description": names[k], "every_days": days, "last_done_days_ago": ago,
                          "due_in_days": days - ago, "unit_price": round(median(prices[k]), 2)})
    return sorted(items, key=lambda i: i["due_in_days"])


async def devices_due(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> int:
    """Items (vehicles, boats, devices…) with a regular service due in the next two weeks,
    or overdue by up to six months (older than that, the customer has likely gone elsewhere),
    not counting items already in the shop on an open job."""
    visits, _, _ = await _service_history(db, org_id)
    every = _intervals(visits)
    today = await _shop_today(db, org_id, now)
    # Items already in the shop on an open job are being looked after: don't count them.
    in_shop = set((await db.execute(select(Order.device_id).where(
        Order.org_id == org_id, Order.device_id.isnot(None), Order.closed_at.is_(None),
        Order.archived == False, Order.is_estimate == False))).scalars().all())  # noqa: E712
    due = set()
    for k, days in every.items():
        for dev, done in visits[k].items():
            ago = (today - max(done)).days
            if days - DUE_SOON_DAYS <= ago <= days + 180 and dev not in in_shop:
                due.add(dev)
    return len(due)
