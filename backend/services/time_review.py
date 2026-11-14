"""Owner decision 6: time flagged for review blocks payroll approval for its period.

A time entry (job clock) or an attendance shift with needs_review=True holds up every payroll
approval whose period holds it, until a manager corrects or confirms it with the edit endpoint
(PATCH /timeclock/entries/{id}, PATCH /timeclock/shifts/{id}), which clears the flag and writes an
audit row. The refusal names each flagged item by person, shop day and why it was flagged.

The period is the one the approval itself uses: a window of shop days on the person's clock, as
naive-UTC [start, end) bounds (timesheet approval and attendance wages use the person's zone; a
payroll accrual uses the shop's, as its ledger sums do). An item counts when it started inside it.
"""
from collections.abc import Iterable
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import AuditLog, Employee, Shift, TimeEntry, TimeEntryAudit
from backend.services.dates import utc_to_wall

AUTO_STOP_HOURS = 12        # = timeclock.AUTO_STOP_HOURS (a service doesn't import a router; a test keeps them equal)
SHIFT_CAP_HOURS = 16        # = timeclock.SHIFT_CAP_HOURS


def _day_text(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _hours(v) -> str:
    v = float(v or 0)
    return f"{v:g} h" if v == int(v) else f"{v:.1f} h"


async def flagged_time(db, org_id: str, employee_ids: Iterable[str], start: datetime, end: datetime,
                       tz: Optional[str], *, entries: bool = True, shifts: bool = True) -> list[dict]:
    """Every flagged, not voided time entry and every flagged shift of these people that started
    in [start, end), oldest first, each as {kind, id, employee_id, employee_name, date, reason}."""
    ids = [i for i in employee_ids if i]
    if not ids:
        return []
    names = {i: n for i, n in (await db.execute(select(Employee.id, Employee.name).where(
        Employee.org_id == org_id, Employee.id.in_(ids)))).all()}
    out: list[tuple[datetime, dict]] = []
    if entries:
        rows = (await db.execute(select(TimeEntry).where(
            TimeEntry.org_id == org_id, TimeEntry.employee_id.in_(ids), TimeEntry.needs_review == True,  # noqa: E712
            TimeEntry.state != "void", TimeEntry.started_at >= start, TimeEntry.started_at < end)
            .order_by(TimeEntry.started_at, TimeEntry.id))).scalars().all()
        self_edited = set()
        if rows:
            self_edited = {eid for eid, actor, emp in (await db.execute(
                select(TimeEntryAudit.time_entry_id, TimeEntryAudit.actor_id, TimeEntry.employee_id)
                .join(TimeEntry, TimeEntry.id == TimeEntryAudit.time_entry_id).where(
                    TimeEntryAudit.time_entry_id.in_([e.id for e in rows]), TimeEntryAudit.action == "edit",
                    TimeEntryAudit.field.in_(("started_at", "ended_at"))))).all() if actor == emp}
        for e in rows:
            who = names.get(e.employee_id) or "Unknown"
            if e.id in self_edited:
                why = f"{who} changed their own hours"
            elif (e.duration_sec or 0) <= 0:
                why = "timer stopped with no time on it (a second timer left running)"
            elif (e.duration_sec or 0) >= AUTO_STOP_HOURS * 3600:
                why = f"timer left running, stopped at the {AUTO_STOP_HOURS} h limit"
            else:
                why = "timer cut short where the next entry starts"
            out.append((e.started_at, {"kind": "time_entry", "id": e.id, "employee_id": e.employee_id,
                                       "employee_name": who, "date": utc_to_wall(e.started_at, tz).date(),
                                       "reason": why}))
    if shifts:
        rows = (await db.execute(select(Shift).where(
            Shift.employee_id.in_(ids), Shift.needs_review == True,  # noqa: E712
            Shift.started_at >= start, Shift.started_at < end)
            .order_by(Shift.started_at, Shift.id))).scalars().all()
        capped: dict = {}
        if rows:
            for sid, diff in (await db.execute(select(AuditLog.entity_id, AuditLog.diff).where(
                    AuditLog.org_id == org_id, AuditLog.entity_type == "shift",
                    AuditLog.action == "shift.auto_capped",
                    AuditLog.entity_id.in_([s.id for s in rows])))).all():
                capped[sid] = diff if isinstance(diff, dict) else {}
        for s in rows:
            who = names.get(s.employee_id) or "Unknown"
            if s.id in capped or float(s.hours_worked or 0) >= SHIFT_CAP_HOURS:
                why = f"no clock-out: shift closed at the {SHIFT_CAP_HOURS} h limit"
                elapsed = capped.get(s.id, {}).get("elapsed_hours")
                if elapsed:
                    why += f" (clocked out after {_hours(elapsed)})"
            elif float(s.hours_worked or 0) <= 0:
                why = "a second open shift, closed with no time on it"
            else:
                why = "shift flagged for a manager to check"
            out.append((s.started_at, {"kind": "shift", "id": s.id, "employee_id": s.employee_id,
                                       "employee_name": who, "date": utc_to_wall(s.started_at, tz).date(),
                                       "reason": why}))
    out.sort(key=lambda x: (x[0], x[1]["kind"], x[1]["id"]))
    return [f for _, f in out]


def blocked_message(flagged: list[dict]) -> str:
    n = len(flagged)
    head = f"Can't approve payroll for this period: {n} {'item needs' if n == 1 else 'items need'} a manager's review."
    lines = [f"{f['employee_name']}, {_day_text(f['date'])}{' (shift)' if f['kind'] == 'shift' else ''}: {f['reason']}."
             for f in flagged]
    return " ".join([head, *lines, "Correct or confirm each one with Edit, then approve again."])


def blocked_detail(flagged: list[dict]) -> dict:
    return {"message": blocked_message(flagged),
            "flagged": [{**f, "date": f["date"].isoformat()} for f in flagged]}


async def assert_nothing_flagged(db, org_id: str, employee_ids: Iterable[str], start: datetime, end: datetime,
                                 tz: Optional[str], **kw) -> None:
    """409 listing the flagged items when any of these people's time in [start, end) needs review."""
    flagged = await flagged_time(db, org_id, employee_ids, start, end, tz, **kw)
    if flagged:
        raise HTTPException(409, blocked_detail(flagged))
