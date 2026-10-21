"""Payroll & commissions API (Mandate 1)."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_date
from backend.models import (
    CommissionRule,
    Employee,
    EmployeeBalanceLedger,
    PayrollRun,
)
from backend.services.auth import assert_not_self, assert_perm, get_current_employee, has_perm, require_perm
from backend.services.payroll import CommissionEngine, _money
from backend.services.payroll_views import (
    adjustments,
    assert_payroll_scope,
    assert_payroll_scope_all,
    commission_breakdown,
    expected_base,
    in_scope,
    payroll_scope_ids,
)
from backend.services.money import q2

router = APIRouter(prefix="/api/payroll", tags=["payroll"])

EVENTS = ["order_created", "order_closed", "order_closed_success", "order_closed_fail",
          "labor_performed", "product_sold",
          "assignee_labor", "assignee_product", "task_closed"]  # Build 86 / 144ds
TIMINGS = ["on_close", "immediately", "on_status"]
TERMS = ["any", "on_time", "overdue", "urgent"]
LEVELS = ["generic", "product", "category", "service", "profile"]
_NOT_ACCRUED = ("draft", "void")   # runs that don't hold a period (overlap guards skip them)


async def _audit(db, current, action: str, entity_type: str, entity_id, diff: Optional[dict] = None):
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action=action,
                       entity_type=entity_type, entity_id=entity_id, diff=diff)


def _ser_rule(r: CommissionRule) -> dict:
    return {
        "id": r.id, "employee_id": r.employee_id, "name": r.name, "event": r.event,
        "calc_type": r.calc_type, "percent_basis": r.percent_basis,
        "amount": float(r.amount or 0), "coefficient": float(r.coefficient or 1),
        "category": r.category, "active": r.active,
        "wo_type_id": r.wo_type_id, "calc_timing": r.calc_timing,
        "trigger_status_key": r.trigger_status_key, "term_condition": r.term_condition,
        "cancel_if_deleted": r.cancel_if_deleted, "rule_level": r.rule_level,
        "product_id": r.product_id, "service_id": getattr(r, "service_id", None),
    }


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    rows = (await db.execute(select(CommissionRule).where(
        CommissionRule.org_id == current.org_id, CommissionRule.active == True))).scalars().all()  # noqa: E712
    ids = await payroll_scope_ids(db, current)
    return {"items": [_ser_rule(r) for r in rows if r.employee_id is None or in_scope(ids, r.employee_id)],
            "events": EVENTS}


async def _assert_rule_scope(db, current, employee_id: Optional[str]) -> None:
    """A rule for one person needs that person in scope; a shop-wide rule needs an unlimited scope."""
    if employee_id:
        await assert_payroll_scope(db, current, employee_id)
    else:
        await assert_payroll_scope_all(db, current)


class RuleIn(BaseModel):
    employee_id: Optional[str] = None
    name: str = "Commission"
    event: str
    calc_type: str = "percent"      # percent|flat
    percent_basis: str = "total"    # total|margin
    amount: float = 0
    coefficient: float = 1
    category: Optional[str] = None
    wo_type_id: Optional[str] = None
    calc_timing: str = "on_close"
    trigger_status_key: Optional[str] = None
    term_condition: str = "any"
    cancel_if_deleted: bool = True
    rule_level: str = "generic"
    product_id: Optional[str] = None
    service_id: Optional[str] = None


def _apply_rule(r: CommissionRule, data: RuleIn) -> None:
    r.employee_id = data.employee_id
    r.name = data.name
    r.event = data.event
    r.calc_type = data.calc_type
    r.percent_basis = data.percent_basis
    r.amount = Decimal(str(data.amount))
    r.coefficient = Decimal(str(data.coefficient))
    r.category = data.category
    r.wo_type_id = data.wo_type_id
    r.calc_timing = data.calc_timing if data.calc_timing in TIMINGS else "on_close"
    r.trigger_status_key = data.trigger_status_key
    r.term_condition = data.term_condition if data.term_condition in TERMS else "any"
    r.cancel_if_deleted = bool(data.cancel_if_deleted)
    r.rule_level = data.rule_level if data.rule_level in LEVELS else "generic"
    r.product_id = data.product_id
    r.service_id = data.service_id


@router.post("/rules", status_code=201)
async def create_rule(
    data: RuleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    if data.event not in EVENTS:
        raise HTTPException(400, f"event must be one of {EVENTS}")
    if data.calc_timing == "on_status" and not data.trigger_status_key:
        raise HTTPException(400, "on_status timing requires trigger_status_key")
    await _assert_rule_scope(db, current, data.employee_id)
    r = CommissionRule(org_id=current.org_id)
    _apply_rule(r, data)
    db.add(r)
    await db.flush()
    await _audit(db, current, "payroll.rule_create", "commission_rule", r.id, _ser_rule(r))
    await db.commit()
    await db.refresh(r)
    return _ser_rule(r)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str, data: RuleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    r = (await db.execute(select(CommissionRule).where(
        CommissionRule.id == rule_id, CommissionRule.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    await _assert_rule_scope(db, current, r.employee_id)
    await _assert_rule_scope(db, current, data.employee_id)
    before = _ser_rule(r)
    _apply_rule(r, data)
    after = _ser_rule(r)
    from backend.services.audit import diff_fields
    await _audit(db, current, "payroll.rule_update", "commission_rule", r.id,
                 diff_fields(before, after, list(after)))
    await db.commit()
    return _ser_rule(r)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    r = (await db.execute(select(CommissionRule).where(
        CommissionRule.id == rule_id, CommissionRule.org_id == current.org_id))).scalar_one_or_none()
    if r:
        await _assert_rule_scope(db, current, r.employee_id)
        r.active = False
        await _audit(db, current, "payroll.rule_delete", "commission_rule", r.id, {"name": r.name})
        await db.commit()
    return {"ok": True}


@router.get("/balance/{employee_id}")
async def employee_balance(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # Payroll managers see anyone's balance; everyone else only their own, if their role lets them.
    if not await has_perm(db, current, "can_manage_payroll"):
        if employee_id != current.id:
            await assert_perm(db, current, "can_manage_payroll")
        await assert_perm(db, current, "can_view_own_compensation")
    else:
        await assert_payroll_scope(db, current, employee_id)
    eng = CommissionEngine(db, current.org_id)
    bal = await eng.balance(employee_id)
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.employee_id == employee_id)
        .order_by(EmployeeBalanceLedger.created_at.desc()).limit(100))).scalars().all()
    return {
        "balance": float(bal),
        "ledger": [{"id": e.id, "entry_type": e.entry_type, "amount": float(e.amount or 0),
                    "event": e.event, "ref_type": e.ref_type, "ref_id": e.ref_id,
                    "note": e.note, "created_at": e.created_at.isoformat() if e.created_at else None}
                   for e in rows],
    }


# ── E1: My earnings — the caller's own pay for a period, broken down ─────────
def _accrual_status(runs: list) -> str:
    live = [r.status for r in runs if r.status != "void"]
    if not live:
        return "not_accrued"
    if all(st == "paid" for st in live):
        return "paid"
    if any(st in ("accrued", "paid") for st in live):
        return "accrued"
    return "draft"


@router.get("/me")
async def my_earnings(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_own_payroll")),
):
    """What the signed-in person earned in a period (default: this month): base, hourly,
    commissions broken down rule → work order → line item, bonuses, penalties, and whether the
    period has been accrued or paid. Only ever the caller's own figures; the role's pay-history
    limit (payroll_visibility_days) still applies."""
    from backend.services import scoping
    today = date.today()
    ps = parse_date(date_from, "from") if date_from else today.replace(day=1)
    pe = parse_date(date_to, "to") if date_to else _month_bounds(today.strftime("%Y-%m"))[1]
    if pe < ps:
        raise HTTPException(400, "from must be on or before to")
    if (pe - ps).days > 366:
        raise HTTPException(400, "Pick a period of a year or less")
    cutoff = scoping.payroll_cutoff(await scoping.role_for(db, current))
    asked_from = ps
    if cutoff and ps < cutoff:
        ps = cutoff
    emp = (await db.execute(select(Employee).where(
        Employee.id == current.id, Employee.org_id == current.org_id))).scalar_one()
    empty = pe < ps                      # the whole period is older than the role may see
    breakdown = [] if empty else await commission_breakdown(db, current.org_id, emp.id, ps, pe)
    adj = {"bonuses": [], "penalties": []} if empty else await adjustments(db, current.org_id, emp.id, ps, pe)
    base = _money(0) if empty else expected_base(emp, ps, pe)
    hourly = _money(0) if empty else await _hourly_for_period(db, current.org_id, emp.id, ps, pe)
    commissions = _money(sum(_money(g["total"]) for g in breakdown))
    bonuses = _money(sum(_money(b["amount"]) for b in adj["bonuses"]))
    penalties = _money(sum(_money(b["amount"]) for b in adj["penalties"]))
    runs = [] if empty else (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id, PayrollRun.employee_id == emp.id,
        PayrollRun.period_start <= pe, PayrollRun.period_end >= ps)
        .order_by(PayrollRun.period_start))).scalars().all()
    return {
        "employee_id": emp.id, "name": emp.name, "wage_type": emp.wage_type,
        "period_start": ps.isoformat(), "period_end": pe.isoformat(),
        "visible_from": cutoff.isoformat() if cutoff and asked_from < cutoff else None,
        "base_salary": float(base), "hourly_total": float(hourly),
        "commissions_total": float(commissions), "bonuses_total": float(bonuses),
        "penalties_total": float(penalties),
        "total": float(_money(base + hourly + commissions + bonuses - penalties)),
        "accrual_status": _accrual_status(runs),
        "accruals": [{"id": r.id, "period_start": r.period_start.isoformat(),
                      "period_end": r.period_end.isoformat(), "status": r.status,
                      "gross_total": float(r.gross_total or 0),
                      "paid_at": r.paid_at.isoformat() if r.paid_at else None}
                     for r in runs if r.status != "void"],
        "commissions": breakdown, "bonuses": adj["bonuses"], "penalties": adj["penalties"],
    }


async def _assert_reward_rights(db, current, bonus, penalty) -> None:
    """A bonus or penalty folded into a pay run needs the same right as adding one on its own."""
    if bonus and float(bonus) > 0:
        await assert_perm(db, current, "can_create_bonuses")
    if penalty and float(penalty) > 0:
        await assert_perm(db, current, "can_create_penalties")


class RunIn(BaseModel):
    employee_id: str
    period_start: str   # YYYY-MM-DD
    period_end: str
    base_salary: float = 0
    bonus: float = 0
    penalty: float = 0
    draft: bool = False


@router.post("/run", status_code=201)
async def create_payroll_run(
    data: RunIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Roll up base salary + ledger commissions/bonuses/penalties for a period into
    a PayrollRun, then post a single balance entry for the base/bonus/penalty parts.
    Commissions are already in the ledger from order events."""
    await assert_perm(db, current, "can_accrue_payroll")
    await _assert_reward_rights(db, current, data.bonus, data.penalty)
    await assert_payroll_scope(db, current, data.employee_id)
    ps = parse_date(data.period_start, "period_start")
    pe = parse_date(data.period_end, "period_end")
    today = date.today()
    if not data.draft and (pe.year, pe.month) >= (today.year, today.month):
        raise HTTPException(400, "Can't accrue payroll for the current or a future month; save as draft instead")
    if not data.draft:
        dupe = (await db.execute(select(PayrollRun).where(
            PayrollRun.org_id == current.org_id, PayrollRun.employee_id == data.employee_id,
            PayrollRun.status.notin_(_NOT_ACCRUED),
            PayrollRun.period_start <= pe, PayrollRun.period_end >= ps))).scalars().first()   # any overlap
        if dupe:
            raise HTTPException(409, "An accrual already covers part of this period for this employee")
    # sum commissions already accrued in the window
    commissions = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.employee_id == data.employee_id,
        EmployeeBalanceLedger.entry_type.in_(["commission", "clawback"]),
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
    # sum hourly wages whose approved timesheet period overlaps this run's window.
    # The timesheet approval posts a ledger row with ref_type='timesheet' and
    # ref_id='{employee}:{from}:{to}', so wages are attributed to the period worked
    # rather than the date the manager happened to approve them.
    hourly_rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.employee_id == data.employee_id,
        EmployeeBalanceLedger.entry_type == "hourly_wage"))).scalars().all()
    hourly = 0
    for row in hourly_rows:
        ref = row.ref_id or ""
        parts = ref.split(":")
        if len(parts) >= 3:
            try:
                ts_from = date.fromisoformat(parts[-2])
                ts_to = date.fromisoformat(parts[-1])
            except ValueError:
                continue
            if ts_from <= pe and ts_to >= ps:   # period overlap
                hourly += row.amount
        else:
            # no parseable period — fall back to posting date
            if ps <= row.created_at.date() <= pe:
                hourly += row.amount
    base = _money(data.base_salary)
    bonus = _money(data.bonus)
    penalty = _money(data.penalty)
    gross = _money(base + _money(commissions) + _money(hourly) + bonus - penalty)
    run = PayrollRun(
        org_id=current.org_id, employee_id=data.employee_id, period_start=ps, period_end=pe,
        base_salary=base, commissions_total=_money(commissions), hourly_total=_money(hourly),
        bonuses_total=bonus, penalties_total=penalty, gross_total=gross,
        status="draft" if data.draft else "accrued",
        accrued_at=None if data.draft else datetime.utcnow())
    db.add(run)
    await db.flush()
    if not data.draft:
        eng = CommissionEngine(db, current.org_id)
        if base:
            await eng._post(employee_id=data.employee_id, entry_type="base", amount=base, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"base:{run.id}", note=f"Base salary {ps}–{pe}")
        if bonus:
            await eng._post(employee_id=data.employee_id, entry_type="bonus", amount=bonus, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"bonus:{run.id}", note="Bonus")
        if penalty:
            await eng._post(employee_id=data.employee_id, entry_type="penalty", amount=-penalty, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"penalty:{run.id}", note="Penalty")
    await _audit(db, current, "payroll.run_create", "payroll_run", run.id, _run_audit(run))
    await db.commit()
    await db.refresh(run)
    return {
        "id": run.id, "base_salary": float(run.base_salary), "commissions_total": float(run.commissions_total),
        "hourly_total": float(run.hourly_total),
        "bonuses_total": float(run.bonuses_total), "penalties_total": float(run.penalties_total),
        "gross_total": float(run.gross_total), "status": run.status,
    }


# ── Build 103: bulk accrual — one accrual per employee, in one action ─────────
class BulkRunIn(BaseModel):
    period_start: str
    period_end: str
    employee_ids: Optional[list[str]] = None   # None = every active employee
    draft: bool = False


@router.post("/run-all", status_code=201)
async def create_payroll_runs_bulk(
    data: BulkRunIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Accrue a whole team for a period in one call.

    Month-end payroll is a batch operation — a shop with fifteen staff should not click
    fifteen times. Each employee still gets exactly one accrual (the per-employee
    duplicate guard from /run is reused), so an employee already accrued for the period
    is skipped rather than double-paid. Employees are processed independently: one failure
    does not abandon the rest, and the response reports what happened per person.
    """
    await assert_perm(db, current, "can_accrue_payroll")
    ps = parse_date(data.period_start, "period_start")
    pe = parse_date(data.period_end, "period_end")
    today = date.today()
    if not data.draft and (pe.year, pe.month) >= (today.year, today.month):
        raise HTTPException(400, "Can't accrue payroll for the current or a future month; save as draft instead")

    scope = await payroll_scope_ids(db, current)
    if data.employee_ids:
        outside = [i for i in data.employee_ids if not in_scope(scope, i)]
        if outside:
            raise HTTPException(403, "Some of these people's payroll is outside what your role can see")
        emps = (await db.execute(select(Employee).where(
            Employee.org_id == current.org_id, Employee.id.in_(data.employee_ids)))).scalars().all()
    else:
        emps = [e for e in (await db.execute(select(Employee).where(
            Employee.org_id == current.org_id, Employee.active == True))).scalars().all()  # noqa: E712
            if in_scope(scope, e.id)]

    accrued, skipped, results = 0, 0, []
    for emp in emps:
        # reuse the duplicate guard: skip anyone already accrued for this period
        if not data.draft:
            dupe = (await db.execute(select(PayrollRun).where(
                PayrollRun.org_id == current.org_id, PayrollRun.employee_id == emp.id,
                PayrollRun.status.notin_(_NOT_ACCRUED),
                PayrollRun.period_start <= pe, PayrollRun.period_end >= ps))).scalars().first()   # any overlap
            if dupe:
                skipped += 1
                results.append({"employee_id": emp.id, "status": "skipped", "reason": "already accrued for part of this period"})
                continue
        try:
            # base salary applies only to fixed-wage staff, matching /calculation
            base = float(emp.wage_value or 0) if emp.wage_type == "fixed" else 0.0
            run = await _accrue_one(db, current.org_id, emp.id, ps, pe,
                                    base_salary=base, draft=data.draft)
            accrued += 1
            await _audit(db, current, "payroll.run_create", "payroll_run", run.id, _run_audit(run))
            results.append({"employee_id": emp.id, "status": run.status,
                            "gross_total": float(run.gross_total)})
        except Exception as exc:                       # one bad employee must not sink the batch
            results.append({"employee_id": emp.id, "status": "error", "reason": str(exc)[:120]})
    await db.commit()
    return {"period": [data.period_start, data.period_end], "accrued": accrued,
            "skipped": skipped, "total": len(emps), "results": results}


def _run_audit(run: PayrollRun) -> dict:
    return {"employee_id": run.employee_id, "period": [run.period_start.isoformat(), run.period_end.isoformat()],
            "status": run.status, "gross_total": float(run.gross_total or 0)}


async def _accrue_one(db: AsyncSession, org_id: str, employee_id: str, ps, pe,
                      *, base_salary: float = 0, bonus: float = 0, penalty: float = 0,
                      draft: bool = False) -> PayrollRun:
    """Shared accrual core: roll up commissions + hourly wages for the window, add the
    base/bonus/penalty, create the run and post the balance entries. Used by both the
    single /run path and the bulk /run-all path so they can never drift apart."""
    commissions = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
        EmployeeBalanceLedger.org_id == org_id,
        EmployeeBalanceLedger.employee_id == employee_id,
        EmployeeBalanceLedger.entry_type.in_(["commission", "clawback"]),
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0

    hourly_rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == org_id,
        EmployeeBalanceLedger.employee_id == employee_id,
        EmployeeBalanceLedger.entry_type == "hourly_wage"))).scalars().all()
    hourly = 0
    for row in hourly_rows:
        parts = (row.ref_id or "").split(":")
        if len(parts) >= 3:
            try:
                ts_from = date.fromisoformat(parts[-2])
                ts_to = date.fromisoformat(parts[-1])
            except ValueError:
                continue
            if ts_from <= pe and ts_to >= ps:
                hourly += row.amount
        elif ps <= row.created_at.date() <= pe:
            hourly += row.amount

    base, bon, pen = _money(base_salary), _money(bonus), _money(penalty)
    gross = _money(base + _money(commissions) + _money(hourly) + bon - pen)
    run = PayrollRun(
        org_id=org_id, employee_id=employee_id, period_start=ps, period_end=pe,
        base_salary=base, commissions_total=_money(commissions), hourly_total=_money(hourly),
        bonuses_total=bon, penalties_total=pen, gross_total=gross,
        status="draft" if draft else "accrued",
        accrued_at=None if draft else datetime.utcnow())
    db.add(run)
    await db.flush()
    if not draft:
        eng = CommissionEngine(db, org_id)
        if base:
            await eng._post(employee_id=employee_id, entry_type="base", amount=base, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"base:{run.id}", note=f"Base salary {ps}–{pe}")
        if bon:
            await eng._post(employee_id=employee_id, entry_type="bonus", amount=bon, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"bonus:{run.id}", note="Bonus")
        if pen:
            await eng._post(employee_id=employee_id, entry_type="penalty", amount=-pen, event=None,
                            rule_id=None, ref_type="payroll_run", ref_id=run.id,
                            idempotency_key=f"penalty:{run.id}", note="Penalty")
    return run


# ── Build 71: live payroll calculation preview + accrual workflow ─────────────
def _month_bounds(month: str):
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return start, end


async def _role_names(db: AsyncSession, org_id: str) -> dict:
    from backend.models import Role
    rows = (await db.execute(select(Role).where(Role.org_id == org_id))).scalars().all()
    return {r.id: r.name for r in rows}


async def _manual_adjust_totals(db, org_id, employee_id, ps, pe):
    """Sum ad-hoc penalties/bonuses (entered on the payroll-calculation page) for a
    period. Bonuses are stored positive, penalties negative; returns both positive."""
    bonus = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
        EmployeeBalanceLedger.org_id == org_id, EmployeeBalanceLedger.employee_id == employee_id,
        EmployeeBalanceLedger.entry_type == "bonus", EmployeeBalanceLedger.ref_type == "manual",
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
    penalty = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
        EmployeeBalanceLedger.org_id == org_id, EmployeeBalanceLedger.employee_id == employee_id,
        EmployeeBalanceLedger.entry_type == "penalty", EmployeeBalanceLedger.ref_type == "manual",
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
    return _money(bonus), _money(abs(penalty))


async def _hourly_for_period(db, org_id, employee_id, ps, pe) -> Decimal:
    """Hourly wages whose approved-timesheet window overlaps the period."""
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == org_id,
        EmployeeBalanceLedger.employee_id == employee_id,
        EmployeeBalanceLedger.entry_type == "hourly_wage"))).scalars().all()
    total = Decimal("0")
    for row in rows:
        parts = (row.ref_id or "").split(":")
        if len(parts) >= 3:
            try:
                ts_from = date.fromisoformat(parts[-2])
                ts_to = date.fromisoformat(parts[-1])
            except ValueError:
                continue
            if ts_from <= pe and ts_to >= ps:
                total += _money(row.amount)
        elif ps <= row.created_at.date() <= pe:
            total += _money(row.amount)
    return _money(total)


async def filtered_employees(db: AsyncSession, current: Employee, *, location_id: Optional[str] = None,
                             role_id: Optional[str] = None, employee_id: Optional[str] = None) -> list[Employee]:
    """E2 — active employees for a payroll view: the caller's payroll scope, then the filters
    (location, role/position, one employee). Shared by the calculation and the payroll report."""
    from backend.services.auth import check_location_access
    if location_id:
        await check_location_access(db, current, location_id)
    q = select(Employee).where(Employee.org_id == current.org_id, Employee.active == True)  # noqa: E712
    if location_id:
        # someone with no home location isn't tied to a branch, so they count in every branch
        q = q.where((Employee.location_id == location_id) | Employee.location_id.is_(None))
    if role_id:
        q = q.where(Employee.role_id == role_id)
    if employee_id:
        q = q.where(Employee.id == employee_id)
    scope = await payroll_scope_ids(db, current)
    emps = [e for e in (await db.execute(q)).scalars().all() if in_scope(scope, e.id)]
    return sorted(emps, key=lambda e: (e.name or "").lower())


@router.get("/calculation")
async def payroll_calculation(
    month: str,
    location_id: Optional[str] = None,
    role_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Live, non-mutating preview of the month's payroll for every employee the caller's payroll
    scope covers, optionally narrowed to a location, a role (position) or one employee."""
    try:
        ps, pe = _month_bounds(month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    emps = await filtered_employees(db, current, location_id=location_id, role_id=role_id,
                                    employee_id=employee_id)
    runs = (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id,
        PayrollRun.period_start == ps, PayrollRun.period_end == pe))).scalars().all()
    # a voided run doesn't hide the live one for the same month
    run_by_emp = {r.employee_id: r for r in sorted(runs, key=lambda r: r.status != "void")}
    roles = await _role_names(db, current.org_id)
    items = []
    for e in emps:
        commissions = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
            EmployeeBalanceLedger.org_id == current.org_id, EmployeeBalanceLedger.employee_id == e.id,
            EmployeeBalanceLedger.entry_type.in_(["commission", "clawback"]),
            func.date(EmployeeBalanceLedger.created_at) >= ps,
            func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
        hourly = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
            EmployeeBalanceLedger.org_id == current.org_id, EmployeeBalanceLedger.employee_id == e.id,
            EmployeeBalanceLedger.entry_type == "hourly_wage",
            func.date(EmployeeBalanceLedger.created_at) >= ps,
            func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
        bonuses, penalties = await _manual_adjust_totals(db, current.org_id, e.id, ps, pe)
        base = _money(e.wage_value) if e.wage_type == "fixed" else _money(0)
        run = run_by_emp.get(e.id)
        total = base + _money(commissions) + _money(hourly) + bonuses - penalties
        items.append({
            "employee_id": e.id, "name": e.name, "wage_type": e.wage_type,
            "position": roles.get(e.role_id) or "—", "role_id": e.role_id, "location_id": e.location_id,
            "base_salary": float(base), "commissions_total": float(_money(commissions)),
            "hourly_total": float(_money(hourly)),
            "bonuses_total": float(bonuses), "penalties_total": float(penalties),
            "computed_total": float(_money(total)),
            "accrual_status": run.status if run else None,
            "accrual_id": run.id if run else None,
        })
    grand = {
        "base_salary": float(_money(sum(i["base_salary"] for i in items))),
        "commissions_total": float(_money(sum(i["commissions_total"] for i in items))),
        "hourly_total": float(_money(sum(i["hourly_total"] for i in items))),
        "bonuses_total": float(_money(sum(i["bonuses_total"] for i in items))),
        "penalties_total": float(_money(sum(i["penalties_total"] for i in items))),
        "computed_total": float(_money(sum(i["computed_total"] for i in items))),
    }
    return {"month": month, "period_start": ps.isoformat(), "period_end": pe.isoformat(),
            "filters": {"location_id": location_id, "role_id": role_id, "employee_id": employee_id},
            "items": items, "totals": grand}


@router.get("/calculation/export.csv")
async def export_calculation(
    month: str,
    location_id: Optional[str] = None,
    role_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """E2 — the payroll calculation as it is on screen (same scope and filters), as CSV."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from backend.core.spreadsheet import safe_row
    await assert_perm(db, current, "can_export_payroll")
    data = await payroll_calculation(month, location_id, role_id, employee_id, db, current)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee", "Position", "Wage type", "Base", "Hourly", "Commission", "Bonuses",
                "Penalties", "Total", "Accrual"])
    for i in data["items"]:
        w.writerow(safe_row([i["name"], i["position"], i["wage_type"] or "", i["base_salary"], i["hourly_total"],
                             i["commissions_total"], i["bonuses_total"], i["penalties_total"],
                             i["computed_total"], i["accrual_status"] or "not accrued"]))
    t = data["totals"]
    w.writerow(["Total", "", "", t["base_salary"], t["hourly_total"], t["commissions_total"],
                t["bonuses_total"], t["penalties_total"], t["computed_total"], ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="payroll_{month}.csv"'})


# ── Build 144h: ad-hoc penalties & bonuses on the payroll-calculation page ────
class AdjustmentIn(BaseModel):
    employee_id: str
    kind: str            # penalty|bonus
    amount: float
    note: Optional[str] = None
    month: Optional[str] = None   # YYYY-MM; dates the entry mid-period (defaults to today)


@router.post("/adjustment", status_code=201)
async def add_adjustment(
    data: AdjustmentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    if data.kind not in ("penalty", "bonus"):
        raise HTTPException(400, "kind must be penalty|bonus")
    await assert_perm(db, current, "can_create_bonuses" if data.kind == "bonus" else "can_create_penalties")
    if data.amount is None or float(data.amount) <= 0:
        raise HTTPException(400, "amount must be a positive number")
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await assert_payroll_scope(db, current, emp.id)
    await assert_not_self(db, current, emp.id, "add a bonus or penalty for yourself")
    amt = _money(data.amount)
    signed = amt if data.kind == "bonus" else -amt
    # date the entry inside the chosen month so it lands in that period's calculation
    when = datetime.utcnow()
    if data.month:
        try:
            ps, pe = _month_bounds(data.month)
            if not (ps <= when.date() <= pe):
                when = datetime(ps.year, ps.month, 15, 12, 0, 0)
        except (ValueError, IndexError):
            pass
    entry = EmployeeBalanceLedger(
        org_id=current.org_id, employee_id=data.employee_id, entry_type=data.kind,
        amount=signed, ref_type="manual", ref_id=f"manual:{current.id}",
        note=data.note or data.kind.capitalize(), created_at=when)
    db.add(entry)
    await db.flush()
    await _audit(db, current, f"payroll.{data.kind}_add", "employee", data.employee_id,
                 {"entry_id": entry.id, "amount": float(signed), "note": entry.note})
    await db.commit()
    await db.refresh(entry)
    return {"id": entry.id, "entry_type": entry.entry_type, "amount": float(entry.amount),
            "note": entry.note, "created_at": entry.created_at.isoformat()}


@router.delete("/adjustment/{entry_id}")
async def remove_adjustment(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    e = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.id == entry_id,
        EmployeeBalanceLedger.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Adjustment not found")
    if e.ref_type != "manual" or e.entry_type not in ("penalty", "bonus"):
        raise HTTPException(400, "Only manual penalties/bonuses can be removed here")
    await assert_payroll_scope(db, current, e.employee_id)
    await _audit(db, current, f"payroll.{e.entry_type}_remove", "employee", e.employee_id,
                 {"entry_id": e.id, "amount": float(e.amount or 0), "note": e.note})
    await db.delete(e)
    await db.commit()
    return {"ok": True}


@router.get("/calculation/detail")
async def calculation_detail(
    employee_id: str, month: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Per-employee breakdown for the payroll-calculation drawer: itemized
    commissions (flat, and grouped rule → document → line item), plus the manual
    penalties and bonuses for the month."""
    try:
        ps, pe = _month_bounds(month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    emp = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await assert_payroll_scope(db, current, emp.id)
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.employee_id == employee_id,
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe)
        .order_by(EmployeeBalanceLedger.created_at))).scalars().all()

    def _row(e):
        return {"id": e.id, "date": e.created_at.isoformat(), "amount": float(e.amount or 0),
                "note": e.note or "", "ref_type": e.ref_type, "ref_id": e.ref_id}

    commissions = [_row(e) for e in rows if e.entry_type in ("commission", "clawback")]
    penalties = [_row(e) for e in rows if e.entry_type == "penalty" and e.ref_type == "manual"]
    bonuses = [_row(e) for e in rows if e.entry_type == "bonus" and e.ref_type == "manual"]
    bonuses_total, penalties_total = await _manual_adjust_totals(db, current.org_id, employee_id, ps, pe)
    hourly = await _hourly_for_period(db, current.org_id, employee_id, ps, pe)
    base = _money(emp.wage_value) if emp.wage_type == "fixed" else _money(0)
    comm_total = _money(sum(_money(c["amount"]) for c in commissions))
    return {
        "employee_id": employee_id, "name": emp.name, "month": month,
        "base_salary": float(base),
        "commissions": commissions, "commissions_total": float(comm_total),
        "hourly_total": float(hourly),
        "penalties": penalties, "penalties_total": float(penalties_total),
        "bonuses": bonuses, "bonuses_total": float(bonuses_total),
        "computed_total": float(_money(base + comm_total + hourly + bonuses_total - penalties_total)),
        "breakdown": await commission_breakdown(db, current.org_id, employee_id, ps, pe),
    }


class AccrueIn(BaseModel):
    employee_id: str
    month: str


@router.post("/calculation/accrue", status_code=201)
async def accrue_calculation(
    data: AccrueIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Snapshot a month's calculated payroll into an accrual. Commissions, hourly
    wages and manual penalties/bonuses are already in the ledger; this records the
    per-employee PayrollRun and posts the base-salary entry (the only piece not yet
    on the ledger). Guards against the current/future month and double accrual."""
    await assert_perm(db, current, "can_accrue_payroll")
    try:
        ps, pe = _month_bounds(data.month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    today = date.today()
    if (pe.year, pe.month) >= (today.year, today.month):
        raise HTTPException(400, "Can't accrue payroll for the current or a future month")
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await assert_payroll_scope(db, current, emp.id)
    dupe = (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id, PayrollRun.employee_id == data.employee_id,
        PayrollRun.status.notin_(_NOT_ACCRUED),
        PayrollRun.period_start <= pe, PayrollRun.period_end >= ps))).scalars().first()   # any overlap
    if dupe:
        raise HTTPException(409, "An accrual already covers part of this period for this employee")

    commissions = (await db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0)).where(
        EmployeeBalanceLedger.org_id == current.org_id, EmployeeBalanceLedger.employee_id == data.employee_id,
        EmployeeBalanceLedger.entry_type.in_(["commission", "clawback"]),
        func.date(EmployeeBalanceLedger.created_at) >= ps,
        func.date(EmployeeBalanceLedger.created_at) <= pe))).scalar() or 0
    hourly = await _hourly_for_period(db, current.org_id, data.employee_id, ps, pe)
    bonuses, penalties = await _manual_adjust_totals(db, current.org_id, data.employee_id, ps, pe)
    base = _money(emp.wage_value) if emp.wage_type == "fixed" else _money(0)
    gross = _money(base + _money(commissions) + hourly + bonuses - penalties)

    run = PayrollRun(
        org_id=current.org_id, employee_id=data.employee_id, period_start=ps, period_end=pe,
        base_salary=base, commissions_total=_money(commissions), hourly_total=hourly,
        bonuses_total=bonuses, penalties_total=penalties, gross_total=gross,
        status="accrued", accrued_at=datetime.utcnow())
    db.add(run)
    await db.flush()
    if base:
        eng = CommissionEngine(db, current.org_id)
        await eng._post(employee_id=data.employee_id, entry_type="base", amount=base, event=None,
                        rule_id=None, ref_type="payroll_run", ref_id=run.id,
                        idempotency_key=f"base:{run.id}", note=f"Base salary {ps}–{pe}")
    await _audit(db, current, "payroll.run_create", "payroll_run", run.id, _run_audit(run))
    await db.commit()
    await db.refresh(run)
    return {"id": run.id, "status": run.status, "gross_total": float(run.gross_total),
            "base_salary": float(run.base_salary), "commissions_total": float(run.commissions_total),
            "hourly_total": float(run.hourly_total), "bonuses_total": float(run.bonuses_total),
            "penalties_total": float(run.penalties_total)}


# ── Build 144i: the second clock — pay from shop-attendance shifts ────────────
class AttendanceWagesIn(BaseModel):
    employee_id: str
    date_from: str   # YYYY-MM-DD
    date_to: str


@router.post("/attendance-wages", status_code=201)
async def post_attendance_wages(
    data: AttendanceWagesIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Post hourly wages from the **shop-attendance** clock (clock-in/out shifts)
    for a period, as opposed to the **labour-hours** clock (time on work orders,
    posted by timesheet approval). Both post an `hourly_wage` ledger entry that the
    payroll calculation sums. Idempotent per employee + window."""
    await assert_perm(db, current, "can_accrue_payroll")
    from backend.models import Shift
    start = parse_date(data.date_from, "date_from")
    end = parse_date(data.date_to, "date_to")
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await assert_payroll_scope(db, current, emp.id)
    if (emp.hourly_basis or "labour") != "attendance":
        # One clock pays: labour-hours staff are paid by timesheet approval, so paying their
        # shifts too would pay the same hours twice.
        raise HTTPException(409, f"{emp.name} is paid from labour hours (timesheet approval). "
                                 "Switch their hourly pay to shop attendance to pay from shifts.")
    await assert_not_self(db, current, emp.id, "post your own attendance wages")
    shifts = (await db.execute(select(Shift).where(
        Shift.employee_id == data.employee_id, Shift.ended_at.isnot(None),
        func.date(Shift.started_at) >= start,
        func.date(Shift.started_at) <= end))).scalars().all()
    hours = q2(sum(s.hours_worked or 0 for s in shifts))
    rate = emp.hourly_cost_rate or (emp.wage_value if emp.wage_type == "hourly" else 0.0) or 0.0
    wage = _money(q2(hours * rate))
    idem = f"attendance:{data.employee_id}:{data.date_from}:{data.date_to}"
    already = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.idempotency_key == EmployeeBalanceLedger.normalize_key(idem)))).scalar_one_or_none()
    posted = False
    if wage > 0 and not already:
        # attribute the wage to the period worked (end of the window), not "now",
        # so it lands in that month's payroll calculation.
        when = datetime(end.year, end.month, end.day, 12, 0, 0)
        db.add(EmployeeBalanceLedger(
            org_id=current.org_id, employee_id=data.employee_id, entry_type="hourly_wage",
            amount=wage, ref_type="attendance", ref_id=f"{data.employee_id}:{data.date_from}:{data.date_to}",
            idempotency_key=idem, created_at=when,
            note=f"Shop attendance wages {data.date_from}–{data.date_to} ({hours}h)"))
        posted = True
        await _audit(db, current, "payroll.attendance_wages", "employee", emp.id,
                     {"window": [data.date_from, data.date_to], "hours": float(hours), "amount": float(wage)})
    await db.commit()
    return {"ok": True, "hours": hours, "rate": float(rate),
            "wage_posted": float(wage), "ledger_posted": posted}


@router.get("/runs")
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    q = select(PayrollRun).where(PayrollRun.org_id == current.org_id)
    scope = await payroll_scope_ids(db, current)
    if scope is not None:
        q = q.where(PayrollRun.employee_id.in_(scope or {""}))
    rows = (await db.execute(q.order_by(PayrollRun.period_start.desc()).limit(200))).scalars().all()
    names = {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id))).scalars().all()}
    items = [{
        "id": r.id, "employee_id": r.employee_id, "employee_name": names.get(r.employee_id, "—"),
        "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
        "base_salary": float(r.base_salary), "commissions_total": float(r.commissions_total),
        "hourly_total": float(r.hourly_total), "bonuses_total": float(r.bonuses_total),
        "penalties_total": float(r.penalties_total), "gross_total": float(r.gross_total),
        "status": r.status,
        "accrued_at": r.accrued_at.isoformat() if r.accrued_at else None,
        "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        "account_id": r.account_id,
        "voided_at": r.voided_at.isoformat() if r.voided_at else None,
        "void_reason": r.void_reason,
    } for r in rows]
    live = [i for i in items if i["status"] != "void"]
    totals = {k: float(_money(sum(i[k] for i in live))) for k in
              ("base_salary", "commissions_total", "hourly_total", "bonuses_total",
               "penalties_total", "gross_total")}
    return {"items": items, "totals": totals}


class PayIn(BaseModel):
    account_id: Optional[str] = None    # the cashbox the pay comes out of
    comment: Optional[str] = None


async def _payroll_flow_item(db: AsyncSession, org_id: str) -> str:
    """The org's "Payroll" expense tag, created on first use, so payouts group in cash reports."""
    from backend.models import CashFlowItem
    item = (await db.execute(select(CashFlowItem).where(
        CashFlowItem.org_id == org_id, CashFlowItem.name == "Payroll",
        CashFlowItem.direction == "expense"))).scalars().first()
    if not item:
        item = CashFlowItem(org_id=org_id, name="Payroll", direction="expense", is_system=True)
        db.add(item)
        await db.flush()
    return item.id


async def _get_run(db, org_id, run_id) -> PayrollRun:
    run = (await db.execute(select(PayrollRun).where(
        PayrollRun.id == run_id, PayrollRun.org_id == org_id).with_for_update())).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/runs/{run_id}/pay")
async def mark_run_paid(
    run_id: str,
    data: Optional[PayIn] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Pay an accrued run: draw the employee's balance down (payout ledger entry) and take the
    money out of the chosen cashbox as a Payroll expense, which is what the P&L counts."""
    from backend.models import Account
    from backend.routers.cash import post_transaction
    from backend.services import scoping
    run = await _get_run(db, current.org_id, run_id)
    await assert_payroll_scope(db, current, run.employee_id)
    if run.status == "draft":
        raise HTTPException(400, "Accrue the draft before marking it paid")
    if run.status == "paid":
        raise HTTPException(409, "This run is already paid")
    if run.status == "void":
        raise HTTPException(409, "This run was voided")
    await assert_not_self(db, current, run.employee_id, "mark your own pay as paid")
    if not data or not data.account_id:
        raise HTTPException(400, "Choose the account the pay comes out of")
    acct = (await db.execute(select(Account).where(
        Account.id == data.account_id, Account.org_id == current.org_id,
        Account.active == True))).scalar_one_or_none()  # noqa: E712
    if not acct:
        raise HTTPException(404, "Account not found")
    allowed = scoping.allowed_accounts(await scoping.role_for(db, current))
    if allowed is not None and acct.id not in allowed:
        raise HTTPException(403, "You can't pay out of this account")
    gross = _money(run.gross_total)
    eng = CommissionEngine(db, current.org_id)
    await eng._post(employee_id=run.employee_id, entry_type="payout", amount=-gross,
                    event=None, rule_id=None, ref_type="payroll_run", ref_id=run.id,
                    idempotency_key=f"payout:{run.id}", note="Payroll payout")
    txn_id = None
    if gross > 0:
        emp_name = (await db.execute(select(Employee.name).where(Employee.id == run.employee_id))).scalar()
        txn = await post_transaction(
            db, current.org_id, acct.id, "expense", -float(q2(gross)),
            cash_flow_item_id=await _payroll_flow_item(db, current.org_id),
            comment=(data.comment or f"Payroll {emp_name or ''} {run.period_start}–{run.period_end}").strip()[:500],
            ref_type="payroll_run", ref_id=run.id, employee_id=run.employee_id)
        await db.flush()
        txn_id = txn.id
    run.status = "paid"
    run.paid_at = datetime.utcnow()
    run.account_id = acct.id
    run.cash_txn_id = txn_id
    await _audit(db, current, "payroll.run_pay", "payroll_run", run.id,
                 {**_run_audit(run), "account_id": acct.id, "cash_txn_id": txn_id})
    await db.commit()
    return {"ok": True, "status": "paid", "cash_txn_id": txn_id}


class VoidIn(BaseModel):
    reason: str


@router.post("/runs/{run_id}/void")
async def void_run(
    run_id: str,
    data: VoidIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Void an accrued or paid run. Everything it posted is reversed by new entries (the ledger
    stays append-only): the base/bonus/penalty accruals and the payout come back off the
    employee's balance, and the cash expense is voided so the money returns to the cashbox and
    leaves the P&L. The period is free to accrue again."""
    from backend.models import Account, Transaction
    from backend.services.money import add_to_column
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(400, "Give a reason for voiding this run")
    run = await _get_run(db, current.org_id, run_id)
    await assert_payroll_scope(db, current, run.employee_id)
    if run.status == "void":
        raise HTTPException(409, "This run is already voided")
    if run.status == "draft":
        raise HTTPException(400, "A draft posts nothing, so there is nothing to void")
    await assert_not_self(db, current, run.employee_id, "void your own pay run")
    posted = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == current.org_id,
        EmployeeBalanceLedger.ref_type == "payroll_run",
        EmployeeBalanceLedger.ref_id == run.id))).scalars().all()
    eng = CommissionEngine(db, current.org_id)
    for e in posted:
        await eng._post(employee_id=e.employee_id, entry_type=e.entry_type, amount=-_money(e.amount),
                        event=None, rule_id=None, ref_type="payroll_run_void", ref_id=run.id,
                        idempotency_key=f"void:{e.id}", note=f"Void of {e.note or e.entry_type}: {reason}"[:500])
    if run.cash_txn_id:
        t = (await db.execute(select(Transaction).where(
            Transaction.id == run.cash_txn_id, Transaction.org_id == current.org_id))).scalar_one_or_none()
        if t and t.status != "deleted":
            acc = (await db.execute(select(Account).where(
                Account.id == t.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
            if acc:
                await add_to_column(db, acc, "balance", -t.amount)   # the money is back in the cashbox
            t.status = "deleted"
            t.void_reason = f"Payroll run voided: {reason}"[:300]
            t.voided_by_id = current.id
    was = run.status
    run.status = "void"
    run.voided_at = datetime.utcnow()
    run.voided_by_id = current.id
    run.void_reason = reason[:300]
    await _audit(db, current, "payroll.run_void", "payroll_run", run.id,
                 {**_run_audit(run), "was": was, "reason": reason[:300]})
    await db.commit()
    return {"ok": True, "status": "void"}


# ── Build 72: per-employee coefficients ──────────────────────────────────────
def _ser_coeff(c) -> dict:
    return {"id": c.id, "employee_id": c.employee_id, "scope_type": c.scope_type,
            "scope_ref": c.scope_ref, "context": c.context, "coefficient": float(c.coefficient or 1)}


@router.get("/coefficients")
async def list_coefficients(
    employee_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    from backend.models import EmployeeCoefficient
    q = select(EmployeeCoefficient).where(EmployeeCoefficient.org_id == current.org_id)
    if employee_id:
        q = q.where(EmployeeCoefficient.employee_id == employee_id)
    scope = await payroll_scope_ids(db, current)
    rows = (await db.execute(q)).scalars().all()
    return {"items": [_ser_coeff(c) for c in rows if in_scope(scope, c.employee_id)]}


class CoeffIn(BaseModel):
    employee_id: str
    scope_type: str
    scope_ref: str
    context: str = "order"
    coefficient: float = 1


@router.post("/coefficients", status_code=201)
async def upsert_coefficient(
    data: CoeffIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    from backend.models import EmployeeCoefficient
    if data.scope_type not in ("product", "service", "product_category", "service_category"):
        raise HTTPException(400, "invalid scope_type")
    if data.context not in ("order", "sales"):
        raise HTTPException(400, "context must be order|sales")
    await assert_payroll_scope(db, current, data.employee_id)
    existing = (await db.execute(select(EmployeeCoefficient).where(
        EmployeeCoefficient.org_id == current.org_id, EmployeeCoefficient.employee_id == data.employee_id,
        EmployeeCoefficient.scope_type == data.scope_type, EmployeeCoefficient.scope_ref == data.scope_ref,
        EmployeeCoefficient.context == data.context))).scalar_one_or_none()
    if existing:
        old = float(existing.coefficient or 1)
        existing.coefficient = Decimal(str(data.coefficient))
        await _audit(db, current, "payroll.coefficient_update", "employee", data.employee_id,
                     {"id": existing.id, "scope": [data.scope_type, data.scope_ref, data.context],
                      "coefficient": [old, float(data.coefficient)]})
        await db.commit()
        return _ser_coeff(existing)
    c = EmployeeCoefficient(
        org_id=current.org_id, employee_id=data.employee_id, scope_type=data.scope_type,
        scope_ref=data.scope_ref, context=data.context, coefficient=Decimal(str(data.coefficient)))
    db.add(c)
    await db.flush()
    await _audit(db, current, "payroll.coefficient_create", "employee", data.employee_id,
                 {"id": c.id, "scope": [data.scope_type, data.scope_ref, data.context],
                  "coefficient": float(data.coefficient)})
    await db.commit()
    await db.refresh(c)
    return _ser_coeff(c)


@router.delete("/coefficients/{coeff_id}")
async def delete_coefficient(
    coeff_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    from backend.models import EmployeeCoefficient
    c = (await db.execute(select(EmployeeCoefficient).where(
        EmployeeCoefficient.id == coeff_id, EmployeeCoefficient.org_id == current.org_id))).scalar_one_or_none()
    if c:
        await assert_payroll_scope(db, current, c.employee_id)
        await _audit(db, current, "payroll.coefficient_delete", "employee", c.employee_id,
                     {"id": c.id, "scope": [c.scope_type, c.scope_ref, c.context],
                      "coefficient": float(c.coefficient or 1)})
        await db.delete(c)
        await db.commit()
    return {"ok": True}
