"""Payroll & commissions API (Mandate 1)."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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
from backend.services.auth import require_perm
from backend.services.payroll import CommissionEngine, _money
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/payroll", tags=["payroll"])

EVENTS = ["order_created", "order_closed", "order_closed_success", "order_closed_fail",
          "labor_performed", "product_sold",
          "assignee_labor", "assignee_product", "task_closed"]  # Build 86 / 144ds
TIMINGS = ["on_close", "immediately", "on_status"]
TERMS = ["any", "on_time", "overdue", "urgent"]
LEVELS = ["generic", "product", "category", "service", "profile"]


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
    return {"items": [_ser_rule(r) for r in rows], "events": EVENTS}


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
    r = CommissionRule(org_id=current.org_id)
    _apply_rule(r, data)
    db.add(r)
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
    _apply_rule(r, data)
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
        r.active = False
        await db.commit()
    return {"ok": True}


@router.get("/balance/{employee_id}")
async def employee_balance(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
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
    ps = parse_date(data.period_start, "period_start")
    pe = parse_date(data.period_end, "period_end")
    today = date.today()
    if not data.draft and (pe.year, pe.month) >= (today.year, today.month):
        raise HTTPException(400, "Can't accrue payroll for the current or a future month; save as draft instead")
    if not data.draft:
        dupe = (await db.execute(select(PayrollRun).where(
            PayrollRun.org_id == current.org_id, PayrollRun.employee_id == data.employee_id,
            PayrollRun.status != "draft",
            PayrollRun.period_start == ps, PayrollRun.period_end == pe))).scalars().first()
        if dupe:
            raise HTTPException(409, "An accrual already exists for this employee and period")
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
    ps = parse_date(data.period_start, "period_start")
    pe = parse_date(data.period_end, "period_end")
    today = date.today()
    if not data.draft and (pe.year, pe.month) >= (today.year, today.month):
        raise HTTPException(400, "Can't accrue payroll for the current or a future month; save as draft instead")

    if data.employee_ids:
        emps = (await db.execute(select(Employee).where(
            Employee.org_id == current.org_id, Employee.id.in_(data.employee_ids)))).scalars().all()
    else:
        emps = (await db.execute(select(Employee).where(
            Employee.org_id == current.org_id, Employee.active == True))).scalars().all()  # noqa: E712

    accrued, skipped, results = 0, 0, []
    for emp in emps:
        # reuse the duplicate guard: skip anyone already accrued for this period
        if not data.draft:
            dupe = (await db.execute(select(PayrollRun).where(
                PayrollRun.org_id == current.org_id, PayrollRun.employee_id == emp.id,
                PayrollRun.status != "draft",
                PayrollRun.period_start == ps, PayrollRun.period_end == pe))).scalars().first()
            if dupe:
                skipped += 1
                results.append({"employee_id": emp.id, "status": "skipped", "reason": "already accrued"})
                continue
        try:
            # base salary applies only to fixed-wage staff, matching /calculation
            base = float(emp.wage_value or 0) if emp.wage_type == "fixed" else 0.0
            run = await _accrue_one(db, current.org_id, emp.id, ps, pe,
                                    base_salary=base, draft=data.draft)
            accrued += 1
            results.append({"employee_id": emp.id, "status": run.status,
                            "gross_total": float(run.gross_total)})
        except Exception as exc:                       # one bad employee must not sink the batch
            results.append({"employee_id": emp.id, "status": "error", "reason": str(exc)[:120]})
    await db.commit()
    return {"period": [data.period_start, data.period_end], "accrued": accrued,
            "skipped": skipped, "total": len(emps), "results": results}


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


@router.get("/calculation")
async def payroll_calculation(
    month: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    """Live, non-mutating preview of the month's payroll for every employee."""
    try:
        ps, pe = _month_bounds(month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id, Employee.active == True))).scalars().all()  # noqa: E712
    runs = (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id,
        PayrollRun.period_start == ps, PayrollRun.period_end == pe))).scalars().all()
    run_by_emp = {r.employee_id: r for r in runs}
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
            "position": roles.get(e.role_id) or "—",
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
            "items": items, "totals": grand}


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
    if data.amount is None or float(data.amount) <= 0:
        raise HTTPException(400, "amount must be a positive number")
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
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
    commissions, plus the manual penalties and bonuses for the month."""
    try:
        ps, pe = _month_bounds(month)
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM")
    emp = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
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
    dupe = (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id, PayrollRun.employee_id == data.employee_id,
        PayrollRun.status != "draft",
        PayrollRun.period_start == ps, PayrollRun.period_end == pe))).scalars().first()
    if dupe:
        raise HTTPException(409, "An accrual already exists for this employee and period")

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
    from backend.models import Shift
    start = parse_date(data.date_from, "date_from")
    end = parse_date(data.date_to, "date_to")
    emp = (await db.execute(select(Employee).where(
        Employee.id == data.employee_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
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
    await db.commit()
    return {"ok": True, "hours": hours, "rate": float(rate),
            "wage_posted": float(wage), "ledger_posted": posted}


@router.get("/runs")
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    rows = (await db.execute(select(PayrollRun).where(
        PayrollRun.org_id == current.org_id).order_by(PayrollRun.period_start.desc()).limit(200))).scalars().all()
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
    } for r in rows]
    totals = {k: float(_money(sum(i[k] for i in items))) for k in
              ("base_salary", "commissions_total", "hourly_total", "bonuses_total",
               "penalties_total", "gross_total")}
    return {"items": items, "totals": totals}


@router.post("/runs/{run_id}/pay")
async def mark_run_paid(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_payroll")),
):
    run = (await db.execute(select(PayrollRun).where(
        PayrollRun.id == run_id, PayrollRun.org_id == current.org_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status == "draft":
        raise HTTPException(400, "Accrue the draft before marking it paid")
    eng = CommissionEngine(db, current.org_id)
    await eng._post(employee_id=run.employee_id, entry_type="payout", amount=-_money(run.gross_total),
                    event=None, rule_id=None, ref_type="payroll_run", ref_id=run.id,
                    idempotency_key=f"payout:{run.id}", note="Payroll payout")
    run.status = "paid"
    run.paid_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "status": "paid"}


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
    rows = (await db.execute(q)).scalars().all()
    return {"items": [_ser_coeff(c) for c in rows]}


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
    existing = (await db.execute(select(EmployeeCoefficient).where(
        EmployeeCoefficient.org_id == current.org_id, EmployeeCoefficient.employee_id == data.employee_id,
        EmployeeCoefficient.scope_type == data.scope_type, EmployeeCoefficient.scope_ref == data.scope_ref,
        EmployeeCoefficient.context == data.context))).scalar_one_or_none()
    if existing:
        existing.coefficient = Decimal(str(data.coefficient))
        await db.commit()
        return _ser_coeff(existing)
    c = EmployeeCoefficient(
        org_id=current.org_id, employee_id=data.employee_id, scope_type=data.scope_type,
        scope_ref=data.scope_ref, context=data.context, coefficient=Decimal(str(data.coefficient)))
    db.add(c)
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
        await db.delete(c)
        await db.commit()
    return {"ok": True}
