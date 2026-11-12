"""Overdue task alerts (tk02).

The "task-overdue-sweep" job (core/scheduled.py, every 15 minutes) tells every assignee once when
an open task passes its due time: staff bell + push, kind "task.overdue", linking to the task.

Rules
  * Only open tasks (todo / in_progress) that are not deleted. Done, cancelled and deleted tasks
    never alert; a task closed between the sweep's read and its send is caught by the conditional
    claim below. An overdue task with nobody on it is claimed (stamped) without sending, so it
    leaves the pending index instead of being rescanned every tick; giving it to someone clears
    the stamp and the newcomer is alerted on the next tick.
  * Once per person per overdue spell. Two guards, both in the database:
      - the task is claimed with a conditional UPDATE (still open, not deleted, not yet stamped,
        same due time); a second worker's UPDATE matches nothing and it moves on;
      - each person gets a TaskOverdueAlert row (unique task+employee) inserted with ON CONFLICT DO
        NOTHING; only people whose row was actually inserted are alerted.
    The job also runs under the job runner's advisory lock (one worker at a time on Postgres).
  * Re-arming: moving due_at later AND into the future (PATCH, move-to-today) clears the stamp and
    deletes the rows, so the new due time alerts again. Moving it earlier, to another time that has
    already passed, or editing anything else does not.
    Adding an assignee clears the stamp only; the next tick alerts just the newcomer.
  * Working hours: an alert that comes due outside the task's branch opening hours is held, not
    dropped, until the next opening; nobody is pinged at 3 a.m. The hours are the branch's
    Location.working_hours (else 09:00-18:00), read on the SHOP's clock (dates.shop_tzname, the
    same clock My day and date-only due dates use), so one shop never runs on two clocks.
  * One transaction per shop per tick: bell rows, claims and alert rows commit together, and the
    pushes (push.notify(deferred=...)) are sent only after that commit, off the event loop.
  * At most OVERDUE_BATCH tasks per shop per tick; the rest go out on the next tick. Branches that
    are outside their opening hours are left out before the batch is taken, so they never use up
    another branch's share.
  * Backlog: migration tk02 stamps tasks that were already overdue when it ran, and records their
    current assignees as told, so deploy day sends nothing for old work and adding someone later
    alerts only them.
"""
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import TASK_CLOSED, Organisation, Task, TaskOverdueAlert
from backend.services.dates import now_utc, shop_tzname, utc_to_wall, wall_to_utc

log = logging.getLogger("tekly.tasks")

OVERDUE_BATCH = 500


def _pending(now: datetime) -> list:
    # The first four conditions are ix_tasks_overdue_pending's predicate, so the planner can use it.
    return [Task.archived_at.is_(None), Task.overdue_alerted_at.is_(None), Task.due_at.is_not(None),
            Task.status.notin_(TASK_CLOSED), Task.due_at < now]


async def _held(db: AsyncSession, location_id: Optional[str], now: datetime, tz: str) -> bool:
    """Is `now` outside the branch's working hours, on the shop's clock?"""
    from backend.services.notification_rules import _location_window, shift_into_working_hours
    local = utc_to_wall(now, tz)
    start, end = await _location_window(db, location_id, local)
    return wall_to_utc(shift_into_working_hours(local, start, end), tz) > now


async def overdue_org_ids(db: AsyncSession, now: Optional[datetime] = None) -> list[str]:
    """Active shops with at least one task waiting for its overdue alert."""
    now = now or now_utc()
    return list((await db.execute(
        select(Task.org_id).join(Organisation, Organisation.id == Task.org_id)
        .where(*_pending(now), Organisation.active == True)  # noqa: E712
        .distinct())).scalars().all())


async def rearm(db: AsyncSession, org_id: str, task_ids: Iterable[str]) -> None:
    """Forget who was told about these tasks' overdue spell (their due time moved later)."""
    ids = list(task_ids)
    if ids:
        await db.execute(delete(TaskOverdueAlert).where(
            TaskOverdueAlert.org_id == org_id, TaskOverdueAlert.task_id.in_(ids)))


async def _first_time(db: AsyncSession, org_id: str, task_id: str, people) -> list[str]:
    """Insert a TaskOverdueAlert per person; return the people whose row was new."""
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    fresh = []
    for emp_id in sorted(people):
        stmt = insert(TaskOverdueAlert).values(
            id=str(uuid.uuid4()), org_id=org_id, task_id=task_id,
            employee_id=emp_id, created_at=now_utc()).on_conflict_do_nothing(
            index_elements=["task_id", "employee_id"])
        if (await db.execute(stmt)).rowcount:
            fresh.append(emp_id)
    return fresh


async def sweep_overdue(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> int:
    """Send the due overdue alerts for one shop and commit. Returns how many tasks were announced."""
    from backend.models import Employee
    from backend.services import notification_rules as nr
    from backend.services.push import dispatch_deferred
    now = now or now_utc()
    # Branches outside opening hours are worked out first and left out of the batch, so 500 held
    # tasks at one branch never starve the branches that are open (one query for the branches).
    tz = await shop_tzname(db, org_id)
    branches = (await db.execute(select(Task.location_id).where(
        Task.org_id == org_id, *_pending(now)).distinct())).scalars().all()
    held = [loc for loc in branches if await _held(db, loc, now, tz)]
    cond = [Task.org_id == org_id, *_pending(now)]
    if held:
        named = [loc for loc in held if loc is not None]
        open_here = Task.location_id.notin_(named) if named else Task.location_id.is_not(None)
        if None in held:
            cond.append(and_(Task.location_id.is_not(None), open_here) if named else open_here)
        else:
            cond.append(or_(Task.location_id.is_(None), open_here))
    rows = (await db.execute(select(Task).where(*cond)
                             .order_by(Task.due_at, Task.id).limit(OVERDUE_BATCH)
                             .execution_options(populate_existing=True))).scalars().all()
    if not rows:
        return 0
    staff = set((await db.execute(select(Employee.id).where(
        Employee.org_id == org_id, Employee.active == True))).scalars().all())  # noqa: E712
    pushes: list = []
    sent = 0
    for t in rows:
        claimed = (await db.execute(update(Task).where(
            Task.id == t.id, Task.org_id == org_id, Task.due_at == t.due_at, *_pending(now))
            .values(overdue_alerted_at=now).execution_options(synchronize_session=False))).rowcount
        if not claimed:
            continue            # closed, deleted, moved or claimed by someone else meanwhile
        fresh = await _first_time(db, org_id, t.id, nr.task_people(t) & staff)
        if not fresh:
            continue
        try:
            await nr.notify_task(db, org_id=org_id, task=t, event="overdue", actor_id=None,
                                 recipients=fresh, deferred=pushes, tz=tz)
        except Exception:  # noqa: BLE001 — the rows stay: one broken alert must not repeat every tick
            log.warning("task overdue alert failed (task=%s)", t.id, exc_info=True)
        sent += 1
    await db.commit()
    await dispatch_deferred(db, pushes)
    return sent
