"""Team E — whose payroll a role may see (E3) and the commission breakdown behind a pay figure
(E1 My earnings, E2 payroll drill-down).

Payroll scope is a per-role limit on top of the payroll permissions: `all` (default), `own`
(only the caller) or `selected` (the employee ids in Role.payroll_employee_ids). The owner and
people without a role are never limited. Every payroll endpoint filters lists through
`payroll_scope_ids` and refuses single-employee actions through `assert_payroll_scope`.

The breakdown reads the append-only ledger and groups each commission (and every claw-back of
it) by rule → document (work order or task) → line item, so a technician can trace any amount
back to the job and line that earned it.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CommissionRule, Employee, EmployeeBalanceLedger, Order, OrderLine, Task
from backend.services.payroll import _money

PAYROLL_SCOPES = ("all", "own", "selected")


# ── E3: payroll scope ─────────────────────────────────────────────────────────
async def payroll_scope_ids(db: AsyncSession, current: Employee) -> Optional[set[str]]:
    """Employee ids whose payroll the caller may see, or None for everyone."""
    from backend.services.auth import is_shop_owner
    from backend.services.scoping import _ids, role_for
    if not current.role_id or await is_shop_owner(db, current):
        return None
    role = await role_for(db, current)
    if role is None:
        return set()                  # a dangling role id never fails open
    scope = getattr(role, "payroll_scope", None) or "all"
    if scope == "own":
        return {current.id}
    if scope == "selected":
        return {str(i) for i in _ids(role.payroll_employee_ids)}
    return None


async def assert_payroll_scope(db: AsyncSession, current: Employee, employee_id: Optional[str]) -> None:
    """403 unless the caller's payroll scope covers this employee."""
    ids = await payroll_scope_ids(db, current)
    if ids is not None and employee_id not in ids:
        raise HTTPException(403, "This person's payroll is outside what your role can see")


async def assert_payroll_scope_all(db: AsyncSession, current: Employee) -> None:
    """For changes that touch everyone (an org-wide commission rule): only an unlimited scope."""
    if await payroll_scope_ids(db, current) is not None:
        raise HTTPException(403, "Your role only sees some people's payroll, so it can't change shop-wide pay rules")


def in_scope(ids: Optional[set[str]], employee_id: Optional[str]) -> bool:
    return ids is None or employee_id in ids


# ── Period helpers ────────────────────────────────────────────────────────────
def expected_base(emp: Employee, ps: date, pe: date) -> Decimal:
    """Fixed salary for the period: a full month earns the monthly wage, a part month its share
    by days (the same figure the payroll calculation shows for a whole month)."""
    if emp.wage_type != "fixed" or not emp.wage_value or pe < ps:
        return _money(0)
    monthly = _money(emp.wage_value)
    total = Decimal("0")
    cur = date(ps.year, ps.month, 1)
    while cur <= pe:
        days = monthrange(cur.year, cur.month)[1]
        m_end = date(cur.year, cur.month, days)
        lo, hi = max(ps, cur), min(pe, m_end)
        covered = (hi - lo).days + 1
        total += monthly if covered == days else monthly * Decimal(covered) / Decimal(days)
        cur = m_end + timedelta(days=1)
    return _money(total)


def _date_window(q, ps: date, pe: date, tzname: str | None):
    """Entries made on the shop's days ps..pe (created_at is naive UTC; the period is shop days)."""
    from backend.services.dates import day_end_utc, day_start_utc
    L = EmployeeBalanceLedger
    return q.where(L.created_at >= day_start_utc(ps, tzname), L.created_at < day_end_utc(pe, tzname))


async def adjustments(db: AsyncSession, org_id: str, employee_id: str, ps: date, pe: date) -> dict:
    """Manual bonuses and penalties dated in the period (penalties shown positive)."""
    from backend.services.dates import shop_tzname
    L = EmployeeBalanceLedger
    rows = (await db.execute(_date_window(select(L).where(
        L.org_id == org_id, L.employee_id == employee_id, L.ref_type == "manual",
        L.entry_type.in_(("bonus", "penalty"))), ps, pe, await shop_tzname(db, org_id)).order_by(L.created_at))).scalars().all()

    def _row(e):
        return {"id": e.id, "date": e.created_at.isoformat(), "amount": float(abs(_money(e.amount))),
                "note": e.note or ""}
    return {"bonuses": [_row(e) for e in rows if e.entry_type == "bonus"],
            "penalties": [_row(e) for e in rows if e.entry_type == "penalty"]}


# ── E1/E2: commission breakdown ───────────────────────────────────────────────
async def commission_breakdown(db: AsyncSession, org_id: str, employee_id: str,
                               ps: date, pe: date) -> list[dict]:
    """Commissions and their reversals in the period, as rule → document → line item.

    A claw-back is posted against the order, but it belongs to the line (or order, or task)
    whose commission it reverses; its accrual group's first commission says which one."""
    from backend.services.dates import shop_tzname
    L = EmployeeBalanceLedger
    rows = (await db.execute(_date_window(select(L).where(
        L.org_id == org_id, L.employee_id == employee_id,
        L.entry_type.in_(("commission", "clawback"))), ps, pe, await shop_tzname(db, org_id)).order_by(L.created_at))).scalars().all()
    if not rows:
        return []

    origin: dict[str, tuple] = {}
    keys = {e.accrual_key for e in rows if e.accrual_key}
    if keys:
        for e in (await db.execute(select(L).where(
                L.org_id == org_id, L.entry_type == "commission", L.accrual_key.in_(keys))
                .order_by(L.created_at))).scalars().all():
            origin.setdefault(e.accrual_key, (e.ref_type, e.ref_id, e.rule_id, e.event))

    def _src(e) -> tuple:
        if e.entry_type == "commission":
            return e.ref_type, e.ref_id, e.rule_id, e.event
        return origin.get(e.accrual_key) or ("order", e.ref_id, e.rule_id, e.event)

    srcs = {e.id: _src(e) for e in rows}
    line_ids = {r for t, r, *_ in srcs.values() if t == "order_line" and r}
    task_ids = {r for t, r, *_ in srcs.values() if t == "task" and r}
    order_ids = {r for t, r, *_ in srcs.values() if t == "order" and r}
    rule_ids = {s[2] for s in srcs.values() if s[2]}

    lines = {}
    if line_ids:
        for ln in (await db.execute(select(OrderLine).join(Order, Order.id == OrderLine.order_id).where(
                OrderLine.id.in_(line_ids), Order.org_id == org_id))).scalars().all():
            lines[ln.id] = ln
            order_ids.add(ln.order_id)
    orders = {o.id: o for o in (await db.execute(select(Order).where(
        Order.id.in_(order_ids or {""}), Order.org_id == org_id))).scalars().all()}
    tasks = {t.id: t for t in (await db.execute(select(Task).where(
        Task.id.in_(task_ids or {""}), Task.org_id == org_id))).scalars().all()}
    rules = {r.id: r for r in (await db.execute(select(CommissionRule).where(
        CommissionRule.id.in_(rule_ids or {""}), CommissionRule.org_id == org_id))).scalars().all()}

    groups: dict = {}
    for e in rows:
        ref_type, ref_id, rule_id, event = srcs[e.id]
        rule = rules.get(rule_id)
        g = groups.setdefault(rule_id or "", {
            "rule_id": rule_id, "rule_name": rule.name if rule else "Other commission",
            "event": (rule.event if rule else None) or event,
            "calc_type": rule.calc_type if rule else None,
            "rate": float(rule.amount or 0) if rule else None,
            "percent_basis": rule.percent_basis if rule else None,
            "active": bool(rule.active) if rule else False,
            "total": Decimal("0"), "_docs": {}})

        line = lines.get(ref_id) if ref_type == "order_line" else None
        if ref_type == "task":
            task = tasks.get(ref_id)
            doc_key = f"task:{ref_id}"
            doc = {"doc_type": "task", "doc_id": ref_id, "number": None,
                   "title": task.title if task else "Task"}
            item_key, item = f"task:{ref_id}", {"line_id": None, "name": task.title if task else "Task", "qty": None}
        else:
            oid = line.order_id if line else ref_id
            o = orders.get(oid)
            doc_key = f"order:{oid}"
            doc = {"doc_type": "estimate" if (o and o.is_estimate) else "order", "doc_id": oid,
                   "number": o.order_number if o else None, "title": None}
            if line:
                item_key, item = f"line:{line.id}", {"line_id": line.id, "name": line.description,
                                                     "qty": float(line.qty or 0)}
            else:
                item_key, item = f"order:{oid}", {"line_id": None, "name": "Whole order", "qty": None}

        d = g["_docs"].setdefault(doc_key, {**doc, "total": Decimal("0"), "last_at": None, "_items": {}})
        it = d["_items"].setdefault(item_key, {**item, "total": Decimal("0"), "entries": []})
        amt = _money(e.amount)
        it["entries"].append({"id": e.id, "date": e.created_at.isoformat(), "amount": float(amt),
                              "kind": "earned" if e.entry_type == "commission" else "reversed",
                              "note": e.note or ""})
        it["total"] += amt
        d["total"] += amt
        d["last_at"] = e.created_at.isoformat()
        g["total"] += amt

    out = []
    for g in groups.values():
        docs = []
        for d in g.pop("_docs").values():
            items = [{**i, "total": float(_money(i["total"]))} for i in d.pop("_items").values()]
            docs.append({**d, "total": float(_money(d["total"])), "items": items})
        docs.sort(key=lambda d: d["last_at"] or "", reverse=True)
        out.append({**g, "total": float(_money(g["total"])), "documents": docs})
    out.sort(key=lambda g: -g["total"])
    return out
